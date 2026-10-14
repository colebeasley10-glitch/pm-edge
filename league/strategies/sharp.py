"""Sharp money: follow large Kalshi trades from the public trade tape.

When a big trade (or cluster) hits a market in the last 45 minutes and the
price you can buy now is no worse than what they paid (+1¢), follow it.
There's no model here; the league's closing-line score is what decides
whether big traders actually know something.

Only markets that settle soon: a futures-style market (an election, an award,
a Fed meeting weeks away) would tie up the bet for weeks or months, so Sharp
skips anything not expected to settle within MAX_HOURS_TO_SETTLE.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import whale_edge
from league.book import Pick, parse_dt, side_quotes

NAME = "sharp"
MAX_PICKS = 4
SLIPPAGE = 0.01
# Skip markets expected to settle further out than this (env SHARP_MAX_HOURS_TO_SETTLE).
MAX_HOURS_TO_SETTLE = float(os.getenv("SHARP_MAX_HOURS_TO_SETTLE", "48"))
DIAG: dict = {}


def settles_soon(market: dict, now: datetime, max_hours: float | None = None) -> bool:
    """True when the market is expected to settle within max_hours. Uses Kalshi's expected
    expiration (the game time for sports) and falls back to close_time; unknown counts as far."""
    limit = MAX_HOURS_TO_SETTLE if max_hours is None else max_hours
    when = parse_dt(market.get("expected_expiration_time")) or parse_dt(market.get("close_time"))
    return when is not None and when <= now + timedelta(hours=limit)


def category_for(ticker: str) -> str:
    t = ticker.upper()
    if any(k in t for k in ("GAME", "MATCH", "FIGHT", "SPREAD", "TOTAL", "NFL", "NBA", "MLB", "NHL", "NCAA")):
        return "Sports"
    if t.startswith(("KXHIGH", "KXLOW", "KXRAIN", "KXSNOW")):
        return "Weather"
    if t.startswith(("KXBTC", "KXETH", "KXSOL", "KXXRP")):
        return "Crypto"
    return "Other"


def run(kalshi, now: datetime, session=None) -> list[Pick]:
    DIAG.clear()
    DIAG["skipped_settles_late"] = 0
    trades = whale_edge._public_trades(
        min_ts=int((now - timedelta(minutes=whale_edge.LOOKBACK_MINUTES)).timestamp()), now=now)
    fast = re.compile(r"(\d+M-|^KX(BTC|ETH|SOL|XRP|DOGE|GOLD|SILVER|WTI|INX|NASDAQ))")
    flows = [x for x in whale_edge._aggregate(trades)
             if not whale_edge._is_fast_financial_market(x["ticker"]) and not fast.search(x["ticker"].upper())]
    flows.sort(key=lambda x: -x["flow_notional_usd"])
    if not flows:
        return []
    markets = kalshi.markets([x["ticker"] for x in flows[:40]])
    picks: list[Pick] = []
    for fl in flows[:40]:
        m = markets.get(fl["ticker"])
        if not m:
            continue
        if not settles_soon(m, now):
            DIAG["skipped_settles_late"] += 1
            continue
        ref = fl["reference_price"]
        if not (0.15 <= ref <= 0.85):
            continue
        _, ask = side_quotes(m, fl["side"])
        if ask is None or ask > ref + SLIPPAGE:
            continue
        picks.append(Pick(
            NAME, m, fl["side"], None,
            f"${fl['flow_notional_usd']:,.0f} bet {fl['side']} at {ref * 100:.0f}¢ "
            f"({fl['flow_trade_count']} trade{'s' if fl['flow_trade_count'] != 1 else ''})",
            category=category_for(fl["ticker"]),
            extra={"title": m.get("title") or fl["ticker"]},
        ))
        if len(picks) >= MAX_PICKS:
            break
    return picks
