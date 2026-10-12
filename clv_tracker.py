"""Closing-line value (CLV) capture for PM Edge signals.

CLV here means: how did the price we were told to pay compare with the
market just BEFORE the event started?  Two closes are recorded per signal:

* closing_mid   - Kalshi side mid at the last pre-game snapshot
* closing_fair  - sportsbook consensus fair for the SAME side at the last
                  pre-game snapshot

v2 fixes (2026-10):
* Never uses a price observed after commence_time.  v1 fetched the live mid
  up to 6h after start, so "CLV" was really in-game score movement.
* Fair lookup is side-matched.  v1 ignored YES/NO and could return the
  opposite side's probability.
* One row per signal_id, and duplicate (ticker, side, entry) signals are
  collapsed so resends do not double-count.
* Writes to a new ledger (pm_edge_clv_ledger_v2.csv) so v1's contaminated
  rows are not mixed into new analysis.

Close source, in order of preference:
1. Live Kalshi quote, when the run happens 0-CLOSE_WINDOW minutes pre-start.
2. Last candidate-ledger snapshot for that ticker+side before commence
   (the watcher logs quotes every run, so this is at most ~30-45 min old).
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

SIGNAL_LEDGER = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))
CANDIDATE_LEDGER = Path(os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv"))
CLV_LEDGER = Path(os.getenv("PM_EDGE_CLV_LEDGER_PATH", "pm_edge_clv_ledger_v2.csv"))
KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
# A live quote counts as "closing" only inside this pre-start window.
CLOSE_WINDOW_MINUTES = float(os.getenv("PM_EDGE_CLV_CLOSE_WINDOW_MINUTES", "35"))
# Snapshot fallback must be at most this old relative to commence.
MAX_SNAPSHOT_AGE_MINUTES = float(os.getenv("PM_EDGE_CLV_MAX_SNAPSHOT_AGE_MINUTES", "90"))
# Give up on a signal this long after start (snapshot fallback only).
FALLBACK_HORIZON_HOURS = float(os.getenv("PM_EDGE_CLV_FALLBACK_HORIZON_HOURS", "36"))

FIELDS = [
    "signal_id", "ticker", "side", "lane", "entry_price",
    "closing_mid", "closing_fair", "clv_cents", "clv_vs_fair_cents",
    "close_source", "close_observed_at", "commence_time",
    "minutes_before_start", "captured_at",
]


def _dt(value: str | None):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_px(x):
    if x is not None and x > 1:
        x /= 100.0
    return x


def _live_side_mid(ticker: str, side: str, getter=requests.get) -> float | None:
    response = getter(f"{KALSHI_PUBLIC_BASE}/markets/{quote(ticker, safe='')}", timeout=12)
    response.raise_for_status()
    payload = response.json()
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        return None

    def px(name):
        value = market.get(name + "_dollars")
        if value in (None, ""):
            value = market.get(name)
        return _norm_px(_float(value))

    bid, ask = px("yes_bid"), px("yes_ask")
    if bid is None or ask is None or ask <= 0:
        return None
    yes_mid = (bid + ask) / 2.0
    return yes_mid if side == "YES" else 1.0 - yes_mid


def _side_mid_from_row(row: dict, side: str) -> float | None:
    if side == "YES":
        bid, ask = _norm_px(_float(row.get("yes_bid"))), _norm_px(_float(row.get("yes_ask")))
    else:
        bid, ask = _norm_px(_float(row.get("no_bid"))), _norm_px(_float(row.get("no_ask")))
        if bid is None or ask is None:
            yb, ya = _norm_px(_float(row.get("yes_bid"))), _norm_px(_float(row.get("yes_ask")))
            if yb is not None and ya is not None:
                bid, ask = 1.0 - ya, 1.0 - yb
    if bid is None or ask is None or ask <= 0:
        return None
    return (bid + ask) / 2.0


def _load_snapshots() -> dict[tuple[str, str], list[tuple[datetime, dict]]]:
    out: dict[tuple[str, str], list[tuple[datetime, dict]]] = {}
    if not CANDIDATE_LEDGER.exists():
        return out
    try:
        with CANDIDATE_LEDGER.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                ticker = str(row.get("ticker") or "")
                side = str(row.get("side") or "").upper()
                ts = _dt(row.get("evaluated_at"))
                if not ticker or side not in {"YES", "NO"} or ts is None:
                    continue
                out.setdefault((ticker, side), []).append((ts, row))
    except OSError:
        return {}
    return out


def _last_before(snaps, commence: datetime, *, need) -> tuple[datetime, float] | None:
    best = None
    for ts, row in snaps or []:
        if ts > commence or commence - ts > timedelta(minutes=MAX_SNAPSHOT_AGE_MINUTES):
            continue
        value = need(row)
        if value is None:
            continue
        if best is None or ts > best[0]:
            best = (ts, value)
    return best


def capture_clv(*, now: datetime | None = None, getter=requests.get) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing: set[str] = set()
    seen_keys: set[tuple[str, str, str]] = set()
    if CLV_LEDGER.exists():
        try:
            with CLV_LEDGER.open("r", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    existing.add(str(row.get("signal_id") or ""))
                    seen_keys.add((str(row.get("ticker")), str(row.get("side")), str(row.get("entry_price"))))
        except OSError:
            pass
    if not SIGNAL_LEDGER.exists():
        return {"captured": 0, "eligible": 0, "skipped_duplicate": 0, "waiting": 0}

    with SIGNAL_LEDGER.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    snapshots = None

    captured = eligible = dupes = waiting = 0
    for signal in rows:
        if str(signal.get("record_type") or "signal") != "signal":
            continue
        signal_id = str(signal.get("signal_id") or "")
        if not signal_id or signal_id in existing:
            continue
        commence = _dt(signal.get("commence_time"))
        ticker = str(signal.get("ticker") or "")
        side = str(signal.get("side") or "").upper()
        entry = _float(signal.get("recommended_limit"))
        if commence is None or not ticker or side not in {"YES", "NO"} or entry is None:
            continue
        minutes_to_start = (commence - now).total_seconds() / 60.0
        if minutes_to_start > CLOSE_WINDOW_MINUTES:
            waiting += 1
            continue  # too early; a later run will catch the close
        if minutes_to_start < -FALLBACK_HORIZON_HOURS * 60:
            continue
        eligible += 1
        key = (ticker, side, str(round(entry, 6)))
        if key in seen_keys:
            dupes += 1
            existing.add(signal_id)
            continue

        if snapshots is None:
            snapshots = _load_snapshots()
        snaps = snapshots.get((ticker, side), [])

        closing_mid = None
        close_source = ""
        close_observed = None
        if 0 <= minutes_to_start <= CLOSE_WINDOW_MINUTES:
            try:
                closing_mid = _live_side_mid(ticker, side, getter=getter)
            except Exception:
                closing_mid = None
            if closing_mid is not None:
                close_source, close_observed = "live_pregame", now
        if closing_mid is None:
            snap = _last_before(snaps, commence, need=lambda r: _side_mid_from_row(r, side))
            if snap is not None:
                close_observed, closing_mid = snap
                close_source = "snapshot_pregame"
        if closing_mid is None:
            if minutes_to_start >= 0:
                waiting += 1
            continue  # no honest pre-game close available

        fair = _last_before(snaps, commence, need=lambda r: _float(r.get("fair_probability")))
        closing_fair = fair[1] if fair else None

        out = {
            "signal_id": signal_id,
            "ticker": ticker,
            "side": side,
            "lane": str(signal.get("lane") or ""),
            "entry_price": round(entry, 6),
            "closing_mid": round(closing_mid, 6),
            "closing_fair": "" if closing_fair is None else round(closing_fair, 6),
            "clv_cents": round((closing_mid - entry) * 100.0, 4),
            "clv_vs_fair_cents": "" if closing_fair is None else round((closing_fair - entry) * 100.0, 4),
            "close_source": close_source,
            "close_observed_at": _iso(close_observed),
            "commence_time": _iso(commence),
            "minutes_before_start": round((commence - close_observed).total_seconds() / 60.0, 2),
            "captured_at": _iso(now),
        }
        CLV_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        write_header = not CLV_LEDGER.exists() or CLV_LEDGER.stat().st_size == 0
        with CLV_LEDGER.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(out)
        existing.add(signal_id)
        seen_keys.add(key)
        captured += 1
    return {"captured": captured, "eligible": eligible, "skipped_duplicate": dupes, "waiting": waiting}


def summarize(path: Path | None = None) -> dict:
    """Average CLV by lane - the number that says whether an edge is real."""
    path = path or CLV_LEDGER
    if not path.exists():
        return {}
    by_lane: dict[str, list[float]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            v = _float(row.get("clv_cents"))
            if v is not None:
                by_lane.setdefault(str(row.get("lane") or "unknown"), []).append(v)
    return {
        lane: {"n": len(v), "avg_clv_cents": round(sum(v) / len(v), 2),
               "beat_close_pct": round(sum(1 for x in v if x > 0) / len(v), 3)}
        for lane, v in by_lane.items()
    }


if __name__ == "__main__":
    import sys
    if "--summary" in sys.argv:
        print(summarize())
    else:
        print(capture_clv())
