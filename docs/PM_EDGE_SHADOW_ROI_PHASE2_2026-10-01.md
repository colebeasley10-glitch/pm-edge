# PM Edge — Shadow ROI Phase 2 (2026-10-01)

**Branch:** `claude/pm-edge-shadow-roi-phase2-20261001`, based on `main@922aec5`.
**Status:** for review only. **Do not merge without review.**

**Ground rules (unchanged from Phase 1):**
- Every lane stays **paper-only**.
- **No** threshold, freshness rule, spread limit, risk/bankroll limit, late-entry rule or event-start protection was loosened.
- No challenger can change production, and there is no auto-promotion.

**Purpose of this phase:** better measurement and execution realism, not more bets.

## A. Files changed

| File | Purpose | Production behavior changed? | Paper fills changed? | Tests |
|---|---|---|---|---|
| `autotrader/engine.py` | Shared production gate `_size_and_gate`; fresh-quote recheck; C1/C3 fields on every decision; research provenance on new theses; spread entries tagged `value_edge_v1_spreadfix` | **Yes — Task A only** | **Yes — can only *remove* fills** whose fresh quote fails the same gate. Fills use the fresh price. | `FreshQuoteRecheckTests`, `ChallengerTests` |
| `autotrader/paper_runner.py` | Requote closure using the scanner's own depth and quality screen; C2 shadow on exits; C2 counterfactual state; QW v3 thread, config, heartbeat, status and scan endpoints, lane-path guard, learning source; rotating run log | Recheck only (A) | As above | Same, plus `QuickWhaleV3Tests` |
| `autotrader/challengers.py` (new) | Pure C1/C2/C3 shadow functions | No | No | `ChallengerTests` |
| `autotrader/c2_shadow.py` (new) | Counterfactual exit accounting kept in its own JSON file | No | No | `test_c2_fields_and_counterfactual` |
| `autotrader/whale_paper_runner.py` | Tape diagnostics; whale theses get `close_time` / `event_start_at` with provenance; maker-fill shadow; QW v3 mode, reaction queue and v3-specific breaker; queue size in status | No for Whale Flow / v1 / v2 (beyond the tape change in `whale_edge.py`) | No | `MakerShadowTests`, `QuickWhaleV3Tests`, `test_whale_paper_runner.py` |
| `whale_edge.py` | Cursor pagination of the public trade tape | **Yes (Task C)**: whale lanes see the full lookback window instead of a truncated one | Indirectly — more complete flows can produce different candidates | `TradeTapePaginationTests` |
| `autotrader/clv_capture.py` (new) | CLV checkpoint capture for open positions in all lanes | No (read-only) | No | `ClvCaptureTests` |
| `autotrader/recorder.py`, `recorder_store.py` | Recorder calls the CLV capture each cycle; new `clv_checkpoints` table (unique per position and checkpoint, 120-day retention) | No | No | `ClvCaptureTests` |
| `autotrader/learning.py` | CLV target = event start, else close time, with provenance; per-lane research segments; `interpretation` labels; execution labels normalized | No (research output only) | No | `ResearchSegmentTests`, `test_learning_clv_uses_only_books_at_or_before_target` |
| `autotrader/probability.py` | Ledger `sigma` goes to `provenance_sigma`, **not** the `sigma` key the engine reads; adds `fair_provenance_version` and `sport_key` | No (`sigma` stays at the 0.03 default exactly as today) | No | `test_logged_sigma_never_reaches_production_thesis` |
| `autotrader/thesis.py` | `from_dict` ignores unknown keys, so research keys on a thesis can't break exit management | No | No | `test_c2_logged_on_production_exit_without_changing_it` |
| `autotrader/storage.py` | `rotate_jsonl` / `append_jsonl` (daily rotation, gzip, retention); archive-aware `tail_jsonl` | No | No | `RunLogRotationTests` |
| `candidate_tracker.py` | New columns: `sigma`, `source_age_s`, `lane_version`, `matcher_shadow_*`. The existing `_ensure_schema` migrates old headers in place. | No | No | `ProvenanceLoggingTests` |
| `pmedge/matcher.py`, `watcher.py` | Shadow matcher validator on every accepted match, logged to the ledger plus run-summary counters | **No** — acceptance is unchanged | No | `MatcherShadowTests` |
| `tests/ledger_isolation.py` (new), `test_performance_tracker.py`, `test_pm_edge_commit3.py` | Redirect all ledger env paths to temp files before import; guard fails if a committed file changes | — | — | `TestHygieneTests` |
| `.github/workflows/autotrader-paper-tests.yml` | Runs the new suite; also triggers on `whale_edge.py`, `candidate_tracker.py` and `tests/**` | — | — | — |

