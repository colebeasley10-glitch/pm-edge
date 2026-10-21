"""Maker Bot: $1 resting Kalshi orders priced better than Pinnacle's fair line.

REAL MONEY (off unless MAKER_BOT_ENABLED=1 and Kalshi write keys are set; otherwise
every round is a dry run that logs what it would have posted).

Each round (right after a Research Lab round, see .github/workflows/maker-bot.yml):

1. RECONCILE. Look up every order we posted on Kalshi. Contracts that filled become a
   bet; an order that expired with nothing filled cost nothing and is dropped from stats.
   On the first round after a fill, record the market's mid price (the "markout"): if it
   is usually below what we paid, we're only getting filled when we're wrong.
2. SETTLE filled bets from Kalshi's official market result.
3. POST new orders from research_lab/data/maker_candidates.json (pre-game markets with a
   Pinnacle fair price fetched minutes ago):
     * PRICE: one cent above the best bid (front of the queue), never at or above the ask
       (post-only: it can only rest, never take). No bid at all -> fair minus 3c;
     * EDGE = Pinnacle fair - price - Kalshi maker fee per contract. Posted only from 1% to
       10% (bigger "edges" are almost always a mismatched market);
     * SIZE scales with edge: 1/6 unit at 1% up to a full unit at 6%+, in a straight line
       (big Sharp flow on the same side counts as +1% edge for sizing only);
     * only contracts priced 10c to 90c;
     * KALSHI EXPIRES THE ORDER ITSELF at the earlier of ORDER_TTL from now or
       START_BUFFER before the game starts. Nothing can rest into the game, even if this
       bot never runs again. Next round re-prices from a fresh Pinnacle line.
4. Limits enforced here: $5 full unit, $25 per Eastern day counting resting orders at
   full price, one bet per game, at most MAX_NEW_PER_RUN new orders per round.
   To scale up, raise CEIL_UNIT / CEIL_DAILY in code (env can only lower them).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"
LEDGER = DATA_DIR / "orders.csv"
STATUS = DATA_DIR / "status.json"
CANDIDATES = ROOT / "research_lab" / "data" / "maker_candidates.json"
PREFIX = "pmmk-"

# Hard ceilings. Env can lower these, never raise them.
CEIL_UNIT = 5.00           # a full unit (6%+ edge)
CEIL_DAILY = 25.00
CEIL_PER_ORDER = CEIL_UNIT  # kept for older callers

MIN_EDGE = 0.01            # fair - price - maker fee; below this nothing is posted
MAX_EDGE = 0.10            # above this the "edge" is almost always a bad market match
FULL_EDGE = 0.06           # edge at which the stake reaches a full unit
MIN_FRACTION = 1 / 6       # stake at MIN_EDGE
SHARP_BONUS = 0.01         # Sharp flow on our side counts as this much extra edge (sizing only)
NO_BID_EDGE = 0.03         # empty book: rest at fair minus this
BAND_LOW, BAND_HIGH = 0.10, 0.90
ORDER_TTL = timedelta(minutes=50)
START_BUFFER = timedelta(minutes=5)
MIN_LIFE = timedelta(minutes=5)           # don't bother posting an order that would live < 5 min
MAX_CANDIDATE_AGE = timedelta(minutes=20)  # candidates file must be this fresh
MAX_NEW_PER_RUN = 10
MAKER_FEE_RATE = 0.0175                   # Kalshi maker fee: ceil(0.0175 * C * P * (1-P)) cents

FIELDS = ["client_order_id", "order_id", "placed_at", "day", "mode", "ticker", "side", "event", "label",
          "game", "sport", "market", "starts", "expires_at", "fair", "price", "contracts", "edge", "sharp",
          "bid", "ask", "status", "filled", "cost", "fee", "result", "pl", "settled_at", "note",
          "mark_mid", "markout", "marked_at"]
RESTING, FILLED, EXPIRED, DRY = "resting", "filled", "expired", "dry_run"
DONE = {"won", "lost", "void"}


# ---------------------------------------------------------------- config
def _env_float(name, default, ceiling):
    try:
        v = float(os.getenv(name, default))
    except ValueError:
        v = default
    return max(0.0, min(v, ceiling))


def unit() -> float:
    return _env_float("MAKER_BOT_UNIT", CEIL_UNIT, CEIL_UNIT)


per_order = unit


def daily_budget() -> float:
    return _env_float("MAKER_BOT_DAILY_BUDGET", CEIL_DAILY, CEIL_DAILY)


def live_enabled() -> bool:
    if os.getenv("MAKER_BOT_ENABLED", "").strip().lower() not in {"1", "true", "on", "yes"}:
        return False
    import kalshi_submit
    return kalshi_submit.write_configured()


# ---------------------------------------------------------------- helpers
def f(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def et_day(d: datetime) -> str:
    return d.astimezone(EASTERN).strftime("%Y%m%d")


def maker_fee(price: float, contracts: int) -> float:
    return math.ceil(MAKER_FEE_RATE * contracts * price * (1 - price) * 100 - 1e-9) / 100


def contracts_for(price: float, cap: float) -> int:
    return int(math.floor(cap / price + 1e-9)) if 0 < price < 1 else 0


def edge_at(fair: float, price: float, contracts: int) -> float:
    """Expected profit per contract as a share of $1, after the maker fee on this order."""
    if contracts < 1:
        return -1.0
    return fair - price - maker_fee(price, contracts) / contracts


def stake_for(edge: float, sharp: bool, full_unit: float) -> float:
    """Dollars to put up: 1/6 unit at MIN_EDGE rising linearly to a full unit at FULL_EDGE."""
    e = edge + (SHARP_BONUS if sharp else 0.0)
    if e < MIN_EDGE:
        return 0.0
    frac = MIN_FRACTION + (1 - MIN_FRACTION) * (e - MIN_EDGE) / (FULL_EDGE - MIN_EDGE)
    return round(full_unit * max(MIN_FRACTION, min(1.0, frac)), 2)


def size(fair: float, price: float, sharp: bool, full_unit: float) -> tuple[int, float]:
    """(contracts, edge) for an order at this price. Never more than a full unit of cost."""
    n = max(1, contracts_for(price, full_unit))
    for _ in range(3):   # fee rounding depends on size; settle on a stable size
        e = edge_at(fair, price, n)
        stake = stake_for(e, sharp, full_unit)
        if stake <= 0:
            return 0, e
        new_n = max(1, int(round(stake / price)))
        while new_n > 1 and new_n * price > full_unit + 1e-9:
            new_n -= 1
        if new_n == n:
            break
        n = new_n
    if n * price > full_unit + 1e-9:
        return 0, edge_at(fair, price, n)
    return n, edge_at(fair, price, n)


def quote(c: dict, full_unit: float) -> tuple[float | None, int, float, str]:
    """(price, contracts, edge, reason). price None = skip."""
    fair, ask, bid = f(c.get("fair")), f(c.get("ask")), f(c.get("bid"))
    if fair is None or ask is None:
        return None, 0, 0.0, "no_price"
    ceiling = round(ask - 0.01, 2)                       # post-only: never touch the ask
    if bid is not None and bid > 0:
        price = round(min(bid + 0.01, ceiling), 2)       # front of the queue
    else:
        price = round(min(math.floor((fair - NO_BID_EDGE) * 100) / 100, ceiling), 2)
    if price < 0.01:
        return None, 0, 0.0, "no_price"
    if not (BAND_LOW - 1e-9 <= price <= BAND_HIGH + 1e-9):
        return None, 0, 0.0, "outside_10c_90c"
    n, e = size(fair, price, bool(c.get("sharp")), full_unit)
    if e > MAX_EDGE:
        return None, 0, e, "edge_too_big_check_match"
    if n < 1 or e + 1e-9 < MIN_EDGE:
        return None, 0, e, "edge_under_1pct"
    return price, n, e, "ok"


def client_id(c: dict, now: datetime) -> str:
    raw = f"{c['ticker']}|{c['side']}|{iso(now)}"
    return PREFIX + hashlib.sha1(raw.encode()).hexdigest()[:20]


# ---------------------------------------------------------------- ledger
def load(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    try:
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        return []


def save(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(path)


def committed_today(rows: list[dict], day: str) -> float:
    """Resting orders at full price + filled cost and fees. Expired-unfilled costs nothing."""
    total = 0.0
    for r in rows:
        if r.get("day") != day or r.get("mode") != "live":
            continue
        if r.get("status") == RESTING:
            total += (f(r.get("contracts"), 0) * f(r.get("price"), 0)
                      + maker_fee(f(r.get("price"), 0.5), int(f(r.get("contracts"), 0) or 0)))
        elif r.get("status") in {FILLED} | DONE:
            total += f(r.get("cost"), 0) + f(r.get("fee"), 0)
    return round(total, 2)


def busy_events(rows: list[dict], now: datetime, mode: str) -> set[str]:
    """Games we already hold or have an order resting on (one bet per game)."""
    out = set()
    for r in rows:
        if r.get("mode") != mode:
            continue
        exp = parse_dt(r.get("expires_at"))
        if r.get("status") in {FILLED} | DONE or (r.get("status") in {RESTING, DRY} and exp and exp > now):
            out.add(r.get("event"))
    return out


# ---------------------------------------------------------------- reconcile / settle
def _order_fill(order: dict) -> float:
    raw = order.get("fill_count_fp") if order.get("fill_count_fp") is not None else order.get("fill_count")
    return f(raw, 0.0) or 0.0


def reconcile(rows: list[dict], orders: list[dict], now: datetime) -> dict:
    """Update resting rows from Kalshi's order list (matched by client_order_id)."""
    by_cid = {str(o.get("client_order_id") or ""): o for o in orders}
    out = {"filled": 0, "expired": 0, "still_resting": 0}
    for r in rows:
        if r.get("mode") != "live" or r.get("status") != RESTING:
            continue
        o = by_cid.get(r["client_order_id"])
        exp = parse_dt(r.get("expires_at"))
        if o:
            r["order_id"] = o.get("order_id") or r.get("order_id")
            filled = _order_fill(o)
            if filled > 0:
                r["filled"] = round(filled, 2)
            status = str(o.get("status") or "").lower()
            finished = status in {"canceled", "cancelled", "executed", "filled", "expired"}
        else:
            filled = f(r.get("filled"), 0.0) or 0.0
            finished = False
        past_expiry = exp is not None and now > exp + timedelta(minutes=2)
        if not (finished or past_expiry):
            out["still_resting"] += 1
            continue
        if not o and past_expiry and filled <= 0:
            r["note"] = (r.get("note") or "") + " order not found on Kalshi after expiry"
        if filled > 0:
            price = f(r.get("price"), 0.0)       # post-only maker fills happen at our limit
            r.update({"status": FILLED, "cost": round(filled * price, 2),
                      "fee": maker_fee(price, int(round(filled)))})
            out["filled"] += 1
        else:
            r.update({"status": EXPIRED, "cost": 0.0, "fee": 0.0, "pl": 0.0})
            out["expired"] += 1
    return out


