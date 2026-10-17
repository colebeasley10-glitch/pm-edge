"""STRUCTURAL_ARB: provable price inconsistencies between related Kalshi contracts.

Two relationships are checked, both read from Kalshi's own structured fields:

LADDER (nested strikes). Within one event, "above X" markets share an underlying and an
expiry. If X1 < X2 then "above X2" implies "above X1". Buying YES "above X1" plus NO
"above X2" pays at least $1 in every outcome (exactly $2 when the result lands between
them). It locks a profit when the two asks plus every fee total less than $1. The same
logic applies to "below X" ladders with the strikes reversed.

NO BASKET (mutually exclusive outcomes). If Kalshi flags an event as mutually exclusive,
at most one of its N markets can settle YES, so one NO on every market pays at least N-1.
It locks a profit when all N NO asks plus fees total less than N-1. Exhaustiveness is not
assumed (if nothing wins, the payout is N, which only helps). A YES basket would need
exhaustiveness, which can't be verified from the API, so it is not traded.

Every leg is priced from its real order book, walked for depth, with fees per leg. A
combination is only paper-filled if every leg fills the same number of units from books
fetched within MAX_LEG_GAP seconds of each other. Otherwise it is logged as an unexecuted
opportunity. Voids are a real risk: a voided leg refunds its cost but can shrink the
locked profit, so these are recorded as "locked unless a leg voids", not "risk-free".
"""
from __future__ import annotations

from research_lab import portfolio
from research_lab.config import TARGET_STAKE, StrategySpec
from research_lab.fills import simulate_buy, taker_fee
from research_lab.ledger import Store, dumps, iso, position_id

MAX_LEG_GAP_S = 30
OPEN = {"open", "active"}


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _ask(m: dict, side: str) -> float | None:
    a = _f(m.get("yes_ask") if side == "YES" else m.get("no_ask"))
    return a if a is not None and 0 < a < 1 else None


def _same_expiry(a: dict, b: dict) -> bool:
    ea = a.get("expected_expiration_time") or a.get("close_time")
    eb = b.get("expected_expiration_time") or b.get("close_time")
    return bool(ea) and ea == eb


def ladder_pairs(event: dict) -> list[dict]:
    """Quote-level ladder candidates: [{legs: [(market, side), (market, side)], kind}]."""
    out = []
    for kind, key in (("greater", "floor_strike"), ("greater_or_equal", "floor_strike"),
                      ("less", "cap_strike"), ("less_or_equal", "cap_strike")):
        ms = [m for m in event["markets"] if m.get("strike_type") == kind and _f(m.get(key)) is not None
              and str(m.get("status") or "").lower() in OPEN]
        ms.sort(key=lambda m: _f(m.get(key)))
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                lo, hi = ms[i], ms[j]
                if _f(lo.get(key)) == _f(hi.get(key)) or not _same_expiry(lo, hi):
                    continue
                # "above": hi-strike YES implies lo-strike YES -> buy YES lo + NO hi
                # "below": lo-strike YES implies hi-strike YES -> buy YES hi + NO lo
                broad, narrow = (lo, hi) if kind.startswith("greater") else (hi, lo)
                out.append({"kind": "ladder", "legs": [(broad, "YES"), (narrow, "NO")], "guaranteed": 1})
    return out


def no_basket(event: dict, max_legs: int) -> dict | None:
    if not event.get("mutually_exclusive"):
        return None
    ms = event["markets"]
    if len(ms) < 2 or len(ms) > max_legs:
        return None
    if any(str(m.get("status") or "").lower() not in OPEN for m in ms):
        return None
    return {"kind": "no_basket", "legs": [(m, "NO") for m in ms], "guaranteed": len(ms) - 1}


def quote_profit(c: dict) -> float | None:
    """Locked profit per unit at top-of-book quotes, fees included (one contract per leg)."""
    total = 0.0
    for m, side in c["legs"]:
        a = _ask(m, side)
        if a is None:
            return None
        total += a + taker_fee(a, 1)
    return c["guaranteed"] - total


def _row(spec, ctx, event, c, decision, reason, info) -> dict:
    return {"run_at": iso(ctx.now), "strategy": spec.key, "ticker": event["event_ticker"],
            "event_ticker": event["event_ticker"], "title": event.get("title", ""),
            "category": event.get("category", ""), "side": c["kind"], "decision": decision, "reason": reason,
            "fair": "", "ask": info.get("unit_cost", ""), "net_edge": info.get("profit_per_unit", ""),
            "quote_age_s": info.get("leg_gap_s", ""), "fair_age_s": "", "spread": "",
            "details": dumps({"legs": [(m["ticker"], s) for m, s in c["legs"]], **info})}


