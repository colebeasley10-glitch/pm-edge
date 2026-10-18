"""Best Price check: was each live Sharp pick cheaper somewhere other than Kalshi? (READ-ONLY)

Every Research Lab round runs a few minutes after PM Edge Watch, which is where the live $1
Sharp bets are placed. For each Sharp pick placed since the last round, this looks up the same
bet on Polymarket's public Gamma API and records both all-in prices (fees included) side by side.
Nothing here places, changes or cancels an order: live betting stays on Kalshi. The point is to
measure how often, and by how much, another venue would have been the better place to buy.

Covered: game/match winner markets (tennis, football, soccer, esports...). Spreads, totals,
ties and props are logged as "not compared" so coverage is visible.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

from league.strategies.polymarket import (GAME_SERIES_RE, _list, fetch_poly_events, kalshi_start,
                                          sim)
from research_lab import config
from research_lab.fills import taker_fee
from research_lab.ledger import _atomic_write_text, iso, parse_dt

SHARP_LIVE = Path("live_sharp_trades.csv")   # read-only: the live Sharp runner owns this file
FIELDS = ["trade_id", "placed_at", "checked_at", "lag_min", "ticker", "pick", "side", "contracts",
          "kalshi_price", "kalshi_cost", "poly_event", "poly_outcome", "poly_ask", "poly_cost",
          "poly_liquidity", "best_venue", "diff_cents", "url", "note"]
MAX_LAG = timedelta(minutes=90)    # older picks are skipped: a late price says nothing about placement time
MIN_NAME_MATCH = 85
MAX_POLY_SPREAD = 0.06


def path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / "BEST_PRICE" / "best_price.csv"


def load(base: Path | None = None) -> list[dict]:
    p = path(base)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _save(rows: list[dict], base: Path | None) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    _atomic_write_text(path(base), buf.getvalue())


def _f(v, default=None):
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def sharp_trades(src: Path | None = None) -> list[dict]:
    p = Path(src or SHARP_LIVE)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def kalshi_cost(t: dict) -> tuple[float | None, float]:
    """All-in Kalshi cost per contract (price + taker fee) and contracts bought (or wanted)."""
    filled = _f(t.get("filled"), 0.0) or 0.0
    if filled > 0 and _f(t.get("avg_price")) is not None:
        price = _f(t.get("avg_price"))
        fee = (_f(t.get("fee"), 0.0) or 0.0) / filled
        return price + fee, filled
    price = _f(t.get("limit_price"))
    if price is None:
        return None, 0.0
    n = _f(t.get("contracts_wanted"), 1.0) or 1.0
    return price + taker_fee(price, n) / n, 0.0


def team_of(title: str) -> str:
    t = str(title or "").strip()
    return t[:-5].strip() if t.lower().endswith(" wins") else ""


def _ask(m: dict) -> tuple[float, float] | None:
    bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    if bid is None or ask is None or not (0 < bid <= ask < 1) or ask - bid > MAX_POLY_SPREAD:
        return None
    return bid, ask


def poly_quote(team: str, side: str, start: datetime, window_h: float, events: list[dict]) -> dict:
    """Polymarket's ask for the same bet: YES = `team` wins, NO = `team` doesn't win.

    Two-outcome markets (tennis, football, esports): NO on a team is the other outcome's ask.
    Yes/No per-team markets (soccer, draws possible): YES = bestAsk, NO = 1 - bestBid.
    Returns {} with a 'note' when there's no single confident match.
    """
    hits = []
    for e in events:
        for m in e.get("markets") or []:
            if m.get("sportsMarketType") != "moneyline":
                continue
            gs = parse_dt(str(m.get("gameStartTime") or "").replace(" ", "T").replace("+00", "+00:00"))
            if gs is None or abs((gs - start).total_seconds()) > window_h * 3600:
                continue
            outs = [str(x) for x in _list(m.get("outcomes"))]
            if len(outs) != 2:
                continue
            if outs[0].lower() in {"yes", "no"}:
                label = str(m.get("groupItemTitle") or "")
                if "draw" in label.lower() or sim(team, label) < MIN_NAME_MATCH:
                    continue
                hits.append((e, m, outs, label, None))
            else:
                scores = [sim(team, o) for o in outs]
                i = 0 if scores[0] >= scores[1] else 1
                if scores[i] < MIN_NAME_MATCH or abs(scores[0] - scores[1]) < 8:
                    continue
                hits.append((e, m, outs, outs[i], i))
    if not hits:
        return {"note": "not found on Polymarket"}
    if len({id(h[0]) for h in hits}) > 1:
        return {"note": "ambiguous Polymarket match (skipped)"}
    e, m, outs, label, i = hits[0]
    ba = _ask(m)
    if ba is None:
        return {"note": "Polymarket market too thin to price"}
    bid, ask = ba
    if i is None:                                   # per-team Yes/No market
        price = ask if side == "YES" else 1.0 - bid
        outcome = f"{label} {'Yes' if side == 'YES' else 'No'}"
    else:                                           # two outcomes: buy `label`, or the other one for NO
        own, other = (ask, 1.0 - bid) if i == 0 else (1.0 - bid, ask)
        price = own if side == "YES" else other
        outcome = label if side == "YES" else outs[1 - i]
    if not 0.01 < price < 0.99:
        return {"note": "Polymarket price out of range"}
    slug = str(e.get("slug") or "")
    return {"event": e.get("title") or "", "outcome": outcome, "ask": round(price, 4),
            "liquidity": round(_f(m.get("liquidityNum"), 0.0) or 0.0),
            "url": f"https://polymarket.com/event/{slug}" if slug else ""}


def record(now: datetime, base: Path | None = None, *, src: Path | None = None, poly_fetch=None) -> dict:
    """Compare new Sharp picks with Polymarket and append them to best_price.csv."""
    rows = load(base)
    seen = {r["trade_id"] for r in rows}
    fresh = []
    for t in sharp_trades(src):
        placed = parse_dt(t.get("placed_at"))
        if (t.get("trade_id") in seen or t.get("status") == "error" or placed is None
                or now - placed > MAX_LAG or placed > now):
            continue
        fresh.append((t, placed))
    info = {"new_picks": len(fresh), "compared": 0, "poly_cheaper": 0}
    if not fresh:
        return info
    events = None
    fee_rate = config.EV_FINDER.params["polymarket_fee_rate"]
    for t, placed in fresh:
        ticker = str(t.get("ticker") or "")
        series, event_ticker = ticker.split("-", 1)[0], ticker.rsplit("-", 1)[0]
        k_cost, contracts = kalshi_cost(t)
        row = {"trade_id": t.get("trade_id"), "placed_at": t.get("placed_at"), "checked_at": iso(now),
               "lag_min": round((now - placed).total_seconds() / 60, 1), "ticker": ticker,
               "pick": f"{t.get('title')} ({t.get('side')})", "side": t.get("side"), "contracts": contracts,
               "kalshi_price": t.get("avg_price") or t.get("limit_price"),
               "kalshi_cost": round(k_cost, 4) if k_cost is not None else ""}
        team, ks = team_of(t.get("title")), kalshi_start(event_ticker)
        if not GAME_SERIES_RE.match(series) or not team or ticker.endswith("-TIE") or not ks or k_cost is None:
            row.update(note="not compared (only game/match winners for now)", best_venue="Kalshi")
            rows.append(row)
            continue
        if events is None:
            try:
                events = (poly_fetch or (lambda n: fetch_poly_events(_session(), n)))(now)
            except Exception as exc:
                info["error"] = f"Polymarket: {type(exc).__name__}"
                return info   # try these picks again next round (they're not marked seen)
        q = poly_quote(team, str(t.get("side") or "YES").upper(), ks[0], ks[1], events)
        if "ask" not in q:
            row.update(note=q.get("note", ""), best_venue="Kalshi")
            rows.append(row)
            continue
        p_cost = q["ask"] * (1 + fee_rate)
        diff = (k_cost - p_cost) * 100
        best = "Polymarket" if diff > 0.05 else "Kalshi"
        row.update(poly_event=q["event"], poly_outcome=q["outcome"], poly_ask=q["ask"],
                   poly_cost=round(p_cost, 4), poly_liquidity=q["liquidity"], best_venue=best,
                   diff_cents=round(diff, 2), url=q["url"], note="")
        rows.append(row)
        info["compared"] += 1
        info["poly_cheaper"] += best == "Polymarket"
    _save(rows, base)
    return info


def _session():
    import requests
    return requests.Session()


def summary(rows: list[dict]) -> dict:
    """How much cheaper the best venue was, on picks Polymarket could price."""
    cmp_ = [r for r in rows if _f(r.get("diff_cents")) is not None]
    cheaper = [r for r in cmp_ if r.get("best_venue") == "Polymarket"]
    saved = sum(max(_f(r["diff_cents"], 0.0), 0.0) / 100 * (_f(r.get("contracts"), 0.0) or 0.0) for r in cmp_)
    spent = sum((_f(r.get("kalshi_cost"), 0.0) or 0.0) * (_f(r.get("contracts"), 0.0) or 0.0) for r in cmp_)
    return {"picks": len(rows), "compared": len(cmp_), "poly_cheaper": len(cheaper),
            "avg_edge_cents": (sum(_f(r["diff_cents"]) for r in cheaper) / len(cheaper)) if cheaper else None,
            "avg_diff_cents": (sum(_f(r["diff_cents"]) for r in cmp_) / len(cmp_)) if cmp_ else None,
            "saved": saved, "spent": spent}
