"""Pure, auditable EV gate for paper-market candidates; never submits orders."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

D = lambda x: Decimal(str(x))

@dataclass(frozen=True)
class Evaluation:
    qualified: bool
    reason: str
    fair_probability: Decimal | None
    executable_price: Decimal | None
    net_edge: Decimal | None

def evaluate(candidate, *, fair_probability=None, fee_per_contract="0",
             slippage_per_contract="0", min_net_edge="0.05",
             quote_age_seconds=None, max_quote_age_seconds=2,
             event_data_age_seconds=None, max_event_data_age_seconds=5,
             large_edge_threshold="0.15", large_edge_max_age_seconds=300):
    """Fail closed if independently estimated fair value or freshness is absent.

    Very large apparent edges are especially vulnerable to stale-fair artifacts:
    Kalshi can react to news before a sportsbook-derived fair refresh arrives.
    Even if the normal fair-value TTL is configured looser, require an outsized
    edge to be backed by a fair value no more than five minutes old.
    """
    if fair_probability is None:
        return Evaluation(False, "missing_independent_probability", None, None, None)
    if quote_age_seconds is None or quote_age_seconds < 0 or quote_age_seconds > max_quote_age_seconds:
        return Evaluation(False, "stale_or_missing_quote", None, None, None)
    if event_data_age_seconds is None or event_data_age_seconds < 0 or event_data_age_seconds > max_event_data_age_seconds:
        return Evaluation(False, "stale_or_missing_event_data", None, None, None)
    try:
        fair, price = D(fair_probability), D(candidate.executable_ask)
        fee, slippage, threshold = D(fee_per_contract), D(slippage_per_contract), D(min_net_edge)
        large_threshold = D(large_edge_threshold)
        large_max_age = float(large_edge_max_age_seconds)
    except (InvalidOperation, ValueError, TypeError):
        return Evaluation(False, "invalid_numeric_input", None, None, None)
    if not (D("0") <= fair <= D("1") and D("0") < price < D("1")):
        return Evaluation(False, "invalid_probability_or_price", None, None, None)
    if any(x < 0 for x in (fee, slippage, threshold, large_threshold)) or large_max_age < 0:
        return Evaluation(False, "negative_cost_or_threshold", None, None, None)
    net_edge = fair - price - fee - slippage
    if net_edge >= large_threshold and float(event_data_age_seconds) > large_max_age:
        return Evaluation(False, "stale_fair_for_large_edge", fair, price, net_edge)
    if net_edge < threshold:
        return Evaluation(False, "insufficient_net_edge", fair, price, net_edge)
    return Evaluation(True, "qualified_paper_candidate", fair, price, net_edge)
