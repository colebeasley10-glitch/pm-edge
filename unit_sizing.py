"""One sizing rule for every live lane (Oct 23, 2026).

Underdogs (price under 50c): risk one unit.
Favorites (price 50c and up): risk whatever it takes to WIN one unit of profit.

A Kalshi/Polymarket contract pays $1, so a contract bought at price p wins (1 - p).

Safety cap: no bet risks more than MAX_RISK_UNITS units. Past -300 (75c) a favorite stops
growing and wins less than a unit, so a 97c pick can't put $160 on the line to win $5.
"""
from __future__ import annotations

import math

MAX_RISK_UNITS = 3.0


def contracts_for_unit(price: float, unit: float) -> int:
    """Whole contracts for one unit at this price (0 if the price or unit is unusable)."""
    if not (0 < price < 1) or unit <= 0:
        return 0
    if price < 0.5:
        return int(math.floor(unit / price + 1e-9))       # risk at most one unit
    n = max(1, int(round(unit / (1 - price))))            # win about one unit
    cap = int(math.floor(MAX_RISK_UNITS * unit / price + 1e-9))
    return max(1, min(n, cap))


def risk_for_unit(price: float, unit: float) -> float:
    """Dollars to risk for one unit at this price, before fees (for bets placed by hand)."""
    if not (0 < price < 1) or unit <= 0:
        return 0.0
    return round(unit if price < 0.5 else min(unit * price / (1 - price), MAX_RISK_UNITS * unit), 2)
