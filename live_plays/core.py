"""Live $5 Today's Plays: real bets copying the two Today's Plays paper lanes.

Source: new paper positions in EV_FINDER_v2 (Today's Plays) and EV_FINDER_v3
(Today's Plays -200 to +150). Both lanes often pick the same game, so each game gets
ONE live bet, from whichever lane picked it first.

* Kalshi picks   -> a real immediate-or-cancel Kalshi order, $5 of contracts at the
                    price the paper pick saw (never chases a moved market).
* Polymarket picks -> can't be automated: a phone alert to bet $5 by hand. Log it on
                    the app's Home tab ("Log a bet") and it counts in the live record.

Hard limits enforced here in code (env can lower, never raise):
* $5 of contracts per bet (fee on top, a few cents);
* $50 per Eastern day of Kalshi spend, fees included; the day's spend is the larger of
  this ledger and Kalshi's own order history;
* client_order_id comes from the paper position id, so a retried round can't double-bet;
* only picks opened in the last LOOKBACK and at least 5 min before the game starts.

Off unless LIVE_PLAYS_ENABLED=1 and Kalshi write keys are present (then a dry run that
only logs what it would do).
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
DATA = Path(os.getenv("LIVE_PLAYS_DATA_DIR", "live_plays/data"))
LAB = Path(os.getenv("RESEARCH_LAB_DATA_DIR", "research_lab/data"))
LANES = {"EV_FINDER_v2": "Today's Plays", "EV_FINDER_v3": "Today's Plays (-200 to +150)"}
PREFIX = "pmtp-"

CEIL_PER_BET = 5.00
CEIL_DAILY = 50.00
# Paper picks opened before this aren't copied (live betting started Oct 22, 2026, 11:15pm ET)
GO_LIVE = datetime(2026, 10, 23, 3, 15, tzinfo=timezone.utc)
LOOKBACK = timedelta(minutes=90)
MIN_LEAD = timedelta(minutes=5)

FIELDS = ["trade_id", "paper_id", "lane", "client_order_id", "placed_at", "day", "venue", "game", "title",
          "market", "ticker", "event_ticker", "side", "limit_price", "contracts_wanted", "status", "order_id",
          "filled", "avg_price", "cost", "fee", "close_time", "commence_time", "result", "settled_at", "pl",
          "paper_price", "fair", "ev", "unit", "note", "last_mid", "last_mid_at", "clv_cents"]


def f(v, default=None):
    try:
        x = float(v)
        return default if math.isnan(x) else x
    except (TypeError, ValueError):
        return default


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def et_day(now: datetime) -> str:
    return now.astimezone(EASTERN).strftime("%Y%m%d")


def _env_float(name: str, default: float, ceiling: float) -> float:
    v = f(os.getenv(name, default), default)
    return max(0.0, min(v, ceiling))


def per_bet() -> float:
    return _env_float("LIVE_PLAYS_PER_BET", CEIL_PER_BET, CEIL_PER_BET)


def daily_budget() -> float:
    return _env_float("LIVE_PLAYS_DAILY_BUDGET", CEIL_DAILY, CEIL_DAILY)


def enabled() -> bool:
    if os.getenv("LIVE_PLAYS_ENABLED", "").strip().lower() not in {"1", "true", "on", "yes"}:
        return False
    import kalshi_submit
    return kalshi_submit.write_configured()


def taker_fee(price: float, contracts: int, rate: float = 0.07) -> float:
    return math.ceil(rate * contracts * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def size(price: float, room: float) -> int:
    """Whole contracts within the per-bet cap whose price + fee also fits today's room."""
    cap = min(per_bet(), room)
    if not (0 < price < 1) or cap <= 0:
        return 0
    n = int(math.floor(cap / price + 1e-9))
    while n > 0 and n * price + taker_fee(price, n) > room + 1e-9:
        n -= 1
    return n


def game_of(title: str) -> str:
    """'Indiana at Michigan: Michigan +7.5' -> 'Indiana at Michigan' (one live bet per game)."""
    return str(title or "").split(":", 1)[0].strip()


