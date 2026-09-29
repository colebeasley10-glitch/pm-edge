import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal

from autotrader.paper_runner import run_once
from autotrader.probability import load_pm_edge_ledger
from autotrader.storage import load_portfolio


FIELDS = [
    "evaluated_at", "stage", "ticker", "side", "fair_probability", "devig_method"
]


class FakeClient:
    def list_open_markets(self, max_markets=3000):
        return [{
            "ticker": "KXTEST",
            "title": "Test sports market",
            "status": "active",
            "volume_24h_fp": "1000",
            "yes_bid_dollars": "0.48",
            "yes_ask_dollars": "0.51",
            "no_bid_dollars": "0.49",
            "no_ask_dollars": "0.52",
        }]

    def get_orderbook(self, ticker, depth=20):
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.48", "25"]],
                "no_dollars": [["0.49", "25"]],
            }
        }


def write_ledger(path, when, probability="0.70"):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({
            "evaluated_at": when.isoformat().replace("+00:00", "Z"),
            "stage": "score",
            "ticker": "KXTEST",
            "side": "YES",
            "fair_probability": probability,
            "devig_method": "power",
        })


class ProbabilityRunnerTests(unittest.TestCase):
    def test_fresh_probability_loads(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.csv"
            write_ledger(ledger, now - timedelta(seconds=60))
            snap = load_pm_edge_ledger(ledger, now=now, max_age_seconds=300)
            self.assertEqual(snap.probabilities[("KXTEST", "YES")], Decimal("0.70"))
            self.assertEqual(snap.ages["KXTEST"], 60)

    def test_stale_probability_fails_closed(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.csv"
            write_ledger(ledger, now - timedelta(minutes=10))
            snap = load_pm_edge_ledger(ledger, now=now, max_age_seconds=300)
            self.assertEqual(snap.probabilities, {})

    def test_paper_runner_fills_only_from_fresh_independent_value(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.csv"
            state = root / "state.json"
            runs = root / "runs.jsonl"
            write_ledger(ledger, now - timedelta(seconds=30), "0.70")
            result = run_once(
                client=FakeClient(),
                state_path=state,
                runs_path=runs,
                ledger_path=ledger,
                max_markets=10,
                max_orderbooks=10,
                probability_max_age_seconds=300,
                now=now,
            )
            self.assertEqual(result["paper_fills"], 1)
            portfolio = load_portfolio(state)
            self.assertIn("KXTEST", portfolio.positions)
            self.assertEqual(portfolio.positions["KXTEST"].side, "YES")
            self.assertLess(portfolio.cash, Decimal("500"))
            self.assertEqual(len(runs.read_text().splitlines()), 1)

    def test_paper_runner_does_not_fill_stale_value(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.csv"
            state = root / "state.json"
            runs = root / "runs.jsonl"
            write_ledger(ledger, now - timedelta(minutes=10), "0.90")
            result = run_once(
                client=FakeClient(),
                state_path=state,
                runs_path=runs,
                ledger_path=ledger,
                now=now,
            )
            self.assertEqual(result["paper_fills"], 0)
            self.assertEqual(load_portfolio(state).cash, Decimal("500.00"))


if __name__ == "__main__":
    unittest.main()
