import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from autotrader.paper_fill_alerts import notify_paper_fills


class PaperFillAlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "fill-alerts.json"
        self.now = datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc)
        self.fill = {
            "decision": "paper_filled",
            "ticker": "KXTEST-YES",
            "side": "YES",
            "contracts": 10,
            "price": "0.42",
            "fee_total": "0.10",
            "market_title": "Test market",
            "source_trade_id": "trade-1",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_sends_only_actual_fill_and_dedupes(self):
        sent = []

        def sender(title, body):
            sent.append((title, body))
            return True

        result = notify_paper_fills(
            "quick_whale_v3",
            [{"decision": "skipped", "ticker": "NOPE", "side": "YES"}, self.fill],
            state_path=self.state,
            sender=sender,
            now=self.now,
        )
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("Quick Whale v3", sent[0][0])
        self.assertIn("10 contracts", sent[0][1])
        self.assertIn("Paper only", sent[0][1])

        again = notify_paper_fills(
            "quick_whale_v3",
            [self.fill],
            state_path=self.state,
            sender=sender,
            now=self.now,
        )
        self.assertEqual(again["queued"], 0)
        self.assertEqual(again["sent"], 0)
        self.assertEqual(len(sent), 1)

    def test_unconfigured_delivery_stays_pending_then_retries(self):
        with mock.patch.dict(os.environ, {
            "PUSHOVER_APP_TOKEN": "",
            "PUSHOVER_USER_KEY": "",
            "AUTOTRADER_FILL_ALERTS_ENABLED": "1",
        }, clear=False):
            result = notify_paper_fills(
                "value_edge",
                [{**self.fill, "source_trade_id": "", "fair_snapshot_id": "fair-1"}],
                state_path=self.state,
                now=self.now,
            )
        self.assertFalse(result["configured"])
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["pending"], 1)

        sent = []
        result = notify_paper_fills(
            "value_edge",
            [],
            state_path=self.state,
            sender=lambda title, body: sent.append((title, body)) or True,
            now=self.now,
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(len(sent), 1)

    def test_failed_send_retries_without_duplicate_queue(self):
        calls = []
        result = notify_paper_fills(
            "whale_flow",
            [self.fill],
            state_path=self.state,
            sender=lambda title, body: calls.append("fail") or False,
            now=self.now,
        )
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["pending"], 1)

        result = notify_paper_fills(
            "whale_flow",
            [self.fill],
            state_path=self.state,
            sender=lambda title, body: calls.append("ok") or True,
            now=self.now,
        )
        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(calls, ["fail", "ok"])


if __name__ == "__main__":
    unittest.main()