# ------------------------------------------------------------------ ledgers
def ledger_path(base: Path | None = None) -> Path:
    return Path(base or DATA) / "bets.csv"


def load(base: Path | None = None) -> list[dict]:
    p = ledger_path(base)
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(rows: list[dict], base: Path | None = None) -> None:
    p = ledger_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(p)


def paper_positions(lab: Path | None = None) -> list[dict]:
    """Every position from both Today's Plays lanes, oldest first, with lane + venue filled in."""
    out = []
    for key, label in LANES.items():
        p = Path(lab or LAB) / key / "positions.csv"
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    d = json.loads(r.get("details") or "{}")
                except ValueError:
                    d = {}
                r["lane"] = label
                r["venue"] = d.get("venue") or ""
                r["shown"] = d.get("shown") or ""
                r["decimal"] = d.get("decimal")
                r["ev"] = d.get("ev")
                r["game"] = game_of(r.get("title"))
                out.append(r)
    out.sort(key=lambda r: r.get("opened_at") or "")
    return out


def paper_record(positions: list[dict], stake: float = CEIL_PER_BET) -> dict:
    """Paper record of both lanes combined, ONE bet per game (first lane to pick it), at `stake` per bet."""
    seen, picks = set(), []
    for r in positions:
        if r["game"] in seen:
            continue
        seen.add(r["game"])
        picks.append(r)

    def rec(rows):
        done = [r for r in rows if r.get("status") in {"won", "lost"}]
        scale = [stake / (f(r.get("cost"), 0) or 10.0) for r in done]
        pl = sum((f(r.get("pl"), 0) or 0) * s for r, s in zip(done, scale))
        w = sum(1 for r in done if r["status"] == "won")
        risked = stake * len(done)
        return {"won": w, "lost": len(done) - w, "pl": round(pl, 2), "risked": round(risked, 2),
                "roi": (pl / risked) if risked else None,
                "open": sum(1 for r in rows if r.get("status") == "open")}

    by_venue = {}
    for v in sorted({r["venue"] for r in picks if r["venue"]}):
        by_venue[v] = rec([r for r in picks if r["venue"] == v])
    since = min((r.get("opened_at") or "" for r in picks), default="")
    return {"all": rec(picks), "by_venue": by_venue, "since": since, "picks": picks}


# ------------------------------------------------------------------ choosing new picks
def new_picks(positions: list[dict], rows: list[dict], now: datetime) -> tuple[list[dict], dict]:
    """Paper picks to copy this round: fresh, after go-live, before kickoff, one per game."""
    skips: dict[str, int] = {}

    def skip(k):
        skips[k] = skips.get(k, 0) + 1

    done_ids = {r.get("paper_id") for r in rows}
    # a game counts as taken once any live row exists for it (bet, alert, or a miss we already tried)
    games = {r.get("game") for r in rows if r.get("status") not in {"skipped"}}
    out = []
    for p in positions:
        opened = parse_dt(p.get("opened_at"))
        if opened is None or opened < GO_LIVE or now - opened > LOOKBACK:
            continue
        if p.get("position_id") in done_ids:
            continue
        if p.get("status") != "open":
            skip("already_settled")
            continue
        start = parse_dt(p.get("commence_time"))
        if start is None or start - now < MIN_LEAD:
            skip("too_close_to_start")
            continue
        if p["game"] in games:
            skip("game_already_bet")
            continue
        if p["venue"] not in {"Kalshi", "Polymarket"}:
            skip("other_venue")
            continue
        games.add(p["game"])
        out.append(p)
    return out, skips


