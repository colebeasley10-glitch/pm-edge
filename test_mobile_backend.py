from __future__ import annotations

import approval_guardrails
import approval_links
import mobile_backend


def main() -> None:
    health = mobile_backend.health()
    assert health["ok"] is True
    assert health["service"] == "pm-edge-mobile-api"

    assert mobile_backend._market_ask({"yes_ask": 0.42}, "YES") == 0.42
    assert mobile_backend._market_ask({"no_ask": 0.58}, "NO") == 0.58
    assert mobile_backend._market_ask({}, "YES") is None

    signal_id = "0123456789abcdefabcd"
    client_id = mobile_backend._client_order_id(signal_id)
    assert client_id.startswith(f"pmedge-{signal_id}-")
    assert approval_links.client_order_id(signal_id, f"native-v1:{signal_id}") == client_id

    epoch = mobile_backend._event_start_epoch({"commence_time": "2026-09-21T17:00:00Z"})
    assert isinstance(epoch, int) and epoch > 0

    assert approval_guardrails.DAILY_RISK_BUDGET_DOLLARS == 75.0

    positions = []
    orders = []
    for i, event in enumerate(("AAA", "BBB", "CCC", "DDD")):
        ticker = f"KXNFLGAME-26SEP17{event}-{event}"
        positions.append({"ticker": ticker, "position_fp": "1"})
        orders.append({
            "ticker": ticker,
            "outcome_side": "YES",
            "client_order_id": f"pmedge-test-{i}",
            "created_time": "2026-09-17T18:00:00Z",
            "initial_count_fp": "20",
            "yes_price_dollars": "0.50",
        })
    now = mobile_backend.datetime(2026, 9, 17, 18, 30, tzinfo=mobile_backend.timezone.utc)
    under_cap = approval_guardrails.evaluate_position_guard(
        ticker="KXNFLGAME-26SEP17EEE-EEE",
        side="YES",
        candidate_risk=5.0,
        positions=positions,
        orders=orders,
        now=now,
    )
    assert under_cap["blocked"] is False
    assert under_cap["sport_open_positions"] == 4

    over_cap = approval_guardrails.evaluate_position_guard(
        ticker="KXNFLGAME-26SEP17FFF-FFF",
        side="YES",
        candidate_risk=40.0,
        positions=positions,
        orders=orders,
        now=now,
    )
    assert over_cap["blocked"] is True
    assert over_cap["reason"] == "daily_open_risk_cap"

    paths = {route.path for route in mobile_backend.app.routes}
    for required in {
        "/health",
        "/v1/dashboard",
        "/v1/bets",
        "/v1/fair-values",
        "/v1/live",
        "/v1/bets/{signal_id}/review",
        "/v1/bets/{signal_id}/submit",
        "/v1/analytics",
        "/v1/system",
    }:
        assert required in paths

    print("PM Edge native mobile API tests: PASS")


if __name__ == "__main__":
    main()
