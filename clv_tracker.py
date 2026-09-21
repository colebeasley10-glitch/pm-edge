from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

SIGNAL_LEDGER = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))
CANDIDATE_LEDGER = Path(os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv"))
CLV_LEDGER = Path(os.getenv("PM_EDGE_CLV_LEDGER_PATH", "pm_edge_clv_ledger.csv"))
KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
FIELDS = [
    "signal_id","ticker","side","entry_price","closing_mid",
    "closing_consensus_fair","clv_cents","captured_at",
]


def _dt(value: str | None):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_mid(ticker: str, getter=requests.get) -> float | None:
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
        x = _float(value)
        if x is not None and x > 1:
            x /= 100.0
        return x
    bid, ask = px("yes_bid"), px("yes_ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _latest_closing_fair(ticker: str, commence: datetime) -> float | None:
    if not CANDIDATE_LEDGER.exists():
        return None
    best = None
    try:
        with CANDIDATE_LEDGER.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("ticker") or "") != ticker:
                    continue
                ts = _dt(row.get("evaluated_at"))
                fair = _float(row.get("fair_probability"))
                if ts is None or fair is None or ts > commence:
                    continue
                age = commence - ts
                if age > timedelta(minutes=45):
                    continue
                if best is None or ts > best[0]:
                    best = (ts, fair)
    except OSError:
        return None
    return best[1] if best else None


def capture_clv(*, now: datetime | None = None, getter=requests.get) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing = set()
    if CLV_LEDGER.exists():
        try:
            with CLV_LEDGER.open("r", encoding="utf-8", newline="") as fh:
                existing = {str(row.get("signal_id") or "") for row in csv.DictReader(fh)}
        except OSError:
            existing = set()
    if not SIGNAL_LEDGER.exists():
        return {"captured": 0, "eligible": 0}

    with SIGNAL_LEDGER.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    captured = eligible = 0
    for signal in rows:
        if str(signal.get("record_type") or "signal") != "signal":
            continue
        signal_id = str(signal.get("signal_id") or "")
        if not signal_id or signal_id in existing:
            continue
        alerted_at = _dt(signal.get("alerted_at"))
        commence = _dt(signal.get("commence_time"))
        if alerted_at is None or commence is None:
            continue
        if alerted_at.date() != commence.date():
            continue
        if commence > now or commence < now - timedelta(hours=6):
            continue
        eligible += 1
        ticker = str(signal.get("ticker") or "")
        side = str(signal.get("side") or "").upper()
        entry = _float(signal.get("recommended_limit"))
        if not ticker or side not in {"YES","NO"} or entry is None:
            continue
        try:
            yes_mid = _market_mid(ticker, getter=getter)
        except Exception:
            continue
        if yes_mid is None:
            continue
        side_mid = yes_mid if side == "YES" else 1.0 - yes_mid
        fair = _latest_closing_fair(ticker, commence)
        clv_cents = (side_mid - entry) * 100.0
        out = {
            "signal_id": signal_id,
            "ticker": ticker,
            "side": side,
            "entry_price": round(entry, 6),
            "closing_mid": round(side_mid, 6),
            "closing_consensus_fair": "" if fair is None else round(fair, 6),
            "clv_cents": round(clv_cents, 4),
            "captured_at": now.isoformat().replace("+00:00", "Z"),
        }
        CLV_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        write_header = not CLV_LEDGER.exists() or CLV_LEDGER.stat().st_size == 0
        with CLV_LEDGER.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(out)
        existing.add(signal_id)
        captured += 1
    return {"captured": captured, "eligible": eligible}


if __name__ == "__main__":
    print(capture_clv())