def kalshi_price(p: dict) -> float | None:
    """The Kalshi ask the paper pick saw, e.g. details shown '44¢' -> 0.44."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*¢", str(p.get("shown") or ""))
    if m:
        v = float(m.group(1)) / 100.0
        return v if 0 < v < 1 else None
    return None


def american(decimal: float | None) -> str:
    if not decimal or decimal <= 1:
        return ""
    return f"+{round((decimal - 1) * 100)}" if decimal >= 2 else f"-{round(100 / (decimal - 1))}"


def _base_row(p: dict, now: datetime, venue: str) -> dict:
    return {"paper_id": p["position_id"], "lane": p["lane"], "placed_at": iso(now), "day": et_day(now),
            "venue": venue, "game": p["game"], "title": p.get("title", ""), "market": p.get("market_type", ""),
            "ticker": p.get("ticker", ""), "event_ticker": p.get("event_ticker", ""), "side": p.get("side", "YES"),
            "close_time": p.get("close_time", ""), "commence_time": p.get("commence_time", ""),
            "paper_price": p.get("shown", ""), "fair": p.get("fair", ""),
            "ev": p.get("ev") if p.get("ev") is not None else p.get("net_edge", ""), "unit": per_bet()}


# ------------------------------------------------------------------ spending
def spent_today(rows: list[dict], day: str) -> float:
    total = 0.0
    for r in rows:
        if r.get("day") != day or r.get("venue") != "Kalshi":
            continue
        if r.get("status") in {"skipped", "error", "no_fill", "dry_run"}:
            continue
        if r.get("status") == "submitted":
            total += (f(r.get("contracts_wanted"), 0) or 0) * (f(r.get("limit_price"), 0) or 0)
        else:
            total += (f(r.get("cost"), 0) or 0) + (f(r.get("fee"), 0) or 0)
    return total


# ------------------------------------------------------------------ the round
def place(picks: list[dict], rows: list[dict], client, now: datetime, *, live: bool, notify=None) -> dict:
    """Kalshi picks -> real orders (when live); Polymarket picks -> phone alerts. Appends to rows."""
    from league import live_sharp   # reuse its proven fill / unclear-reply handling

    day = et_day(now)
    out = {"placed": 0, "filled": 0, "alerts": 0, "skipped": {}, "new": []}

    def skip(k):
        out["skipped"][k] = out["skipped"].get(k, 0) + 1

    spent = spent_today(rows, day)
    if live and any(p["venue"] == "Kalshi" for p in picks):
        try:
            spent = max(spent, client.committed_cost_for_prefix(f"{PREFIX}{day}-"))
        except Exception as exc:
            out["error"] = f"kalshi unreachable: {type(exc).__name__}"
            picks = [p for p in picks if p["venue"] != "Kalshi"]
    cash = None
    if live and client is not None:
        try:
            cash = client.get_balance_dollars()
        except Exception:
            cash = None
    out["cash"] = cash

    for p in picks:
        if p["venue"] == "Polymarket":
            r = {**_base_row(p, now, "Polymarket"), "trade_id": f"pmtp-poly-{p['position_id']}",
                 "status": "alert", "note": f"bet ${per_bet():.0f} by hand at {p.get('shown')}"}
            rows.append(r)
            out["alerts"] += 1
            out["new"].append(r)
            continue
        price = kalshi_price(p)
        if price is None:
            skip("bad_price")
            continue
        room = daily_budget() - spent
        # only full-size bets: when less than one bet's worth is left today, stop for the day
        count = size(price, room) if room + 1e-9 >= per_bet() else 0
        if count < 1:
            skip("daily_budget" if room < per_bet() else "too_expensive")
            rows.append({**_base_row(p, now, "Kalshi"), "trade_id": f"{PREFIX}{day}-{p['position_id']}",
                         "status": "skipped", "note": "daily $ cap reached" if room < per_bet() else "price too high"})
            continue
        if cash is not None and count * price * 1.03 > cash:
            skip("low_cash")
            rows.append({**_base_row(p, now, "Kalshi"), "trade_id": f"{PREFIX}{day}-{p['position_id']}",
                         "status": "skipped", "note": f"Kalshi cash too low (${cash:.2f})"})
            continue
        cid = f"{PREFIX}{day}-{p['position_id']}"
        r = {**_base_row(p, now, "Kalshi"), "trade_id": cid, "client_order_id": cid,
             "limit_price": round(price, 4), "contracts_wanted": count, "status": "submitted"}
        if not live:
            r.update({"status": "dry_run", "note": "live betting off: would have bought "
                      f"{count} @ {price * 100:.0f}¢", "pl": 0.0})
            rows.append(r)
            continue
        try:
            existing = client.find_order_by_client_id(cid)
            order = existing or client.create_buy_order(
                ticker=p["ticker"], outcome_side=p["side"], count=count, max_price=price,
                execution="TAKE", client_order_id=cid)
            order = order.get("order", order) if isinstance(order, dict) else {}
            if not existing and not order.get("fill_count_fp") and not order.get("fill_count"):
                order = client.find_order_by_client_id(cid) or order
            live_sharp._record_fill(r, order, client)
        except Exception as exc:
            r.update({"status": "error", "note": f"{type(exc).__name__}: {str(exc)[:160]}", "pl": 0.0})
            if not live_sharp._is_rejection(exc):
                live_sharp._confirm_unknown(r, client, exc)
            if r["status"] == "error":
                skip("order_error")
        rows.append(r)
        if r["status"] not in {"error", "no_fill"}:
            out["placed"] += 1
            used = (f(r.get("cost"), 0) or 0) + (f(r.get("fee"), 0) or 0)
            spent += used
            if cash is not None:
                cash -= used
        if r["status"] == "open":
            out["filled"] += 1
            out["new"].append(r)
    out["spent_today"] = round(spent, 2)
    if notify and out["new"]:
        try:
            notify(out["new"])
        except Exception as exc:
            out["notify_error"] = type(exc).__name__
    return out


def settle(rows: list[dict], markets: dict, now: datetime) -> list[dict]:
    """Settle open Kalshi bets from Kalshi's official result (same rules as live Sharp)."""
    from league import live_sharp
    kal = [r for r in rows if r.get("venue") == "Kalshi" and r.get("status") == "open"]
    if not kal:
        return []
    return live_sharp.settle(kal, lambda tickers: markets, now=now)


