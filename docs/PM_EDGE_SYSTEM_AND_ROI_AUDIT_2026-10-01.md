# PM Edge — System & ROI Audit (2026-10-01)

Scope: full repository at `main@4df0621` plus the evidence available to this audit session.

**Access limits (read first).** This session had **no Railway access**. There was no CLI or token, and the network policy blocks `*.up.railway.app`. As a result:
- None of the deployed paper state was readable. That covers the `/data/*` portfolios, run logs, learning state and recorder DB.
- **No per-lane ROI analysis of Value Edge, Whale Flow, Quick Whale or Quick Whale v2 paper trades was possible.**

All performance numbers below come from the ledgers committed to git:
- GitHub-Actions alert lanes (`primary`, `price_fish`, `whale` alerts).
- Their hypothetical outcomes and CLV.
- The real approval-link fills.

The paper AutoTrader lanes share the alert lanes' fair-value pipeline. So these numbers say something about fair-value quality, but they are **not** the paper lanes' results.

---

## 1. Architecture map

| Component | Entrypoint | Runs where | Persistent state |
|---|---|---|---|
| Value Edge paper AutoTrader | `python -m autotrader.paper_runner` (`main`) | Railway `autotrader-paper` | `/data/autotrader-paper.json` (portfolio), `/data/autotrader-paper-runs.jsonl`, `/data/autotrader-learning.json`, `/data/autotrader-fair-refresh.json`; fair ledger `AUTOTRADER_LEDGER` (default **relative** `pm_edge_candidate_ledger.csv`) |
| Whale Flow / Quick Whale / Quick Whale v2 | Threads inside the same `paper_runner` process (`WHALE_*_ENABLED`); logic in `autotrader/whale_paper_runner.py` + `whale_edge.py` | same container | `/data/whale-paper-*.json(l)`, `/data/quick-whale-paper-*`, `/data/quick-whale-v2-paper-*` |
| Research recorder | thread `start_recorder_thread()` (`autotrader/recorder.py`) | same container | `/data/autotrader-recorder.sqlite3` |
| Value Edge watchdog | daemon thread `_value_watchdog` → `os._exit(70)` | same container | — |
| Status/trigger HTTP API | `ThreadingHTTPServer` in `paper_runner` (`/health`, `/v1/status`, `/v1/*whale*-status`, `/v1/learning-status`, `/v1/runs`, `/v1/fair-values`, POST `*run-scan`) | same container | — |
| Fair-value refresh (fallback) | subprocess `pm_edge_runner_v28.py` → `watcher.py` with paper-only env | same container | watch state at `<ledger dir>/autotrader-pm-edge-watch-state.json` |
| Alert watcher (human lanes) | `.github/workflows/pm-edge-watch.yml` every 30 min → `pm_edge_runner_v28.py` | GitHub Actions | commits ledgers to `main` **and** copies them to `railway/code-change-HB1cBE` |
| PM Edge mobile API | `mobile_backend.py` / `app.py` | Railway, deployed from **`railway/code-change-HB1cBE`** (149 commits ahead / 486 behind `main`, forked 2026-09-21) | committed CSV ledgers |
| PWA | `mobile/server.js` (+ Expo web build) | Railway | cookie session |
| Fair-value pipeline | `watcher.py` → `pmedge/fair_value.py` (`fair_from_event`, trimmed-mean `robust_consensus`, power devig) → `candidate_tracker.py` (`stage=score` rows) → `autotrader/probability.py` (`_build_snapshot`) | — | `pm_edge_candidate_ledger.csv` |
| Paper execution | `autotrader/engine.py` (taker at ask+slippage, top-of-book depth), `portfolio.py`, `exit_engine.py`, `fees.py` | — | — |
| Learning | `autotrader/learning.py` (read-only research snapshot) | — | `/data/autotrader-learning.json` |

---

## 2. Performance evidence that exists (and its limits)

### 2a. Alert-lane hypothetical outcomes (`pm_edge_signal_outcomes.csv`, n = 74 settled, 2026-09-20 → 09-24)

The fill price assumed is the recommended limit, so fills are assumed rather than observed.

