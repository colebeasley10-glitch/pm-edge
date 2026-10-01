"""Trade-tape horizon semantics (whale pagination fix).

A fake Kalshi /markets/trades server is simulated with configurable ordering,
min_ts handling and page size, so every stop rule and the explicit truncation
reporting can be exercised without network access.
"""
import inspect
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from ledger_isolation import CommittedLedgerGuard, isolate_runtime_ledgers  # noqa: E402

isolate_runtime_ledgers()

import whale_edge  # noqa: E402
from autotrader import whale_paper_runner  # noqa: E402
from autotrader.engine import PaperEngine  # noqa: E402

NOW = datetime(2026, 10, 1, 18, 0, 0, tzinfo=timezone.utc)
MIN_TS = int((NOW - timedelta(minutes=2)).timestamp())


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


class FakeTape:
    """Fake trades endpoint.

    trades: list of (trade_id, datetime). Served newest-first by default.
    respect_min_ts: apply min_ts server-side (as the original code assumed).
    """

    def __init__(self, trades, *, page_size=1000, respect_min_ts=True, newest_first=True,
                 duplicate_first_of_page=False):
        self.trades = sorted(trades, key=lambda x: x[1], reverse=newest_first)
        self.page_size = page_size
        self.respect = respect_min_ts
        self.dup = duplicate_first_of_page
        self.calls = []

    def __call__(self, params):
        self.calls.append(dict(params))
        rows = self.trades
        if self.respect:
            rows = [t for t in rows if t[1].timestamp() >= params["min_ts"]]
        start = int(params.get("cursor") or 0)
        size = min(self.page_size, int(params["limit"]))
        page = rows[start:start + size]
        out = [{"trade_id": tid, "created_time": iso(ts), "ticker": "KXT"} for tid, ts in page]
        if self.dup and start and out:
            prev = rows[start - 1]
            out.insert(0, {"trade_id": prev[0], "created_time": iso(prev[1]), "ticker": "KXT"})
        nxt = start + size
        return {"trades": out, "cursor": str(nxt) if nxt < len(rows) else ""}


def spread_trades(n, *, span_seconds, end=NOW, prefix="t"):
    step = span_seconds / max(1, n)
    return [(f"{prefix}{i}", end - timedelta(seconds=i * step)) for i in range(n)]


def fetch(tape, **kw):
    diag = {}
    rows = whale_edge._public_trades(min_ts=MIN_TS, fetch_page=tape, diagnostics=diag, now=NOW,
                                     clock=lambda: NOW, **kw)
    return rows, diag


