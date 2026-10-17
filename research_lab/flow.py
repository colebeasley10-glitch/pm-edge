"""Public Kalshi trade-flow metrics (the "Sharp" signal), measured per market and side.

These are descriptive numbers only. A burst of prints can be one trader splitting an
order, or hedging; the Hybrid strategy uses flow as a filter, never as a probability.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from research_lab.ledger import parse_dt


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def trade_side(t: dict) -> str | None:
    s = str(t.get("taker_outcome_side") or t.get("taker_side") or "").upper()
    return s if s in {"YES", "NO"} else None


def trade_price(t: dict, side: str) -> float | None:
    p = _f(t.get("yes_price_dollars" if side == "YES" else "no_price_dollars"))
    if p is None:  # legacy cents fields
        c = _f(t.get("yes_price" if side == "YES" else "no_price"))
        p = None if c is None else c / 100.0
    return p if p is not None and 0 < p < 1 else None


def trade_count(t: dict) -> float:
    return max(0.0, _f(t.get("count_fp")) or _f(t.get("count")) or 0.0)


@dataclass
class Flow:
    ticker: str
    side: str
    trades: int = 0
    contracts: float = 0.0
    notional: float = 0.0
    largest: float = 0.0
    vwap: float | None = None
    latest: datetime | None = None
    market_notional: float = 0.0     # both sides, same market

    @property
    def share(self) -> float | None:
        return self.notional / self.market_notional if self.market_notional > 0 else None

    def as_dict(self, now: datetime) -> dict:
        return {"flow_trades": self.trades, "flow_contracts": round(self.contracts, 2),
                "flow_notional": round(self.notional, 2), "flow_largest": round(self.largest, 2),
                "flow_vwap": None if self.vwap is None else round(self.vwap, 4),
                "flow_share": None if self.share is None else round(self.share, 3),
                "flow_age_min": None if self.latest is None else round((now - self.latest).total_seconds() / 60, 1)}


def aggregate(trades: list[dict], since: datetime) -> dict[tuple[str, str], Flow]:
    """Flow per (ticker, side) for trades at or after `since`."""
    flows: dict[tuple[str, str], Flow] = {}
    by_market: dict[str, float] = defaultdict(float)
    weighted: dict[tuple[str, str], float] = defaultdict(float)
    for t in trades:
        ts = parse_dt(t.get("created_time"))
        side = trade_side(t)
        ticker = str(t.get("ticker") or "")
        if ts is None or ts < since or not side or not ticker:
            continue
        price, n = trade_price(t, side), trade_count(t)
        if price is None or n <= 0:
            continue
        fl = flows.setdefault((ticker, side), Flow(ticker, side))
        fl.trades += 1
        fl.contracts += n
        fl.notional += n * price
        fl.largest = max(fl.largest, n)
        weighted[(ticker, side)] += n * price
        if fl.latest is None or ts > fl.latest:
            fl.latest = ts
        by_market[ticker] += n * price
    for key, fl in flows.items():
        fl.vwap = weighted[key] / fl.contracts if fl.contracts else None
        fl.market_notional = by_market[fl.ticker]
    return flows


def flow_bucket(trades: int | None) -> str:
    if not trades:
        return "0"
    if trades < 5:
        return "1-4"
    if trades < 10:
        return "5-9"
    if trades < 20:
        return "10-19"
    return "20+"