| Segment | n | W–L | P/L | Risked | ROI |
|---|---|---|---|---|---|
| **All** | 74 | 38–36 | +$28.91 | $293.09 | +9.9% |
| price_fish | 36 | 19–17 | +36.39 | 156.61 | +23.2% |
| primary | 11 | 6–5 | +8.77 | 47.23 | +18.6% |
| whale (alerts) | 27 | 13–14 | −16.25 | 89.25 | −18.2% |
| h2h | 37 | 22–15 | +57.65 | 164.35 | +35.1% |
| spread | 7 | 3–4 | +2.51 | 24.49 | +10.2% |
| total | 3 | 0–3 | −15.00 | 15.00 | −100% |
| side NO | 33 | 19–14 | +35.13 | 124.87 | +28.1% |
| side YES | 41 | 19–22 | −6.21 | 168.22 | −3.7% |
| price < 0.30 | 8 | 4–4 | +13.05 | 11.95 | +109% |
| price 0.30–0.45 | 28 | 13–15 | +20.46 | 88.54 | +23.1% |
| price 0.45–0.60 | 22 | 11–11 | +0.25 | 93.74 | +0.3% |
| price ≥ 0.60 | 16 | 10–6 | −4.85 | 98.85 | −4.9% |
| edge 4–6% | 36 | 19–17 | +36.39 | 156.61 | +23.2% |
| edge ≥ 10% | 9 | 6–3 | +15.93 | 40.07 | +39.8% |

**Calibration.** This uses the 48 settled signals that have a sportsbook fair.
- Expected wins by fair: 24.9. Expected wins at the Kalshi price: 20.8. Actual wins: **25**. One SD under the fair is about 3.3 wins.
- Brier score: 0.2428 for the fair, 0.2536 for the Kalshi price.
- The fair looks calibrated and beat the price by about 1.3σ. **That is not statistically significant.**

**CLV** (`pm_edge_clv_ledger.csv`, n = 40): mean closing-mid CLV is **+6.4¢** (26 positive, 2 flat, 12 negative).
- price_fish: +10.2¢ (n = 20).
- primary: +4.5¢ (n = 8).
- whale: +1.4¢ (n = 12).

This is the most encouraging signal available, but n = 40.

### 2b. Real approval-link fills (`pm_edge_auto_ledger.csv` + `pm_edge_fill_ledger.csv`)

Across 55 settled real orders over 50 distinct tickers, the record was **16–39**:

| Measure | Value |
|---|---|
| P/L | −$65.43 on $203.43 risked (ROI −32%) |
| Expected wins at the prices actually paid | 23.1 |
| Actual wins | 16 (≈ −2σ) |
| Taker fills (by contract) | 50 / 246 won, gross ROI **−56%** |
| Maker fills (by contract) | 88 / 212 won, gross ROI **+2%** |
| Spread markets, NO side | **6–15, ROI −39%** (9.2 wins expected at price) |
| Spread markets, YES side | 4–5, ROI +16% |
| KXNCAAFSPREAD | 7–15, −$27.58 |
| Loss concentration by date | 9/17–9/19 builds (pre-v26/v28 guards): −$41.51 of the −$65.43 |

### 2c. Candidate ledger (`pm_edge_candidate_ledger.csv`, 7,225 `score` rows, 09-21 → 10-01)

**Fair age at scoring:** median **156 min**, p90 361 min. Only 8.5% of rows are ≤ 5 min old and 17% are ≤ 35 min.

**Apparent edge vs. fair age.** This covers the 1,569 rows that have both timestamps.

| Fair age | n | share net edge ≥ 5% |
|---|---|---|
| ≤ 5 min | 134 | **0.0%** |
| 5–35 min | 134 | **0.0%** |
| 35–120 min | 308 | **0.0%** |
| > 120 min | 993 | 7.2% (4.8% ≥ 10%) |

**Every apparent ≥5% edge with a known fair age came from a fair more than 2 hours old.** Overall, 4.2% of score rows show ≥15% "edge" and the maximum is 55%.

**Spread rows show a large side asymmetry:**
- NO side: 156 of 210 rows (74%) show ≥ 5% edge.
- YES side: 46 of 520 rows (9%).

This is a systematic contract-semantics or devig artifact, not market inefficiency. It lines up with the alternate-spread pairing bug fixed below (§4, A3) and with the real-money NO-spread losses.

---

