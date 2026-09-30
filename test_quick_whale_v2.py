import unittest

from autotrader.whale_paper_runner import _quick_v2_gate


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


if __name__ == "__main__":
    unittest.main()