def side_mid(m: dict, side: str) -> float | None:
    bid, ask = (m.get("yes_bid"), m.get("yes_ask")) if side == "YES" else (m.get("no_bid"), m.get("no_ask"))
    bid, ask = f(bid), f(ask)
    if bid is None or ask is None or not (0 < bid <= ask < 1):
        return None
    return (bid + ask) / 2


def mark(rows: list[dict], markets: dict, now: datetime) -> int:
    """First pre-game look after a fill: mid price minus what we paid (in cents per contract).
    Consistently negative = we only get filled when the market has moved against us."""
    n = 0
    for r in rows:
        if r.get("status") != FILLED or r.get("markout") not in ("", None):
            continue
        start = parse_dt(r.get("starts"))
        if start is None or now >= start:
            continue
        m = markets.get(r["ticker"]) or {}
        mid = side_mid(m, r["side"])
        if mid is None:
            continue
        r.update({"mark_mid": round(mid, 4), "markout": round((mid - f(r.get("price"), 0.0)) * 100, 2),
                  "marked_at": iso(now)})
        n += 1
    return n


def settle(rows: list[dict], markets: dict, now: datetime) -> int:
    n = 0
    for r in rows:
        if r.get("status") != FILLED:
            continue
        m = markets.get(r["ticker"]) or {}
        result = str(m.get("result") or "").lower()
        if result not in {"yes", "no", "void", "voided", "canceled", "cancelled"}:
            continue
        cost, fee, filled = f(r.get("cost"), 0.0), f(r.get("fee"), 0.0), f(r.get("filled"), 0.0)
        if result in {"yes", "no"}:
            won = result == r["side"].lower()
            pl = (filled - cost - fee) if won else -(cost + fee)
            r.update({"status": "won" if won else "lost", "result": result, "pl": round(pl, 2)})
        else:
            r.update({"status": "void", "result": "void", "pl": 0.0})
        r["settled_at"] = iso(now)
        n += 1
    return n


