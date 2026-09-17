"""PM Edge v23 launcher: approval-first execution staging.

v23 keeps all v22 pricing, safety, VALUE, Price Fish, overnight, and sizing
behavior. It adds a deterministic execution layer that turns actionable alerts
into structured order intents for later approval.

Important: v23 does not submit real-money orders. Every staged intent is marked
``awaiting_approval`` and ``live_submission_permitted=False``.
"""
from __future__ import annotations

import json
import math

import execution_guard
import watcher
import pm_edge_runner_v22 as v22

V23_STATS = {
    "order_intents_staged": 0,
    "order_intents_duplicate": 0,
    "order_intents_risk_blocked": 0,
    "order_intents_other_blocked": 0,
}


def _safe_float(value, default=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _effective_lane(tier: str, q) -> str:
    tier = str(tier or "primary").lower()
    q_lane = str(getattr(q, "pmedge_lane", "") or "").lower()
    if q_lane == "value":
        return "value"
    if tier == "speculative":
        return "price_fish"
    return tier or "primary"


def _size_for_intent(q, lane: str):
    price = _safe_float(getattr(q, "recommended_limit", None))
    if price is None or not (0.0 < price < 1.0):
        return None

    v20 = v22.v21.v20
    if lane == "value":
        return v20.calculate_share_order(
            price,
            target_profit=v22.VALUE_TARGET_WIN_PROFIT_DOLLARS,
            max_risk=v22.VALUE_MAX_RISK_DOLLARS,
        )
    return v20.calculate_share_order(price)


def _stage_from_alert(market: dict, fair_entry: dict, q, tier: str) -> dict:
    lane = _effective_lane(tier, q)
    side = str(getattr(q, "side", "") or "").upper()
    order = _size_for_intent(q, lane)
    if order is None:
        return {"staged": False, "reason": "unable_to_size"}

    fair_side = v22._side_probability(fair_entry, side)
    return execution_guard.stage_order_intent(
        ticker=str(market.get("ticker") or ""),
        market_title=str(market.get("title") or ""),
        side=side,
        lane=lane,
        limit_price=float(order["price"]),
        shares=int(order["shares"]),
        max_loss=float(order["max_loss"]),
        fair_probability=fair_side,
        net_edge=_safe_float(getattr(q, "net_edge", None)),
        books=fair_entry.get("n_books"),
        source_version=23,
    )


def _record_stage_result(result: dict) -> None:
    if result.get("staged"):
        V23_STATS["order_intents_staged"] += 1
        return
    reason = str(result.get("reason") or "")
    if reason == "duplicate":
        V23_STATS["order_intents_duplicate"] += 1
    elif reason in {"per_order_risk_limit", "daily_risk_limit"}:
        V23_STATS["order_intents_risk_blocked"] += 1
    else:
        V23_STATS["order_intents_other_blocked"] += 1


def install_v23_patch() -> None:
    watcher.WATCHER_VERSION = 23

    original_record_alert = watcher.record_alert

    def record_alert_v23(state, market, fair_entry, q, tier="primary"):
        original_record_alert(state, market, fair_entry, q, tier=tier)
        result = _stage_from_alert(market, fair_entry, q, tier)
        _record_stage_result(result)

    watcher.record_alert = record_alert_v23

    original_send_push = watcher.send_push

    def send_push_v23(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v20 Auto-Sized is live",
            "✅ PM Edge Watch v21 Overnight Look-Ahead is live",
            "✅ PM Edge Watch v22 Value Volume is live",
        }:
            title = "✅ PM Edge Watch v23 Approval Execution is live"

        upper_title = str(title).upper()
        actionable = (
            "PM EDGE" in upper_title
            and "PASS" not in upper_title
            and "DIGEST" not in upper_title
            and "IS LIVE" not in upper_title
        )
        if actionable and "EXECUTION PREVIEW:" not in str(message):
            message = (
                str(message).rstrip()
                + "\n\nEXECUTION PREVIEW: staged for approval; no real-money order was placed automatically."
            )
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v23


def self_test() -> None:
    class FakeQ:
        side = "YES"
        recommended_limit = 0.42
        net_edge = 0.061

    assert _effective_lane("primary", FakeQ()) == "primary"
    assert _effective_lane("speculative", FakeQ()) == "price_fish"

    class ValueQ(FakeQ):
        pmedge_lane = "value"

    assert _effective_lane("speculative", ValueQ()) == "value"

    order = _size_for_intent(FakeQ(), "primary")
    assert order is not None
    assert order["shares"] == 9
    assert abs(order["max_loss"] - 3.78) < 1e-9

    value_order = _size_for_intent(ValueQ(), "value")
    assert value_order is not None
    assert value_order["max_loss"] <= v22.VALUE_MAX_RISK_DOLLARS + 1e-9

    print("PM Edge v23 approval-execution self-test: PASS")


def main() -> int:
    v22.v21.v20.v19.v18.v17.v16.install_v16_patch()
    v22.v21.v20.v19.v18.v17.install_v17_patch()
    v22.v21.v20.v19.v18.install_v18_patch()
    v22.v21.v20.v19.install_v19_patch()
    v22.v21.v20.install_v20_patch()
    v22.v21.install_v21_patch()
    v22.install_v22_patch()
    install_v23_patch()

    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v22.v21.v20.v19.v18.v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v22.v21.v20.v19.v18.v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(v22.v21.v20.v19.v18.V18_STATS, sort_keys=True))
    print("PM_EDGE_V19_REALISTIC_FISH " + json.dumps(v22.v21.v20.v19.V19_STATS, sort_keys=True))
    print("PM_EDGE_V20_AUTO_SIZE " + json.dumps(v22.v21.v20.V20_STATS, sort_keys=True))
    print("PM_EDGE_V21_OVERNIGHT " + json.dumps(v22.v21.V21_STATS, sort_keys=True))
    print("PM_EDGE_V22_VALUE " + json.dumps(v22.V22_STATS, sort_keys=True))
    print("PM_EDGE_V23_EXECUTION " + json.dumps(V23_STATS, sort_keys=True))
    print("PM_EDGE_EXECUTION_QUEUE " + json.dumps(execution_guard.queue_summary(), sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
