import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pmedge.fair_v2 import book_weight, compute_fair_v2_metrics


class FairV2Tests(unittest.TestCase):
    def test_sharp_weighting_prefers_pinnacle(self):
        now = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
        rows = [
            {"book_key": "pinnacle", "probability": 0.60, "source_ts": now.isoformat()},
            {"book_key": "softbook", "probability": 0.50, "source_ts": now.isoformat()},
        ]
        m = compute_fair_v2_metrics(
            rows, market_type="h2h", event_start=now + timedelta(hours=5), processed_at=now
        )
        self.assertAlmostEqual(m.weighted_fair, (0.60 * 2 + 0.50 * 0.5) / 2.5)
        self.assertEqual(m.n_sharp, 1)
        self.assertGreater(m.n_eff, 1)

    def test_sigma_increases_when_source_is_older(self):
        now = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
        fresh = [
            {"book_key": "pinnacle", "probability": 0.55, "source_ts": now.isoformat()},
            {"book_key": "bookmaker", "probability": 0.56, "source_ts": now.isoformat()},
            {"book_key": "softbook", "probability": 0.54, "source_ts": now.isoformat()},
        ]
        stale_ts = (now - timedelta(minutes=20)).isoformat()
        stale = [dict(x, source_ts=stale_ts) for x in fresh]
        a = compute_fair_v2_metrics(
            fresh, market_type="h2h", event_start=now + timedelta(hours=1), processed_at=now
        )
        b = compute_fair_v2_metrics(
            stale, market_type="h2h", event_start=now + timedelta(hours=1), processed_at=now
        )
        self.assertGreater(b.sigma, a.sigma)
        self.assertGreater(b.source_age_s, a.source_age_s)

    def test_near_start_age_penalty_is_larger(self):
        now = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
        ts = (now - timedelta(minutes=10)).isoformat()
        rows = [
            {"book_key": "pinnacle", "probability": 0.55, "source_ts": ts},
            {"book_key": "bookmaker", "probability": 0.56, "source_ts": ts},
        ]
        far = compute_fair_v2_metrics(
            rows, market_type="h2h", event_start=now + timedelta(hours=30), processed_at=now
        )
        near = compute_fair_v2_metrics(
            rows, market_type="h2h", event_start=now + timedelta(minutes=10), processed_at=now
        )
        self.assertGreater(near.sigma_age, far.sigma_age)
        self.assertGreater(near.sigma, far.sigma)

    def test_method_is_shadow_only_versioned(self):
        now = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
        m = compute_fair_v2_metrics(
            [{"book_key": "pinnacle", "probability": 0.55, "source_ts": now.isoformat()}],
            market_type="h2h",
            processed_at=now,
        )
        self.assertEqual(m.method_version, "fair_v2_shadow_1")


if __name__ == "__main__":
    unittest.main()