## B. Value Edge fresh-quote recheck

**How it works:**
- A candidate that passes the production gate is re-quoted immediately before the simulated fill: one `get_orderbook(depth=1)` call.
- The fresh quote goes through the scanner's own `_with_orderbook_depth` and `discover`. That reapplies the same spread, depth and validity screen.
- It then runs through the **same** `_size_and_gate` (sizing, order-level fee, slippage, `evaluate`) with:
  - the fresh quote's age, and
  - the unchanged `min_net_edge`, quote-age and fair-age limits.
- Only a pass fills, and it fills at the fresh price, size and fee.
- A candidate rejected on the initial quote is never re-quoted, so **the recheck can only remove fills**.

**Logged on each recheck:** `initial_quote_price`, `initial_quote_age_s`, `recheck_quote_price`, `recheck_quote_age_s`, `recheck_net_edge`, `recheck_result` (`passed` / `rejected:<gate reason>` / `rejected_no_executable_fresh_quote`) and `recheck_latency_ms`.

**Stale-quote risk reduced:** the fill price is now at most about one request old, instead of up to the full sequential-scan duration.

**Thresholds:** unchanged (5% edge, quote max age, fair max age).

**Kill switch:** `AUTOTRADER_VALUE_FRESH_RECHECK=0`.

## C. CLV coverage

**Lanes covered:** Value Edge, Whale Flow, Quick Whale v1, Quick Whale v2 and Quick Whale v3. Portfolio paths come from the same env names the worker uses.

**Checkpoints:**
- `T_MINUS_10`: captured once, 9–11 min before the target.
- `START_OR_CLOSE`: captured once, from 2 min before to 1 min after the target.

The windows are sized to the 60 s recorder cycle. Each capture writes:
- `ob_snapshots` (raw book),
- `ob_top` (so the point-in-time `book_at` can see it), and
- `clv_checkpoints`, labelled by checkpoint, de-duplicated on `(position_key, checkpoint)`.

**Target time:**
- The thesis `event_start_at` is used if present:
  - Value Edge: sportsbook commence time.
  - Whale Flow: PM Edge fair-cache commence time, else the game time parsed from the Kalshi ticker, with provenance recorded.
- Otherwise the thesis `close_time` (Kalshi close).
- Otherwise, for legacy positions, a labelled `kalshi_market_close_time_lookup`.
- **Never fabricated:** quick lanes record `event_start_at = null`, and naive timestamps are rejected.

**Look-ahead:** learning computes CLV with `book_at(target)`, which only returns rows received **at or before** the target. A test proves that a later book is ignored. Checkpoints are evaluation-only; no entry decision reads them.

**Remaining gaps:**
- Positions opened inside a checkpoint window skip that checkpoint (common for quick lanes on `T_MINUS_10`).
- At most 40 books are captured per cycle.
- Positions closed before the target have no checkpoints, by design.
- Whale Flow positions opened before this change carry no start time, so they use the close-time lookup. For sports, that is often much later than the game.

## D. Whale trade-tape pagination

- **Page cap:** `PM_EDGE_WHALE_TRADE_PAGE_CAP`, default 10 pages of 1,000 trades.
- **Stop rules:**
  - no cursor,
  - empty page,
  - oldest row on the page is at or before `min_ts`,
  - page cap reached.
- **De-duplication:** by `trade_id` across pages. Rows without an ID are kept.
- **Diagnostics:** `trade_pages_fetched`, `trade_rows_fetched`, `trade_rows_deduped`, `trade_oldest_ts`, `trade_page_cap_hit` and `trade_page_cap`. They are logged on each whale run as `trade_tape`.
- **Read-only:** public endpoint only, no paid calls.
- **Behavior note:** this applies to every whale lane, including QW v2. In busy windows, flows that were previously truncated away can now be evaluated. That was the explicit purpose of Task C, but it is a (data-completeness) change to the v2 sample.

## E. Maker-fill shadow

**Current production model (unchanged):**
- It fills when a same-side print at or below target is seen **and** the current ask is at or below target.
- It sizes with `available = max(contracts, ask_size)`.

