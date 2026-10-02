import unittest
from datetime import datetime, timedelta, timezone

from autotrader.portfolio import PaperPortfolio
from autotrader.whale_paper_runner import _eligible_flow_rows, _quick_v2_gate


class QuickWhaleV2GateTests(unittest.TestCase):
    def test_rejects_adverse_move_after_whale(self):
        result = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.50",
            current_ask="0.52",
            flow_age_seconds=20,
            seconds_to_close=600,
            flow_trade_count=3,
            flow_notional_usd=1500,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "quick_v2_adverse_move_after_whale")

    def test_accepts_small_confirming_follow_through(self):
        result = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.55",
            current_ask="0.56",
            flow_age_seconds=20,
            seconds_to_close=600,
            flow_trade_count=3,
            flow_notional_usd=1500,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "quick_v2_confirmed")

    def test_rejects_chasing_large_follow_through(self):
        result = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.57",
            current_ask="0.58",
            flow_age_seconds=20,
            seconds_to_close=600,
            flow_trade_count=3,
            flow_notional_usd=1500,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "quick_v2_follow_through_too_expensive")

    def test_rejects_single_print_without_block(self):
        result = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.54",
            current_ask="0.55",
            flow_age_seconds=20,
            seconds_to_close=600,
            flow_trade_count=1,
            flow_notional_usd=5000,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "quick_v2_insufficient_flow_persistence")

    def test_rejects_wide_book_and_late_market(self):
        wide = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.50",
            current_ask="0.55",
            flow_age_seconds=20,
            seconds_to_close=600,
            flow_trade_count=3,
            flow_notional_usd=1500,
        )
        self.assertEqual(wide["reason"], "quick_v2_spread_too_wide")

        late = _quick_v2_gate(
            reference_cents=55,
            current_bid="0.54",
            current_ask="0.55",
            flow_age_seconds=20,
            seconds_to_close=120,
            flow_trade_count=3,
            flow_notional_usd=1500,
        )
        self.assertEqual(late["reason"], "quick_v2_too_close_to_close")


class QuickWhaleFastRoutingTests(unittest.TestCase):
    def test_fast_market_reaches_budget_before_large_football_flows(self):
        now = datetime(2026, 10, 2, 0, 40, tzinfo=timezone.utc)

        class Client:
            def __init__(self):
                self.market_calls = []

            def get_market(self, ticker):
                self.market_calls.append(ticker)
                return {
                    "ticker": ticker,
                    "event_ticker": ticker + "-EVENT",
                    "title": ticker,
                    "status": "open",
                    "close_time": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                }

            def get_orderbook(self, ticker, depth=20):
                return {
                    "orderbook_fp": {
                        "yes_dollars": [["0.49", "1000"]],
                        "no_dollars": [["0.50", "1000"]],
                    }
                }

        def flow(ticker, notional):
            return {
                "ticker": ticker,
                "side": "YES",
                "trade_id": ticker + "-trade",
                "flow_contracts": 1000,
                "flow_notional_usd": notional,
                "flow_trade_count": 3,
                "reference_price_cents": 50,
                "block_trade": False,
                "latest_time": now - timedelta(seconds=20),
            }

        football = [
            flow(f"KXNFLGAME-26OCT02-TEAM{i}", 10000 - i)
            for i in range(6)
        ]
        fast = flow("KXBTC15M-26OCT020040-V3", 900)

        client = Client()
        decisions = _eligible_flow_rows(
            football + [fast],
            client,
            {"market_meta": {}, "pending": []},
            PaperPortfolio(),
            now,
            max_flows=1,
            slippage="0",
            mode="quick_v2",
            max_flow_age_seconds=55,
            min_seconds_to_close=180,
        )

        self.assertTrue(any(row.get("ticker") == fast["ticker"] for row in decisions))
        self.assertEqual(client.market_calls[0], fast["ticker"])


if __name__ == "__main__":
    unittest.main()
