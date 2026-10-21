"""Export pre-game maker candidates for the Maker Bot (read-only; no orders here).

Each Research Lab round, every Kalshi game/spread/total market the lab priced off a
FRESH Pinnacle line this round becomes a candidate: Pinnacle's no-vig fair price,
the current Kalshi bid/ask for that side, the start time, and whether big Sharp flow
(the public trade tape, last 45 min) is on the same side. The keyed Maker Bot
workflow reads research_lab/data/maker_candidates.json right after this round.

Only sports re-fetched from Pinnacle this round appear, so a candidate's fair price
is never more than a few minutes old when the bot sees it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from research_lab.ledger import _atomic_write_text, iso

FILE = "maker_candidates.json"
MIN_LEAD = timedelta(minutes=15)       # nothing that starts sooner than this
MAX_LEAD = timedelta(hours=36)
SHARP_MIN_TRADES = 20                  # same bar as Live Sharp
SHARP_MIN_SHARE = 0.60                 # our side's share of the market's recent flow
SHARP_MAX_AGE_MIN = 45


def _side_quotes(m: dict, side: str) -> tuple[float | None, float | None]:
    if side == "YES":
        return m.get("yes_bid"), m.get("yes_ask")
    return m.get("no_bid"), m.get("no_ask")


def sharp_info(flow, now: datetime) -> dict | None:
    if flow is None or flow.trades < SHARP_MIN_TRADES:
        return None
    share = flow.share
    if share is None or share < SHARP_MIN_SHARE:
        return None
    if flow.latest is None or (now - flow.latest).total_seconds() / 60 > SHARP_MAX_AGE_MIN:
        return None
    return flow.as_dict(now)


def build(picks: list, markets: dict, flows: dict, now: datetime) -> list[dict]:
    out, seen = [], set()
    for pk in picks:
        ticker, side = pk.settle_ticker, pk.settle_side
        if not ticker or (ticker, side) in seen or pk.starts is None:
            continue
        if not (now + MIN_LEAD <= pk.starts <= now + MAX_LEAD):
            continue
        m = markets.get(ticker)
        if not m or str(m.get("status") or "active").lower() not in {"active", "open", "initialized"}:
            continue
        bid, ask = _side_quotes(m, side)
        if ask is None or not (0 < float(ask) < 1):
            continue
        seen.add((ticker, side))
        out.append({"ticker": ticker, "side": side, "event": pk.event or m.get("event_ticker") or ticker,
                    "label": pk.label, "game": pk.game, "league": pk.league, "sport": pk.sport,
                    "market": pk.market, "starts": iso(pk.starts), "fair": round(float(pk.fair), 4),
                    "bid": None if bid is None else float(bid), "ask": float(ask),
                    "sharp": sharp_info(flows.get((ticker, side)), now)})
    return out


def write(base: Path, picks: list, markets: dict, flows: dict, now: datetime, flow_complete: bool = True) -> dict:
    rows = build(picks, markets, flows, now)
    _atomic_write_text(Path(base) / FILE, json.dumps(
        {"at": iso(now), "flow_complete": flow_complete, "candidates": rows}, default=str))
    return {"candidates": len(rows), "sharp_agrees": sum(1 for r in rows if r["sharp"])}
