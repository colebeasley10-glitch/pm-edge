import unittest
from decimal import Decimal
from autotrader.scanner import Candidate
from autotrader.evaluator import evaluate

C = Candidate("TEST", "Test", "YES", Decimal(".50"), Decimal("20"),
              Decimal(".02"), Decimal("1000"))

class EvaluatorTests(unittest.TestCase):
    def test_fail_closed_missing_probability(self):
        self.assertEqual(evaluate(C, quote_age_seconds=0, event_data_age_seconds=0).reason,
                         "missing_independent_probability")

    def test_reject_stale_inputs(self):
        self.assertEqual(evaluate(C, fair_probability=".80", quote_age_seconds=4,
                                  event_data_age_seconds=0).reason, "stale_or_missing_quote")
        self.assertEqual(evaluate(C, fair_probability=".80", quote_age_seconds=0,
                                  event_data_age_seconds=10).reason, "stale_or_missing_event_data")

    def test_large_edge_requires_extra_fresh_fair(self):
        result = evaluate(
            C,
            fair_probability=".90",
            quote_age_seconds=0,
            event_data_age_seconds=301,
            max_event_data_age_seconds=1200,
        )
        self.assertFalse(result.qualified)
        self.assertEqual(result.reason, "stale_fair_for_large_edge")
        self.assertEqual(result.net_edge, Decimal(".40"))

    def test_large_edge_allowed_when_fair_is_under_five_minutes_old(self):
        result = evaluate(
            C,
            fair_probability=".90",
            quote_age_seconds=0,
            event_data_age_seconds=299,
            max_event_data_age_seconds=1200,
        )
        self.assertTrue(result.qualified)

    def test_net_edge_after_costs(self):
        result = evaluate(C, fair_probability=".60", fee_per_contract=".02",
                          slippage_per_contract=".01", quote_age_seconds=0,
                          event_data_age_seconds=0)
        self.assertTrue(result.qualified)
        self.assertEqual(result.net_edge, Decimal(".07"))

    def test_reject_low_edge(self):
        result = evaluate(C, fair_probability=".55", fee_per_contract=".02",
                          slippage_per_contract=".01", quote_age_seconds=0,
                          event_data_age_seconds=0)
        self.assertFalse(result.qualified)
        self.assertEqual(result.reason, "insufficient_net_edge")

if __name__ == "__main__":
    unittest.main()
