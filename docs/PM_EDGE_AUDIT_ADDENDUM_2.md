# PM EDGE — ADDENDUM 2: PINNACLE BENCHMARK, SHARP MOVES, SHADOW LAYER

**Status:** Audit and shadow only.  
**Production rule:** No production betting logic changes without explicit owner approval.

## Finding that changes the priority order

The production watcher does not set `ODDS_REGIONS` and therefore inherits the code default of `us`. The Odds API currently lists Pinnacle under the `eu` region, so the current production fair-value path is **not Pinnacle-anchored** and does not contain a designated sharp-book benchmark.

Audit implication: treat the lack of a sharp anchor as a leading candidate explanation for negative realized ROI. The current broad US-region consensus may share market information and retail-flow effects with Kalshi, so measured Kalshi-vs-consensus divergence may not be independent edge. This is a hypothesis to test directly, not an assumption that every US-region bookmaker is recreational or equally soft.

## Track A — runnable now, no API spend

Deliver this first. Do not wait on Track B.

The candidate ledger stores proportional, Power, and Shin fair values for candidates after the devig diagnostics became populated.

Requirements:
- Compare all three methods on identical candidate rows. No per-method surviving-bet subsets.
- Report per-candidate fair/edge shifts and test whether those shifts correlate with realized outcomes and CLV where matched data exist.
- Break out by Kalshi price bucket and favorite/underdog.
- Do not assume one method is universally superior.
- Flag every inferential cell with n < 30 as **NOT INTERPRETABLE**.
- Repeated half-hour rescans are not independent bets. Row-level comparisons may measure deterministic method shifts, but outcome/CLV inference must use unique signal-level observations.

## Track B — blocked by missing historical bookmaker quotes

The ledger does not preserve individual historical bookmaker quotes, so historical Pinnacle-only fair values cannot be reconstructed from PM Edge's database alone.

Requirements:
- Report historical Pinnacle reconstruction as **NOT AVAILABLE HISTORICALLY** from the existing PM Edge ledgers.
- Do not estimate unlogged Pinnacle prices.
- Before spending Odds API credits, verify the actual account plan and whether historical access is included.
- Historical backfill must not run without explicit owner approval.
- Report expected quota cost before any backfill.

Current provider documentation indicates:
- Historical odds require a paid usage plan.
- Historical odds cost 10 credits per region/bookmaker-group per market.
- A direct `bookmakers=pinnacle` request counts as one bookmaker group (up to 10 bookmakers is equivalent to one region), therefore one historical snapshot for one market is expected to cost 10 credits if data are returned.
- A candidate needing multiple market queries/snapshots can cost more. Batch featured-market reconstruction by sport/timestamp when possible rather than blindly paying per candidate.
- The repository/workflow references a 500-credit monthly budget, but the actual subscription tier must be independently verified before concluding historical access exists.

## Pinnacle feed delay — test before building Sharp Moves

The Odds API states that Pinnacle odds are sourced from Pinnacle's public website and may incur delay.

Before a Sharp Moves screen is promoted beyond audit/shadow:
- Log bookmaker market `last_update` / provider timestamp where supplied.
- Log PM Edge fetch timestamp separately.
- Measure the observed Pinnacle feed-age distribution.
- Measure Kalshi response/convergence time after genuine sharp-market moves.
- If Kalshi convergence is typically faster than Pinnacle feed age, label the proposed Sharp Moves signal structurally unreachable using this feed. Do not ship a misleading UI signal.

## Shadow-layer evaluation — avoid the hypothetical-portfolio trap

- Evaluate shadow candidates at the executable Kalshi ask for the intended side and realistic size.
- Capture order-book depth at best bid/ask; do not assume quoted price is fillable for intended size.
- Primary comparison metric is CLV, not hypothetical ROI.
- Use the same closing reference and price convention for shadow and live candidates.
- The only permitted live-vs-shadow execution difference is actual fill price versus attempted/executable quote.
- Do not use midpoint or last trade as a substitute for executable entry.

## One qualifying benchmark, not four

The app may display:
- Pinnacle fair
- sharp consensus
- broad consensus
- Kalshi price

But configuration must designate exactly **one** qualifying benchmark. All other fair values are diagnostic context only and must not opportunistically enter qualify/reject logic.

During this pass the production qualifying benchmark remains unchanged.

## Forward logging — every candidate

### 1. Kalshi
Log:
- bid
- ask
- midpoint
- volume
- best-bid depth
- best-ask depth
- intended size
- executable average price for intended size when depth requires walking levels
- exchange timestamp if available
- PM Edge fetch timestamp