# ------------------------------------------------------------------ live record
def match_logged(rows: list[dict], logged: list[dict], results: dict) -> list[dict]:
    """Polymarket alerts joined with the bets you logged by hand for the same pick."""
    out = []
    used = set()
    for r in rows:
        if r.get("venue") != "Polymarket" or r.get("status") != "alert":
            continue
        hit = None
        for b in logged:
            if b.get("bet_id") in used:
                continue
            same_pick = (b.get("settle_ticker") and b.get("settle_ticker") == r.get("ticker")
                         and (b.get("settle_side") or "YES") == (r.get("side") or "YES"))
            same_label = game_of(b.get("game")) == r.get("game") and str(b.get("label") or "") in str(r.get("title"))
            if same_pick or same_label:
                hit = b
                break
        if hit:
            used.add(hit.get("bet_id"))
            res = results.get(hit.get("bet_id") or "") or {}
            out.append({**r, "logged": True, "stake": f(hit.get("stake"), 0), "odds": hit.get("odds"),
                        "status": res.get("status", "open"), "pl": res.get("pl")})
        else:
            out.append({**r, "logged": False})
    return out


def live_record(rows: list[dict], poly: list[dict]) -> dict:
    kal = [r for r in rows if r.get("venue") == "Kalshi" and r.get("status") in {"open", "won", "lost", "void"}]
    logged = [r for r in poly if r.get("logged")]
    done_k = [r for r in kal if r["status"] in {"won", "lost"}]
    done_p = [r for r in logged if r.get("status") in {"won", "lost"}]
    pl = sum(f(r.get("pl"), 0) or 0 for r in done_k) + sum(f(r.get("pl"), 0) or 0 for r in done_p)
    risked = (sum((f(r.get("cost"), 0) or 0) + (f(r.get("fee"), 0) or 0) for r in done_k)
              + sum(f(r.get("stake"), 0) or 0 for r in done_p))
    won = sum(1 for r in done_k + done_p if r["status"] == "won")
    return {"won": won, "lost": len(done_k) + len(done_p) - won, "pl": round(pl, 2),
            "roi": (pl / risked) if risked else None,
            "open": sum(1 for r in kal if r["status"] == "open") + sum(1 for r in logged if r.get("status") == "open"),
            "kalshi_bets": len(kal), "poly_logged": len(logged),
            "poly_unlogged": sum(1 for r in poly if not r.get("logged"))}