def execute(c: dict, ctx, min_profit: float) -> tuple[str, str, dict]:
    """Fill every leg from its order book. Returns (decision, reason, info)."""
    books = []
    for m, side in c["legs"]:
        b = ctx.book(m["ticker"])
        if b is None:
            return "NO_FILL", "order_book_unavailable", {}
        books.append(b)
    times = [b.fetched_at for b in books]
    gap = (max(times) - min(times)).total_seconds()
    info = {"leg_gap_s": round(gap, 1)}
    if gap > MAX_LEG_GAP_S:
        return "NO_FILL", "legs_not_simultaneous", info
    tops = [b.best_ask(side) for b, (_, side) in zip(books, c["legs"])]
    if any(t is None for t in tops):
        return "NO_FILL", "leg_missing_ask", info
    unit_cost = sum(tops)
    units = max(1, int(TARGET_STAKE // unit_cost))
    last = "insufficient_depth_on_a_leg"
    # depth: shrink units until every leg can fill them at a price that still locks profit
    while units >= 1:
        fills = [simulate_buy(b.asks(side), units, 0.99) for b, (_, side) in zip(books, c["legs"])]
        if all(fl.contracts == units for fl in fills):
            cost = sum(fl.cost for fl in fills)
            fee = sum(fl.fee for fl in fills)
            profit = c["guaranteed"] * units - cost - fee
            info.update({"units": units, "unit_cost": round((cost + fee) / units, 4),
                         "profit_per_unit": round(profit / units, 4), "fills": fills})
            if profit / units + 1e-9 >= min_profit:
                return "FILLED", "locked_unless_a_leg_voids", info
            units -= 1  # deeper levels ate the profit; try fewer units
            last = "not_profitable_after_depth_and_fees"
            continue
        units = min(units - 1, min(fl.contracts for fl in fills))
        last = "insufficient_depth_on_a_leg"
    info.pop("fills", None)
    return "NO_FILL", last, info


def run_structural(spec: StrategySpec, store: Store, ctx) -> dict:
    p = spec.params
    s = {"scanned": 0, "relationships": 0, "qualified": 0, "filled": 0, "no_fill": 0, "rejected": 0}
    cands = []
    for ev in ctx.events:
        s["scanned"] += 1
        rel = ladder_pairs(ev)
        nb = no_basket(ev, p["max_legs"])
        if nb:
            rel.append(nb)
        s["relationships"] += len(rel)
        for c in rel:
            q = quote_profit(c)
            if q is not None and q >= p["min_locked_profit"]:
                cands.append((q, ev, c))
    cands.sort(key=lambda x: -x[0])
    checks = 0
    for q, ev, c in cands:
        s["qualified"] += 1
        store.bump("qualified_quote_opportunities")
        legs_n = len(c["legs"])
        if checks + legs_n > p["max_orderbook_checks"]:
            store.log_candidate(_row(spec, ctx, ev, c, "REJECTED", "orderbook_check_budget", {"quote_profit": round(q, 4)}))
            s["rejected"] += 1
            continue
        checks += legs_n
        decision, reason, info = execute(c, ctx, p["min_locked_profit"])
        info["quote_profit"] = round(q, 4)
        fills = info.pop("fills", None)
        if decision != "FILLED":
            store.log_candidate(_row(spec, ctx, ev, c, decision, reason, info))
            s["no_fill"] += 1
            continue
        committed = round(sum(fl.committed for fl in fills), 2)
        key = "|".join(f"{m['ticker']}:{sd}" for m, sd in c["legs"])
        pid = position_id(spec.key, key, c["kind"], ctx.now)
        ok, why = portfolio.check_limits(store, event=ev["event_ticker"], ticker=key, committed=committed, now=ctx.now)
        if not ok or store.has_position(pid):
            store.log_candidate(_row(spec, ctx, ev, c, "REJECTED", why if not ok else "duplicate_this_run", info))
            s["rejected"] += 1
            continue
        legs = [{"ticker": m["ticker"], "title": m.get("title", ""), "side": sd, "contracts": fl.contracts,
                 "avg_price": fl.avg_price, "cost": fl.cost, "fee": fl.fee, "levels": fl.levels,
                 "quote_at": iso(ctx.books[m["ticker"]].fetched_at), "outcome": None}
                for (m, sd), fl in zip(c["legs"], fills)]
        units = info["units"]
        store.add_position({
            "position_id": pid, "strategy": spec.id, "version": spec.version, "opened_at": iso(ctx.now),
            "day": portfolio.et_day(ctx.now), "ticker": key, "event_ticker": ev["event_ticker"],
            "title": ev.get("title", ""), "category": ev.get("category", ""), "market_type": c["kind"],
            "side": c["kind"].upper(), "contracts": units, "wanted": units,
            "entry_price": info["unit_cost"], "cost": round(sum(fl.cost for fl in fills), 2),
            "fee": round(sum(fl.fee for fl in fills), 2), "committed": committed, "fill_status": "filled",
            "fair": "", "net_edge": info["profit_per_unit"],
            "reason": (f"{c['kind'].replace('_', ' ')}: {len(legs)} legs cost ${info['unit_cost']:.2f}/unit, "
                       f"pays at least ${c['guaranteed']}/unit (locked unless a leg voids)"),
            "quote_at": iso(min(ctx.books[m["ticker"]].fetched_at for m, _ in c["legs"])),
            "close_time": max(str(m.get("close_time") or "") for m, _ in c["legs"]),
            "details": dumps({"guaranteed_per_unit": c["guaranteed"], "leg_gap_s": info["leg_gap_s"],
                              "quote_profit": info["quote_profit"]}),
            "legs": dumps(legs), "status": "open",
        })
        store.log_candidate(_row(spec, ctx, ev, c, "FILLED", reason, info))
        s["filled"] += 1
    return s
