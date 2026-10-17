"""Phase 2 (shadow ROI research) regression tests.

Covers: Value Edge fresh-quote recheck (A), CLV checkpoint capture (B), trade
tape pagination (C), fair-value provenance logging (D), run-log rotation (E),
test-ledger isolation (F), conservative maker-fill shadow (G), matcher shadow
(H), challengers C1/C2/C3, Quick Whale v3, research segmentation, and the
paper-only / no-look-ahead guarantees.
"""
import csv
import gzip
import json
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from ledger_isolation import CommittedLedgerGuard, isolate_runtime_ledgers  # noqa: E402

isolate_runtime_ledgers()

import candidate_tracker  # noqa: E402
import whale_edge  # noqa: E402
from autotrader import c2_shadow, challengers, clv_capture, learning, paper_runner, whale_paper_runner  # noqa: E402
from autotrader.portfolio import PaperPortfolio  # noqa: E402
from autotrader.probability import load_pm_edge_ledger  # noqa: E402
from autotrader.recorder_store import RecorderStore  # noqa: E402
from autotrader.storage import append_jsonl, jsonl_archives, load_portfolio, rotate_jsonl, save_portfolio, tail_jsonl  # noqa: E402
from pmedge.market_parse import parse_market  # noqa: E402
from pmedge.matcher import match_event, shadow_team_match, shadow_validate_match  # noqa: E402
from test_autotrader_probability_runner import FakeClient, write_ledger  # noqa: E402

NOW = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent


def _run_value(root, client, probability="0.70", **kwargs):
    ledger = root / "ledger.csv"
    write_ledger(ledger, NOW - timedelta(seconds=30), probability)
    return paper_runner.run_once(
        client=client, state_path=root / "s.json", runs_path=root / "r.jsonl",
        ledger_path=ledger, max_markets=10, max_orderbooks=10,
        probability_max_age_seconds=300, now=NOW, **kwargs,
    )


def _filled(run):
    return [d for d in run["decisions"] if d.get("decision") == "paper_filled"]


# --------------------------------------------------------------------------- A
class FreshQuoteRecheckTests(unittest.TestCase):
    def test_unchanged_fresh_quote_fills_and_logs_recheck(self):
        with tempfile.TemporaryDirectory() as d:
            run = _run_value(Path(d), FakeClient())
        fill = _filled(run)[0]
        self.assertEqual(fill["recheck_result"], "passed")
        self.assertEqual(fill["initial_quote_price"], "0.51")
        self.assertEqual(fill["recheck_quote_price"], "0.51")
        self.assertEqual(fill["recheck_quote_age_s"], 0.0)
        self.assertIsNotNone(fill["recheck_net_edge"])
        self.assertIn("recheck_latency_ms", fill)

    def test_moved_quote_blocks_fill_under_same_rules(self):
        class MovingClient(FakeClient):
            calls = 0

            def get_orderbook(self, ticker, depth=20):
                MovingClient.calls += 1
                if MovingClient.calls == 1:
                    return super().get_orderbook(ticker, depth)
                # Market repriced: YES 0.62 bid / 0.65 ask (tight spread); edge vs 0.70 < 5%.
                return {"orderbook_fp": {"yes_dollars": [["0.62", "25"]], "no_dollars": [["0.35", "25"]]}}

        with tempfile.TemporaryDirectory() as d:
            run = _run_value(Path(d), MovingClient())
            self.assertEqual(run["paper_fills"], 0)
            self.assertEqual(load_portfolio(Path(d) / "s.json").positions, {})
        rejected = [x for x in run["decisions"] if x.get("decision") == "recheck_rejected"]
        self.assertTrue(rejected)
        self.assertEqual(rejected[0]["recheck_result"], "rejected:insufficient_net_edge")
        self.assertEqual(rejected[0]["recheck_quote_price"], "0.65")

    def test_vanished_book_blocks_fill(self):
        class EmptyAfterFirst(FakeClient):
            calls = 0

            def get_orderbook(self, ticker, depth=20):
                EmptyAfterFirst.calls += 1
                if EmptyAfterFirst.calls == 1:
                    return super().get_orderbook(ticker, depth)
                return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}

        with tempfile.TemporaryDirectory() as d:
            run = _run_value(Path(d), EmptyAfterFirst())
        self.assertEqual(run["paper_fills"], 0)
        self.assertTrue(any(
            x.get("recheck_result") == "rejected_no_executable_fresh_quote" for x in run["decisions"]
        ))

    def test_fill_uses_fresh_price(self):
        class ImprovingClient(FakeClient):
            calls = 0

            def get_orderbook(self, ticker, depth=20):
                ImprovingClient.calls += 1
                if ImprovingClient.calls == 1:
                    return super().get_orderbook(ticker, depth)
                return {"orderbook_fp": {"yes_dollars": [["0.48", "25"]], "no_dollars": [["0.50", "25"]]}}

        with tempfile.TemporaryDirectory() as d:
            _run_value(Path(d), ImprovingClient())
            pos = load_portfolio(Path(d) / "s.json").positions["KXTEST"]
        self.assertEqual(pos.entry, Decimal("0.505"))  # fresh ask 0.50 + 0.005 slippage

    def test_recheck_can_only_remove_fills(self):
        # Low-edge candidate rejected initially is never re-quoted into a fill.
        with tempfile.TemporaryDirectory() as d:
            run = _run_value(Path(d), FakeClient(), probability="0.55")
        self.assertEqual(run["paper_fills"], 0)
        self.assertFalse(any("recheck_result" in x for x in run["decisions"]))

    def test_threshold_unchanged(self):
        self.assertEqual(paper_runner.PaperEngine().min_net_edge, Decimal("0.05"))


