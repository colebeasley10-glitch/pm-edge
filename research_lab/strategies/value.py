"""VALUE_ONLY and HYBRID_SHARP_VALUE.

Both start from the same independently verified sportsbook fair value:

1. the scanner matched the Kalshi market to a sportsbook event (strict team + date match);
2. the line (spread/total number) and market type re-parse identically from the live market;
3. kickoff is known and still ahead, and Kalshi settles after kickoff;
4. sportsbook prices are fresh, from >= 3 books that agree;
5. net edge = fair - ask - Kalshi fee per contract - buffers, at the CURRENT ask;
6. edge too large to be believable is rejected as a probable data error.

VALUE_ONLY paper-buys anything that passes. HYBRID additionally needs recent public trade
flow on the same side (count, dollars, share, recency); flow never changes the fair price.
"""
from __future__ import annotations

from datetime import timedelta

from pmedge.market_parse import parse_market
from research_lab import portfolio
from research_lab.config import TARGET_STAKE, StrategySpec
from research_lab.fills import contracts_for_stake, fee_per_contract, simulate_buy
from research_lab.flow import flow_bucket
from research_lab.ledger import Store, dumps, iso, parse_dt, position_id
from research_lab.snapshot import FairQuote

SUPPORTED_TYPES = {"h2h", "spread", "total", "team_total"}
LINE_TYPES = {"spread", "total", "team_total"}


def net_edge(fair: float, price: float, contracts: int, buffers: float) -> float:
    return fair - price - fee_per_contract(price, contracts) - buffers


def limit_price(fair: float, best_ask: float, contracts: int, buffers: float, min_edge: float,
                max_price: float) -> float:
    """Highest whole-cent price, at or above the best ask, where the edge still clears min_edge."""
    p = best_ask
    best = best_ask
    while p + 0.01 <= max_price + 1e-9:
        p = round(p + 0.01, 2)
        if net_edge(fair, p, contracts, buffers) + 1e-9 < min_edge:
            break
        best = p
    return best


def _reject(reason: str, **kw) -> dict:
    return {"ok": False, "reason": reason, **kw}


def evaluate(q: FairQuote, ctx, params: dict) -> dict:
    """Value test for one market. Returns a decision dict (ok, reason, side, fair, ...)."""
    now = ctx.now
    m = ctx.markets.get(q.ticker)
    if m is None:
        return _reject("kalshi_market_unavailable")
    if str(m.get("status") or "").lower() not in {"open", "active"}:
        return _reject("market_not_open")
    # 1. identity: strict matcher must have passed on the scanner side
    if q.matcher_pass is not True:
        return _reject("match_unverified", detail=q.matcher_reason)
    if q.market_type not in SUPPORTED_TYPES:
        return _reject("unsupported_market_type")
    # 2. the live contract must still parse to the same market type and line
    spec = parse_market(m)
    if spec.market_type != q.market_type:
        return _reject("market_type_mismatch")
    if q.market_type in LINE_TYPES:
        if q.point is None or spec.point is None:
            return _reject("line_missing")
        if abs(float(spec.point) - float(q.point)) > 1e-6:
            return _reject("line_mismatch")
    # 3. timing and settlement rules
    if q.commence_time is None:
        return _reject("kickoff_unknown")
    if params.get("pregame_only") and now >= q.commence_time - timedelta(minutes=5):
        return _reject("kickoff_passed")
    close_dt = parse_dt(m.get("close_time"))
    if close_dt is not None and close_dt < q.commence_time:
        return _reject("settlement_before_kickoff")
    occ = parse_dt(m.get("occurrence_datetime"))
    if occ is not None and abs((occ - q.commence_time).total_seconds()) > 6 * 3600:
        return _reject("event_date_mismatch")
    rules = (str(m.get("rules_primary") or "") + " " + str(m.get("rules_secondary") or "")).lower()
    if "regulation" in rules and "overtime" not in rules and "soccer" not in q.sport_key:
        # sportsbook lines include overtime; a regulation-only contract settles differently
        return _reject("settlement_rules_differ")
    # 4. sportsbook data quality
    if q.fair_at is None or now - q.fair_at > timedelta(minutes=params["max_fair_age_min"]):
        return _reject("stale_sportsbook_prices",
                       fair_age_s=None if q.fair_at is None else int((now - q.fair_at).total_seconds()))
    if q.books < params["min_books"]:
        return _reject("too_few_books")
    if q.dispersion is not None and q.dispersion > params["max_dispersion"]:
        return _reject("books_disagree")
    fair_yes = q.fair_yes(params.get("devig", "power"))
    if fair_yes is None or not (0.02 < fair_yes < 0.98):
        return _reject("fair_unavailable")
    # 5. pick the side with the better edge at the current top-of-book ask (cheap check first)
    buffers = params["model_buffer"] + params["slippage_buffer"]
    best = None
    for side in ("YES", "NO"):
        ask = m.get("yes_ask") if side == "YES" else m.get("no_ask")
        if ask is None or not (0 < float(ask) < 1):
            continue
        ask = float(ask)
        n = contracts_for_stake(ask, TARGET_STAKE)
        fair = fair_yes if side == "YES" else 1.0 - fair_yes
        e = net_edge(fair, ask, n, buffers)
        if best is None or e > best["edge"]:
            best = {"side": side, "ask": ask, "fair": fair, "edge": e}
    if best is None:
        return _reject("no_executable_ask")
    common = {"side": best["side"], "fair": round(best["fair"], 4), "ask": best["ask"],
              "net_edge": round(best["edge"], 4),
              "fair_age_s": int((now - q.fair_at).total_seconds())}
    if best["edge"] > params["max_edge"]:
        return _reject("suspicious_edge_review", **common)
    if best["edge"] + 1e-9 < params["min_net_edge"]:
        return _reject("edge_below_min", **common)
    if not (params["min_price"] <= best["ask"] <= params["max_price"]):
        return _reject("price_out_of_range", **common)
    # 6. confirm against the real order book (depth, freshness, spread)
    book = ctx.book(q.ticker)
    if book is None:
        return _reject("order_book_unavailable", **common)
    asks = book.asks(best["side"])
    if not asks:
        return _reject("no_book_depth", **common)
    quote_age = (now - book.fetched_at).total_seconds()
    if quote_age > params["max_quote_age_min"] * 60:
        return _reject("stale_kalshi_quote", **common)
    book_ask = asks[0][0]
    n = contracts_for_stake(book_ask, TARGET_STAKE)
    e = net_edge(best["fair"], book_ask, n, buffers)
    common.update({"ask": book_ask, "net_edge": round(e, 4), "quote_age_s": int(quote_age)})
    bid = book.best_bid(best["side"])
    spread = None if bid is None else round(book_ask - bid, 4)
    common["spread"] = spread
    if spread is not None and spread > params["max_spread"]:
        return _reject("wide_spread", **common)
    if e + 1e-9 < params["min_net_edge"]:
        return _reject("edge_below_min", **common)
    if e > params["max_edge"]:
        return _reject("suspicious_edge_review", **common)
    lim = limit_price(best["fair"], book_ask, n, buffers, params["min_net_edge"], params["max_price"])
    edges = {}
    for method in ("power", "proportional", "shin"):
        fy = q.fair_yes(method)
        if fy is not None:
            fs = fy if best["side"] == "YES" else 1.0 - fy
            edges[method] = round(net_edge(fs, book_ask, n, buffers), 4)
    return {"ok": True, "reason": "value_qualified", **common, "contracts": n, "limit": lim, "asks": asks,
            "edges_by_method": edges}


