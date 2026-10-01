"""Regression tests for the 2026-10 reliability/correctness audit fixes.

Covers: Kalshi timeout wiring in the long-running worker, the watchdog (scan
hang and lane-heartbeat staleness), the fail-closed scan budget, bounded run-log
tail reads, lane path separation, alternate-spread devig pairing, truthful
status (live marks, liveness, closed-position record), and the guarantee that
learning output never changes production entry rules.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from autotrader import paper_runner, whale_paper_runner
from autotrader.portfolio import PaperPortfolio
from autotrader.scanner import scan_public
from autotrader.storage import load_portfolio, save_portfolio, tail_jsonl
from pmedge.fair_value import _book_prob_spread
from test_autotrader_probability_runner import FakeClient, write_ledger


def _reset_watchdog_state():
    with paper_runner._VALUE_STAGE_LOCK:
        paper_runner._VALUE_STAGE_STATE.update(active=False, started_monotonic=None, stage=None)
        paper_runner._WORKER_HEARTBEATS.clear()


class TimeoutWiringTests(unittest.TestCase):
    def test_worker_clients_use_configured_short_timeout(self):
        built = []

        class RecordingClient:
            def __init__(self, base_url, timeout=20):
                built.append(timeout)

        class Stop(Exception):
            pass

        def fake_run_once(**kwargs):
            raise Stop()

        env = {
            "AUTOTRADER_KALSHI_TIMEOUT_SECONDS": "6",
            "WHALE_PAPER_ENABLED": "0",
            "WHALE_QUICK_PAPER_ENABLED": "0",
            "WHALE_QUICK_V2_PAPER_ENABLED": "0",
        }
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, env), \
                patch.object(paper_runner, "KalshiPublicClient", RecordingClient), \
                patch.object(paper_runner, "run_once", fake_run_once), \
                patch.object(sys, "argv", [
                    "paper_runner", "--once",
                    "--state", str(Path(d) / "s.json"), "--runs", str(Path(d) / "r.jsonl"),
                ]):
            with self.assertRaises(Stop):
                paper_runner.main()
        # main() configures the process-wide Kalshi limiter; don't leak it.
        from pmedge.rate_limit import configure_shared_limiter
        configure_shared_limiter(rate=0, burst=1)
        self.assertTrue(built)
        self.assertEqual(set(built), {6})


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        _reset_watchdog_state()

    def tearDown(self):
        _reset_watchdog_state()

    def test_idle_worker_is_healthy(self):
        self.assertIsNone(paper_runner._watchdog_check(420))

    def test_hung_value_scan_requires_restart(self):
        with paper_runner._VALUE_STAGE_LOCK:
            paper_runner._VALUE_STAGE_STATE.update(
                active=True, started_monotonic=time.monotonic() - 500, stage="market_scan_start",
            )
        reason = paper_runner._watchdog_check(420)
        self.assertIsNotNone(reason)
        self.assertEqual(reason["stage"], "market_scan_start")

    def test_scan_within_timeout_is_not_killed(self):
        with paper_runner._VALUE_STAGE_LOCK:
            paper_runner._VALUE_STAGE_STATE.update(
                active=True, started_monotonic=time.monotonic() - 100, stage="fair_load_start",
            )
        self.assertIsNone(paper_runner._watchdog_check(420))

    def test_stale_lane_heartbeat_requires_restart(self):
        paper_runner._worker_beat("quick_whale_v2", 900, now_monotonic=time.monotonic() - 1000)
        paper_runner._worker_beat("whale_flow", 1800)
        reason = paper_runner._watchdog_check(420)
        self.assertIsNotNone(reason)
        self.assertEqual(set(reason["stale_workers"]), {"quick_whale_v2"})

    def test_watchdog_loop_exits_with_failure_code(self):
        with paper_runner._VALUE_STAGE_LOCK:
            paper_runner._VALUE_STAGE_STATE.update(
                active=True, started_monotonic=time.monotonic() - 999, stage="settlement_start",
            )
        codes = []
        paper_runner._value_watchdog(
            420, exit_fn=codes.append, sleep_fn=lambda _s: None, max_checks=1,
        )
        self.assertEqual(codes, [70])

    def test_scan_error_clears_active_scan(self):
        paper_runner._value_stage("scan_start")
        paper_runner._value_stage("scan_error", error="X")
        self.assertIsNone(paper_runner._watchdog_check(300))


class ScanBudgetTests(unittest.TestCase):
    def test_expired_budget_fails_closed_without_network_calls(self):
        calls = []

        class CountingClient(FakeClient):
            def get_market(self, ticker):
                calls.append(("market", ticker))
                return super().get_market(ticker)

            def get_orderbook(self, ticker, depth=20):
                calls.append(("book", ticker))
                return super().get_orderbook(ticker, depth)

        diag = {}
        found = scan_public(
            CountingClient(), ticker_filter={"KXTEST", "KXOTHER"}, diagnostics=diag,
            deadline_monotonic=time.monotonic() - 1,
        )
        self.assertEqual(found, [])
        self.assertEqual(calls, [])
        self.assertEqual(diag["deadline_skipped"], 2)

    def test_open_budget_scans_normally(self):
        diag = {}
        found = scan_public(
            FakeClient(), ticker_filter={"KXTEST"}, diagnostics=diag,
            deadline_monotonic=time.monotonic() + 60,
        )
        self.assertTrue(found)
        self.assertEqual(diag["deadline_skipped"], 0)


class TailJsonlTests(unittest.TestCase):
    def test_matches_full_read_and_skips_partial_trailing_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "runs.jsonl"
            rows = [{"i": i, "pad": "x" * (i % 37)} for i in range(500)]
            text = "".join(json.dumps(r) + "\n" for r in rows)
            text += "not json\n[1,2]\n" + json.dumps({"i": 500}) + "\n" + '{"i": 501, "partial'
            path.write_text(text, encoding="utf-8")
            expected = rows + [{"i": 500}]
            for limit in (1, 7, 50, 501, 10_000):
                self.assertEqual(tail_jsonl(path, limit, block_size=64), expected[-limit:])

    def test_missing_or_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tail_jsonl(Path(d) / "missing.jsonl", 5), [])
            empty = Path(d) / "empty.jsonl"
            empty.write_text("")
            self.assertEqual(tail_jsonl(empty, 5), [])


class LanePathSeparationTests(unittest.TestCase):
    def test_shared_portfolio_path_is_refused(self):
        with self.assertRaises(RuntimeError):
            paper_runner._assert_distinct_lane_paths({
                "value_edge": {"portfolio": "/data/a.json", "runs": "/data/v.jsonl"},
                "whale_flow": {"portfolio": "/data/./a.json", "runs": "/data/w.jsonl"},
            })

    def test_distinct_paths_are_allowed(self):
        paper_runner._assert_distinct_lane_paths({
            "value_edge": {"portfolio": "/data/a.json", "runs": "/data/v.jsonl"},
            "quick_whale_v2": {"portfolio": "/data/q.json", "runner_state": "/data/qs.json",
                               "runs": "/data/q.jsonl"},
        })

    def test_whale_lane_scan_never_touches_value_edge_bankroll(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            value_state = root / "value.json"
            seeded = PaperPortfolio()
            self.assertTrue(seeded.buy("KXVALUE", "YES", 5, "0.50", "0.50", 5, "0.01"))
            save_portfolio(seeded, value_state)
            before = value_state.read_bytes()

            with patch.object(whale_paper_runner.whale_edge, "_public_trades", return_value=[]):
                whale_paper_runner.run_once(
                    client=FakeClient(), state_path=root / "whale.json",
                    runner_state_path=root / "whale-state.json", runs_path=root / "whale.jsonl",
                    now=now,
                )
            self.assertEqual(value_state.read_bytes(), before)
            whale = load_portfolio(root / "whale.json")
            self.assertEqual(whale.cash, Decimal("500.00"))
            self.assertEqual(whale.positions, {})


class RestartPersistenceTests(unittest.TestCase):
    def test_portfolio_round_trip_preserves_accounting_and_thesis(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            p = PaperPortfolio()
            thesis = {"lane": "value_edge", "entry_edge_net": "0.06"}
            self.assertTrue(p.buy("KXA", "NO", 4, "0.40", "0.40", 4, "0.02", exposure_key="EV1",
                                  fair_snapshot_id="snap1", thesis=thesis))
            self.assertTrue(p.buy("KXB", "YES", 3, "0.30", "0.30", 3, "0.01"))
            self.assertTrue(p.settle("KXB", "YES"))
            save_portfolio(p, path)
            q = load_portfolio(path)
            self.assertEqual(q.cash, p.cash)
            self.assertEqual(q.realized_pnl, p.realized_pnl)
            self.assertEqual(q.ledger, p.ledger)
            self.assertEqual(q.positions["KXA"].thesis, thesis)
            # Re-entry guards survive a restart.
            self.assertFalse(q.buy("KXC", "YES", 1, "0.5", "0.5", 1, exposure_key="EV1"))
            self.assertFalse(q.buy("KXD", "YES", 1, "0.5", "0.5", 1, fair_snapshot_id="snap1"))


class AlternateSpreadPairingTests(unittest.TestCase):
    BOOK = {"markets": [{"key": "alternate_spreads", "outcomes": [
        {"name": "Green Bay Packers", "price": 1.80, "point": -3.5},
        {"name": "Green Bay Packers", "price": 2.60, "point": 3.5},
        {"name": "New York Jets", "price": 1.55, "point": 3.5},
        {"name": "New York Jets", "price": 2.10, "point": -3.5},
    ]}]}

    def test_target_is_devigged_against_opponent_mirrored_line(self):
        got = _book_prob_spread(self.BOOK, "Green Bay Packers", -3.5, {})
        p_gb, p_nyj = 1 / 1.80, 1 / 1.55
        self.assertAlmostEqual(got["proportional"], p_gb / (p_gb + p_nyj), places=9)

    def test_plus_side_uses_its_own_complement(self):
        got = _book_prob_spread(self.BOOK, "Green Bay Packers", 3.5, {})
        p_gb, p_nyj = 1 / 2.60, 1 / 2.10
        self.assertAlmostEqual(got["proportional"], p_gb / (p_gb + p_nyj), places=9)

    def test_missing_opponent_line_fails_closed(self):
        book = {"markets": [{"key": "alternate_spreads", "outcomes": [
            {"name": "Green Bay Packers", "price": 1.80, "point": -3.5},
            {"name": "Green Bay Packers", "price": 2.60, "point": 3.5},
        ]}]}
        self.assertIsNone(_book_prob_spread(book, "Green Bay Packers", -3.5, {}))

    def test_main_spread_unchanged(self):
        book = {"markets": [{"key": "spreads", "outcomes": [
            {"name": "Green Bay Packers", "price": 1.70, "point": -3.5},
            {"name": "New York Jets", "price": 2.20, "point": 3.5},
        ]}]}
        got = _book_prob_spread(book, "New York Jets", 3.5, {})
        p_nyj, p_gb = 1 / 2.20, 1 / 1.70
        self.assertAlmostEqual(got["proportional"], p_nyj / (p_nyj + p_gb), places=9)


class StatusTruthfulnessTests(unittest.TestCase):
    def test_value_status_marks_open_positions_at_live_bid(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "s.json"
            p = PaperPortfolio()
            self.assertTrue(p.buy("KXTEST", "YES", 10, "0.50", "0.50", 10, "0.00"))
            save_portfolio(p, state)
            with patch.dict(os.environ, {"AUTOTRADER_RECORDER_ENABLED": "0"}):
                status = paper_runner._paper_status(state, Path(d) / "r.jsonl", market_client=FakeClient())
            # Fake YES best bid is 0.48 -> unrealized = 10 * (0.48 - 0.50).
            self.assertEqual(Decimal(status["portfolio"]["unrealized_pnl"]), Decimal("-0.20"))
            self.assertEqual(status["positions"][0]["current_bid"], "0.48")
            self.assertFalse(status["worker_online"])  # no scan recorded yet

    def test_value_record_counts_sold_positions(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "s.json"
            p = PaperPortfolio()
            self.assertTrue(p.buy("KXA", "YES", 10, "0.40", "0.40", 10))
            self.assertTrue(p.sell("KXA", "0.55", 10))
            self.assertTrue(p.buy("KXB", "YES", 10, "0.40", "0.40", 10))
            self.assertTrue(p.settle("KXB", "NO"))
            save_portfolio(p, state)
            with patch.dict(os.environ, {"AUTOTRADER_RECORDER_ENABLED": "0"}):
                status = paper_runner._paper_status(state, Path(d) / "r.jsonl")
            self.assertEqual(status["record"], "1-1")
            self.assertEqual(status["closed_count"], 2)
            self.assertEqual(status["settled_count"], 1)

    def test_value_worker_online_tracks_last_run_age(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(paper_runner._run_is_recent({"observed_at": now.isoformat()}, 300, now=now))
        stale = (now - timedelta(hours=2)).isoformat()
        self.assertFalse(paper_runner._run_is_recent({"observed_at": stale}, 300, now=now))

    def test_whale_status_reports_offline_when_scans_stop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
            (root / "rs.json").write_text(json.dumps({"schema": 1, "last_scan_at": stale}))
            status = whale_paper_runner._status(
                root / "p.json", root / "rs.json", root / "r.jsonl", FakeClient(),
                strategy="quick_whale_v2", scan_interval_seconds=60,
            )
            self.assertFalse(status["worker_online"])
            fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            (root / "rs.json").write_text(json.dumps({"schema": 1, "last_scan_at": fresh}))
            status = whale_paper_runner._status(
                root / "p.json", root / "rs.json", root / "r.jsonl", FakeClient(),
                strategy="quick_whale_v2", scan_interval_seconds=60,
            )
            self.assertTrue(status["worker_online"])


class LearningCannotMutateProductionTests(unittest.TestCase):
    def test_challenger_state_does_not_change_production_min_edge(self):
        now = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            learning = root / "learning.json"
            # A (hypothetical) research state recommending a stricter 7% bar.
            learning.write_text(json.dumps({
                "schema": 1, "status": "shadow_testing",
                "challenger": {"rules": {"min_net_edge": 0.07}, "status": "shadow_testing"},
            }))
            ledger = root / "ledger.csv"
            # YES ask 0.51 + 0.005 slippage + ~0.018 fee vs fair 0.59 -> ~5.7% net:
            # passes production 5%, would fail the challenger's 7%.
            write_ledger(ledger, now - timedelta(seconds=30), "0.59")
            env = {k: str(root / f"missing-{k}") for k in (
                "WHALE_PAPER_RUNS", "WHALE_PAPER_PORTFOLIO_STATE", "WHALE_QUICK_RUNS",
                "WHALE_QUICK_PORTFOLIO_STATE", "WHALE_QUICK_V2_RUNS", "WHALE_QUICK_V2_PORTFOLIO_STATE",
            )}
            with patch.dict(os.environ, env):
                result = run = paper_runner.run_once(
                    client=FakeClient(), state_path=root / "s.json", runs_path=root / "r.jsonl",
                    ledger_path=ledger, max_markets=10, max_orderbooks=10,
                    probability_max_age_seconds=300, now=now, min_net_edge="0.05",
                    learning_state_path=learning,
                )
            self.assertEqual(result["paper_fills"], 1)
            self.assertTrue(run["learning"]["production_rules_unchanged"])
            saved = json.loads(learning.read_text())
            self.assertFalse(saved["auto_promotion_enabled"])
            self.assertFalse(saved["controls"]["production_mutation_allowed"])


if __name__ == "__main__":
    unittest.main()
