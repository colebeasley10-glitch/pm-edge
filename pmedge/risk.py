from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Sizing:
    contracts: int
    cost_dollars: float
    kelly_fraction_raw: float
    bankroll_fraction_used: float
    reason: str


def size_trade(
    *, fair_prob: float, price: float, fee_per_contract: float,
    bankroll: float, kelly_fraction: float, max_trade_pct: float,
    max_contracts: int, min_contracts: int = 1,
) -> Sizing:
    if price <= 0 or price >= 1 or bankroll <= 0:
        return Sizing(0, 0.0, 0.0, 0.0, "invalid price/bankroll")

    # Kelly fraction of bankroll staked for a binary contract with cost p and fair q.
    raw_kelly = max(0.0, (fair_prob - price) / max(1e-9, 1.0 - price))
    target_frac = min(max_trade_pct, raw_kelly * max(0.0, kelly_fraction))
    budget = bankroll * target_frac
    unit_cost = price + fee_per_contract
    contracts = min(max_contracts, int(math.floor(budget / unit_cost))) if unit_cost > 0 else 0
    if contracts < min_contracts:
        return Sizing(0, 0.0, raw_kelly, 0.0, "signal qualifies but risk budget is below minimum contract size")
    cost = contracts * unit_cost
    return Sizing(
        contracts, cost, raw_kelly, cost / bankroll,
        "fractional Kelly capped by max-trade percentage",
    )
