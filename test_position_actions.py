import position_actions


def test_position_action_round_trip(monkeypatch):
    monkeypatch.setenv("PM_EDGE_APPROVAL_SECRET", "test-secret")
    token = position_actions.create_position_action_token(
        action="SELL", ticker="ABC", side="YES", shares=8,
        limit_price=0.37, fair_probability=0.29, signal_gap=0.08,
        books=5, now=1000, ttl_seconds=600,
    )
    x = position_actions.verify_position_action_token(token, now=1100)
    assert x["action"] == "SELL"
    assert x["side"] == "YES"
    assert x["shares"] == 8
    assert x["limit_price"] == 0.37
    assert x["fair_probability"] == 0.29
    assert x["signal_gap"] == 0.08


def test_position_action_expiry(monkeypatch):
    monkeypatch.setenv("PM_EDGE_APPROVAL_SECRET", "test-secret")
    token = position_actions.create_position_action_token(
        action="ADD", ticker="ABC", side="NO", shares=4,
        limit_price=0.25, fair_probability=0.40, signal_gap=0.15,
        books=6, now=1000, ttl_seconds=60,
    )
    try:
        position_actions.verify_position_action_token(token, now=1200)
    except ValueError as exc:
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("expected expiry")
