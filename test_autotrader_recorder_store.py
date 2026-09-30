import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autotrader.recorder_store import RecorderStore, _pack_json, unpack_json


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

    def test_market_meta_hash_ignores_fast_quote_fields(self):
        static = {
            "ticker": "KXTEST",
            "event_ticker": "KXE",
            "series_ticker": "KXS",
            "title": "Test market",
            "subtitle": "Outcome",
            "rules_primary": "Rule",
            "rules_secondary": "",
            "open_time": "2026-09-29T00:00:00Z",
            "close_time": "2026-09-30T00:00:00Z",
            "expected_expiration_time": "2026-09-30T00:00:00Z",
            "fee_type": "quadratic",
        }
        first = {
            **static,
            "yes_bid_dollars": "0.40",
            "yes_ask_dollars": "0.42",
            "volume_24h_fp": "100",
            "open_interest_fp": "50",
            "updated_time": "2026-09-29T12:00:00Z",
            "price_ranges": [{"start": "0.01", "end": "0.99"}],
        }
        second = {
            **static,
            "yes_bid_dollars": "0.60",
            "yes_ask_dollars": "0.62",
            "volume_24h_fp": "999",
            "open_interest_fp": "80",
            "updated_time": "2026-09-29T12:05:00Z",
            "price_ranges": [{"start": "0.02", "end": "0.98"}],
        }
        self.assertEqual(
            RecorderStore._market_content_hash(first, "fees-v1"),
            RecorderStore._market_content_hash(second, "fees-v1"),
        )

    def test_raw_json_compression_round_trip(self):
        payload = {
            "ticker": "TEST",
            "rules_primary": "x" * 5000,
            "nested": {"a": [1, 2, 3]},
        }
        packed = _pack_json(payload)
        self.assertTrue(packed.startswith("zlib64:"))
        self.assertLess(len(packed), len(str(payload)))
        self.assertEqual(unpack_json(packed), payload)

    def test_weather_settlement_calibration(self):
        received = self.t0
        self.store.record_weather_shadow_fairs(
            [{
                "ticker": "WX1",
                "event_ticker": "W-E1",
                "city": "Test City",
                "target_date": "2026-09-29",
                "bucket_label": "70° to 71°",
                "variable": "temperature_2m_max",
                "p_shadow": 0.8,
                "sampling_sigma": 0.1,
                "n_members": 20,
                "ensemble_mean_f": 70.5,
                "ensemble_stdev_f": 1.0,
                "latitude": 1.0,
                "longitude": 2.0,
                "timezone": "UTC",
                "model": "test",
                "rounding_assumption": "nearest_integer_half_up",
                "promotion_blocker": "test",
            }],
            received_at=received,
        )
        self.store.record_weather_settlements(
            [{
                "ticker": "WX1",
                "event_ticker": "W-E1",
                "status": "settled",
                "result": "YES",
                "yes_settlement_value": 1.0,
                "source_hint": "the_weather_company",
                "target_date": "2026-09-29",
                "bucket_label": "70° to 71°",
            }],
            observed_at=received + timedelta(hours=1),
        )
        summary = self.store.weather_calibration_summary()
        self.assertEqual(summary["n"], 1)
        self.assertAlmostEqual(summary["brier"], 0.04, places=6)

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
