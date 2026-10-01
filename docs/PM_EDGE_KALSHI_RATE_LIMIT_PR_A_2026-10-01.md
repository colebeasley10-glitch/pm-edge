# PR A: Kalshi rate limiting, 429 retries, partial trade tape (2026-10-01)

**Branch:** `claude/kalshi-rate-limit-429-20261001`, from `main` (which includes PR #38 / `858c315`).
**Scope:** reliability only. Paper-only. No strategy rule, threshold, page cap or lane state is changed. **Quick Whale v3 stays OFF.**
**What comes next:** this is step 1 of the two-step plan. PR B (the shared trade-tape collector) comes after this is deployed and measured.

## Why

From the post-PR #38 sample:

| Measure | Value |
|---|---|
| QW v2 scans that ended in an explicit 429 | 4 of 30 (13%) |
| Of those, 429s hit mid-pagination | 3 |
| Lost per 429 | the scan's already-fetched pages, plus ~190 s of lane backoff |

The 429s coincided with Whale Flow, recorder and Value Edge activity. The problem is aggregate burst load from one process, not any single lane.

## Changes

| File | Change |
|---|---|
| `pmedge/rate_limit.py` (new) | Thread-safe token bucket. A single **process-wide** limiter. `get_with_retry()` retries **only HTTP 429**: it honors `Retry-After` (seconds or HTTP-date), otherwise backs off exponentially with ±25% jitter (≈1 s, 2 s, 4 s), with bounded retries and a bounded wait. Pacing is **off unless configured**, so CI, GitHub Actions and short tools are unchanged except for the bounded 429 retry. |
| `pmedge/kalshi.py` | `KalshiPublicClient._get` (public GETs only) goes through the shared limiter and the 429 retry. An exhausted 429 still raises `KalshiError` with "429" in it, so the existing lane backoff detection is unchanged. Per-client `retry_stats`. |
| `whale_edge.py` | The trade-tape page fetch uses the same limiter and retry (with per-thread retry counters). `_public_trades`: if a page **after the first** still fails, the pages already fetched are **kept**, and the window is reported as truncated. New diagnostics: `trade_partial_after_error`, `trade_fetch_error`, `trade_rate_limited`, `trade_rate_limit_retries`. A first-page failure still raises, as before. |
| `autotrader/whale_paper_runner.py` | A partial tape no longer puts the lane into its 180 s backoff. A failed first page, or a 429 on a market/orderbook call, still does. The run log line adds `trade_page_order` (previously unverifiable), `trade_rate_limited`, `trade_rate_limit_retries` and `trade_partial_after_error`. |
| `autotrader/paper_runner.py` | The worker configures the process-wide limiter at startup: default **10 req/s, burst 10**, which is half of Kalshi's documented basic read limit of about 20/s. A startup log line shows the settings. `/v1/status` adds `kalshi_rate_limiter` (rate, burst, requests paced, total wait). |
| tests | `test_kalshi_rate_limit.py` (18 tests). The audit-hardening test resets the limiter after calling `main()`. Both are added to CI. |

**Bug caught by the tests:** a float-precision livelock in the token bucket. A refill landing at 0.9999999999999998 produced a wait below clock resolution, so the loop never advanced. It's fixed with a tolerance and a 1 ms minimum sleep.

## Config (new, optional; defaults shown)

| Variable | Default | Notes |
|---|---|---|
| `PM_EDGE_KALSHI_RATE_LIMIT_RPS` | `10` in the worker; off elsewhere | Lower to 5 if 429s persist. |
| `PM_EDGE_KALSHI_RATE_LIMIT_BURST` | `10` in the worker | |
| `PM_EDGE_KALSHI_429_MAX_RETRIES` | `3` | |
| `PM_EDGE_KALSHI_429_MAX_WAIT_SECONDS` | `30` | |

**No production env change is required.** `PM_EDGE_WHALE_TRADE_PAGE_CAP` stays at `10`.

## Expected effect

**What the limiter does at 10 req/s** (QW v2's 10 pages, Whale Flow's 10 pages, recorder bursts, Value Edge market and orderbook calls):
- It paces simultaneous bursts across threads instead of letting them pile up.
- Steady load on the trade tape is about 12 req/min, plus recorder and Value Edge traffic. That is well under 10/s on average, so it mostly smooths peaks.

**Fetch latency:** about the same as today (around 3 s for 10 pages). It only goes up when lanes overlap, by up to ~1 s per 10 queued requests.

**429s:** fewer, because bursts are spread out. When one still happens:
- the page is retried after the server's `Retry-After` or the backoff,
- if it ultimately fails, the data already fetched is kept, and
- the lane doesn't go dark for 190 s.

**Value Edge:** pacing can lengthen its per-ticker market fetch when other lanes are busy. The existing 150 s scan budget fails closed by skipping fetches and counting them in `deadline_skipped`. Watch that counter after deploy.

**What this does not fix:** coverage. `trade_covers_since_last_scan` stays roughly where it was (cap 10 is unchanged). That is PR B's job.

## Tests

| Command | Result |
|---|---|
| Required 19-module `unittest` suite | 153 OK |
| `test_kalshi_rate_limit` (new) + `test_whale_tape_horizon` | 34 OK |
| `test_whale_paper_runner.py` | 6 OK |
| `test_pm_edge_regressions.py` | 14 passed |
| `tests/phase0_golden_probe.py --verify-parent` | PASS |
| Every other `test_*.py` script, the launcher self-tests, the Pinnacle self-test | exit 0 |
| Committed ledgers after the run | unchanged |

**New tests cover:**
- Token bucket: disabled mode, burst then steady rate, thread safety, env overrides.
- Retry: `Retry-After` in seconds and HTTP-date form, exponential backoff, the wait cap, exhausted retries, no retry on non-429 errors.
- Kalshi client: retries then succeeds; an exhausted 429 still raises with "429".
- Trade tape: keeps partial pages after a mid-pagination 429; a first-page failure still raises; a partial tape does not trigger the lane's long backoff.
- All public clients and the trade tape share **one** limiter.

## Deploy and verify (after review; do not perform before approval)

1. Confirm the PR head SHA is unchanged and CI is green. Then merge.
2. Deploy `autotrader-paper`. Keep `AUTOTRADER_VALUE_FRESH_RECHECK=1`, `WHALE_QUICK_V3_PAPER_ENABLED=0` and `PM_EDGE_WHALE_TRADE_PAGE_CAP=10`. Leave the new env vars unset (defaults apply).
3. **Verify:**
   - the startup line `"kalshi_rate_limiter": "configured"` shows 10/10;
   - `/v1/status` shows `kalshi_rate_limiter`;
   - Value Edge lifecycle is normal, with no watchdog restart;
   - `deadline_skipped` stays about 0;
   - CLV errors are 0;
   - the PWA and API endpoints return 200.
4. **Measure over at least 30 QW v2 scans and at least 6 Whale Flow scans (log lines only):**
   - 429 count (`trade_rate_limited`, `api_error`) vs. 4 of 30 before;
   - `trade_rate_limit_retries`;
   - `trade_partial_after_error` count;
   - lane-backoff events;
   - `trade_page_order` (expected `newest_first`, which confirms the PR #38 root-cause assumption);
   - median `trade_fetch_seconds`.
5. Coverage (`trade_covers_since_last_scan`) is **not** expected to pass yet. **v3 stays OFF** until PR B is deployed and the full Step 4 gate passes.