class HorizonTests(unittest.TestCase):
    def test_01_one_page_covers_horizon(self):
        rows, d = fetch(FakeTape(spread_trades(300, span_seconds=110)))
        self.assertEqual(len(rows), 300)
        self.assertEqual(d["trade_pages_fetched"], 1)
        self.assertTrue(d["trade_horizon_covered"])
        self.assertFalse(d["trade_page_cap_hit"])
        self.assertEqual(d["trade_window_coverage_fraction"], 1.0)

    def test_02_03_multiple_pages_follow_cursor(self):
        tape = FakeTape(spread_trades(2500, span_seconds=110))
        rows, d = fetch(tape)
        self.assertEqual(len(rows), 2500)
        self.assertEqual(d["trade_pages_fetched"], 3)
        self.assertEqual([c.get("cursor") for c in tape.calls], [None, "1000", "2000"])
        self.assertTrue(all(c["min_ts"] == MIN_TS for c in tape.calls))
        self.assertTrue(d["trade_horizon_covered"])
        self.assertEqual(d["trade_page_order"], "newest_first")

    def test_04_duplicate_ids_across_pages(self):
        rows, d = fetch(FakeTape(spread_trades(2500, span_seconds=110), duplicate_first_of_page=True))
        self.assertEqual(len(rows), 2500)
        self.assertEqual(len({r["trade_id"] for r in rows}), 2500)
        self.assertEqual(d["trade_rows_deduped"], 2)

    def test_05_06_out_of_window_rows_are_dropped_and_stop_paging(self):
        # Server ignoring min_ts: in-window trades then older ones.
        trades = spread_trades(1500, span_seconds=110) + spread_trades(
            3000, span_seconds=3000, end=NOW - timedelta(minutes=3), prefix="old")
        tape = FakeTape(trades, respect_min_ts=False)
        rows, d = fetch(tape)
        self.assertEqual(len(rows), 1500)
        self.assertTrue(all(whale_edge._parse_iso(r["created_time"]).timestamp() >= MIN_TS for r in rows))
        self.assertGreater(d["trade_rows_outside_window"], 0)
        self.assertEqual(d["trade_pages_fetched"], 2)  # stopped once the boundary was crossed
        self.assertTrue(d["trade_horizon_covered"])

    def test_07_08_cap_before_horizon_is_reported_as_truncation(self):
        rows, d = fetch(FakeTape(spread_trades(25000, span_seconds=118)), page_cap=10)
        self.assertEqual(len(rows), 10000)
        self.assertTrue(d["trade_page_cap_hit"])
        self.assertFalse(d["trade_horizon_covered"])
        self.assertLess(d["trade_window_coverage_fraction"], 0.5)
        self.assertGreater(d["trade_window_coverage_fraction"], 0.3)

    def test_09_exact_horizon_reached(self):
        trades = [("edge", datetime.fromtimestamp(MIN_TS, tz=timezone.utc)),
                  ("a", NOW - timedelta(seconds=5)), ("b", NOW - timedelta(seconds=10))]
        rows, d = fetch(FakeTape(trades, respect_min_ts=False, page_size=2))
        self.assertIn("edge", {r["trade_id"] for r in rows})   # exactly min_ts is in window
        self.assertTrue(d["trade_horizon_covered"])
        self.assertEqual(d["trade_rows_outside_window"], 0)

    def test_10_no_cursor_means_complete(self):
        rows, d = fetch(lambda p: {"trades": [{"trade_id": "x", "created_time": iso(NOW)}]})
        self.assertEqual(len(rows), 1)
        self.assertTrue(d["trade_horizon_covered"])

    def test_11_empty_page(self):
        rows, d = fetch(lambda p: {"trades": [], "cursor": "abc"})
        self.assertEqual(rows, [])
        self.assertTrue(d["trade_horizon_covered"])
        self.assertEqual(d["trade_pages_fetched"], 1)

    def test_12_missing_or_malformed_created_time_is_excluded(self):
        page = {"trades": [
            {"trade_id": "ok", "created_time": iso(NOW - timedelta(seconds=3))},
            {"trade_id": "missing"},
            {"trade_id": "bad", "created_time": "not-a-time"},
        ], "cursor": ""}
        rows, d = fetch(lambda p: page)
        self.assertEqual([r["trade_id"] for r in rows], ["ok"])
        self.assertEqual(d["trade_rows_missing_ts"], 2)

    def test_13_busy_period_simulation(self):
        # ~150 trades/s => 18,000 in the 2-minute window; 10 pages cover ~67s.
        rows, d = fetch(FakeTape(spread_trades(18000, span_seconds=119)), page_cap=10)
        self.assertEqual(len(rows), 10000)
        self.assertFalse(d["trade_horizon_covered"])
        self.assertTrue(0.5 < d["trade_window_coverage_fraction"] < 0.6)
        # Same load with a cap sized for it covers the window.
        rows, d = fetch(FakeTape(spread_trades(18000, span_seconds=119)), page_cap=20)
        self.assertEqual(len(rows), 18000)
        self.assertTrue(d["trade_horizon_covered"])

    def test_oldest_first_server_is_detected_and_not_falsely_covered(self):
        trades = spread_trades(3000, span_seconds=110)
        rows, d = fetch(FakeTape(trades, newest_first=False), page_cap=2)
        self.assertEqual(d["trade_page_order"], "oldest_first")
        self.assertFalse(d["trade_horizon_covered"])
        self.assertTrue(d["trade_page_cap_hit"])


class QuickV2CallerTests(unittest.TestCase):
    class Client:
        def get_market(self, ticker):
            return {"ticker": ticker, "event_ticker": "EV", "title": "Bitcoin price up in next 15 minutes?",
                    "status": "open", "result": "", "close_time": "2026-10-01T18:15:00Z"}

        def get_orderbook(self, ticker, depth=20):
            return {"orderbook_fp": {"yes_dollars": [["0.49", "100"]], "no_dollars": [["0.50", "100"]]}}

    def test_14_quick_v2_two_minute_lookback_and_labels(self):
        trades = spread_trades(12000, span_seconds=119) + spread_trades(
            500, span_seconds=600, end=NOW - timedelta(minutes=5), prefix="old")
        tape = FakeTape(trades, respect_min_ts=False)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prev = iso(NOW - timedelta(seconds=60))
            (root / "s.json").write_text('{"schema":1,"last_scan_at":"%s"}' % prev)
            with patch.object(whale_edge, "_fetch_trade_page", tape), \
                    patch.object(whale_edge, "_now", lambda: NOW):
                run = whale_paper_runner.run_once(
                    client=self.Client(), state_path=root / "p.json", runner_state_path=root / "s.json",
                    runs_path=root / "r.jsonl", lookback_minutes=2, mode="quick_v2", now=NOW,
                    max_flow_age_seconds=55, min_seconds_to_close=180,
                )
        tape_diag = run["trade_tape"]
        self.assertEqual(tape.calls[0]["min_ts"], MIN_TS)
        self.assertEqual(tape_diag["trade_requested_lookback_seconds"], 120.0)
        self.assertEqual(run["public_trades"], 10000)
        self.assertFalse(tape_diag["trade_horizon_covered"])
        self.assertTrue(tape_diag["trade_page_cap_hit"])
        # 10k of 12k in ~119s reach back ~99s, past the previous scan 60s ago.
        self.assertTrue(tape_diag["trade_covers_since_last_scan"])
        self.assertEqual(run["lane_version"], "quick_whale_v2_pagination_v2")
        self.assertEqual(run["strategy"], "quick_whale_v2")


