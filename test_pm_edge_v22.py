"""Focused tests for PM Edge v22 VALUE lane."""
import pm_edge_runner_v22 as v22


class FakeSettings:
    maker_fee_rate = 0.0175
    taker_fee_rate = 0.07


def find_fixture():
    fair = {"fair_yes_prob": 0.50, "n_books": 4, "sport_title": "Test"}
    for cents in range(30, 49):
        bid = cents / 100.0
        market = {
            "ticker": f"VALUE-{cents}",
            "title": "Team A vs Team B: Team A wins",
            "yes_bid": bid,
            "yes_ask": min(0.99, bid + 0.02),
            "no_bid": max(0.01, 0.98 - bid),
            "no_ask": max(0.02, 0.99 - bid),
        }
        primary = v22._raw_score_market(market, fair, FakeSettings(), None)
        value = v22.find_value_trade(market, fair, FakeSettings())
        if not primary.qualifies and value is not None:
            return market, fair, primary, value
    return None


def test_value_lane_has_real_middle_band():
    fixture = find_fixture()
    assert fixture is not None, "No current-price market clears VALUE while missing Primary"
    market, fair, primary, value = fixture
    print("VALUE_FIXTURE", market)
    print("VALUE_RESULT", value.side, value.recommended_limit, value.net_edge, value.execution)
    assert primary.qualifies is False
    assert v22._q_lane(value) == "value"
    assert int(fair["n_books"]) >= 4


def test_two_books_never_enough_for_value():
    fixture = find_fixture()
    assert fixture is not None
    market, fair, _, _ = fixture
    assert v22.find_value_trade(market, {**fair, "n_books": 2}, FakeSettings()) is None


def test_value_state_is_separate():
    fixture = find_fixture()
    assert fixture is not None
    market, fair, _, value = fixture
    state = {"alerts": {}}
    ok, reason = v22.watcher.should_alert(state, market, fair, value, tier="value")
    assert ok and reason == "NEW"
    v22.watcher.record_alert(state, market, fair, value, tier="value")
    assert any(key.endswith("|VALUE") for key in state["alerts"])


if __name__ == "__main__":
    tests = [test_value_lane_has_real_middle_band, test_two_books_never_enough_for_value, test_value_state_is_separate]
    for test in tests:
        test()
        print("PASS", test.__name__)
