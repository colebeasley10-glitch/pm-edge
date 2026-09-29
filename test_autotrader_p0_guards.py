import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import watcher
from autotrader.paper_guard import PaperOrderClient
from autotrader.paper_runner import _assert_no_kalshi_write_credentials
from pmedge.fair_value import fair_from_event
from pm_edge_runner_v28 import _alternate_devig_same_side_qualifies


class P0GuardTests(unittest.TestCase):
    def test_started_game_rejected_even_when_expiration_is_future(self):
        now = datetime(2026, 9, 29, 18, 30, tzinfo=timezone.utc)
        market = {
            "ticker": "KXTEST",
            "_kalshi_game_time": "2026-09-29T18:00:00Z",
            "close_time": "2026-09-30T02:00:00Z",
            "expected_expiration_time": "2026-09-30T03:00:00Z",
            "expiration_time": "2026-09-30T04:00:00Z",
        }
        event = {"commence_time": "2026-09-29T18:00:00Z"}
        with patch("watcher.now_utc", return_value=now):
            self.assertFalse(watcher._pregame_safety_ok(market, event))

    def test_spread_line_mismatch_is_explicit_and_not_nearest_line(self):
        spec = SimpleNamespace(
            market_type="spread",
            selection="Team A",
            point=-3.5,
        )
        event = {
            "bookmakers": [{
                "title": "Book One",
                "markets": [{
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Team A", "point": -2.5, "price": 1.91},
                        {"name": "Team B", "point": 2.5, "price": 1.91},
                    ],
                }],
            }],
        }
        consensus, diagnostics = fair_from_event(
            spec,
            event,
            {},
            min_books=1,
            max_dispersion=1.0,
            return_diagnostics=True,
        )
        self.assertIsNone(consensus)
        self.assertIn("Book One:line_mismatch", diagnostics["book_drop_reasons"])

    def test_pattern_based_kalshi_secret_guard(self):
        with patch.dict(os.environ, {"KALSHI_FUTURE_PRIVATE_PEM": "secret"}, clear=True):
            with self.assertRaises(RuntimeError):
                _assert_no_kalshi_write_credentials()

    def test_paper_order_client_always_raises(self):
        client = PaperOrderClient()
        with self.assertRaises(RuntimeError):
            client.create_buy_order(ticker="KXTEST")
        with self.assertRaises(RuntimeError):
            client.create_sell_order(ticker="KXTEST")

    def test_cross_devig_metric_requires_same_side_edge(self):
        q = SimpleNamespace(side="YES", net_edge=0.07)
        fair = {
            "fair_yes_prob": 0.60,
            "fair_proportional": 0.59,
            "fair_shin": 0.54,
            "n_books": 4,
        }
        self.assertTrue(_alternate_devig_same_side_qualifies(q, fair, "fair_proportional"))
        self.assertFalse(_alternate_devig_same_side_qualifies(q, fair, "fair_shin"))


if __name__ == "__main__":
    unittest.main()
