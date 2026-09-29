import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autotrader.recorder_store import RecorderStore


class RecorderStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RecorderStore(str(Path(self.tmp.name) / "recorder.sqlite3"))
        self.t0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_book_at_never_uses_future_received_row(self):
        self.store.record_top(
            {
                "ticker": "TEST",
                "yes_bid": 0.40,
                "yes_ask": 0.42,
                "no_bid": 0.58,
                "no_ask": 0.60,
                "yes_bid_size": 10,
                "yes_ask_size": 10,
            },
            received_at=self.t0,
            processed_at=self.t0,
        )
        self.store.record_top(
            {
                "ticker": "TEST",
                "yes_bid": 0.60,
                "yes_ask": 0.62,
                "no_bid": 0.38,
                "no_ask": 0.40,
                "yes_bid_size": 10,
                "yes_ask_size": 10,
            },
            received_at=self.t0 + timedelta(seconds=10),
            processed_at=self.t0 + timedelta(seconds=10),
        )
        row = self.store.book_at("TEST", self.t0 + timedelta(seconds=5))
        self.assertAlmostEqual(row["yes_bid"], 0.40)
        self.assertAlmostEqual(row["yes_ask"], 0.42)

    def test_market_meta_is_append_only_on_change(self):
        raw = {
            "ticker": "TEST",
            "event_ticker": "EVENT",
            "title": "Will it happen?",
            "status": "open",
            "updated_time": "2026-09-29T11:59:00Z",
        }
        self.assertTrue(
            self.store.record_market_meta(
                raw, received_at=self.t0, fee_schedule_id="fee-v1"
            )
        )
        self.assertFalse(
            self.store.record_market_meta(
                raw,
                received_at=self.t0 + timedelta(minutes=1),
                fee_schedule_id="fee-v1",
            )
        )
        changed = dict(raw, status="closed")
        self.assertTrue(
            self.store.record_market_meta(
                changed,
                received_at=self.t0 + timedelta(minutes=2),
                fee_schedule_id="fee-v1",
            )
        )
        self.assertEqual(self.store.counts()["markets_meta"], 2)

    def test_trade_overlap_is_idempotent(self):
        trade = {
            "trade_id": "T1",
            "ticker": "TEST",
            "count_fp": "5.00",
            "yes_price_dollars": "0.5000",
            "no_price_dollars": "0.5000",
            "created_time": "2026-09-29T12:00:00Z",
            "taker_outcome_side": "yes",
            "is_block_trade": False,
        }
        self.assertEqual(self.store.record_trades([trade], received_at=self.t0), 1)
        self.assertEqual(
            self.store.record_trades(
                [trade], received_at=self.t0 + timedelta(seconds=2)
            ),
            0,
        )
        rows = self.store.trades_between(
            "TEST", self.t0 - timedelta(seconds=1), self.t0 + timedelta(seconds=5)
        )
        self.assertEqual(len(rows), 1)

    def test_point_in_time_audit_passes_empty_store(self):
        audit = self.store.audit_point_in_time()
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["violations"], 0)
        self.assertEqual(self.store.counts()["audit_runs"], 1)

    def test_point_in_time_audit_catches_future_book_reference(self):
        with self.store.connect() as con:
            con.execute(
                """INSERT INTO intents(
                    intent_id,created_at,lane,lane_version,book_id,market_ticker,
                    side,action,ob_received_at,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "i1",
                    self.t0.isoformat(),
                    "value_edge",
                    "v1",
                    "research:value_edge",
                    "TEST",
                    "YES",
                    "open",
                    (self.t0 + timedelta(seconds=1)).isoformat(),
                    "rejected",
                ),
            )
        audit = self.store.audit_point_in_time()
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["checks"]["intent_future_book"], 1)

    def test_schema_has_v11_fair_age_fields(self):
        with self.store.connect() as con:
            cols = {
                row["name"] for row in con.execute("PRAGMA table_info(fair_snapshots)")
            }
        self.assertTrue(
            {"source_age_s", "system_age_s", "delivery_lag_s"}.issubset(cols)
        )


if __name__ == "__main__":
    unittest.main()