**Shadow model `maker_tradethrough_v1`:**
- It accumulates public-tape volume at or better than the resting target, counting only trades printed **after** the maker order was created.
- Trade IDs are de-duplicated across scans and persisted on the pending order.
- `maker_shadow_fill_qty = min(order contracts, qualifying volume)`; it never uses the ask size.

**Logged on every pending decision:**
- `pending_resting`, `pending_expired`, `pending_dropped`, and production fills.
- Fields: `maker_shadow_model_version`, `maker_shadow_would_fill`, `maker_shadow_fill_qty`, `maker_shadow_target`, `maker_shadow_trade_through_volume`, `maker_shadow_strictly_better_volume`, `maker_shadow_first_fill_time` and `maker_shadow_reason`.

**No claim of superiority.** Compare the two models once enough pending orders resolve.

## F. Matcher shadow

**Validator:**
- A token-boundary team match. Allowed forms: exact tokens; initialisms ("NY" → New York); 3–4 letter team codes ("PIT" → Pittsburgh), which also need a full shared word; and in-order gaps for non-qualifier words ("Fukuoka Hawks" vs "Fukuoka SoftBank Hawks").
- Extra school or city qualifier words fail: tech, state, A&M, (OH), city, north, south, and so on.
- Same New York local date, when both dates are known.

| Pair | Score | Result |
|---|---|---|
| Georgia vs Georgia Tech | 0.50 | fail: qualifier `tech` |
| Georgia vs Georgia Bulldogs | 0.95 | pass |
| Miami vs Miami (OH) | 0.50 | fail |
| Kansas vs Kansas City Chiefs | 0.50 | fail |
| PIT Steelers vs Pittsburgh Steelers | 0.90 | pass |
| NY Jets vs New York Jets | 0.90 | pass |
| Boston vs Houston | 0.00 | fail |

**Logged:**
- Per match: `matcher_shadow_pass`, `_reason`, `_team_score` and `_date_match` in the candidate ledger.
- Run summary: `matcher_shadow_accepted_matches`, `matcher_shadow_would_fail` and `matcher_shadow_fail_reasons`.

**Replay on committed data:** of 236 distinct accepted ticker/event matches, 49 could be joined to the committed events cache. **0 of 49 would fail** (35 date-verified, 14 date-unverified). Treat this as a small, non-representative sample. The live run-summary counters are the real measurement.

**Production enforcement: none.**

## G. Challengers (shadow only; production unchanged)

**C1 — stale-fair edge rule.** Logged on **every** Value Edge decision.
- Fields: `challenger_c1_version`, `challenger_c1_required_edge`, `challenger_c1_pass`, `challenger_c1_reason`, `challenger_c1_fair_age_s` and `challenger_c1_source_age_s`.
- Required edge = 5% + 0.1 pp per minute of fair age beyond 5 min.
- Reject if edge ≥ 15% or book source age > 15 min.
- Source age = `source_age_s` recorded when the fair was computed, plus the fair's age. Both are known at decision time.
- `pass_source_age_unknown` marks rows written before this provenance existed.

**C2 — EV-only exit.** Logged on every production SELL / EXIT_THESIS / REDUCE.
- Fields: `challenger_c2_would_exit`, `challenger_c2_reason`, `challenger_c2_bid_net_fee`, `challenger_c2_current_fair` and `challenger_c2_production_exit_reason`.
- Executed exits are written to `/data/autotrader-c2-exit-shadow.json` (`AUTOTRADER_C2_SHADOW_STATE`).
- Once the market finalizes, it computes `production_exit_pnl`, `counterfactual_hold_to_settlement_pnl`, `challenger_c2_pnl` and `challenger_c2_pnl_difference`.
- A summary is added to every Value Edge run (`challenger_c2_summary`).
- The portfolio is never altered.

**C3 — spread tracking.** Logged on every decision: `challenger_c3_is_spread`, `challenger_c3_is_no_side` and `challenger_c3_lane_version`.
- A new spread entry's thesis gets `lane_version = value_edge_v1_spreadfix` only when its fair row carries `lane_version = fair_provenance_v1_spreadfix`, i.e. it was written by the corrected devig code.
- Fairs from the legacy mobile-API fork are **not** tagged.
- Historical trades are not rewritten, and NO spreads are not blocked.

## H. Quick Whale v3

**Separate everything:**
- Paths:
  - `/data/quick-whale-v3-paper-portfolio.json`
  - `/data/quick-whale-v3-paper-state.json`
  - `/data/quick-whale-v3-paper-runs.jsonl`
