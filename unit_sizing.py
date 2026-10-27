"""One sizing rule for every live lane (Oct 23, 2026).

Underdogs (price under 50c): risk one unit.
Favorites (50c up to 75c / -300): risk whatever it takes to WIN one unit of profit.
Heavier favorites (over 75c): no bet at all. Winning a full unit there means risking more
than 3 units, and a trimmed bet would break the win-a-unit rule, so those picks are skipped.

A Kalshi/Polymarket contract pays $1, so a contract bought at price p wins (1 - p).
"""
from __future__ import annotations

import math

MAX_FAV_PRICE = 0.75   # -300; anything pricier is skipped on every live lane


def too_chalky(price: float) -> bool:
    """True for favorites priced over 75c (worse than -300): live lanes skip these."""
    return price > MAX_FAV_PRICE + 1e-9


def contracts_for_unit(price: float, unit: float) -> int:
    """Whole contracts for one unit at this price (0 if unusable or over 75c)."""
    if not (0 < price < 1) or unit <= 0 or too_chalky(price):
        return 0
    if price < 0.5:
        return int(math.floor(unit / price + 1e-9))       # risk at most one unit
    return max(1, int(round(unit / (1 - price))))         # win about one unit


def risk_for_unit(price: float, unit: float) -> float:
    """Dollars to risk for one unit at this price, before fees (0 if over 75c)."""
    if not (0 < price < 1) or unit <= 0 or too_chalky(price):
        return 0.0
    return round(unit if price < 0.5 else unit * price / (1 - price), 2)


def contracts_for_flat_risk(price: float, unit: float) -> int:
    """Oct 27, 2026: flat risk on every live lane -- whole contracts costing at most one unit,
    favorites included (a 74c favorite no longer risks ~3 units to win one). 0 over 75c."""
    if not (0 < price < 1) or unit <= 0 or too_chalky(price):
        return 0
    return int(math.floor(unit / price + 1e-9))
