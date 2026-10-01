"""Shadow-only challenger rules for the Value Edge lane.

Every function here is pure and observational: it reads the facts that were
available at the original decision time and returns fields to LOG beside the
production decision. Nothing in this module may be used to accept, reject,
size, or exit a production paper position. Promotion of any challenger
requires the manual gate documented in autotrader/learning.py.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

D = lambda value: Decimal(str(value))

C1_VERSION = "c1_stale_fair_v1"
C1_BASE_EDGE = D("0.05")
C1_EDGE_PER_MINUTE = D("0.001")          # +0.1 percentage point per minute
C1_FREE_MINUTES = D("5")
C1_MAX_EDGE = D("0.15")                  # larger "edges" are treated as artifacts
C1_MAX_SOURCE_AGE_S = 900.0              # 15 minutes

C2_VERSION = "c2_ev_exit_v1"
C3_VERSION = "c3_spread_tracking_v1"
SPREADFIX_LANE_VERSION = "value_edge_v1_spreadfix"
SPREADFIX_PROVENANCE = "fair_provenance_v1_spreadfix"


def _dec(value):
    if value in (None, ""):
        return None
    try:
        return D(value)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def c1_required_edge(fair_age_s):
    age = _dec(fair_age_s)
    if age is None:
        return None
    extra_minutes = max(D("0"), age / D("60") - C1_FREE_MINUTES)
    return C1_BASE_EDGE + C1_EDGE_PER_MINUTE * extra_minutes


def c1_fields(*, net_edge, fair_age_s, source_age_at_fair_s):
    """C1: fair-age-scaled edge bar plus artifact/source-age rejects.

    ``source_age_at_fair_s`` is the sportsbook data age recorded when the fair
    was computed. At decision time the book data is that old PLUS the fair's
    own age, so both are known without looking ahead.
    """
    fair_age = _float(fair_age_s)
    src_at_fair = _float(source_age_at_fair_s)
    source_age = None
    if src_at_fair is not None and fair_age is not None:
        source_age = src_at_fair + fair_age
    out = {
        "challenger_c1_version": C1_VERSION,
        "challenger_c1_fair_age_s": None if fair_age is None else round(fair_age, 3),
        "challenger_c1_source_age_s": None if source_age is None else round(source_age, 3),
        "challenger_c1_required_edge": None,
        "challenger_c1_pass": None,
        "challenger_c1_reason": "",
    }
    required = c1_required_edge(fair_age)
    if required is not None:
        out["challenger_c1_required_edge"] = str(required.quantize(D("0.00001")))
    edge = _dec(net_edge)
    if edge is None or required is None:
        out["challenger_c1_reason"] = "no_net_edge_or_fair_age"
        return out
    if edge >= C1_MAX_EDGE:
        out.update(challenger_c1_pass=False, challenger_c1_reason="edge_too_large_likely_artifact")
    elif source_age is not None and source_age > C1_MAX_SOURCE_AGE_S:
        out.update(challenger_c1_pass=False, challenger_c1_reason="book_source_too_old")
    elif edge < required:
        out.update(challenger_c1_pass=False, challenger_c1_reason="below_age_scaled_edge")
    else:
        out.update(
            challenger_c1_pass=True,
            challenger_c1_reason="pass" if source_age is not None else "pass_source_age_unknown",
        )
    return out


def c3_fields(*, market_type, side, fair_provenance_version):
    is_spread = str(market_type or "").strip().lower() == "spread"
    lane_version = (
        SPREADFIX_LANE_VERSION
        if is_spread and str(fair_provenance_version or "") == SPREADFIX_PROVENANCE
        else "value_edge_v1"
    )
    return {
        "challenger_c3_version": C3_VERSION,
        "challenger_c3_is_spread": is_spread,
        "challenger_c3_is_no_side": bool(is_spread and str(side).upper() == "NO"),
        "challenger_c3_lane_version": lane_version,
    }


def c2_fields(*, production_action, production_reason, executable_bid, exit_fee_per_contract,
              current_side_fair):
    """C2: exit only when the net bid is at least the current independent fair."""
    bid = _dec(executable_bid)
    fee = _dec(exit_fee_per_contract) or D("0")
    fair = _dec(current_side_fair)
    out = {
        "challenger_c2_version": C2_VERSION,
        "challenger_c2_production_exit_reason": str(production_reason or ""),
        "challenger_c2_bid_net_fee": None,
        "challenger_c2_current_fair": None if fair is None else str(fair),
        "challenger_c2_would_exit": None,
        "challenger_c2_reason": "",
    }
    if bid is None or fair is None:
        out["challenger_c2_reason"] = "missing_bid_or_fair"
        return out
    net = bid - fee
    out["challenger_c2_bid_net_fee"] = str(net)
    if net >= fair:
        out.update(challenger_c2_would_exit=True, challenger_c2_reason="net_bid_at_or_above_fair")
    else:
        out.update(
            challenger_c2_would_exit=False,
            challenger_c2_reason=(
                "hold_thesis_move_is_not_exit_reason"
                if str(production_action).upper() == "EXIT_THESIS"
                else "hold_net_bid_below_fair"
            ),
        )
    return out
