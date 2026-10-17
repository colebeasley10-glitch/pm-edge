"""EV_FINDER: OddsJam-style +EV and arbitrage finder across every venue you have.

For each upcoming game (moneyline), Pinnacle's de-vigged price is the fair line. Every
venue's price is compared to it:  EV = fair_probability x decimal_odds - 1.

* +EV: the best-priced venue for a side with EV >= min_ev gets one paper bet (~$10) at
  that venue's price, and (optionally) a phone alert so you can place it by hand.
  Kalshi bets here are paper too; nothing in the lab places orders.
* Arbitrage: when the best price on each side, across venues, adds up to less than 100%,
  it's logged and alerted (both legs shown). Not paper-traded: the result is fixed by math.

Paper bets settle from Kalshi's official result for that team's game market. A game with
no Kalshi market can still alert, but isn't paper-tracked (no trustworthy settlement).
Sportsbook prices come from snapshots that can be hours old: alerts say how old, and
the price must be re-checked before betting.
"""
from __future__ import annotations

import os
from datetime import timedelta

import requests

from research_lab import portfolio
from research_lab.config import TARGET_STAKE, StrategySpec
from research_lab.ev_data import american
from research_lab.ledger import Store, dumps, iso, parse_dt, position_id


def _row(spec, ctx, g, side, decision, reason, info) -> dict:
    return {"run_at": iso(ctx.now), "strategy": spec.key, "ticker": g.settle.get(side, "") or g.key,
            "event_ticker": g.key, "title": f"{g.title()}: {g.team(side)}", "category": g.event.league,
            "side": side, "decision": decision, "reason": reason, "fair": round(g.fair[side], 4),
            "ask": info.get("decimal", ""), "net_edge": info.get("ev", ""), "quote_age_s": info.get("age_s", ""),
            "fair_age_s": "", "spread": "", "details": dumps(info)}


def _age_s(ctx, o) -> int | None:
    return None if o.at is None else int((ctx.now - o.at).total_seconds())