# --------------------------------------------------------------------------- C
class TradeTapePaginationTests(unittest.TestCase):
    MIN_TS = int(datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc).timestamp())

    @staticmethod
    def _trade(tid, minute):
        return {"trade_id": tid, "created_time": f"2026-09-29T13:{minute:02d}:00Z"}

    def _pages(self, pages):
        calls = []

        def fetch(params):
            calls.append(dict(params))
            return pages[len(calls) - 1]
        return fetch, calls

    def test_single_page(self):
        fetch, calls = self._pages([{"trades": [self._trade("a", 5)], "cursor": None}])
        diag = {}
        rows = whale_edge._public_trades(min_ts=self.MIN_TS, fetch_page=fetch, diagnostics=diag)
        self.assertEqual([r["trade_id"] for r in rows], ["a"])
        self.assertEqual(diag["trade_pages_fetched"], 1)
        self.assertFalse(diag["trade_page_cap_hit"])

    def test_multiple_pages_follow_cursor_and_dedupe(self):
        fetch, calls = self._pages([
            {"trades": [self._trade("c", 9), self._trade("b", 8)], "cursor": "p2"},
            {"trades": [self._trade("b", 8), self._trade("a", 7)], "cursor": None},
        ])
        diag = {}
        rows = whale_edge._public_trades(min_ts=self.MIN_TS, fetch_page=fetch, diagnostics=diag)
        self.assertEqual([r["trade_id"] for r in rows], ["c", "b", "a"])
        self.assertEqual(calls[1]["cursor"], "p2")
        self.assertEqual(diag["trade_rows_fetched"], 4)
        self.assertEqual(diag["trade_rows_deduped"], 1)
        self.assertEqual(diag["trade_oldest_ts"], "2026-09-29T13:07:00Z")

    def test_stops_when_horizon_covered(self):
        fetch, calls = self._pages([
            {"trades": [self._trade("b", 5), self._trade("old", 0)], "cursor": "more"},
            {"trades": [self._trade("never", 0)], "cursor": None},
        ])
        whale_edge._public_trades(min_ts=self.MIN_TS, fetch_page=fetch)
        self.assertEqual(len(calls), 1)

    def test_empty_page_stops(self):
        fetch, calls = self._pages([{"trades": [], "cursor": "x"}])
        self.assertEqual(whale_edge._public_trades(min_ts=self.MIN_TS, fetch_page=fetch), [])
        self.assertEqual(len(calls), 1)

    def test_page_cap(self):
        def fetch(params):
            n = int(params.get("cursor") or 0)
            return {"trades": [self._trade(f"t{n}", 30)], "cursor": str(n + 1)}
        diag = {}
        rows = whale_edge._public_trades(min_ts=self.MIN_TS, fetch_page=fetch, page_cap=3, diagnostics=diag)
        self.assertEqual(len(rows), 3)
        self.assertTrue(diag["trade_page_cap_hit"])