- Its own $500 bankroll, seen IDs, pending/reaction queue, breaker and stats. Learning lane: `quick_whale_v3`.
- The startup lane-path guard refuses any collision with v2.

**Reaction queue:**
- A flow is queued only when v2's gate fails **solely** with `quick_v2_wait_for_reaction`. This is checked by re-running the same gate with the age set to the minimum.
- If a second failure exists, it is logged as `v3_not_queued_reason`.
- The queue is bounded to 50 entries.
- The 60 s cadence would always see a queued flow as stale on the next scan. So v3 waits **inside the same scan** until the flow becomes eligible, capped at `WHALE_QUICK_V3_MAX_REACTION_WAIT_SECONDS` (default 12 s, max 30).
- It then re-evaluates the flow with a fresh book through the **identical** v2 gate, caps and risk checks.
- Every outcome is logged with `v3_requeue_reevaluation=true`: entry, stale, too expensive, spread, close, notional, persistence.

**Circuit breaker:** stops after 25 settled trades (`WHALE_QUICK_V3_MAX_SETTLED_TRADES`) or −$50 realized (`WHALE_QUICK_V3_MAX_REALIZED_LOSS`). Reasons are `quick_v3_*`. **No conclusions at 25 trades.**

**Operations:**
- Status: `/v1/quick-whale-v3-status` (includes `reaction_queue_size`).
- Manual scan: `/v1/quick-whale-v3-run-scan`.
- Heartbeat: `quick_whale_v3`.
- **Disabled by default.** Enable with `WHALE_QUICK_V3_PAPER_ENABLED=1`.
- The mobile app has no v3 screen yet.

## I. Test results (final run on this branch)

| Command | Result |
|---|---|
| `python -m unittest` (the 18 required modules) | **112 tests OK** |
| `python -m unittest test_autotrader_phase2_shadow` (new) | **41 tests OK** |
| `python test_whale_paper_runner.py` | **6 tests OK** |
| `python test_pm_edge_regressions.py` | **14 passed, 0 failed** |
| `python tests/phase0_golden_probe.py --verify-parent` (non-shallow clone) | **PASS** |
| Every other `test_*.py` script, plus `pm_edge_runner_v18…v28 --self-test` and `pinnacle_shadow.py --self-test` | all exit 0 |
| Committed ledger/state files after running every test script | **unchanged** |

## J. Risks / open questions

1. **Recheck latency vs. volume.** One extra book fetch per qualifying candidate. It can only reduce fills; watch `recheck_result` counts.
2. **Pagination changes QW v2's data mid-sample** in busy windows (see D). If v2's sample must stay strictly comparable, set `PM_EDGE_WHALE_TRADE_PAGE_CAP=1` for that lane's process. That needs a per-lane override, which is not implemented.
3. **v3 in-scan wait** blocks the v3 thread for at most 12 s per scan. Other lanes run in their own threads.
4. **Candidate-ledger migration.** The first write after deploy rewrites the CSV header in place, using the existing `_ensure_schema`. The mobile API fork reads the synced CSV with `DictReader`/pandas, so extra columns are tolerated. This has not been verified against that fork's code.
5. **The CLV close-time fallback for sports** (legacy Whale Flow positions) can be much later than the game, so it is labelled by provenance; filter on it.
6. **C1 source age** is unknown for fair rows written before this deploy (`pass_source_age_unknown`).
7. **The matcher replay** covered only 49 joinable matches.

## K. What should be deployed

**Safe reliability code** (can ship with normal review):
- Run-log rotation, retention and archive-aware tail.
- Test-ledger isolation.
- Thesis `from_dict` tolerance.
- Whale status queue size.

**Production-affecting correctness / realism** (review the behavior notes):
- Value Edge fresh-quote recheck (A). It can only remove fills; kill switch `AUTOTRADER_VALUE_FRESH_RECHECK=0`.
- Trade-tape pagination (C). It affects every whale lane's flow candidates.

**Shadow research code** (no production effect):
- C1/C2/C3.
- Maker-fill shadow.
- Matcher shadow.
- Provenance columns.
- CLV checkpoint capture.
- Research segmentation.

**New paper experimental lane:**
- Quick Whale v3. It ships disabled; enable it deliberately with `WHALE_QUICK_V3_PAPER_ENABLED=1`.

**Mobile API drift:** nothing in this PR requires changing the `railway/code-change-HB1cBE` source branch. The only cross-service touchpoint is the candidate-ledger header gaining columns (see J-4).
