import json
import tempfile
from pathlib import Path

import position_signals


class FakeClient:
    def get_positions(self, limit=200):
        return [{"ticker": "ABC", "position_fp": "8.00"}]


def _state(path: Path, fair: float) -> None:
    path.write_text(json.dumps({
        "fair_cache": {
            "ABC": {
                "fair_yes_prob": fair,
                "n_books": 5,
                "fetched_at": "2099-01-01T00:00:00Z",
                "commence_time": "2099-01-02T00:00:00Z",
            }
        }
    }))


def main() -> None:
    old_state = position_signals.WATCH_STATE_PATH
    old_market = position_signals._market
    old_fresh = position_signals._fresh_enough
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            position_signals.WATCH_STATE_PATH = p
            position_signals._fresh_enough = lambda fair: True

            _state(p, 0.30)
            position_signals._market = lambda ticker: {
                "status": "active", "title": "Test market",
                "yes_bid_dollars": "0.39", "yes_ask_dollars": "0.40",
            }
            signals = position_signals.evaluate_positions(FakeClient())
            assert signals[0]["action"] == "SELL"
            assert signals[0]["side"] == "YES"

            _state(p, 0.45)
            position_signals._market = lambda ticker: {
                "status": "active", "title": "Test market",
                "yes_bid_dollars": "0.29", "yes_ask_dollars": "0.30",
            }
            signals = position_signals.evaluate_positions(FakeClient())
            assert signals[0]["action"] == "ADD"
            assert signals[0]["shares"] >= 1
    finally:
        position_signals.WATCH_STATE_PATH = old_state
        position_signals._market = old_market
        position_signals._fresh_enough = old_fresh

    print("Position signal tests: PASS")


if __name__ == "__main__":
    main()