## 3. ROI-leak attribution (the 16 hypotheses)

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| 1 | Bad fair-value estimates | **Yes, for alternate spreads** (bug A3); h2h calibration looks OK (n = 48) | §2c spread asymmetry; real NO-spread 6–15 |
| 2 | Stale fair values | **Yes — the dominant source of apparent edge** | §2c: 0% ≥ 5% edges when fair ≤ 2 h |
| 3 | Sportsbook/Kalshi mismatches | Likely contributor | Substring team matching (§4 I-4); 4.2% of rows show ≥ 15% edge |
| 4 | Poor execution | Inconclusive | Real taker fills −56% vs maker +2%; n ≈ 60 fills, mostly early builds |
| 5 | Chasing | QW v2 caps chase at +2¢ over flow VWAP; Value Edge has no chase path | Code |
| 6 | Fees/slippage | Not primary | Real fees were 2.3% of cost; paper fees are modeled conservatively (taker rate on maker fills) |
| 7 | Weak low-edge bets | Insufficient evidence | 4–6% bucket was +23% (n = 36) |
| 8 | Price ranges | Insufficient evidence (D) | ≥ 0.45 roughly flat or negative, n = 38 |
| 9 | Sports/market types | Spreads (NO) and totals look bad; D for the rest | §2a/b |
| 10 | Late entries | Guarded (300 s pregame buffer, quick lanes' close-time guard) | Code |
| 11 | Correlated exposure | Guarded per event (`exposure_key`); no cross-event correlation | Code |
| 12 | Whale selection | Alert whale lane −18% (n = 27), CLV +1.4¢ (n = 12) — D | §2a |
| 13 | Maker adverse selection | Not observed in real fills (maker +2%); paper maker sim is optimistic (R-2) | §2b |
| 14 | Risk sizing | Flat ~$10 per position; not a leak | Code |
| 15 | Optimistic paper fills | Yes, in places | See §6 |
| 16 | Randomness | **Most lane-level differences are within noise** | n of 27–74 per lane |

---

## 4. Findings and changes

**Classes:** A = clear bug fix · B = high-confidence process improvement · C = strategy challenger (shadow only) · D = insufficient evidence.

### Changes made (this commit)

**A1 — Kalshi timeout fix never reached production.** `run_once` only applied `AUTOTRADER_KALSHI_TIMEOUT_SECONDS` when it built its own client. `main()` always passes in a client built with the 20 s default, so every production request kept a 20 s timeout. `main()` now builds the Value Edge and all whale clients with the configured timeout.

**A2 — Value Edge dashboard showed every open position as a 100% loss.** `/v1/status` called `portfolio.snapshot()` with no bids, so open positions were marked at $0. It now marks at the live executable bid, which matches the whale endpoints, and returns `current_bid` per position.

**A3 — Alternate-spread devig paired the wrong outcomes** (`pmedge/fair_value.py::_book_prob_spread`).
- The old code paired outcomes by `|point|` only, then keyed them by team name.
- In alternate-spread feeds, "Team −3.5" could be devigged against "Team +3.5", and the dict collision returned the wrong line's probability.
- Reproduced example: 0.409 returned against a true ≈ 0.46.
- The fix pairs the target with exactly one opponent row at the mirrored line, and fails closed otherwise. Main-line and pick'em results are unchanged.

**A4 — "Win / Closed" record ignored sold positions.** Value Edge actively exits, but the record only counted SETTLE rows. The record now covers closed round trips (sold or settled), with `record_basis`, `closed_count` and `settled_count` reported separately. Applied to both Value Edge and whale status.

**A5 — `worker_online` was hard-coded `true`.** A stalled lane still showed "running in the cloud". It is now derived from the last persisted scan time on both endpoints.

**A6 — PWA auth bypass when `PM_EDGE_SESSION_SECRET` is unset.** The cookie was `HMAC("", const)` and therefore forgeable. Auth now fails closed without a secret or passcode, and uses constant-time comparison.

**A7 — Recorder metadata ignored `status` changes.** open→closed→settled transitions were never versioned, so a backtest couldn't tell when trading stopped. This was caught by an existing failing test. Fixed.

**B1 — Watchdog now also covers the whale lanes.**
- Each lane loop heartbeats.
- A lane silent for more than max(900 s, 6 × interval) forces the same exit(70) restart.
- Heartbeats are exposed in `/v1/status`.
- The watchdog logic was refactored into `_watchdog_check` so it can be tested.

**B2 — Fail-closed scan budget** (`AUTOTRADER_VALUE_SCAN_BUDGET_SECONDS`, default 150).
- `scan_public` fetches `get_market` for **every** fair ticker, then orderbooks, sequentially.
- With a degraded API that can legitimately run past the 420 s watchdog, which would cause a restart loop. That loop could exhaust Railway's ON_FAILURE retry cap and leave the service down.
- Remaining fetches are now skipped once the budget passes, and the skips are counted in `deadline_skipped`.

**B3 — Bounded reverse reads of run logs** (`storage.tail_jsonl`).
- The learning loop re-read **four** ever-growing JSONL files from byte 0 inside every Value Edge scan.
- The status endpoints did the same on every app refresh.
- Estimated growth is tens of MB/day, mostly from QW v2 writing up to 100 decisions per minute. That gives scan time unbounded growth, which is a plausible contributor to the observed "Value Edge stall".

**B4 — Startup guard against lane bankroll contamination.** The process refuses to start if any two enabled lanes share a portfolio, runner-state or run-log path.

**Tests.** Two stale tests were corrected:
- The naive-timestamp test was a no-op after the ledger switched to `Z`.
- The exit test predated the advisory `ADD_REVIEW` action.

### Important findings not changed (recommendations)

- **I-1 (B) Fair freshness is measured against computation time, not book data time.** `fair_fetched_at = iso_now()` when the watcher builds the fair. Book `last_update` (`source_age_s`) is ignored by the Value Edge gate. Log and gate on source age.
- **I-2 (B) Quote-age budget vs. sequential fetching.** Up to 30 orderbooks are fetched one by one and aged at decision time against a 5 s limit. The highest-volume books are fetched first, so they are the most likely to be rejected as stale. Re-fetch the book for each qualified candidate immediately before the simulated fill. This is more realistic, and it recovers volume without loosening any rule.
- **I-3 (B) `sigma` never reaches Value Edge.** `candidate_tracker.FIELDS` has no `sigma` column, so every thesis uses the hard-coded 0.03. The exit engine's hold value (`fair − σ`) is therefore a constant 3¢ haircut. Wiring σ through changes exits, so ship it as a new `lane_version`.
- **I-4 (B) Team matching treats substrings as 0.96 similarity.** "Georgia" ⊂ "Georgia Tech", "Miami" ⊂ "Miami (OH)". With a 36 h time tolerance, a wrong college game can match when the real one is absent from the odds feed. Require token-boundary matches and the same local date.
- **I-5 (B) Value Edge has no upper edge cap.** The watcher skips edges above `MAX_AUTO_EDGE` = 15% as suspicious. Value Edge accepts any edge ≥ 5% when the fair is ≤ 5 min old. Given §2c, edges ≥ 15% should be shadow-logged, not traded.
- **I-6 (B) Deployment drift.** The live mobile API runs a 486-commit-old fork, so `main` fixes to `mobile_backend.py` never deploy. Each 30-minute ledger push also triggers a redeploy of that service.
- **I-7 (B) Odds budget may reset on restart.** If `AUTOTRADER_LEDGER` is left at its relative default, the refresh watch state lives on the container's ephemeral filesystem. The daily Odds-API counter can then reset on each restart or redeploy. Set `AUTOTRADER_LEDGER=/data/pm_edge_candidate_ledger.csv`. (This is unverified because Railway was not visible.)
- **I-8 (B) Run logs have no retention.** `/data/*-runs.jsonl` grows without limit on a small volume. Add daily rotation with gzip.
- **I-9 (B) CLV is almost never computable for paper lanes.** The recorder only snapshots the top-20 two-sided markets by volume. Snapshot every open paper position's book at T−10 min and at event start. Whale theses have no `event_start_at` at all.
- **I-10 (A, not applied) QW v2 "wait_for_reaction" discards flows.** Flows younger than 8 s are rejected, but their trade IDs are marked seen, so they are never re-evaluated. That contradicts the rule's own name. I didn't apply this fix because it changes trade selection mid-sample (the 25-trade review breaker is running). Fix it at the next lane version.
- **I-11 (B) Trade tape is truncated.** `whale_edge._public_trades` makes one page call of 1,000 trades with no cursor. In busy windows, older trades in the 20-minute lookback are silently dropped and flows are split.
- **I-12** `test_pm_edge_commit6.py` fails on `main`: it expects moneyline-only bulk refresh, but the watcher now bulk-requests spreads and totals. Budget accounting is correct (it uses `x-requests-last`). Decide whether the test or the policy is right.
- **I-12b (B) A test writes into the real fill ledger.** Running `test_performance_tracker.py` appends fixture rows to the committed `pm_edge_fill_ledger.csv`. Any workflow that runs the tests and then commits ledgers would contaminate real performance data. Point the test at a temp path.
- **I-13** The HTTP API is open when `AUTOTRADER_API_TOKEN` is unset (POST `run-scan` included). That is paper-only, but set the token.
- **I-14** Whale `_market_meta` caches `status` forever, so later status checks see first-seen status. This is low impact because orderbook and close-time checks catch it.

### Paper-trading realism

- **R-1** Value Edge entry fills at ask + 0.5¢ and is capped at top-of-book size. That is reasonable for size ≤ 10 contracts. The quote can be up to 5 s old (see I-2).
- **R-2 (B) Whale Flow maker fills are optimistic.**
  - Fills use `available = max(p.contracts, ask_size)`, so book depth never limits them.
  - Fill quantity is not bounded by volume traded at or below the target.
  - A fill is triggered when the ask falls to the target, which is in effect a taker fill booked as MAKE.
  - Use trade-through volume, as `recorder_store.maker_probe` already does.
- **R-3 (C) EXIT_THESIS dumps at any price.** When the fair moves more than 2σ against the position, the runner sells at bid − fee even when that is below the new fair. That turns a fair-value revision into a realized loss beyond EV.
- **R-4** Exits only happen while a fresh pregame fair exists. After start − 5 min, positions freeze and are held to settlement. That is correct given there is no live model.
- **R-5** Fees use the general 7% taker formula everywhere, including simulated maker fills. That is conservative.

---

## 5. Strategy challengers (shadow-test only; no production change)

### C1 — Value Edge: fair-age-scaled edge requirement

**Current rule:** net edge ≥ 5%. Fair age ≤ `AUTOTRADER_FAIR_MAX_AGE_SECONDS` (code default 300 s; the refresh env default suggests 2,100 s in production). Edges ≥ 15% need a fair ≤ 300 s old.

**Proposed rule:** with fair age *a* in minutes, require net edge ≥ 5% + 0.1% × max(0, *a* − 5). Also add hard rejects:
- edge ≥ 15% (log only), and
- book source age (`source_age_s`) > 15 min.

**Hypothesis:** large apparent edges are mostly stale fairs that Kalshi has already repriced.

**Evidence:** §2c — 0 of 576 rows with fair ≤ 2 h showed ≥ 5% edge, while 7.2% of the > 2 h rows did. This is from the candidate ledger, not from executed trades.

**Mechanism:** removes adverse-selection entries where Kalshi has information that the fair lacks.

**Downside:** lower volume. A genuinely slow Kalshi market can't be exploited when the fair is old.

**Backtest without look-ahead:** replay `score` rows using only `fair_fetched_at ≤ evaluated_at`. Grade each with:
- Kalshi settlement, and
- the recorder's `book_at(ticker, commence − 1 min)` closing mid, using the point-in-time accessor only.

Compare the accepted vs. rejected sets on ROI and CLV.

**Forward shadow:** log `challenger_c1_decision` beside every production decision in the Value Edge run row. Don't change fills.

**Promotion bar:** ≥ 200 settled production trades and ≥ 80 trades where the two rules disagree. CLV difference > 0 with a 95% CI excluding 0. Manual approval.

### C2 — Hold to settlement instead of EXIT_THESIS / take-profit

**Current rule:** sell when bid − fee ≥ fair − 0.03. EXIT_THESIS at a 2σ adverse fair move.

**Proposed rule:** only exit when bid − fee ≥ current fair (pure EV comparison). Never on thesis invalidation alone.

**Evidence:** none yet from data. This is EV arithmetic: each forced exit gives up (fair − (bid − fee)) per contract.

**Backtest:** for every SELL in the Value Edge ledger, compute the counterfactual settlement P/L from Kalshi results (no look-ahead, since it uses outcomes only).

**Shadow:** record the counterfactual alongside each exit.

**Promotion bar:** ≥ 100 exits.

### C3 — Spread markets: separate lane_version after the A3 fix

**Rule:** after deploying A3, tag spread entries `value_edge_v1_spreadfix`. For the first 100 settled spread trades, shadow-trade only NO-side spreads.

**Evidence:**
- Real NO spreads went 6–15 (−39%).
- The ledger shows a 74% vs 9% side asymmetry.

**Promotion bar:** 100 settled shadow trades with CLV ≥ 0.

### C4 — QW v2: re-queue "wait_for_reaction" flows (from I-10) as `quick_whale_v3`

Run it as a separate paper lane with its own bankroll, so v2's 25-trade sample stays clean.

### C5 — Price-band filter (≥ 0.60)

Not proposed for production. The ≥ 0.60 bucket is −4.9% on n = 16, which is **class D**. Keep collecting data.

### Where lower volume may help

C1 and C3. The evidence says the stale and NO-spread entries are the leak.

### Where volume can be added without lowering the bar

These are all process fixes (I-2, I-10, I-11). None of them loosens a threshold:
- **I-2:** re-fetch the book before the fill, which recovers quotes currently rejected as stale.
- **I-10:** stop discarding flows that are still inside the reaction window.
- **I-11:** paginate the trade tape.

---

## 6. Lane-by-lane status

| Lane | Entry trigger | Main rejects | Sizing | Exit | Trustworthiness of paper P/L | Data verdict |
|---|---|---|---|---|---|---|
| Value Edge | fresh fair vs. fresh ask, net ≥ 5% after fee + slippage | stale fair/quote, start − 5 min, whole-number lines, same event, same snapshot, risk caps (2% / position, 10% total of $500) | ~$10 per position at top-of-book | thesis exits (C2) or settlement | Medium; fills realistic, exits EV-leaky | **No data visible — need Railway state** |
| Whale Flow | aggregated taker flow ≥ thresholds, price ≤ flow VWAP (TAKE) or rest 1¢ better (MAKE) | fast markets, late or unverified start, same event, ref price outside 15–85¢ | ~$10 | settlement only | **Optimistic maker fills (R-2)** | No paper data; alert lane −18% (n = 27), D |
| Quick Whale (v1) | fresh fast-market flow, fill ≤ VWAP | entries disabled | ~$10 | settlement | Medium | No data; frozen — keep for history |
| Quick Whale v2 | flow aged 8–55 s, ≥ 2 trades and ≥ $500, spread ≤ 4¢, mid ≥ VWAP − 1¢, ask and fill ≤ VWAP + 2¢, ≥ 180 s to close | as listed; breaker at 25 settled or −$50 | ~$10 | settlement | Medium; no-chase logic is **genuinely enforced** (fill hard-capped at VWAP + 2¢) | No data; breaker means n ≤ 25 — **cannot conclude anything at 25** |

**Learning loop.** No path from learning output back into production rules was found. `min_net_edge` comes only from CLI/env. The learning file is only ever read for display. A regression test now pins this.

---

## 7. Next 5 actions

1. **Deploy this branch to `autotrader-paper`** and confirm in the logs:
   - `value_edge_stage` cycles every ~300 s,
   - `deadline_skipped` stays at 0 when the API is healthy, and
   - `worker_heartbeats` appear in the status output.

   Also verify these on Railway:
   - restart policy is ON_FAILURE with a high max-retries,
   - the `/data` volume is mounted,
   - `AUTOTRADER_LEDGER` points under `/data`, and
   - exactly one replica is running.
2. **Export the four lanes' `/data` portfolios and run logs** and rerun §2-style segmentation per lane. No per-lane ROI conclusion is possible until then.
3. **Ship I-2 (book re-fetch before fill) and I-9 (closing-book capture for every open position)** so paper fills and CLV become trustworthy.
4. **Start shadow C1 and C3** (stale-fair and spread checks) as logged challenger decisions on every Value Edge scan.
5. **Rebase or retire `railway/code-change-HB1cBE`** so the mobile API runs reviewed `main` code, and make it stop redeploying on every ledger push.
