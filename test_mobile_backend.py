from __future__ import annotations

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

    paths = {route.path for route in mobile_backend.app.routes}
    for required in {
        "/health",
        "/v1/dashboard",
        "/v1/bets",
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