def _candidate_row(spec: StrategySpec, q: FairQuote, ctx, decision: str, reason: str, d: dict,
                   extra: dict | None = None) -> dict:
    m = ctx.markets.get(q.ticker) or {}
    return {
        "run_at": iso(ctx.now), "strategy": spec.key, "ticker": q.ticker, "event_ticker": m.get("event_ticker", ""),
        "title": m.get("title") or q.title, "category": q.sport_title or q.sport_key, "side": d.get("side", ""),
        "decision": decision, "reason": reason, "fair": d.get("fair", ""), "ask": d.get("ask", ""),
        "net_edge": d.get("net_edge", ""), "quote_age_s": d.get("quote_age_s", ""),
        "fair_age_s": d.get("fair_age_s", ""), "spread": d.get("spread", ""),
        "details": dumps({k: v for k, v in {**d, **(extra or {})}.items() if k not in {"asks", "ok"}}),
    }


def _open(spec: StrategySpec, store: Store, q: FairQuote, ctx, d: dict, reason: str, extra: dict) -> str:
    """Simulate the fill and record the paper position. Returns the candidate decision."""
    m = ctx.markets.get(q.ticker) or {}
    event = m.get("event_ticker") or ""
    pid = position_id(spec.key, q.ticker, d["side"], ctx.now)
    if store.has_position(pid):
        store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", "duplicate_this_run", d, extra))
        return "REJECTED"
    est = d["contracts"] * d["ask"] + fee_per_contract(d["ask"], d["contracts"]) * d["contracts"]
    ok, why = portfolio.check_limits(store, event=event, ticker=q.ticker, committed=est, now=ctx.now)
    if not ok:
        store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", why, d, extra))
        return "REJECTED"
    fill = simulate_buy(d["asks"], d["contracts"], d["limit"])
    if fill.contracts <= 0:
        store.log_candidate(_candidate_row(spec, q, ctx, "NO_FILL", fill.reason, d, extra))
        return "NO_FILL"
    ok, why = portfolio.check_limits(store, event=event, ticker=q.ticker, committed=fill.committed, now=ctx.now)
    if not ok:
        store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", why, d, extra))
        return "REJECTED"
    fair = d["fair"]
    buffers = spec.params["model_buffer"] + spec.params["slippage_buffer"]
    edge_at_fill = fair - fill.avg_price - fill.fee / fill.contracts - buffers
    side = d["side"]
    fy_prop, fy_shin = q.fair_yes("proportional"), q.fair_yes("shin")
    store.add_position({
        "position_id": pid, "strategy": spec.id, "version": spec.version, "opened_at": iso(ctx.now),
        "day": portfolio.et_day(ctx.now), "ticker": q.ticker, "event_ticker": event,
        "title": m.get("title") or q.title, "category": q.sport_title or q.sport_key, "market_type": q.market_type,
        "side": side, "contracts": fill.contracts, "wanted": fill.wanted, "entry_price": fill.avg_price,
        "cost": fill.cost, "fee": fill.fee, "committed": fill.committed, "fill_status": fill.status,
        "fair": fair,
        "fair_proportional": "" if fy_prop is None else round(fy_prop if side == "YES" else 1 - fy_prop, 4),
        "fair_shin": "" if fy_shin is None else round(fy_shin if side == "YES" else 1 - fy_shin, 4),
        "net_edge": round(edge_at_fill, 4), "reason": reason[:240],
        "quote_at": iso(ctx.books[q.ticker].fetched_at) if ctx.books.get(q.ticker) else "",
        "fair_at": iso(q.fair_at), "commence_time": iso(q.commence_time), "close_time": m.get("close_time") or "",
        "details": dumps({"books": q.books, "dispersion": q.dispersion, "selection": q.selection,
                          "point": q.point, "sport_key": q.sport_key, "levels": fill.levels,
                          "limit": d["limit"], "edges_by_method": d.get("edges_by_method"),
                          "quote_age_s": d.get("quote_age_s"), "spread": d.get("spread"), **extra}),
        "legs": "", "status": "open",
    })
    store.log_candidate(_candidate_row(spec, q, ctx, "FILLED" if fill.status == "filled" else "PARTIAL",
                                       fill.reason or "filled", d, extra))
    return "FILLED"