def run_ev_finder(spec: StrategySpec, store: Store, ctx) -> dict:
    p = spec.params
    s = {"scanned": 0, "qualified": 0, "filled": 0, "no_fill": 0, "rejected": 0, "arbs": 0, "alerts": []}
    for g in ctx.ev_games:
        s["scanned"] += 1
        best_by_side = {}
        for side in ("home", "away"):
            fresh = [o for o in g.offers if o.side == side and o.at is not None
                     and ctx.now - o.at <= timedelta(minutes=p["max_price_age_min"])]
            if not fresh:
                continue
            best = max(fresh, key=lambda o: o.decimal)
            best_by_side[side] = best
            ev = g.fair[side] * best.decimal - 1
            info = {"venue": best.venue, "shown": best.shown, "decimal": round(best.decimal, 4), "ev": round(ev, 4),
                    "fair_odds": american(1 / g.fair[side]), "age_s": _age_s(ctx, best),
                    "others": sorted({f"{o.venue} {o.shown}" for o in fresh if o is not best})[:6]}
            if ev > p["max_ev"]:
                store.log_candidate(_row(spec, ctx, g, side, "REJECTED", "suspicious_ev_review", info))
                s["rejected"] += 1
                continue
            if ev + 1e-9 < p["min_ev"]:
                store.log_candidate(_row(spec, ctx, g, side, "REJECTED", "ev_below_min", info))
                s["rejected"] += 1
                continue
            s["qualified"] += 1
            s["alerts"].append(f"{best.venue}: {g.team(side)} ML {best.shown} · fair {info['fair_odds']} · "
                               f"EV +{ev * 100:.1f}%" + (f" · price {info['age_s'] // 60} min old"
                                                         if info["age_s"] and info["age_s"] > 600 else ""))
            ticker = g.settle.get(side)
            if not ticker:
                store.log_candidate(_row(spec, ctx, g, side, "NO_FILL", "no_kalshi_settlement_market", info))
                s["no_fill"] += 1
                continue
            stake = TARGET_STAKE
            ok, why = portfolio.check_limits(store, event=g.key, ticker=ticker, committed=stake, now=ctx.now)
            pid = position_id(spec.key, ticker, best.venue, ctx.now)
            if not ok or store.has_position(pid):
                store.log_candidate(_row(spec, ctx, g, side, "REJECTED", why if not ok else "duplicate_this_run", info))
                s["rejected"] += 1
                continue
            store.add_position({
                "position_id": pid, "strategy": spec.id, "version": spec.version, "opened_at": iso(ctx.now),
                "day": portfolio.et_day(ctx.now), "ticker": ticker, "event_ticker": g.key,
                "title": f"{g.title()}: {g.team(side)} wins", "category": g.event.league, "market_type": "h2h",
                # payout model: $stake at decimal odds -> 'contracts' paying $1 each if the team wins
                "side": "YES", "contracts": round(stake * best.decimal, 4), "wanted": round(stake * best.decimal, 4),
                "entry_price": round(1 / best.decimal, 4), "cost": stake, "fee": 0.0, "committed": stake,
                "fill_status": "filled", "fair": round(g.fair[side], 4), "net_edge": round(ev, 4),
                "reason": f"{best.venue} {best.shown} vs fair {info['fair_odds']} (+{ev * 100:.1f}% EV)",
                "quote_at": iso(best.at), "fair_at": iso(g.event.generated_at), "commence_time": iso(g.event.starts),
                "details": dumps({**info, "venue": best.venue, "team": g.team(side), "manual_bet": best.venue != "Kalshi"}),
                "legs": "", "status": "open",
            })
            store.log_candidate(_row(spec, ctx, g, side, "FILLED", f"paper bet at {best.venue}", info))
            s["filled"] += 1
        # arbitrage: best price each side, different venues, adds up to < 100%
        if len(best_by_side) == 2:
            h, a = best_by_side["home"], best_by_side["away"]
            total = 1 / h.decimal + 1 / a.decimal
            if h.venue != a.venue and total < 1 - p["min_arb"]:
                s["arbs"] += 1
                store.bump("arbitrage_found")
                info = {"legs": [f"{h.venue} {g.event.home} {h.shown}", f"{a.venue} {g.event.away} {a.shown}"],
                        "profit_pct": round(1 / total - 1, 4)}
                store.log_candidate(_row(spec, ctx, g, "home", "ARB_ALERT", "arbitrage", info))
                s["alerts"].append(f"ARB +{(1 / total - 1) * 100:.1f}%: {h.venue} {g.event.home} {h.shown} + "
                                   f"{a.venue} {g.event.away} {a.shown}")
    return s


def push_alerts(lines: list[str], store: Store, now, max_per_run: int = 5) -> int:
    """Phone alerts for new +EV/arb finds (bet by hand). Same line isn't re-sent within 6 hours."""
    if not lines:
        return 0
    sent = store.stats.setdefault("alerts_sent", {})
    cutoff = now - timedelta(hours=6)
    for k in [k for k, v in sent.items() if (parse_dt(v) or now) < cutoff]:
        sent.pop(k)
    fresh = [x for x in lines if x.split(" · price")[0] not in sent][:max_per_run]
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not fresh or not user or not token or os.getenv("RESEARCH_LAB_EV_PUSH", "1") in {"0", "false", "off"}:
        return 0
    payload = {"token": token, "user": user,
               "title": f"🎯 +EV found ({len(fresh)}) · bet by hand · PAPER-tracked",
               "message": ("\n".join(fresh) + "\nConfirm the price before betting.")[:1024]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip()
    if url:
        payload.update({"url": url.rstrip("/") + "/Research_Lab_EV_Finder", "url_title": "Open +EV board"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    except Exception:
        return 0
    for x in fresh:
        sent[x.split(" · price")[0]] = iso(now)
    return len(fresh)
