from __future__ import annotations
from dataclasses import dataclass
import math
from .utils import clamp, ceil_to, floor_to


@dataclass(frozen=True)
class QuoteDecision:
    side: str
    execution: str
    fair_prob: float
    best_bid: float | None
    best_ask: float | None
    recommended_limit: float | None
    hard_max: float
    expected_fee_per_contract: float
    raw_edge: float | None
    net_edge: float | None
    spread: float | None
    qualifies: bool
    reason: str


def fee_per_contract(price: float, rate: float, contracts: int = 1) -> float:
    contracts = max(1, int(contracts))
    raw = rate * contracts * price * (1.0 - price)
    return max(0.0, math.ceil(raw * 100.0) / 100.0) / contracts


def _hard_max(fair_prob: float, min_net_edge: float, nonfee_buffer: float, fee_rate: float) -> float:
    best = 0.01
    for i in range(1, 10000):
        p = i / 10000
        net = fair_prob - p - nonfee_buffer - fee_per_contract(p, fee_rate)
        if net >= min_net_edge:
            best = p
        else:
            break
    return clamp(best, 0.01, 0.99)


def price_side(
    *,
    side: str,
    fair_prob: float,
    best_bid: float | None,
    best_ask: float | None,
    min_net_edge: float,
    nonfee_buffer: float,
    maker_fee_rate: float,
    taker_fee_rate: float,
    tick_size: float = 0.01,
) -> QuoteDecision:
    if side not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    if not (0 < fair_prob < 1):
        raise ValueError("fair probability must be between 0 and 1")

    hard_max_maker = _hard_max(fair_prob, min_net_edge, nonfee_buffer, maker_fee_rate)
    hard_max_taker = _hard_max(fair_prob, min_net_edge, nonfee_buffer, taker_fee_rate)

    spread = None
    if best_bid is not None and best_ask is not None:
        spread = max(0.0, best_ask - best_bid)

    maker_price = None
    maker_edge = None
    if best_bid is not None:
        candidate = ceil_to(best_bid + tick_size, tick_size)
        if best_ask is not None:
            candidate = min(candidate, floor_to(best_ask - tick_size, tick_size))
        candidate = min(candidate, floor_to(hard_max_maker, tick_size))
        if candidate > best_bid + 1e-9 and (best_ask is None or candidate < best_ask - 1e-9):
            candidate = clamp(candidate, 0.01, 0.99)
            maker_price = candidate
            maker_edge = fair_prob - candidate - nonfee_buffer - fee_per_contract(candidate, maker_fee_rate)

    taker_edge = None
    if best_ask is not None:
        taker_edge = fair_prob - best_ask - nonfee_buffer - fee_per_contract(best_ask, taker_fee_rate)

    if maker_price is not None and maker_edge is not None and maker_edge >= min_net_edge:
        return QuoteDecision(
            side, "MAKE", fair_prob, best_bid, best_ask, maker_price, hard_max_maker,
            fee_per_contract(maker_price, maker_fee_rate),
            fair_prob - maker_price, maker_edge, spread, True,
            "Resting limit preserves minimum net edge after modeled maker fee and buffers.",
        )

    if best_ask is not None and taker_edge is not None and taker_edge >= min_net_edge and best_ask <= hard_max_taker + 1e-9:
        return QuoteDecision(
            side, "TAKE", fair_prob, best_bid, best_ask, best_ask, hard_max_taker,
            fee_per_contract(best_ask, taker_fee_rate),
            fair_prob - best_ask, taker_edge, spread, True,
            "Current ask preserves minimum net edge after modeled taker fee and buffers.",
        )

    return QuoteDecision(
        side, "PASS", fair_prob, best_bid, best_ask, None, min(hard_max_maker, hard_max_taker),
        0.0, None if best_ask is None else fair_prob - best_ask,
        taker_edge, spread, False,
        "No executable price preserves the configured minimum net edge.",
    )


def best_binary_trade(
    *, fair_yes_prob: float, yes_bid: float | None, yes_ask: float | None,
    no_bid: float | None, no_ask: float | None, min_net_edge: float,
    nonfee_buffer: float, maker_fee_rate: float, taker_fee_rate: float,
    tick_size: float = 0.01,
) -> QuoteDecision:
    yes_q = price_side(
        side="YES", fair_prob=fair_yes_prob, best_bid=yes_bid, best_ask=yes_ask,
        min_net_edge=min_net_edge, nonfee_buffer=nonfee_buffer,
        maker_fee_rate=maker_fee_rate, taker_fee_rate=taker_fee_rate, tick_size=tick_size,
    )
    no_q = price_side(
        side="NO", fair_prob=1 - fair_yes_prob, best_bid=no_bid, best_ask=no_ask,
        min_net_edge=min_net_edge, nonfee_buffer=nonfee_buffer,
        maker_fee_rate=maker_fee_rate, taker_fee_rate=taker_fee_rate, tick_size=tick_size,
    )

    def score(q):
        return q.net_edge if q.qualifies and q.net_edge is not None else -999

    return max((yes_q, no_q), key=score)
