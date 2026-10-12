"""Sharp money: follow large Kalshi trades from the public trade tape.

When a big trade (or cluster) hits a market in the last 45 minutes and the
price you can buy now is no worse than what they paid (+1¢), follow it.
There's no model here; the league's closing-line score is what decides
whether big traders actually know something.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import whale_edge
from league.book import Pick, side_quotes

NAME = "sharp"
MAX_PICKS = 4
SLIPPAGE = 0.01


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
    trades = whale_edge._public_trades(
        min_ts=int((now - timedelta(minutes=whale_edge.LOOKBACK_MINUTES)).timestamp()), now=now)
    flows = [x for x in whale_edge._aggregate(trades) if not whale_edge._is_fast_financial_market(x["ticker"])]
    flows.sort(key=lambda x: -x["flow_notional_usd"])
    if not flows:
        return []
    markets = kalshi.markets([x["ticker"] for x in flows[:40]])
    picks: list[Pick] = []
    for fl in flows[:40]:
        m = markets.get(fl["ticker"])
        if not m:
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
