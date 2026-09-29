import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autotrader.paper_runner import _refresh_local_fair_values, run_once
from autotrader.probability import load_pm_edge_api, load_pm_edge_ledger
from autotrader.storage import load_portfolio

FIELDS = ["evaluated_at", "stage", "ticker", "side", "fair_probability", "devig_method"]


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
        return {"orderbook_fp": {
            "yes_dollars": [["0.48", "25"]],
            "no_dollars": [["0.49", "25"]],
        }}

    def get_market(self, ticker):
        return {"ticker": ticker, "status": "open", "result": ""}


def write_ledger(path, when, probability="0.70", side="YES"):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({
            "evaluated_at": when.isoformat().replace("+00:00", "Z"),
            "stage": "score",
            "ticker": "KXTEST",
            "side": side,
            "fair_probability": probability,
            "devig_method": "power",
        })


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ProbabilityRunnerTests(unittest.TestCase):
    def test_fresh_probability_loads(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.csv"
            write_ledger(ledger, now - timedelta(seconds=60))
            snap = load_pm_edge_ledger(ledger, now=now, max_age_seconds=300)
            self.assertEqual(snap.probabilities[("KXTEST", "YES")], Decimal("0.70"))
            self.assertEqual(snap.ages[("KXTEST", "YES")], 60)
            self.assertEqual(snap.ages["KXTEST"], 60)

    def test_stale_probability_fails_closed(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.csv"
            write_ledger(ledger, now - timedelta(minutes=10))
            snap = load_pm_edge_ledger(ledger, now=now, max_age_seconds=300)
            self.assertEqual(snap.probabilities, {})

    def test_future_probability_fails_closed(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.csv"
            write_ledger(ledger, now + timedelta(minutes=2))
            snap = load_pm_edge_ledger(ledger, now=now, max_age_seconds=300)
            self.assertEqual(snap.probabilities, {})

    def test_read_only_api_adapter(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        payload = {"items": [{
            "ticker": "KXTEST",
            "side": "YES",
            "fair_probability": 0.66,
            "alerted_at": (now - timedelta(seconds=45)).isoformat().replace("+00:00", "Z"),
            "source_version": "27",
        }]}
        with patch("autotrader.probability.requests.get", return_value=FakeResponse(payload)) as get:
            snap = load_pm_edge_api("https://example.invalid", "token", now=now, max_age_seconds=300)
        self.assertEqual(snap.probabilities[("KXTEST", "YES")], Decimal("0.66"))
        self.assertEqual(snap.ages[("KXTEST", "YES")], 45)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://example.invalid/v1/fair-values")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")

    def test_local_refresh_is_quota_guarded_by_cooldown(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.csv"
            refresh_state = root / "refresh.json"
            completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with patch.dict("os.environ", {"ODDS_API_KEY": "test-key"}, clear=False), \
                    patch("autotrader.paper_runner.subprocess.run", return_value=completed) as run:
                first = _refresh_local_fair_values(
                    ledger_path=ledger,
                    refresh_state_path=refresh_state,
                    now=now,
                    refresh_interval_seconds=1800,
                )
                second = _refresh_local_fair_values(
                    ledger_path=ledger,
                    refresh_state_path=refresh_state,
                    now=now + timedelta(minutes=5),
                    refresh_interval_seconds=1800,
                )
            self.assertTrue(first["attempted"])
            self.assertEqual(second["reason"], "refresh_cooldown")
            self.assertEqual(run.call_count, 1)
            child_env = run.call_args.kwargs["env"]
            self.assertEqual(child_env["PM_EDGE_MODE"], "paper")
            self.assertEqual(child_env["PM_EDGE_ORDER_SUBMISSION_ENABLED"], "0")
            self.assertEqual(child_env["PUSHOVER_USER_KEY"], "")

    def test_paper_runner_fills_only_from_fresh_independent_value(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "ledger.csv"
            state = root / "state.json"
            runs = root / "runs.jsonl"
            write_ledger(ledger, now - timedelta(seconds=30), "0.70")
            result = run_once(
                client=FakeClient(), state_path=state, runs_path=runs,
                ledger_path=ledger, max_markets=10, max_orderbooks=10,
                probability_max_age_seconds=300, now=now,
            )
            self.assertEqual(result["paper_fills"], 1)
            portfolio = load_portfolio(state)
            self.assertIn("KXTEST", portfolio.positions)
            self.assertEqual(portfolio.positions["KXTEST"].side, "YES")
            self.assertEqual(portfolio.positions["KXTEST"].entry, Decimal("0.515"))
            self.assertEqual(portfolio.positions["KXTEST"].entry_fee, Decimal("0.01"))
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
                client=FakeClient(), state_path=state, runs_path=runs,
                ledger_path=ledger, now=now,
            )
            self.assertEqual(result["paper_fills"], 0)
            self.assertEqual(load_portfolio(state).cash, Decimal("500.00"))


if __name__ == "__main__":
    unittest.main()
