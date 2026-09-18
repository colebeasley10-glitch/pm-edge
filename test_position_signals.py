import json

import position_signals


class FakeClient:
    def get_positions(self, limit=200):
        return [{"ticker": "ABC", "position_fp": "8.00"}]


def test_sell_signal(monkeypatch, tmp_path):
    state = {
        "fair_cache": {
            "ABC": {
                "fair_yes_prob": 0.30,
                "n_books": 5,
                "fetched_at": "2099-01-01T00:00:00Z",
                "commence_time": "2099-01-02T00:00:00Z",
            }
        }
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    monkeypatch.setattr(position_signals, "WATCH_STATE_PATH", p)
    monkeypatch.setattr(position_signals, "_fresh_enough", lambda fair: True)
    monkeypatch.setattr(position_signals, "_market", lambda ticker: {
        "status": "active", "title": "Test market",
        "yes_bid_dollars": "0.39", "yes_ask_dollars": "0.40",
    })
    signals = position_signals.evaluate_positions(FakeClient())
    assert signals[0]["action"] == "SELL"
    assert signals[0]["side"] == "YES"


def test_add_signal(monkeypatch, tmp_path):
    state = {
        "fair_cache": {
            "ABC": {
                "fair_yes_prob": 0.45,
                "n_books": 5,
                "fetched_at": "2099-01-01T00:00:00Z",
                "commence_time": "2099-01-02T00:00:00Z",
            }
        }
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state))
    monkeypatch.setattr(position_signals, "WATCH_STATE_PATH", p)
    monkeypatch.setattr(position_signals, "_fresh_enough", lambda fair: True)
    monkeypatch.setattr(position_signals, "_market", lambda ticker: {
        "status": "active", "title": "Test market",
        "yes_bid_dollars": "0.29", "yes_ask_dollars": "0.30",
    })
    signals = position_signals.evaluate_positions(FakeClient())
    assert signals[0]["action"] == "ADD"
    assert signals[0]["shares"] >= 1
