import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autotrader.lead_lag import analyze_lead_lag
from autotrader.recorder_store import RecorderStore


class LeadLagTests(unittest.TestCase):
    def test_received_time_lead_lag_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "r.sqlite3")
            store = RecorderStore(db)
            t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

            def fair(ts, probability):
                store.record_fair_snapshot(
                    "TEST",
                    {
                        "fetched_at": ts.isoformat(),
                        "fair_v2_weighted_yes_prob": probability,
                        "sigma": 0.02,
                        "sigma_base": 0.01,
                        "sigma_disagree": 0.01,
                        "sigma_age": 0.0,
                        "n_books": 2,
                        "n_sharp": 1,
                        "n_eff": 1.5,
                        "disagreement": 0.01,
                        "devig_method": "power",
                        "book_inputs": [{
                            "book_key": "pinnacle",
                            "book_title": "Pinnacle",
                            "source_ts": ts.isoformat(),
                            "probability": probability,
                        }],
                        "source_age_s": 0,
                        "system_age_s": 0,
                        "delivery_lag_s": 0,
                        "fair_v2_method_version": "fair_v2_shadow_1",
                    },
                )

            fair(t0, 0.50)
            fair(t0 + timedelta(minutes=1), 0.54)

            store.record_top(
                {
                    "ticker": "TEST",
                    "yes_bid": 0.49,
                    "yes_ask": 0.51,
                    "no_bid": 0.49,
                    "no_ask": 0.51,
                },
                received_at=t0 + timedelta(seconds=50),
                processed_at=t0 + timedelta(seconds=50),
            )
            store.record_top(
                {
                    "ticker": "TEST",
                    "yes_bid": 0.52,
                    "yes_ask": 0.54,
                    "no_bid": 0.46,
                    "no_ask": 0.48,
                },
                received_at=t0 + timedelta(minutes=2),
                processed_at=t0 + timedelta(minutes=2),
            )

            result = analyze_lead_lag(
                db,
                min_sharp_move=0.02,
                horizons_seconds=(60,),
            )
            self.assertEqual(result["n_triggers"], 1)
            self.assertEqual(result["n_timely"], 1)
            self.assertEqual(result["horizons"]["60"]["n"], 1)
            self.assertGreater(result["horizons"]["60"]["mean_signed_move"], 0)


if __name__ == "__main__":
    unittest.main()
