import unittest

from autotrader.learning import _build_challenger
from autotrader.adaptive_shadow import learn_samples, _profile_signal


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

    def test_adaptive_shadow_learns_each_completed_trade_without_mutating_production(self):
        state = {
            "processed_trade_ids": [],
            "profiles": {},
            "recommendations": {},
            "production_mutation_allowed": False,
        }
        rows = []
        for i in range(6):
            rows.append({
                "lane": "quick_whale_v2",
                "ticker": f"KXTEST-{i}",
                "side": "NO",
                "contracts": 10,
                "entry_price": "0.80",
                "risked": "8.00",
                "pnl": "-4.00",
                "flow_notional_usd": "1200",
                "thesis": {
                    "source": "quick_whale_v2",
                    "series": "KXTEST",
                    "entered_at": f"2026-10-02T0{i}:00:00Z",
                    "close_time": f"2026-10-02T0{i}:30:00Z",
                    "source_trade_id": f"trade-{i}",
                },
            })
        out = learn_samples(rows, state)
        rec = out["recommendations"]["quick_whale_v2"]
        self.assertEqual(rec["settled_trades"], 6)
        self.assertLess(rec["position_size_multiplier"], 1.0)
        self.assertTrue(rec["shadow_only"])
        self.assertTrue(rec["production_rules_unchanged"])
        self.assertFalse(out["production_mutation_allowed"])

    def test_adaptive_shadow_deduplicates_trade_updates(self):
        state = {
            "processed_trade_ids": [],
            "profiles": {},
            "recommendations": {},
        }
        row = {
            "lane": "quick_whale_v3",
            "ticker": "KXTEST-ONE",
            "side": "YES",
            "contracts": 10,
            "entry_price": "0.50",
            "risked": "5.00",
            "pnl": "1.00",
            "flow_notional_usd": "1500",
            "thesis": {
                "source": "quick_whale_v3",
                "series": "KXTEST",
                "entered_at": "2026-10-02T01:00:00Z",
                "close_time": "2026-10-02T02:00:00Z",
                "source_trade_id": "one",
            },
        }
        learn_samples([row], state)
        first_n = state["profiles"]["lane:quick_whale_v3"]["n"]
        learn_samples([row], state)
        self.assertEqual(state["profiles"]["lane:quick_whale_v3"]["n"], first_n)

    def test_adaptive_signal_is_confidence_shrunk(self):
        weak = {
            "n": 1,
            "mean_roi": -1.0,
            "ewma_roi": -1.0,
        }
        strong = {
            "n": 50,
            "mean_roi": -1.0,
            "ewma_roi": -1.0,
        }
        weak_signal = _profile_signal(weak)
        strong_signal = _profile_signal(strong)
        self.assertGreater(weak_signal["shrunk_roi"], strong_signal["shrunk_roi"])
        self.assertGreater(weak_signal["target_size_multiplier"], strong_signal["target_size_multiplier"])


if __name__ == "__main__":
    unittest.main()
