"""Settle paper positions from Kalshi's official market results, and record closing lines.

Only Kalshi's own `result` field settles a position. Unknown or missing results keep
the position open (pending); nothing is settled from scores or assumed winners.
"""
from __future__ import annotations

from datetime import datetime

from research_lab.ledger import Store, dumps, iso, loads, parse_dt
from research_lab.portfolio import f

VOID_RESULTS = {"void", "voided", "all_no_void"}
VOID_STATUSES = {"voided", "canceled", "cancelled"}


def leg_outcome(market: dict | None) -> str | None:
    """'yes' | 'no' | 'void' | None (not settled / unknown)."""
    if not market:
        return None
    result = str(market.get("result") or "").lower()
    status = str(market.get("status") or "").lower()
    if result in {"yes", "no"}:
        return result
    if result in VOID_RESULTS or status in VOID_STATUSES:
        return "void"
    return None


def _mid(market: dict, side: str) -> float | None:
    yb, ya = market.get("yes_bid"), market.get("yes_ask")
    if yb is None or ya is None:
        return None
    mid = (float(yb) + float(ya)) / 2.0
    return mid if side == "YES" else 1.0 - mid


def settle_store(store: Store, markets: dict[str, dict], now: datetime) -> dict:
    """Settle or mark every open position in one strategy's ledger. Returns counts."""
    out = {"settled": 0, "pending": 0, "marked": 0}
    for p in store.open_positions():
        legs = loads(p.get("legs"), None)
        if legs:
            _settle_multi(p, legs, markets, now, out)
            continue
        m = markets.get(p.get("ticker") or "")
        outcome = leg_outcome(m)
        contracts = f(p.get("contracts"))
        cost, fee = f(p.get("cost")), f(p.get("fee"))
        if outcome in {"yes", "no"}:
            won = (outcome == "yes") == (p.get("side") == "YES")
            payout = contracts if won else 0.0
            p.update({"status": "won" if won else "lost", "result": outcome, "settled_at": iso(now),
                      "payout": round(payout, 2), "pl": round(payout - cost - fee, 2)})
            closing = f(p.get("last_mid"), None)
            if closing is not None:
                p["closing_mid"] = round(closing, 4)
                p["clv_cents"] = round((closing - f(p.get("entry_price"))) * 100.0, 2)
            out["settled"] += 1
        elif outcome == "void":
            # Kalshi returns the cost of a voided contract; fees are treated as refunded too.
            p.update({"status": "void", "result": "void", "settled_at": iso(now), "payout": round(cost + fee, 2),
                      "pl": 0.0})
            out["settled"] += 1
        else:
            out["pending"] += 1
            # closing line: keep marking only until kickoff (or market close if kickoff unknown)
            cut = parse_dt(p.get("commence_time")) or parse_dt(p.get("close_time"))
            if m and (cut is None or now < cut):
                mid = _mid(m, p.get("side") or "YES")
                if mid is not None and str(m.get("status") or "").lower() in {"open", "active", ""}:
                    p["last_mid"], p["last_mid_at"] = round(mid, 4), iso(now)
                    out["marked"] += 1
    return out


def _settle_multi(p: dict, legs: list[dict], markets: dict[str, dict], now: datetime, out: dict) -> None:
    """A multi-leg position settles only when every leg has an official result."""
    payout = 0.0
    for leg in legs:
        if leg.get("outcome") is None:
            leg["outcome"] = leg_outcome(markets.get(leg["ticker"]))
    if any(leg.get("outcome") is None for leg in legs):
        p["legs"] = dumps(legs)
        out["pending"] += 1
        return
    for leg in legs:
        n = f(leg.get("contracts"))
        if leg["outcome"] == "void":
            payout += f(leg.get("cost")) + f(leg.get("fee"))
        elif (leg["outcome"] == "yes") == (leg.get("side") == "YES"):
            payout += n
    pl = round(payout - f(p.get("cost")) - f(p.get("fee")), 2)
    p.update({"legs": dumps(legs), "status": "won" if pl > 0 else "lost" if pl < 0 else "closed",
              "result": ",".join(str(leg["outcome"]) for leg in legs), "settled_at": iso(now),
              "payout": round(payout, 2), "pl": pl})
    out["settled"] += 1


def tickers_to_check(store: Store) -> list[str]:
    out = []
    for p in store.open_positions():
        legs = loads(p.get("legs"), None)
        if legs:
            out.extend(leg["ticker"] for leg in legs)
        elif p.get("ticker"):
            out.append(p["ticker"])
    return out