### 2. Pinnacle
Log:
- raw odds
- proportional fair
- Power fair
- Shin fair
- bookmaker/market last-update timestamp
- PM Edge fetch timestamp

### 3. Broad consensus
Log:
- proportional fair
- Power fair
- Shin fair
- individual contributing bookmaker keys/titles
- each contributing raw quote
- dispersion
- region/bookmaker request used
- provider timestamps and PM Edge fetch timestamp

### 4. Sharp consensus
Log:
- Pinnacle
- Circa if available
- every other book explicitly classified as sharp
- exact weight assigned to each source
- exact devig method
- resulting fair value

### 5. Movement
Log/derive:
- Pinnacle 30 minutes prior and current
- Kalshi 30 minutes prior and current
- Pinnacle delta
- Kalshi delta
- divergence/lag
- feed age at both observations

### 6. Outcome
Log:
- attempted entry
- filled / partial / unfilled
- actual fill price and size
- closing Kalshi price using the agreed closing convention
- Pinnacle close using the same market/side definition
- CLV
- result
- realized P/L

### 7. Submission-time execution snapshot — Addendum 4
Log at order submission:
- explicit `execution_type_at_submission`: MAKER / TAKER
- Kalshi best bid price and depth
- Kalshi best ask price and depth
- intended order size
- source/feed quote timestamp when supplied
- PM Edge fetch timestamp for the quote/order-book snapshot
- order submission timestamp
- computed quote age at submission

### 8. Alert-time frozen devig snapshot — Addendum 4
Log at alert creation, and link durably to signal/order:
- proportional fair
- Power fair
- Shin fair
- active production devig method
- active qualifying benchmark
- net edge under each devig method using the same alert-time trade state
- alert timestamp

Never use a later rescan to reconstruct alert-time devig flip status.

### 9. Alert disposition — Addendum 4
For every sent alert, persist one terminal/intermediate disposition with timestamp and reason:
- submitted
- owner_skipped
- approval_expired
- approval_dismissed
- submit_failed
- superseded
- other

This field is required to distinguish intentional non-execution from manual/tooling loss in the alert-to-attempt funnel.


### Addendum 4 forward-logging requirements

In addition to the fields above, freeze the following at the moment they become knowable:
- maker/taker execution tag **at order submission**, not reconstructed only from fills;
- Kalshi best bid/ask depth **at order submission** plus intended size and executable average price for that size;
- quote-age components at submission: provider/feed timestamp, PM Edge fetch timestamp, and submission timestamp;
- Proportional, Power and Shin fair values **at alert time**, linked by immutable signal id to the eventual order. Never substitute a later rescan for the alert-time values.

These are logging/audit fields only and do not alter qualification, pricing, sizing or execution decisions.

### 10. Qualified-to-gate disposition — Addendum 4
For every score row that is marked qualified, persist whether downstream gate evaluation occurred and the terminal disposition:
- gate_evaluated = true/false
- gate decision / suppression reason when evaluated
- explicit no-gate reason when gate evaluation does not occur
- disposition timestamp

This prevents qualified observations from disappearing between qualification and alert gating without an auditable reason.

## Promotion sample requirement

Do not promote a Pinnacle/sharp benchmark to production because it looks favorable after a few dozen bets.

Working minimum:
- **250 unique settled shadow candidates** before considering benchmark promotion,
- preferably **300+**,
- with at least **75 CLV-observed candidates** in any core sport/market bucket relied upon for a bucket-specific claim.

At an expected 5–25 raw candidates per day, deduplication, market coverage, settlement, and missing sharp-book quotes mean a decision-quality sample is expected to take **weeks to months**, not days.

Promotion still requires:
- positive/credible CLV versus the current benchmark,
- acceptable calibration,
- no evidence the result is driven by one sport/price bucket,
- execution/depth realism,
- and owner approval.

## Scope

In scope for this pass:
- +EV audit/shadow tooling
- Sharp Moves research/logging
- Market Tracker audit/shadow functionality
- Analytics

Out of scope:
- DFS/fantasy lineup optimization

## Unchanged constraints

- Production betting decisions remain unchanged during this pass.
- Preserve the PWA/dashboard.
- Preserve the clickable profit calendar.
- Preserve bearer-token authentication.
- Preserve public `/health`.
- Preserve CORS behavior.
- Preserve the secure same-origin proxy.
- Preserve the pandas object-dtype fix, including `lane`.
- Import-test `mobile_backend.py` before any backend deployment.
- Production backend deploy target remains `mobile-backend-live`.
- Never reconstruct missing historical data by assumption.
- Report missing historical data as **NOT AVAILABLE HISTORICALLY** and convert each missing field into a forward-logging requirement.
