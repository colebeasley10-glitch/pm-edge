import unittest
from decimal import Decimal

from autotrader.production_mirror import (
    MirrorIntent,
    MirrorPolicy,
    allocate,
    opportunity_score,
)

D = Decimal


class ProductionMirrorTests(unittest.TestCase):
    def intent(self, intent_id, edge, sigma="0.01", cost="0.01",
               hold="2", capital="10", p_fill="1", event="E1",
               clusters=(), snapshot=None):
        return MirrorIntent(
            intent_id=intent_id,
            lane="test",
            lane_version="v1",
            ticker=f"T-{intent_id}",
            side="YES",
            edge=D(edge),
            sigma=D(sigma),
            execution_cost=D(cost),
            p_fill=D(p_fill),
            expected_hold_hours=D(hold),
            requested_capital=D(capital),
            event_id=event,
            cluster_ids=tuple(clusters),
            fair_snapshot_id=snapshot,
        )

    def test_score_rewards_edge_and_shorter_hold(self):
        policy = MirrorPolicy()
        slow = self.intent("slow", "0.08", hold="4")
        fast = self.intent("fast", "0.08", hold="1")
        self.assertGreater(opportunity_score(fast, policy), opportunity_score(slow, policy))

    def test_uncertainty_can_zero_out_opportunity(self):
        x = self.intent("x", "0.03", sigma="0.03", cost="0.01")
        self.assertEqual(opportunity_score(x, MirrorPolicy()), D("0"))

    def test_allocator_respects_shared_total_cap(self):
        intents = [
            self.intent(str(i), "0.10", event=f"E{i}", capital="10")
            for i in range(10)
        ]
        decisions = allocate(intents)
        accepted = [d for d in decisions if d.accepted]
        self.assertEqual(sum(d.allocated_capital for d in accepted), D("50"))

    def test_allocator_respects_event_cap(self):
        policy = MirrorPolicy(event_cap=D("0.02"))
        intents = [
            self.intent("a", "0.10", event="SAME", capital="10"),
            self.intent("b", "0.09", event="SAME", capital="10"),
        ]
        decisions = allocate(intents, policy=policy)
        self.assertEqual(sum(d.accepted for d in decisions), 1)
        self.assertIn("event_cap", {d.reason for d in decisions})

    def test_fill_probability_affects_ranking(self):
        a = self.intent("high_fill", "0.08", p_fill="0.9")
        b = self.intent("low_fill", "0.08", p_fill="0.2")
        decisions = allocate([b, a])
        self.assertGreater(
            next(d.score for d in decisions if d.intent_id == "high_fill"),
            next(d.score for d in decisions if d.intent_id == "low_fill"),
        )


if __name__ == "__main__":
    unittest.main()
