"""Realistic paper fills: walk the real order book, whole contracts, Kalshi fees.

A paper fill is only recorded at prices that were actually on the book in the
snapshot we fetched, never better than the ask and never below it. If the book is
missing or too thin, the result says so and nothing is filled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from research_lab.config import TAKER_FEE_RATE


def taker_fee(price: float, contracts: float, rate: float = TAKER_FEE_RATE) -> float:
    """Kalshi taker fee for one fill: ceil to the cent of rate * C * P * (1 - P)."""
    if contracts <= 0:
        return 0.0
    raw = rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0 - 1e-9) / 100.0


def fee_per_contract(price: float, contracts: int) -> float:
    return taker_fee(price, contracts) / max(1, contracts)


@dataclass
class Fill:
    status: str                         # filled | partial | no_fill
    contracts: int = 0
    avg_price: float | None = None
    cost: float = 0.0                   # contracts * prices paid, before fees
    fee: float = 0.0
    wanted: int = 0
    levels: list = field(default_factory=list)   # [(price, contracts)] actually taken
    reason: str = ""

    @property
    def committed(self) -> float:
        return round(self.cost + self.fee, 2)


def contracts_for_stake(price: float, stake: float) -> int:
    """Whole contracts so contracts * price stays at or under the stake (at least 1)."""
    if not (0 < price < 1):
        return 0
    return max(1, int(math.floor(stake / price + 1e-9)))


def simulate_buy(asks: list[tuple[float, float]], wanted: int, max_price: float) -> Fill:
    """Take liquidity from `asks` (cheapest first) up to `wanted` contracts at <= max_price.

    Fees are charged per price level (each level is a separate fill), rounded up to the
    cent, which matches or slightly overstates Kalshi's real fee.
    """
    if wanted <= 0:
        return Fill("no_fill", wanted=wanted, reason="nothing_to_buy")
    if not asks:
        return Fill("no_fill", wanted=wanted, reason="no_book_depth")
    left, cost, fee, took = wanted, 0.0, 0.0, []
    for price, size in asks:
        if price > max_price + 1e-9 or left <= 0:
            break
        n = int(min(left, math.floor(size + 1e-9)))
        if n <= 0:
            continue
        took.append((price, n))
        cost += n * price
        fee += taker_fee(price, n)
        left -= n
    filled = wanted - left
    if filled <= 0:
        return Fill("no_fill", wanted=wanted, reason="no_liquidity_at_price")
    return Fill("filled" if left == 0 else "partial", contracts=filled, avg_price=round(cost / filled, 4),
                cost=round(cost, 2), fee=round(fee, 2), wanted=wanted, levels=took,
                reason="" if left == 0 else "book_too_thin")
