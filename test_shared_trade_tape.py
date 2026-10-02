import unittest
from datetime import datetime, timedelta, timezone

from autotrader.shared_trade_tape import SharedTradeTape


def _trade(tid, when, *, ticker="KXTEST", side="YES", price="0.55", count="100"):
    return {
        "trade_id": tid,
        "ticker": ticker,
        "taker_outcome_side": side,
        "yes_price_dollars": price,
        "no_price_dollars": str(1 - float(price)),
        "count_fp": count,
        "is_block_trade": False,
        "created_time": when.isoformat().replace("+00:00", "Z"),
    }


class SharedTradeTapeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)

    def test_one_fetch_populates_multiple_consumers(self):
        pages = {
            None: {
                "trades": [
                    _trade("t1", self.now - timedelta(seconds=5)),
                    _trade("t2", self.now - timedelta(seconds=10)),
                ],
                "cursor": "c1",
            },
            "c1": {
                "trades": [
                    _trade("t3", self.now - timedelta(seconds=20)),
                    _trade("t4", self.now - timedelta(seconds=30)),
                ],
                "cursor": None,
            },
        }
        calls = []

        def fetch(params):
            calls.append(dict(params))
            return pages[params.get("cursor")]

        tape = SharedTradeTape(
            retention_minutes=2,
            page_cap=10,
            fetch_page=fetch,
            clock=lambda: self.now,
        )
        result = tape.refresh(now=self.now)
        self.assertTrue(result["backfill_complete"])
        self.assertEqual(len(calls), 2)

        min_ts = int((self.now - timedelta(seconds=40)).timestamp())
        first, first_diag = tape.snapshot(min_ts=min_ts, now=self.now)
        second, second_diag = tape.snapshot(min_ts=min_ts, now=self.now)

        self.assertEqual(len(first), 4)
        self.assertEqual(len(second), 4)
        self.assertTrue(first_diag["trade_horizon_covered"])
        self.assertTrue(second_diag["trade_horizon_covered"])
        self.assertEqual(first_diag["trade_source"], "shared_collector")
        # Reading the cache for a second lane must not hit Kalshi again.
        self.assertEqual(len(calls), 2)

    def test_partial_warmup_is_usable_only_for_covered_continuity(self):
        def fetch(params):
            return {
                "trades": [
                    _trade("t1", self.now - timedelta(seconds=4)),
                    _trade("t2", self.now - timedelta(seconds=9)),
                ],
                "cursor": "more",
            }

        tape = SharedTradeTape(
            retention_minutes=2,
            page_cap=1,
            fetch_page=fetch,
            clock=lambda: self.now,
        )
        result = tape.refresh(now=self.now)
        self.assertFalse(result["backfill_complete"])

        recent_min = int((self.now - timedelta(seconds=8)).timestamp())
        _, recent_diag = tape.snapshot(min_ts=recent_min, now=self.now)
        self.assertTrue(recent_diag["trade_horizon_covered"])

        older_min = int((self.now - timedelta(seconds=30)).timestamp())
        _, older_diag = tape.snapshot(min_ts=older_min, now=self.now)
        self.assertFalse(older_diag["trade_horizon_covered"])
        self.assertLess(older_diag["trade_window_coverage_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()