# ---------------------------------------------------------------- post
def load_candidates(now: datetime, path: Path | None = None) -> tuple[list[dict], str]:
    path = path or CANDIDATES
    try:
        d = json.loads(path.read_text())
    except Exception:
        return [], "no candidates file"
    at = parse_dt(d.get("at"))
    if at is None or now - at > MAX_CANDIDATE_AGE:
        return [], f"candidates are stale ({d.get('at')})"
    return d.get("candidates") or [], "ok"


def plan(candidates: list[dict], rows: list[dict], now: datetime, mode: str) -> tuple[list[dict], dict]:
    """New orders to post this round (respecting every limit), plus reasons for skips."""
    cap = per_order()
    day = et_day(now)
    room = daily_budget() - committed_today(rows, day)
    busy = busy_events(rows, now, mode)
    skips: dict[str, int] = {}
    ranked = []
    for c in candidates:
        start = parse_dt(c.get("starts"))
        if start is None:
            skips["no_start_time"] = skips.get("no_start_time", 0) + 1
            continue
        expires = min(now + ORDER_TTL, start - START_BUFFER)
        if expires - now < MIN_LIFE:
            skips["too_close_to_start"] = skips.get("too_close_to_start", 0) + 1
            continue
        if c.get("event") in busy:
            skips["already_on_game"] = skips.get("already_on_game", 0) + 1
            continue
        price, n, e, why = quote(c, cap)
        if price is None:
            skips[why] = skips.get(why, 0) + 1
            continue
        ranked.append({**c, "price": price, "contracts": n, "edge": round(e, 4),
                       "stake": round(n * price, 2), "expires_at": iso(expires)})
    ranked.sort(key=lambda x: x["edge"] + (SHARP_BONUS if x.get("sharp") else 0), reverse=True)
    picked = []
    for c in ranked:
        if len(picked) >= MAX_NEW_PER_RUN:
            skips["run_limit"] = skips.get("run_limit", 0) + 1
            continue
        if c["event"] in busy:
            skips["already_on_game"] = skips.get("already_on_game", 0) + 1
            continue
        need = c["contracts"] * c["price"] + maker_fee(c["price"], c["contracts"])
        if mode == "live" and need > room + 1e-9:
            skips["daily_budget"] = skips.get("daily_budget", 0) + 1
            continue
        room -= need if mode == "live" else 0
        busy.add(c["event"])
        picked.append(c)
    return picked, skips


