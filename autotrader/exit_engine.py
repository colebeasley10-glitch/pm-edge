"""Deterministic paper-only active exit engine.

This engine does not submit orders. It converts the current position state,
fresh independent fair value, executable exit book, and opportunity cost into a
paper management action.

Decision priority:
SETTLE/FREEZE -> EXIT_THESIS -> SELL -> REDUCE -> ADD -> HOLD.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .thesis import PositionThesis

D = lambda x: Decimal(str(x))


@dataclass(frozen=True)
class ExitInputs:
    current_fair: Decimal | None
    current_sigma: Decimal | None
    fair_age_seconds: float | None
    executable_bid: Decimal | None
    exit_fee_per_contract: Decimal
    exit_available: int
    time_to_event_seconds: float | None
    expected_remaining_hold_hours: Decimal
    opportunity_cost_per_capital_hour: Decimal
    best_competing_score: Decimal | None = None
    liquidity_deteriorating: bool = False
    source_suspended: bool = False
    rules_changed: bool = False
    event_started: bool = False


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reason: str
    hold_value: Decimal | None
    exit_value: Decimal | None
    opportunity_cost: Decimal
    qty_fraction: Decimal = D("1")


def side_fair(yes_fair: Decimal, side: str) -> Decimal:
    return yes_fair if side == "YES" else D("1") - yes_fair


def evaluate_exit(
    thesis: PositionThesis,
    inputs: ExitInputs,
    *,
    contracts: int,
    position_entry_cost_per_contract: Decimal | None = None,
    add_edge_floor: Decimal = D("0.05"),
    reduce_edge_floor: Decimal = D("0.02"),
) -> ExitDecision:
    if contracts < 1:
        raise ValueError("contracts must be positive")

    oc = max(D("0"), inputs.opportunity_cost_per_capital_hour) * max(
        D("0"), inputs.expected_remaining_hold_hours
    )
    exit_value = None
    if inputs.executable_bid is not None:
        bid = D(inputs.executable_bid)
        fee = D(inputs.exit_fee_per_contract)
        if D("0") <= bid <= D("1") and D("0") <= fee <= bid:
            exit_value = bid - fee

    current_side_fair = None
    hold_value = None
    if inputs.current_fair is not None:
        yes_fair = D(inputs.current_fair)
        if D("0") < yes_fair < D("1"):
            current_side_fair = side_fair(yes_fair, thesis.side)
            sigma = max(D("0"), D(inputs.current_sigma or 0))
            hold_value = current_side_fair - sigma

    # 1. Event started: freeze if we no longer have a trustworthy live fair.
    if inputs.event_started and thesis.freeze_at_event_start:
        if hold_value is None:
            return ExitDecision(
                "FREEZE", "event_started_without_live_fair", None, exit_value, oc
            )
        # Exception: a bid materially above risk-adjusted hold value can be sold.
        if (
            exit_value is not None
            and inputs.exit_available >= contracts
            and exit_value >= hold_value + max(D("0.01"), D(inputs.current_sigma or 0))
        ):
            return ExitDecision(
                "SELL", "event_started_exit_bid_above_hold_value",
                hold_value, exit_value, oc
            )
        return ExitDecision("FREEZE", "event_started", hold_value, exit_value, oc)

    # 2. Thesis invalidation.
    if inputs.rules_changed:
        return ExitDecision("EXIT_THESIS", "rules_changed", hold_value, exit_value, oc)
    if inputs.source_suspended:
        return ExitDecision("EXIT_THESIS", "fair_source_suspended", hold_value, exit_value, oc)
    if (
        inputs.fair_age_seconds is not None
        and inputs.fair_age_seconds > thesis.max_fair_age_seconds
        and (inputs.time_to_event_seconds is None or inputs.time_to_event_seconds <= 3600)
    ):
        return ExitDecision("EXIT_THESIS", "fair_too_stale_near_event", hold_value, exit_value, oc)

    if current_side_fair is not None and thesis.entry_sigma > 0:
        adverse_move = thesis.entry_fair if thesis.side == "YES" else D("1") - thesis.entry_fair
        adverse_move -= current_side_fair
        invalidation_band = thesis.invalidation_fair_move_sigma * max(
            thesis.entry_sigma, D(inputs.current_sigma or 0)
        )
        if adverse_move > invalidation_band:
            return ExitDecision(
                "EXIT_THESIS", "fair_moved_against_thesis",
                hold_value, exit_value, oc
            )

    # 3. Sell when executable liquidation + capital opportunity exceeds hold.
    if (
        hold_value is not None
        and exit_value is not None
        and inputs.exit_available >= contracts
        and exit_value >= hold_value - oc
    ):
        return ExitDecision(
            "SELL", "exit_value_plus_redeployment_beats_hold",
            hold_value, exit_value, oc
        )

    # 4. Reduce when edge shrank and concentration/liquidity risk is rising.
    if (
        current_side_fair is not None
        and exit_value is not None
        and inputs.exit_available >= max(1, contracts // 2)
    ):
        current_edge = current_side_fair - exit_value
        if current_edge < reduce_edge_floor and inputs.liquidity_deteriorating:
            return ExitDecision(
                "REDUCE", "edge_shrunk_and_liquidity_deteriorating",
                hold_value, exit_value, oc, D("0.5")
            )

    # 5. Add only if the fair thesis strengthened relative to executable price.
    if current_side_fair is not None and inputs.executable_bid is not None:
        # We intentionally cannot infer an executable add price from the exit bid.
        # This action is advisory only until an ask is supplied by the caller.
        if current_side_fair - thesis.entry_price >= add_edge_floor:
            return ExitDecision(
                "ADD_REVIEW", "fair_supports_more_edge_recheck_ask_required",
                hold_value, exit_value, oc
            )

    return ExitDecision("HOLD", "hold_value_preferred", hold_value, exit_value, oc)
