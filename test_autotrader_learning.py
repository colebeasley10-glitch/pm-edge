import unittest

from autotrader.learning import _build_challenger


def sample(edge, roi, risked="10"):
    risk = float(risked)
    return {
        "lane": "value_edge",
        "edge": str(edge),
        "risked": str(risk),
        "pnl": str(risk * float(roi)),
        "clv": None,
    }


class LearningLoopTests(unittest.TestCase):
    def test_does_not_change_rules_with_small_sample(self):
        rows = [sample("0.055", -0.10) for _ in range(10)]
        result = _build_challenger(rows, "0.05")
        self.assertEqual(result["rules"]["min_net_edge"], 0.05)
        self.assertEqual(result["proposals"], [])
        self.assertTrue(result["production_rules_unchanged"])
        self.assertFalse(result["auto_promotion_enabled"])

    def test_proposes_challenger_only_after_evidence_threshold(self):
        rows = (
            [sample("0.055", -0.10) for _ in range(40)]
            + [sample("0.085", 0.08) for _ in range(80)]
        )
        result = _build_challenger(rows, "0.05")
        self.assertEqual(result["rules"]["min_net_edge"], 0.07)
        self.assertEqual(result["status"], "shadow_testing")
        self.assertEqual(result["proposals"][0]["rule"], "min_net_edge")
        self.assertTrue(result["production_rules_unchanged"])
        self.assertFalse(result["auto_promotion_enabled"])

    def test_never_auto_promotes_even_with_large_sample(self):
        rows = (
            [sample("0.055", -0.10) for _ in range(200)]
            + [sample("0.085", 0.08) for _ in range(250)]
        )
        result = _build_challenger(rows, "0.05")
        self.assertTrue(result["promotion_gate"]["eligible_for_review"])
        self.assertTrue(result["promotion_gate"]["requires_manual_approval"])
        self.assertFalse(result["auto_promotion_enabled"])


if __name__ == "__main__":
    unittest.main()