def new_row(c: dict, now: datetime, mode: str) -> dict:
    return {"client_order_id": client_id(c, now), "order_id": "", "placed_at": iso(now), "day": et_day(now),
            "mode": mode, "ticker": c["ticker"], "side": c["side"], "event": c["event"], "label": c["label"],
            "game": c.get("game", ""), "sport": c.get("sport", ""), "market": c.get("market", ""),
            "starts": c["starts"], "expires_at": c["expires_at"], "fair": c["fair"], "price": c["price"],
            "contracts": c["contracts"], "edge": c["edge"], "sharp": "yes" if c.get("sharp") else "",
            "bid": c.get("bid"), "ask": c.get("ask"), "status": RESTING if mode == "live" else DRY,
            "filled": 0, "cost": "", "fee": "", "result": "", "pl": "", "settled_at": "", "note": ""}


def place(client, c: dict, row: dict) -> None:
    """Send one post-only order that Kalshi expires by itself before the game starts."""
    expires = int(parse_dt(row["expires_at"]).timestamp())
    try:
        resp = client.create_buy_order(ticker=c["ticker"], outcome_side=c["side"], count=int(c["contracts"]),
                                       max_price=float(c["price"]), execution="MAKE",
                                       client_order_id=row["client_order_id"], expires_at=expires)
        order = resp.get("order", resp) if isinstance(resp, dict) else {}
        row["order_id"] = order.get("order_id") or ""
        filled = _order_fill(order)
        if filled > 0:
            row["filled"] = round(filled, 2)
    except Exception as exc:
        rejected = getattr(exc, "status_code", None) not in (None, 0)
        if rejected:   # Kalshi said no (e.g. post-only would have crossed): nothing is resting
            row.update({"status": "rejected", "cost": 0.0, "fee": 0.0, "pl": 0.0,
                        "note": f"{type(exc).__name__}: {str(exc)[:140]}"})
        else:          # unknown outcome: keep it as resting; next round's reconcile finds the truth
            row["note"] = f"unclear reply, will reconcile: {type(exc).__name__}: {str(exc)[:120]}"


# ---------------------------------------------------------------- stats
def summarize(rows: list[dict], mode: str = "live", sharp: bool | None = None) -> dict:
    rows = [r for r in rows if r.get("mode") == mode and (sharp is None or bool(r.get("sharp")) == sharp)]
    posted = [r for r in rows if r.get("status") != "rejected"]
    decided = [r for r in posted if r.get("status") in {FILLED, EXPIRED} | DONE]
    fills = [r for r in posted if r.get("status") in {FILLED} | DONE]
    done = [r for r in fills if r.get("status") in {"won", "lost"}]
    staked = sum(f(r.get("cost"), 0) + f(r.get("fee"), 0) for r in done)
    pl = sum(f(r.get("pl"), 0) for r in done)
    return {"posted": len(posted), "resting": sum(r.get("status") == RESTING for r in posted),
            "fill_rate": len(fills) / len(decided) if decided else None, "fills": len(fills),
            "won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
            "open": sum(r.get("status") == FILLED for r in fills), "pl": round(pl, 2),
            "roi": pl / staked if staked else None,
            "avg_edge": (sum(f(r.get("edge"), 0) for r in fills) / len(fills)) if fills else None,
            "staked": round(staked, 2),
            "avg_markout": (lambda m: sum(m) / len(m) if m else None)(
                [f(r.get("markout")) for r in fills if f(r.get("markout")) is not None])}
