import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from autotrader.storage import load_portfolio
from autotrader.whale_paper_runner import run_once


class FakeClient:
    def get_market(self, ticker):
        return {
            "ticker": ticker,
            "event_ticker": "KXNFLGAME-TEST",
            "title": "Test football market",
            "subtitle": "Test matchup",
            "yes_sub_title": "Test Team wins",
            "no_sub_title": "Test Team does not win",
            "status": "open",
            "result": "",
        }

    def get_orderbook(self, ticker, depth=20):
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.49", "100"]],
                "no_dollars": [["0.50", "100"]],
            }
        }


FLOW = {
    "ticker": "KXNFLGAME-30JAN01TEST-TEST",
    "side": "YES",
    "reference_price": 0.55,
    "reference_price_cents": 55,
    "flow_contracts": 1000,
    "flow_notional_usd": 550.0,
    "flow_trade_count": 2,
    "largest_trade_contracts": 700,
    "block_trade": False,
    "trade_id": "trade-1",
    "latest_time": None,
}


class WhalePaperRunnerTests(unittest.TestCase):
    def test_take_flow_auto_fills_paper_only_without_paid_odds_calls(self):
        now = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("autotrader.whale_paper_runner.whale_edge._public_trades", return_value=[{"trade_id": "trade-1"}]), \
                    patch("autotrader.whale_paper_runner.whale_edge._aggregate", return_value=[dict(FLOW)]), \
                    patch("autotrader.whale_paper_runner.whale_edge._late_entry_reason", return_value=None):
                result = run_once(
                    client=FakeClient(),
                    state_path=root / "portfolio.json",
                    runner_state_path=root / "runner.json",
                    runs_path=root / "runs.jsonl",
                    now=now,
                )
            self.assertEqual(result["paper_fills"], 1)
            self.assertEqual(result["credit_usage"]["paid_odds_api_calls"], 0)
            portfolio = load_portfolio(root / "portfolio.json")
            self.assertIn(FLOW["ticker"], portfolio.positions)
            self.assertEqual(portfolio.positions[FLOW["ticker"]].side, "YES")

    def test_worse_live_price_rests_maker_order_instead_of_assuming_fill(self):
        class MakerClient(FakeClient):
            def get_orderbook(self, ticker, depth=20):
                return {
                    "orderbook_fp": {
                        "yes_dollars": [["0.46", "100"]],
                        "no_dollars": [["0.47", "100"]],
                    }
                }

        now = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
        flow = dict(FLOW)
        flow["reference_price"] = 0.50
        flow["reference_price_cents"] = 50
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("autotrader.whale_paper_runner.whale_edge._public_trades", return_value=[{"trade_id": "trade-1"}]), \
                    patch("autotrader.whale_paper_runner.whale_edge._aggregate", return_value=[flow]), \
                    patch("autotrader.whale_paper_runner.whale_edge._late_entry_reason", return_value=None), \
                    patch("autotrader.whale_paper_runner.whale_edge._trusted_start", return_value=None):
                result = run_once(
                    client=MakerClient(),
                    state_path=root / "portfolio.json",
                    runner_state_path=root / "runner.json",
                    runs_path=root / "runs.jsonl",
                    now=now,
                )
            self.assertEqual(result["paper_fills"], 0)
            self.assertEqual(result["maker_orders_resting"], 1)
            self.assertEqual(result["pending_orders"], 1)
            self.assertEqual(load_portfolio(root / "portfolio.json").positions, {})


if __name__ == "__main__":
    unittest.main()