# --------------------------------------------------------------------------- D
class ProvenanceLoggingTests(unittest.TestCase):
    def test_new_fields_logged_and_old_header_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.csv"
            old_fields = [f for f in candidate_tracker.FIELDS
                          if f not in {"sigma", "source_age_s", "lane_version"} and not f.startswith("matcher_shadow")]
            with path.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=old_fields)
                w.writeheader()
                w.writerow({"decision_id": "old-1", "ticker": "KXOLD", "stage": "score"})
            market = {"ticker": "KXNEW", "title": "t", "_matcher_shadow": {
                "matcher_shadow_pass": True, "matcher_shadow_reason": "ok",
                "matcher_shadow_team_score": 0.95, "matcher_shadow_date_match": True}}
            fair = {"fair_yes_prob": 0.6, "sigma": 0.021, "source_age_s": 42.5, "fetched_at": "2026-09-29T13:59:00Z"}
            q = SimpleNamespace(side="YES", recommended_limit=0.5, qualifies=False, net_edge=0.01)
            self.assertTrue(candidate_tracker.record_candidate(
                market, fair, q, lane="primary", stage="score", decision="SCORED", path=path,
            )["recorded"])
            rows = list(csv.DictReader(path.open()))
        self.assertEqual(rows[0]["decision_id"], "old-1")
        self.assertEqual(rows[0]["sigma"], "")
        self.assertEqual(rows[1]["sigma"], "0.021")
        self.assertEqual(rows[1]["source_age_s"], "42.5")
        self.assertEqual(rows[1]["lane_version"], candidate_tracker.PROVENANCE_VERSION)
        self.assertEqual(rows[1]["matcher_shadow_pass"], "True")

    def test_logged_sigma_never_reaches_production_thesis(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ledger = root / "ledger.csv"
            write_ledger(ledger, NOW - timedelta(seconds=30), "0.70")
            text = ledger.read_text().splitlines()
            text[0] += ",sigma,source_age_s"
            text[1] += ",0.20,30"
            ledger.write_text("\n".join(text) + "\n")
            snap = load_pm_edge_ledger(ledger, now=NOW, max_age_seconds=300)
            ctx = snap.contexts[("KXTEST", "YES")]
            self.assertNotIn("sigma", ctx)
            self.assertEqual(ctx["provenance_sigma"], "0.20")
            paper_runner.run_once(
                client=FakeClient(), state_path=root / "s.json", runs_path=root / "r.jsonl",
                ledger_path=ledger, max_markets=10, max_orderbooks=10,
                probability_max_age_seconds=300, now=NOW,
            )
            thesis = load_portfolio(root / "s.json").positions["KXTEST"].thesis
        self.assertEqual(thesis["entry_sigma"], "0.03")


# --------------------------------------------------------------------------- E
class RunLogRotationTests(unittest.TestCase):
    def _touch(self, path, when):
        ts = when.timestamp()
        os.utime(path, (ts, ts))

    def test_rotation_gzip_retention_and_tail(self):
        today = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "quick-whale-v2-paper-runs.jsonl"
            # Day 1 and day 2 written, rotated each following day.
            for offset, rows in ((3, [1, 2]), (2, [3, 4]), (1, [5])):
                with log.open("a") as fh:
                    for i in rows:
                        fh.write(json.dumps({"i": i}) + "\n")
                self._touch(log, today - timedelta(days=offset))
                rotate_jsonl(log, now=today - timedelta(days=offset - 1), retain_days=30)
            names = sorted(p.name for p in Path(d).iterdir())
            self.assertIn("quick-whale-v2-paper-runs.2026-10-07.jsonl.gz", names)
            self.assertIn("quick-whale-v2-paper-runs.2026-10-08.jsonl.gz", names)
            self.assertIn("quick-whale-v2-paper-runs.2026-10-09.jsonl", names)  # newest stays plain
            self.assertFalse(log.exists())
            append_jsonl(log, {"i": 6})
            self.assertEqual([r["i"] for r in tail_jsonl(log, 10)], [1, 2, 3, 4, 5, 6])
            self.assertEqual([r["i"] for r in tail_jsonl(log, 3)], [4, 5, 6])
            with gzip.open(Path(d) / "quick-whale-v2-paper-runs.2026-10-07.jsonl.gz", "rt") as fh:
                self.assertEqual([json.loads(x)["i"] for x in fh], [1, 2])

    def test_today_log_is_never_rotated(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "autotrader-paper-runs.jsonl"
            log.write_text('{"i":1}\n')
            result = rotate_jsonl(log, retain_days=30)
            self.assertIsNone(result["rotated"])
            self.assertTrue(log.exists())

    def test_retention_deletes_old_archives_only_for_that_lane(self):
        today = datetime(2026, 10, 10, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = root / "whale-paper-runs.2026-08-01.jsonl.gz"
            recent = root / "whale-paper-runs.2026-10-01.jsonl.gz"
            other_lane = root / "quick-whale-paper-runs.2026-08-01.jsonl.gz"
            for p in (old, recent, other_lane):
                with gzip.open(p, "wt") as fh:
                    fh.write('{"i":1}\n')
            rotate_jsonl(root / "whale-paper-runs.jsonl", now=today, retain_days=30)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(other_lane.exists())
            self.assertEqual(len(jsonl_archives(root / "quick-whale-paper-runs.jsonl")), 1)


# --------------------------------------------------------------------------- F
class TestHygieneTests(unittest.TestCase):
    def test_ledger_env_paths_point_at_temp(self):
        for env in ("PM_EDGE_FILL_LEDGER_PATH", "PM_EDGE_SIGNAL_LEDGER_PATH",
                    "PM_EDGE_CANDIDATE_LEDGER_PATH", "PM_EDGE_AUTO_LEDGER_PATH"):
            self.assertNotEqual(Path(os.environ[env]).resolve().parent, ROOT)

    def test_guard_detects_modification(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pm_edge_fill_ledger.csv").write_text("a\n")
            with self.assertRaises(AssertionError):
                with CommittedLedgerGuard(root):
                    (root / "pm_edge_fill_ledger.csv").write_text("a\nb\n")


# --------------------------------------------------------------------------- G
class MakerShadowTests(unittest.TestCase):
    def _pending(self, created):
        return whale_paper_runner.PendingMaker(
            ticker="KXT", side="YES", contracts=20, target="0.45",
            created_at=created.isoformat(), expires_at=(created + timedelta(minutes=30)).isoformat(),
            source_trade_id="s", flow_contracts=100, flow_notional_usd=50.0,
            reference_price_cents=46, title="t",
        )

    def test_counts_only_post_creation_volume_at_or_better(self):
        created = NOW
        p = self._pending(created)
        trades = [
            {"trade_id": "before", "ticker": "KXT", "yes_price_dollars": "0.40", "count_fp": "50",
             "created_time": (created - timedelta(seconds=5)).isoformat()},
            {"trade_id": "above", "ticker": "KXT", "yes_price_dollars": "0.47", "count_fp": "50",
             "created_time": (created + timedelta(seconds=5)).isoformat()},
            {"trade_id": "at", "ticker": "KXT", "yes_price_dollars": "0.45", "count_fp": "3",
             "created_time": (created + timedelta(seconds=6)).isoformat()},
            {"trade_id": "through", "ticker": "KXT", "yes_price_dollars": "0.44", "count_fp": "4",
             "created_time": (created + timedelta(seconds=7)).isoformat()},
            {"trade_id": "other", "ticker": "KXZ", "yes_price_dollars": "0.10", "count_fp": "99",
             "created_time": (created + timedelta(seconds=7)).isoformat()},
        ]
        whale_paper_runner._update_maker_shadow(p, trades)
        whale_paper_runner._update_maker_shadow(p, trades)  # same tape again: no double count
        fields = whale_paper_runner._maker_shadow_fields(p)
        self.assertEqual(fields["maker_shadow_trade_through_volume"], 7.0)
        self.assertEqual(fields["maker_shadow_strictly_better_volume"], 4.0)
        self.assertEqual(fields["maker_shadow_fill_qty"], 7)  # <= traded volume, not ask size
        self.assertTrue(fields["maker_shadow_would_fill"])
        self.assertEqual(fields["maker_shadow_model_version"], "maker_tradethrough_v1")

    def test_shadow_does_not_change_production_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._pending(NOW - timedelta(minutes=1))
            state = {"pending": [whale_paper_runner.asdict(p)], "market_meta": {}}
            portfolio = PaperPortfolio()
            trades = [{"trade_id": "t", "ticker": "KXT", "yes_price_dollars": "0.40", "count_fp": "9",
                       "taker_side": "no", "created_time": NOW.isoformat()}]

            class NoAsk:
                def get_orderbook(self, ticker, depth=20):
                    return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}

            decisions = whale_paper_runner._process_pending(portfolio, NoAsk(), state, trades, NOW, "0.005")
            self.assertEqual(portfolio.positions, {})
            self.assertEqual(portfolio.cash, Decimal("500.00"))
            self.assertEqual(len(state["pending"]), 1)
            self.assertTrue(decisions[0]["maker_shadow_would_fill"])
            self.assertEqual(decisions[0]["decision"], "pending_resting")


# --------------------------------------------------------------------------- H
class MatcherShadowTests(unittest.TestCase):
    def test_examples(self):
        self.assertLess(shadow_team_match("Georgia", "Georgia Tech", {})[0], 0.9)
        self.assertGreaterEqual(shadow_team_match("Georgia", "Georgia Bulldogs", {})[0], 0.9)
        self.assertGreaterEqual(shadow_team_match("NY Jets", "New York Jets", {})[0], 0.9)
        self.assertGreaterEqual(shadow_team_match("PIT Steelers", "Pittsburgh Steelers", {})[0], 0.9)
        self.assertEqual(shadow_team_match("Boston Red Sox", "Boston Red Sox", {})[0], 1.0)
        self.assertLess(shadow_team_match("Miami", "Miami (OH)", {})[0], 0.9)

    def test_date_match(self):
        spec = SimpleNamespace(participant_a="Georgia Bulldogs", participant_b="Alabama Crimson Tide")
        event = {"home_team": "Alabama Crimson Tide", "away_team": "Georgia Bulldogs",
                 "commence_time": "2026-10-03T23:30:00Z"}  # 19:30 New York
        same = shadow_validate_match(spec, event, {}, "2026-10-03T19:30:00-04:00")
        diff = shadow_validate_match(spec, event, {}, "2026-10-10T19:30:00-04:00")
        unknown = shadow_validate_match(spec, event, {}, None)
        self.assertTrue(same["matcher_shadow_pass"])
        self.assertTrue(same["matcher_shadow_date_match"])
        self.assertFalse(diff["matcher_shadow_pass"])
        self.assertEqual(diff["matcher_shadow_reason"], "date_mismatch")
        self.assertEqual(unknown["matcher_shadow_reason"], "ok_date_unverified")

    def test_strict_matcher_rejects_georgia_vs_georgia_tech(self):
        spec = parse_market({"ticker": "KX", "title": "Georgia at Clemson: Georgia wins"})
        events = [{"id": "e1", "home_team": "Clemson Tigers", "away_team": "Georgia Tech Yellow Jackets",
                   "commence_time": "2026-10-03T23:30:00Z"}]
        em = match_event(spec, events, {}, None)
        self.assertIsNone(em.event)  # fail closed: different team
        self.assertFalse(em.shadow["matcher_shadow_pass"])
        self.assertIn("qualifier", em.shadow["matcher_shadow_reason"])


# ------------------------------------------------------------------- challengers
class ChallengerTests(unittest.TestCase):
    def test_c1_required_edge_schedule(self):
        self.assertEqual(challengers.c1_required_edge(300), Decimal("0.05"))
        self.assertEqual(challengers.c1_required_edge(600), Decimal("0.055"))
        self.assertEqual(challengers.c1_required_edge(900), Decimal("0.060"))
        self.assertEqual(challengers.c1_required_edge(60), Decimal("0.05"))

    def test_c1_rejects(self):
        big = challengers.c1_fields(net_edge="0.16", fair_age_s=60, source_age_at_fair_s=10)
        old_src = challengers.c1_fields(net_edge="0.08", fair_age_s=120, source_age_at_fair_s=800)
        stale_fair = challengers.c1_fields(net_edge="0.055", fair_age_s=840, source_age_at_fair_s=10)
        ok = challengers.c1_fields(net_edge="0.07", fair_age_s=120, source_age_at_fair_s=None)
        self.assertEqual(big["challenger_c1_reason"], "edge_too_large_likely_artifact")
        self.assertEqual(old_src["challenger_c1_reason"], "book_source_too_old")
        self.assertEqual(old_src["challenger_c1_source_age_s"], 920.0)
        self.assertEqual(stale_fair["challenger_c1_reason"], "below_age_scaled_edge")
        self.assertEqual(ok["challenger_c1_reason"], "pass_source_age_unknown")

    def test_c1_never_changes_production_fill(self):
        # Fake setup has a ~17% edge: production fills, C1 flags it as an artifact.
        with tempfile.TemporaryDirectory() as d:
            run = _run_value(Path(d), FakeClient())
        fill = _filled(run)[0]
        self.assertFalse(fill["challenger_c1_pass"])
        self.assertEqual(fill["challenger_c1_reason"], "edge_too_large_likely_artifact")
        for row in run["decisions"]:
            self.assertIn("challenger_c1_pass", row)
            self.assertIn("challenger_c3_is_spread", row)

    def test_c2_fields_and_counterfactual(self):
        f = challengers.c2_fields(production_action="EXIT_THESIS", production_reason="fair_moved_against_thesis",
                                  executable_bid="0.40", exit_fee_per_contract="0.02", current_side_fair="0.50")
        self.assertFalse(f["challenger_c2_would_exit"])
        self.assertEqual(f["challenger_c2_bid_net_fee"], "0.38")
        state = {"pending": [], "resolved": []}
        c2_shadow.record_exit(state, ticker="KX", side="YES", contracts=10, entry_price="0.45",
                              entry_fee_per_contract="0.01", exit_price="0.40", exit_fee_per_contract="0.02",
                              production_exit_pnl="-0.80", production_action="EXIT_THESIS",
                              production_reason="x", c2=f, exited_at=NOW)
        from pmedge.kalshi import normalize_market
        unresolved = c2_shadow.resolve(state, lambda t: {"ticker": t, "status": "open"}, normalize=normalize_market)
        self.assertEqual(unresolved, 0)
        done = c2_shadow.resolve(
            state, lambda t: {"ticker": t, "status": "finalized", "result": "yes", "settlement_value_dollars": "1.00"},
            normalize=normalize_market,
        )
        self.assertEqual(done, 1)
        row = state["resolved"][0]
        self.assertEqual(Decimal(row["counterfactual_hold_to_settlement_pnl"]), Decimal("5.40"))
        self.assertEqual(Decimal(row["challenger_c2_pnl_difference"]), Decimal("6.20"))

    def test_c2_logged_on_production_exit_without_changing_it(self):
        portfolio = PaperPortfolio()
        thesis = {
            "thesis_version": "1", "lane": "value_edge", "lane_version": "value_edge_v1", "ticker": "KXTEST",
            "side": "YES", "fair_snapshot_id": "s", "fair_source": "x", "entry_fair": "0.70",
            "entry_sigma": "0.03", "entry_edge_net": "0.10", "entry_price": "0.55",
            "entry_fee_per_contract": "0.01", "entry_slippage_per_contract": "0.005",
            "expected_hold_hours": "4", "expected_ev_per_contract": "0.1", "expected_ev_per_capital_hour": "0.01",
            "event_start_at": "2026-09-29T20:00:00Z", "created_at": "2026-09-29T13:00:00Z",
            "invalidation_fair_move_sigma": "2.0",
            "extra_research_key": "ignored-by-from_dict",
        }
        self.assertTrue(portfolio.buy("KXTEST", "YES", 10, "0.55", "0.55", 10, "0.01", thesis=thesis))
        fair = SimpleNamespace(probabilities={("KXTEST", "YES"): Decimal("0.50")}, contexts={},
                               ages={("KXTEST", "YES"): 30})
        state = {"pending": [], "resolved": []}
        rows = paper_runner._manage_open_positions(portfolio, FakeClient(), fair, now=NOW, c2_state=state)
        row = rows[0]
        self.assertEqual(row["action"], "EXIT_THESIS")      # production behavior as before
        self.assertTrue(row["executed"])
        self.assertNotIn("KXTEST", portfolio.positions)
        self.assertFalse(row["challenger_c2_would_exit"])   # C2 would have held
        self.assertEqual(len(state["pending"]), 1)

    def test_c3_versioning(self):
        tagged = challengers.c3_fields(market_type="spread", side="NO",
                                       fair_provenance_version="fair_provenance_v1_spreadfix")
        legacy = challengers.c3_fields(market_type="spread", side="NO", fair_provenance_version="")
        h2h = challengers.c3_fields(market_type="h2h", side="NO", fair_provenance_version="fair_provenance_v1_spreadfix")
        self.assertEqual(tagged["challenger_c3_lane_version"], "value_edge_v1_spreadfix")
        self.assertTrue(tagged["challenger_c3_is_no_side"])
        self.assertEqual(legacy["challenger_c3_lane_version"], "value_edge_v1")
        self.assertFalse(h2h["challenger_c3_is_spread"])


# --------------------------------------------------------------------------- B
class ClvCaptureTests(unittest.TestCase):
    def _portfolio(self, root, thesis):
        p = PaperPortfolio()
        self.assertTrue(p.buy("KXQ", "YES", 5, "0.50", "0.50", 5, "0.01", thesis=thesis))
        path = root / "lane.json"
        save_portfolio(p, path)
        return path

    def test_checkpoints_captured_once_and_positions_untouched(self):
        close = NOW + timedelta(minutes=10)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = self._portfolio(root, {"source": "quick_whale_v2", "close_time": close.isoformat(),
                                          "close_time_provenance": "kalshi_market_close_time",
                                          "event_start_at": None})
            before = path.read_bytes()
            store = RecorderStore(str(root / "rec.sqlite3"))
            lanes = {"quick_whale_v2": str(path)}
            first = clv_capture.capture_clv_checkpoints(store, FakeClient(), now=NOW, lane_portfolios=lanes)
            again = clv_capture.capture_clv_checkpoints(store, FakeClient(), now=NOW + timedelta(seconds=30),
                                                         lane_portfolios=lanes)
            start = clv_capture.capture_clv_checkpoints(store, FakeClient(), now=close - timedelta(seconds=60),
                                                         lane_portfolios=lanes)
            self.assertEqual(first["captured"], 1)
            self.assertEqual(again["already_captured"], 1)
            self.assertEqual(start["captured"], 1)
            self.assertEqual(path.read_bytes(), before)
            key = clv_capture.position_key("quick_whale_v2", load_portfolio(path).positions["KXQ"])
            rows = store.clv_checkpoints_for(key)
            self.assertEqual([r["checkpoint"] for r in rows], ["T_MINUS_10", "START_OR_CLOSE"])
            self.assertEqual(rows[0]["target_provenance"], "kalshi_market_close_time")

    def test_legacy_position_uses_labelled_close_lookup(self):
        close = NOW + timedelta(seconds=30)

        class CloseClient(FakeClient):
            def get_market(self, ticker):
                row = super().get_market(ticker)
                row["close_time"] = close.isoformat()
                return row

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = self._portfolio(root, None)
            store = RecorderStore(str(root / "rec.sqlite3"))
            out = clv_capture.capture_clv_checkpoints(store, CloseClient(), now=NOW,
                                                      lane_portfolios={"whale_flow": str(path)})
            self.assertEqual(out["captured"], 1)
            key = clv_capture.position_key("whale_flow", load_portfolio(path).positions["KXQ"])
            self.assertEqual(store.clv_checkpoints_for(key)[0]["target_provenance"],
                             "kalshi_market_close_time_lookup")

    def test_learning_clv_uses_only_books_at_or_before_target(self):
        target = datetime.now(timezone.utc) - timedelta(minutes=1)
        with tempfile.TemporaryDirectory() as d:
            store = RecorderStore(str(Path(d) / "rec.sqlite3"))
            store.record_top({"ticker": "KXQ", "yes_bid": 0.58, "yes_ask": 0.60},
                             received_at=target - timedelta(seconds=60))
            store.record_top({"ticker": "KXQ", "yes_bid": 0.95, "yes_ask": 0.97},
                             received_at=target + timedelta(seconds=30))  # after target: must be ignored
            sample = {"ticker": "KXQ", "side": "YES", "entry_price": "0.50",
                      "thesis": {"close_time": target.isoformat(), "close_time_provenance": "kalshi_market_close_time"}}
            clv = learning._closing_clv(sample, store)
        self.assertEqual(clv, Decimal("0.59") - Decimal("0.50"))
        self.assertEqual(sample["clv_target_provenance"], "kalshi_market_close_time")

    def test_whale_thesis_records_close_time_without_fabricating_start(self):
        timing = whale_paper_runner._position_timing(
            "KXBTC15M-X", "YES", {"close_time": "2026-09-30T16:15:00Z"}, quick=True,
        )
        self.assertEqual(timing["close_time"], "2026-09-30T16:15:00Z")
        self.assertEqual(timing["close_time_provenance"], "kalshi_market_close_time")
        self.assertIsNone(timing["event_start_at"])
        self.assertIsNone(timing["event_start_provenance"])


# ------------------------------------------------------------------ QW v3
class QuickClient:
    def __init__(self, books=None):
        self.books = list(books or [])

    def get_market(self, ticker):
        return {"ticker": ticker, "event_ticker": f"EV-{ticker}", "title": "Bitcoin price up in next 15 minutes?",
                "status": "open", "result": "", "close_time": "2026-09-30T16:15:00Z"}

    def get_orderbook(self, ticker, depth=20):
        if self.books:
            return self.books.pop(0)
        return {"orderbook_fp": {"yes_dollars": [["0.49", "100"]], "no_dollars": [["0.50", "100"]]}}


V3_NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)


def _v3_flow(age_seconds):
    return {"ticker": "KXBTC15M-26SEP301615-V3", "side": "YES", "reference_price": 0.50,
            "reference_price_cents": 50, "flow_contracts": 1100, "flow_notional_usd": 550.0,
            "flow_trade_count": 2, "largest_trade_contracts": 700, "block_trade": False,
            "trade_id": "trade-v3", "latest_time": V3_NOW - timedelta(seconds=age_seconds)}


def _run_quick(root, mode, client, flow, sleeps=None, **kwargs):
    with patch("autotrader.whale_paper_runner.whale_edge._public_trades", return_value=[{"trade_id": "trade-v3"}]), \
            patch("autotrader.whale_paper_runner.whale_edge._aggregate", return_value=[flow]):
        return whale_paper_runner.run_once(
            client=client, state_path=root / f"{mode}-p.json", runner_state_path=root / f"{mode}-s.json",
            runs_path=root / f"{mode}-r.jsonl", lookback_minutes=2, mode=mode, now=V3_NOW,
            max_flow_age_seconds=55, min_seconds_to_close=180,
            sleep_fn=(sleeps.append if sleeps is not None else (lambda s: None)), **kwargs,
        )


class QuickWhaleV3Tests(unittest.TestCase):
    def test_v2_discards_young_flow_but_v3_requeues_and_enters(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            v2 = _run_quick(root, "quick_v2", QuickClient(), _v3_flow(3))
            self.assertEqual(v2["paper_fills"], 0)
            self.assertIn("quick_v2_wait_for_reaction", {x.get("reason") for x in v2["decisions"]})
            sleeps = []
            v3 = _run_quick(root, "quick_v3", QuickClient(), _v3_flow(3), sleeps=sleeps)
            self.assertEqual(v3["strategy"], "quick_whale_v3")
            self.assertEqual(sleeps, [5.0])
            self.assertEqual(v3["paper_fills"], 1)
            requeued = [x for x in v3["decisions"] if x.get("v3_requeue_reevaluation")]
            self.assertEqual(requeued[0]["decision"], "paper_filled")
            v3p = load_portfolio(root / "quick_v3-p.json")
            self.assertEqual(v3p.positions["KXBTC15M-26SEP301615-V3"].thesis["source"], "quick_whale_v3")
            self.assertEqual(load_portfolio(root / "quick_v2-p.json").positions, {})
            self.assertEqual(load_portfolio(root / "quick_v2-p.json").cash, Decimal("500.00"))

    def test_v3_requeue_rejects_when_price_moved(self):
        moved = {"orderbook_fp": {"yes_dollars": [["0.55", "100"]], "no_dollars": [["0.43", "100"]]}}
        first = {"orderbook_fp": {"yes_dollars": [["0.49", "100"]], "no_dollars": [["0.50", "100"]]}}
        with tempfile.TemporaryDirectory() as d:
            run = _run_quick(Path(d), "quick_v3", QuickClient([first, moved]), _v3_flow(3))
        self.assertEqual(run["paper_fills"], 0)
        requeued = [x for x in run["decisions"] if x.get("v3_requeue_reevaluation")]
        self.assertEqual(requeued[0]["reason"], "quick_v2_follow_through_too_expensive")

    def test_v3_only_queues_when_waiting_is_the_sole_failure(self):
        wide = {"orderbook_fp": {"yes_dollars": [["0.40", "100"]], "no_dollars": [["0.50", "100"]]}}
        with tempfile.TemporaryDirectory() as d:
            run = _run_quick(Path(d), "quick_v3", QuickClient([wide]), _v3_flow(3))
        self.assertEqual(run["paper_fills"], 0)
        self.assertEqual(run["reaction_queue_size"], 0)
        row = [x for x in run["decisions"] if x.get("v3_not_queued_reason")][0]
        self.assertEqual(row["v3_not_queued_reason"], "quick_v2_spread_too_wide")

    def test_v3_circuit_breaker_is_its_own(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = PaperPortfolio()
            p.ledger = [{"action": "SETTLE", "ticker": f"T{i}", "pnl": "0.1"} for i in range(25)]
            save_portfolio(p, root / "quick_v3-p.json")
            run = _run_quick(root, "quick_v3", QuickClient(), _v3_flow(3))
            self.assertEqual(run["circuit_breaker_reason"], "quick_v3_review_sample_reached")
            self.assertFalse(run["new_entries_enabled"])
            v2 = _run_quick(root, "quick_v2", QuickClient(), _v3_flow(20))
            self.assertIsNone(v2["circuit_breaker_reason"])

    def test_v3_lane_paths_cannot_collide_with_v2(self):
        with self.assertRaises(RuntimeError):
            paper_runner._assert_distinct_lane_paths({
                "quick_whale_v2": {"portfolio": "/data/q.json"},
                "quick_whale_v3": {"portfolio": "/data/q.json"},
            })


# -------------------------------------------------------------- research/security
class ResearchSegmentTests(unittest.TestCase):
    def test_segments_are_per_lane_and_labelled_descriptive(self):
        samples = [
            {"lane": "value_edge", "pnl": "1", "risked": "5", "entry_price": "0.40", "side": "NO",
             "ticker": "KXNFLSPREAD-X", "thesis": {"market_type": "spread", "fair_age_s": 30}},
            {"lane": "quick_whale_v3", "pnl": "-1", "risked": "5", "entry_price": "0.62", "side": "YES",
             "ticker": "KXBTC15M-X", "execution": "TAKE", "flow_notional_usd": 900, "thesis": {}},
        ]
        seg = learning._research_segments(samples)
        self.assertEqual(set(seg), {"value_edge", "quick_whale_v3"})
        self.assertEqual(seg["value_edge"]["market_type"]["spread"]["trades"], 1)
        self.assertEqual(seg["value_edge"]["all"]["interpretation"], "descriptive_only")
        self.assertEqual(seg["quick_whale_v3"]["flow_notional"]["$250-$1k"]["trades"], 1)


class PaperOnlySecurityTests(unittest.TestCase):
    def test_whale_lanes_refuse_write_credentials(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"KALSHI_PRIVATE_KEY": "x"}):
            with self.assertRaises(RuntimeError):
                _run_quick(Path(d), "quick_v3", QuickClient(), _v3_flow(3))

    def test_paper_packages_never_reference_order_submission(self):
        forbidden = re.compile(r"place_event_order|KalshiAuthClient|kalshi_submit|/portfolio/(events/)?orders")
        files = [p for p in (ROOT / "autotrader").glob("*.py") if p.name != "paper_guard.py"]
        files.append(ROOT / "whale_edge.py")
        offenders = [p.name for p in files if forbidden.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    with CommittedLedgerGuard():
        unittest.main()


def test_strict_matcher_handles_aliased_short_names():
    from pmedge.matcher import load_aliases
    aliases = load_aliases()
    assert shadow_team_match("USC", "USC Trojans", aliases)[0] >= 0.90
    assert shadow_team_match("Ole Miss", "Ole Miss Rebels", aliases)[0] >= 0.90
    assert shadow_team_match("Ole Miss", "Mississippi State Bulldogs", aliases)[0] < 0.90
    assert shadow_team_match("Miami", "Miami (OH) RedHawks", aliases)[0] < 0.90
