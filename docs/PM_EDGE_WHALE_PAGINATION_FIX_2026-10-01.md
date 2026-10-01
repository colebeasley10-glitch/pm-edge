# Whale trade-tape pagination: horizon fix (2026-10-01)

**Branch:** `claude/whale-pagination-horizon-fix-20261001`, from `main` (which includes the PR #37 merge `888340b`).
**Rules:** paper-only. No threshold, risk or strategy rule was changed. **Quick Whale v3 stays OFF.**

## 1. Root cause

`whale_edge._public_trades` (added in PR #37) sends `min_ts` on every page. It stopped paging when:
- there was no cursor,
- a page was empty,
- the oldest row on a page was at or before `min_ts`, or
- 10 pages had been fetched.

**The `min_ts` stop rule is effectively dead.** The original single-page code also relied on Kalshi applying `min_ts` server-side, so the server never returns a row at or before `min_ts`. Paging can therefore only end on cursor exhaustion or the page cap.

**Why exactly 10,000 rows:** 10 full pages of 1,000, with `fresh_trades = 10000`, means the server kept returning full pages that satisfied the filter. That only happens when the 2-minute window holds **at least 10,000 trades** across all Kalshi markets, i.e. at least about 83 trades per second.

**Eliminating the other causes on the brief's list:**

| Hypothesis | Verdict | Why |
|---|---|---|
| (1) `min_ts` ignored with a cursor, or (2) pagination walking the wrong way | Excluded | Either would put out-of-window rows on page 1 or 2. Those rows would trigger the stop (with newest-first order), or would come first (with oldest-first order). Neither produces 10 full in-window pages. |
| (3, 9) Timestamp parsing | Excluded | Python 3.11 parses every Kalshi `created_time` variant tested (Z, milliseconds, microseconds, nanoseconds). |
| (5) Wrong `min_ts` from the caller | Excluded | The caller sends `now − lookback`, and QW v2's default lookback is 2 min. Confirm `WHALE_QUICK_V2_LOOKBACK_MINUTES` has no override on Railway. |
| (6) Dedupe not affecting stopping | Irrelevant | `fresh = 10000` shows there were no duplicates. |
| (7) Kalshi has more than 10k trades in 2 minutes | **Most likely root cause** | Fits every observation. |

**What the old code got wrong, given that volume:**
- It set `trade_page_cap_hit` but never said the window was incomplete.
- It had no in-window check and no coverage measure.
- It measured flow age against the scan start, before a multi-second, multi-page fetch. That made every flow look younger than it was, which weakened the stale-flow guard.

**Not verifiable from this environment.** The network policy blocks Kalshi. The deployed PR #37 run logs already carry `trade_tape.trade_oldest_ts`:
- If it is **after** `observed_at − 120 s`, the high-volume explanation is confirmed.
- If it is **before**, the server is ignoring `min_ts`. The new code handles that case too: those rows are filtered out and paging stops.

## 2. Old behavior (PR #37, `888340b`)

- Up to 10 pages, with `min_ts` on each request.
- Every row was returned regardless of timestamp, including rows without a timestamp.
- The `min_ts` stop rule was unreachable when the server respected `min_ts`.
- `trade_page_cap_hit` was the only truncation signal. There was no coverage or horizon flag.
- Flow age and time-to-close were measured against the scan start time.

## 3. New behavior

- Same cursor pagination and the same cap (10), with `min_ts` on every request.
- **Returned rows are only in-window trades:** `min_ts ≤ created_time`.
  - Rows older than `min_ts` are dropped and counted.
  - Rows with a missing or malformed `created_time` are dropped and counted, because their window membership is unknown.
- Page order is inferred from the data (`newest_first` / `oldest_first` / `mixed` / `unknown`).
- **Stop rules:**
  - no cursor, or an empty page: the window is complete;
  - a **newest-first** page reaches a row at or before `min_ts`: complete;
  - cap reached with a cursor pending: **truncated**.
- An oldest-first or unknown order is never treated as covered by crossing the boundary.
- De-duplication by `trade_id` across pages is unchanged.
- **New diagnostics** (logged on every whale run as `trade_tape`; the key fields also appear in the `QUICK_WHALE_*_PAPER_RUN` log line):
  - `trade_tape_version`
  - `trade_pages_fetched`, `trade_rows_fetched`, `trade_rows_deduped`
  - `trade_rows_in_window`, `trade_rows_outside_window`, `trade_rows_missing_ts`
  - `trade_oldest_ts`, `trade_oldest_in_window_ts`, `trade_newest_ts`
  - `trade_page_cap_hit`, `trade_page_cap`
  - **`trade_horizon_covered`**, `trade_window_coverage_fraction`
  - `trade_requested_min_ts`, `trade_requested_lookback_seconds`
  - `trade_page_order`, `trade_fetch_seconds`
  - **`trade_covers_since_last_scan`**, `trade_previous_scan_at`
- **Flow age and time-to-close are now judged at the time the tape is in hand** (`evaluated_at` on the run). This is measurement only, not a rule change: the 8 s / 55 s / 180 s limits are identical. Tests with a fixed `now` are unaffected.

## 4. Was the cap actually truncating the horizon?

**Yes, in busy periods.** Ten full pages without exhausting the cursor means trades inside the requested window were not fetched.

**But it doesn't necessarily lose trades for the quick lanes.** Quick lanes scan every ~60 s with a 2-minute lookback, and anything older than the previous scan was already fetched (or missed) by that scan. So a trade is only **permanently lost** if the tape fails to reach back to the previous scan.

`trade_covers_since_last_scan` measures exactly that. In the busy-period test (about 100 trades/s), 10 pages reach back about 99 s, which covers the 60 s since the last scan.

## 5. Page cap: keep 10 for now

**I have not changed the production default.** Raising it before measuring would only hide the problem.

**Decision rule, after ~24 h of the new diagnostics:**
- If `trade_covers_since_last_scan` is true on ≥ 99% of QW v2 scans, then **10 is adequate** and the cap stays.
- If not, raise `PM_EDGE_WHALE_TRADE_PAGE_CAP` to the 99th percentile of the pages needed. Estimate that as `trade_pages_fetched / trade_window_coverage_fraction × (60 / 120)` for the since-last-scan requirement. Cap it at 30.

**Cost and limits:**
- Worst case: `cap` requests per scan per lane.
- QW v2 (and v3 when enabled) at 60 s, plus Whale Flow at 300 s: about 2–3 lanes × 10 = 20–30 requests per minute at cap 10, or 60–90 at cap 30.
- That is far below Kalshi's public read limits (Basic tier ≈ 20 requests/s).
- The real constraint is latency: about 0.3–0.5 s per page, so 30 pages is about 10–15 s per scan. That fetch time now counts toward flow age, which is correct but means fewer flows survive the 55 s limit.

**A per-lane cap is not justified yet.** All lanes share the same tape density. Revisit only if one lane's requirement clearly differs.

## 6. Tests added (`test_whale_tape_horizon.py`, 16 tests, in CI)

1. One page covers the horizon.
2. and 3. Multiple pages with cursor pagination (`min_ts` re-sent on every page).
4. Duplicate IDs across pages.
5. and 6. Out-of-window rows are dropped and stop paging (server ignoring `min_ts`).
7. and 8. Cap reached before the horizon gives `trade_horizon_covered=false` and coverage under 50%.
9. A row exactly at `min_ts` is inside the window and counts as covered.
10. No cursor means complete.
11. Empty page.
12. Missing or malformed `created_time` is excluded and counted.
13. Busy-period simulation: 18k trades in 2 min, comparing cap 10 vs cap 20.
14. The Quick Whale v2 caller with a 2-minute lookback. This reproduces the production symptom (`public_trades=10000`), checks that the lane version and the covers-since-last-scan flag are logged, and confirms out-of-window rows are excluded.

Also:
- Oldest-first ordering is detected and never falsely marked as covered.
- QW v2 period classification across the boundary, plus the read-only report tool.
- **No-threshold-change assertions:** v2 gate defaults, whale price bands, page cap 10, the Value Edge 5% minimum edge, and the v2 circuit breaker.

## 7. Production impact

**Changes:**
- Whale lanes now receive only in-window trades. When the server respects `min_ts`, that is identical to before.
- Rows without timestamps are excluded.
- Flow age now includes the tape-fetch time, which is slightly stricter staleness and slightly earlier eligibility for the reaction wait, i.e. correct measurement.

**Unchanged:**
- Every threshold, risk control and bankroll.
- The Value Edge path, including the fresh-quote recheck.
- C1/C2/C3 stay shadow-only, and CLV capture is untouched.
- Whale Flow and QW v1 rules.
- QW v3 stays disabled by default.

No authenticated client and no order code were added (the existing security test still passes).

## 8. QW v2 version boundary

Constants live in `autotrader/whale_paper_runner.py`:

| Period | Covers | How a record is classified |
|---|---|---|
| `quick_whale_v2_pre_pagination` | before `2026-10-01T17:37:55.407Z` | by timestamp |
| `quick_whale_v2_pagination_v1` | from that deploy (`888340b`) until this fix | by timestamp (no label on these records) |
| `quick_whale_v2_pagination_v2` | from this fix onward | explicit label |

**From this fix onward:**
- New run records carry `lane_version`.
- New whale theses carry `lane_version`, `trade_tape_version` and `entered_at`.
- Historical records are **not** rewritten, and v2's bankroll, state and results are untouched.

**Old trades** have no entry timestamp. `tools/qw_v2_version_report.py` assigns them a period by joining each trade's `source_trade_id` to the run log (including archives) where it filled. It is read-only and reports each period separately, never pooled, labelled `descriptive_only` below 40 trades.

## 9. Risks

- If Kalshi ever drops `created_time` from trade rows, all rows would be excluded, so the lanes stop entering (fail closed). That is visible in `trade_rows_missing_ts`.
- In very busy periods, longer fetch times make more quick flows stale. That is correct, but it lowers volume.
- With the cap left at 10, truncation will still occur in peaks. It is now reported explicitly.

## 10. Deployment verification (after review and merge; do not perform before approval)

1. Confirm the PR head SHA is unchanged and CI is green. Then merge.
2. Deploy `autotrader-paper` with `AUTOTRADER_VALUE_FRESH_RECHECK=1` and `WHALE_QUICK_V3_PAPER_ENABLED=0`. Record the deploy timestamp: it is the start of `quick_whale_v2_pagination_v2` (the explicit label also marks it).
3. Check the logs and run files:
   - The Value Edge lifecycle is fine and there is no watchdog restart.
   - The `QUICK_WHALE_V2_PAPER_RUN` lines show `lane_version=quick_whale_v2_pagination_v2` plus the `trade_horizon_covered`, `trade_page_cap_hit`, `trade_window_coverage_fraction` and `trade_covers_since_last_scan` fields.
   - `trade_rows_outside_window` and `trade_rows_missing_ts` are about 0.
   - CLV errors are 0, the PWA and API endpoints return 200, and the mobile API still reads the ledger.
4. **Acceptance for Step 4:**
   - `trade_horizon_covered=true`, **or**
   - when truncated (`trade_page_cap_hit=true`), `trade_covers_since_last_scan=true` on ≥ 99% of QW v2 scans over the observation window.
   If neither holds, report the coverage numbers and use the cap decision rule in §5. **Do not enable v3.**
5. Confirm the root cause: check `trade_tape.trade_page_order` (expected `newest_first`) and `trade_oldest_in_window_ts` versus `trade_requested_min_ts`.

## 11. Quick Whale v3

**It remains OFF** (`WHALE_QUICK_V3_PAPER_ENABLED=0`) until this fix is deployed and the Step 4 checks above pass. Only then set it to 1, redeploy, and verify v3 on its own:
- the startup log line,
- `/v1/quick-whale-v3-status` returns 200 with `reaction_queue_size`,
- the v3 heartbeat,
- only `/data/quick-whale-v3-*` files are created,
- v2 files and statistics are untouched.