def _summary() -> dict:
    return {"scanned": 0, "qualified": 0, "rejected": 0, "filled": 0, "no_fill": 0}


def run_value_only(spec: StrategySpec, store: Store, ctx) -> dict:
    s = _summary()
    for q in ctx.quotes_for(spec.params):
        s["scanned"] += 1
        d = evaluate(q, ctx, spec.params)
        if not d["ok"]:
            s["rejected"] += 1
            store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", d["reason"], d))
            continue
        s["qualified"] += 1
        reason = (f"Books {d['fair']:.0%} vs Kalshi {d['ask'] * 100:.0f}¢: "
                  f"{d['net_edge'] * 100:.1f} pt net edge ({q.books} books)")
        out = _open(spec, store, q, ctx, d, reason, {})
        s["filled" if out == "FILLED" else "no_fill" if out == "NO_FILL" else "rejected"] += 1
    return s


def flow_check(ctx, ticker: str, side: str, params: dict) -> tuple[bool, str, dict]:
    fl = ctx.flows.get((ticker, side))
    if fl is None:
        return False, "no_recent_flow", {"flow_trades": 0, "flow_bucket": "0"}
    info = {**fl.as_dict(ctx.now), "flow_bucket": flow_bucket(fl.trades)}
    if not ctx.flow_complete:
        info["flow_window_truncated"] = True
    if fl.trades < params["flow_min_trades"]:
        return False, "flow_too_few_trades", info
    if fl.notional < params["flow_min_notional"]:
        return False, "flow_too_small", info
    if (fl.share or 0) < params["flow_min_share"]:
        return False, "flow_not_one_sided", info
    if fl.latest is None or ctx.now - fl.latest > timedelta(minutes=params["flow_max_age_min"]):
        return False, "flow_too_old", info
    return True, "sharp_confirmed", info


def run_hybrid(spec: StrategySpec, store: Store, ctx) -> dict:
    s = _summary()
    for q in ctx.quotes_for(spec.params):
        s["scanned"] += 1
        d = evaluate(q, ctx, spec.params)
        if not d["ok"]:
            s["rejected"] += 1
            store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", d["reason"], d))
            continue
        ok, why, info = flow_check(ctx, q.ticker, d["side"], spec.params)
        if not ok:
            s["rejected"] += 1
            store.log_candidate(_candidate_row(spec, q, ctx, "REJECTED", why, d, info))
            continue
        s["qualified"] += 1
        reason = (f"Books {d['fair']:.0%} vs Kalshi {d['ask'] * 100:.0f}¢ "
                  f"({d['net_edge'] * 100:.1f} pt edge) + {info['flow_trades']} trades / "
                  f"${info['flow_notional']:,.0f} on {d['side']}")
        out = _open(spec, store, q, ctx, d, reason, info)
        s["filled" if out == "FILLED" else "no_fill" if out == "NO_FILL" else "rejected"] += 1
    return s