class VersionBoundaryTests(unittest.TestCase):
    def test_period_classification(self):
        f = whale_paper_runner.quick_v2_version_for
        self.assertEqual(f("2026-10-01T17:37:55.406Z"), "quick_whale_v2_pre_pagination")
        self.assertEqual(f("2026-10-01T17:37:55.407Z"), "quick_whale_v2_pagination_v1")
        self.assertEqual(f("2026-10-01T19:00:00Z", "quick_whale_v2_pagination_v2"), "quick_whale_v2_pagination_v2")
        self.assertEqual(f(None), "quick_whale_v2_unknown_period")

    def test_report_tool_splits_without_pooling(self):
        import json
        from autotrader.portfolio import PaperPortfolio
        from autotrader.storage import save_portfolio
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
        import qw_v2_version_report as report
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = PaperPortfolio()
            p.buy("A", "YES", 5, "0.50", "0.50", 5, "0.01", thesis={"source": "quick_whale_v2", "source_trade_id": "pre"})
            p.settle("A", "YES")
            p.buy("B", "YES", 5, "0.50", "0.50", 5, "0.01", thesis={"source": "quick_whale_v2", "source_trade_id": "mid"})
            p.settle("B", "NO")
            p.buy("C", "YES", 5, "0.50", "0.50", 5, "0.01", thesis={
                "source": "quick_whale_v2", "source_trade_id": "new", "lane_version": "quick_whale_v2_pagination_v2"})
            p.settle("C", "YES")
            save_portfolio(p, root / "p.json")
            runs = [
                {"observed_at": "2026-10-01T16:00:00Z", "decisions": [{"decision": "paper_filled", "source_trade_id": "pre"}]},
                {"observed_at": "2026-10-01T18:00:00Z", "decisions": [{"decision": "paper_filled", "source_trade_id": "mid"}]},
            ]
            (root / "r.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))
            before = (root / "p.json").read_bytes()
            out = report.build_report(root / "p.json", root / "r.jsonl")
            self.assertEqual((root / "p.json").read_bytes(), before)  # read-only
        periods = out["periods"]
        self.assertEqual(set(periods), {"quick_whale_v2_pre_pagination", "quick_whale_v2_pagination_v1",
                                        "quick_whale_v2_pagination_v2"})
        self.assertEqual(periods["quick_whale_v2_pre_pagination"]["wins"], 1)
        self.assertEqual(periods["quick_whale_v2_pagination_v1"]["losses"], 1)
        self.assertTrue(all(v["interpretation"] == "descriptive_only" for v in periods.values()))


class NoThresholdChangeTests(unittest.TestCase):
    def test_quick_v2_gate_defaults_unchanged(self):
        sig = inspect.signature(whale_paper_runner._quick_v2_gate).parameters
        expected = {"min_flow_age_seconds": 8, "max_flow_age_seconds": 55, "min_seconds_to_close": 180,
                    "max_spread": "0.04", "max_adverse_mid_cents": 1, "max_follow_through_cents": 2,
                    "min_flow_notional_usd": 500, "min_flow_trades": 2}
        for name, value in expected.items():
            self.assertEqual(sig[name].default, value, name)

    def test_whale_and_value_thresholds_unchanged(self):
        self.assertEqual(whale_edge.MIN_REFERENCE_CENTS, 15)
        self.assertEqual(whale_edge.MAX_REFERENCE_CENTS, 85)
        self.assertEqual(whale_edge.MAX_REST_GAP_CENTS, 4)
        self.assertEqual(whale_edge.TRADE_PAGE_CAP, 10)
        self.assertEqual(PaperEngine().min_net_edge, Decimal("0.05"))
        runner_sig = inspect.signature(whale_paper_runner.run_once).parameters
        self.assertEqual(runner_sig["quick_v2_max_settled_trades"].default, 25)
        self.assertEqual(runner_sig["quick_v2_max_realized_loss"].default, "50")


if __name__ == "__main__":
    with CommittedLedgerGuard():
        unittest.main()
