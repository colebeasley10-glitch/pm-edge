"""Kalshi prediction-market fee helpers for paper execution.

General prediction taker fees are rounded up to the next cent at the order level.
Special series can have different rates; the paper engine uses the general rate
conservatively unless an explicit series-aware rate is supplied later.
"""
from decimal import Decimal, ROUND_CEILING

D = lambda value: Decimal(str(value))


def prediction_fee(contracts, price, *, rate="0.07", multiplier="1"):
    count = int(contracts)
    p = D(price)
    r = D(rate)
    m = D(multiplier)
    if count < 1 or not (D("0") < p < D("1")) or r < 0 or m < 0:
        raise ValueError("invalid fee inputs")
    raw = m * r * D(count) * p * (D("1") - p)
    cents = (raw * D("100")).to_integral_value(rounding=ROUND_CEILING)
    return cents / D("100")


def effective_fee_per_contract(contracts, price, *, rate="0.07", multiplier="1"):
    total = prediction_fee(contracts, price, rate=rate, multiplier=multiplier)
    return total / D(int(contracts))
