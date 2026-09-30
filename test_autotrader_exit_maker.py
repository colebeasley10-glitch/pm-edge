import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from autotrader.exit_engine import ExitInputs, evaluate_exit
from autotrader.recorder_store import RecorderStore
from autotrader.thesis import PositionThesis

D = Decimal


class ExitAndMakerTests(unittest.TestCase):
    def thesis(self):
        return PositionThesis(
            thesis_version="1",
            lane="value_edge",
            lane_version="v1",
            ticker="TEST",
            side="YES",
            fair_snapshot_id="fair1",
            fair_source="sportsbook_no_vig",
            entry_fair=D("0.60"),
            entry_sigma=D("0.02"),
            entry_edge_net=D("0.06"),
            entry_price=D("0.52"),
            entry_fee_per_contract=D("0.017"),
            entry_slippage_per_contract=D("0.005"),
            expected_hold_hours=D("4"),
            expected_ev_per_contract=D("0.04"),
            expected_ev_per_capital_hour=D("0.019"),
            event_start_at="2026-09-30T01:00:00Z",
            created_at="2026-09-29T20:00:00Z",
        )

    def test_exit_when_liquidation_plus_redeployment_beats_hold(self):
        d = evaluate_exit(
            self.thesis(),
            ExitInputs(
                current_fair=D("0.60"),
                current_sigma=D("0.02"),
                fair_age_seconds=30,
                executable_bid=D("0.58"),
                exit_fee_per_contract=D("0.01"),
                exit_available=20,
                time_to_event_seconds=7200,
                expected_remaining_hold_hours=D("2"),
                opportunity_cost_per_capital_hour=D("0.02"),
            ),
            contracts=10,
        )
        self.assertEqual(d.action, "SELL")

    def test_price_drop_alone_is_not_stop_loss(self):
        d = evaluate_exit(
            self.thesis(),
            ExitInputs(
                current_fair=D("0.60"),
                current_sigma=D("0.02"),
                fair_age_seconds=30,
                executable_bid=D("0.45"),
                exit_fee_per_contract=D("0.01"),
                exit_available=20,
                time_to_event_seconds=7200,
                expected_remaining_hold_hours=D("2"),
                opportunity_cost_per_capital_hour=D("0"),
            ),
            contracts=10,
        )
        self.assertEqual(d.action, "HOLD")

    def test_adverse_fair_move_invalidates_thesis(self):
        d = evaluate_exit(
            self.thesis(),
            ExitInputs(
                current_fair=D("0.50"),
                current_sigma=D("0.02"),
                fair_age_seconds=30,
                executable_bid=D("0.49"),
                exit_fee_per_contract=D("0.01"),
                exit_available=20,
                time_to_event_seconds=7200,
                expected_remaining_hold_hours=D("2"),
                opportunity_cost_per_capital_hour=D("0"),
            ),
            contracts=10,
        )
        self.assertEqual(d.action, "EXIT_THESIS")

    def test_maker_observation_logs_both_sides_without_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecorderStore(str(Path(directory) / "r.sqlite3"))
            n = store.record_maker_observations(
                {
                    "ticker": "TEST",
                    "event_ticker": "EVENT",
                    "yes_bid": 0.48,
                    "yes_ask": 0.52,
                    "no_bid": 0.48,
                    "no_ask": 0.52,
                    "yes_bid_size": 12,
                    "yes_ask_size": 8,
                    "volume_24h": 1000,
                    "liquidity_dollars": 500,
                },
                received_at=datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(n, 2)
            self.assertEqual(store.counts()["maker_observations"], 2)


if __name__ == "__main__":
    unittest.main()
