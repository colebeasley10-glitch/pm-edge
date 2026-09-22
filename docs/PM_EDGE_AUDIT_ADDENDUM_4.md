# PM EDGE — ADDENDUM 4: PRE-REGISTRATION AND FUNNEL GAPS

**Pre-registered:** 2026-09-22 10:29 America/New_York (14:29 UTC)  
**Mode:** Ledger/audit only. No production betting-logic changes. No Odds API credits. Continue trading at the current tiny size to generate data.

## 0. Retroactive validation

All reconstructed ledger figures in prior Track A reports were re-run against the same ledger snapshot/cutoff used by each report.

Status:
- **EXACT** — reconciles to ledger/calculation within displayed rounding.
- **SUPERSEDED** — does not reconcile or mixed incompatible cohorts.
- **NOT AVAILABLE HISTORICALLY** — required historical field was never logged.

Moving ledgers are not used to invalidate older reports. The exhaustive figure-by-figure reconciliation is in:
`docs/PM_EDGE_RETRO_VALIDATION_2026-09-22.md`.

Material supersessions found:
1. Preliminary report "126 unique qualifying identities" -> **124**. The old 126 count was unique identities with a nonblank recommended limit, not unique qualified identities.
2. The old immediate-follow-up threshold-flip Section 3 is **SUPERSEDED IN FULL**. Correct snapshot values are Power 1,006; Proportional 669; Shin 842; Power-not-Proportional 339.
3. The statement "250 >60c Power-qualified observations -> 1 settled bet" mixed cohorts. The one >60c strict settlement belongs to the older execution cohort, not the 250-row three-devig candidate cohort.

## 1. Maker/taker test — pre-registered, do not restrict taker yet

Keep BOTH maker and taker execution running at the current tiny size.

Historical baseline at registration:
- maker strict settlements: n=25, 10W-15L, +1.4% dollar-weighted ROI
- taker strict settlements: n=24, 4W-20L, -59.7% dollar-weighted ROI
- both cells n<30: **NOT INTERPRETABLE**

The maker/taker assignment was not randomized and overlaps v26/v27/v28, sport, market, lane and time differences.

Multiple-comparisons caveat:
the same 49 strict settlements have already been sliced by sport, price bucket, devig, regime and execution type. A striking split is easier to find after many cuts. Maker/taker receives more weight only because this diagnostic was specified before its result was inspected.

### Exact check of the rough "46%, 2.9 sigma" statement

Using actual taker fill prices:
- mean taker entry price: **41.1%**, not 46%
- entry-price-implied expected wins: **9.86**
- observed wins: **4**
- expected-win SD: **2.33**
- standardized deficit: **-2.52 SD**, not -2.9

This calculation is descriptive only. Entry price is not true probability and order outcomes are not guaranteed independent.

### Pre-registered decision rule

**Decision point:** cumulative strict settled TAKER count reaches **n=60**.

**Primary metric:** dollar-weighted strict-settlement ROI difference at that cutoff:
`Delta_ROI = ROI_TAKER - ROI_MAKER`

Use all strict maker settlements available through the same cutoff.

**Planning volatility:** per-bet realized ROI SD = **117.1 percentage points**, frozen from the 54-realized-order planning sample already validated in the audit.

**Power / detectable-effect guardrail at TAKER n=60:**
- with MAKER n=25, SE(`Delta_ROI`) = **27.9pp**;
- with MAKER n=30, SE(`Delta_ROI`) = **26.2pp**;
- with MAKER n=40, SE(`Delta_ROI`) = **23.9pp**;
- therefore the expected SE range for a maker arm of roughly 25-40 is **23.9-27.9pp**;
- a 60pp gap is about **2.15-2.51 SE**, so it is detectable only at roughly the two-SE level;
- the corresponding two-sided 95% 1.96-SE resolution is **46.8-54.6pp**;
- the conventional two-sided alpha=0.05, 80%-power MDE is **67.0-78.1pp** across MAKER n=40 down to n=25.

At the n=60 cutoff, recompute the SE using the actual strict MAKER count available through the same cutoff and the frozen 117.1pp planning SD. Define the pre-registered 80%-power MDE as:
`MDE_80 = (1.96 + 0.8416) * SE(Delta_ROI)`.

**Interpretation rule:** any observed absolute ROI difference smaller than the applicable `MDE_80` is **INCONCLUSIVE** for claims that taker execution is acceptable. A result such as -20pp at n=60 must not be read as vindication of takers. The original sign trigger below is recorded separately from this power statement.

**Trigger:**
- if `Delta_ROI < 0` at TAKER n=60, record the pre-registered taker-deficit trigger as met and restrict taker execution only after explicit owner approval;
- if `Delta_ROI >= 0`, the trigger is not met;
- failure to meet the sign trigger is **not** affirmative evidence that taker execution is acceptable when the observed difference is inside the MDE guardrail.

This does not contradict the earlier approximately 26,900-settlement calculation: that calculation targeted a **2pp** ROI effect at alpha=0.05 and 80% power, whereas this frozen n=60 test can only resolve effects on the order of many tens of percentage points.

Secondary diagnostics:
- maker/taker CLV
- outcome rate vs entry-implied expectation
- fees/spread
- sport/market/lane mix
- calibration

Do not redefine the n=60 trigger, primary metric, planning SD, or MDE rule after seeing interim results.

Persist maker/taker **at submission**, not reconstructed later from fills.

## 2. The >60c funnel hole

Population: the original 2,960 three-devig-comparable scoring observations.

Correct >60c population:
- Power-qualified observations: **250**
- Power-not-Proportional: **237 / 250 = 94.8%**
- Proportional-qualified: 13

The single >60c strict settlement in the 49-settlement historical execution ledger is from the earlier pre-candidate-instrumentation cohort. It is **not** linked to these 250 observations. Therefore the old implied "250 -> 1 settled" funnel is **SUPERSEDED**.

### Same-cohort raw funnel

- Power-qualified >60c observations: **250**
- matching gate rows: **249**
  - ALERT_SUPPRESSED: **248**
    - cooldown_active: **214**
    - event_cap: **34**
  - ALERT_ELIGIBLE: **1**
- qualified observation with no gate row: **1**
  - reason: **NOT AVAILABLE HISTORICALLY**
- sent alerts: **1**
- attempted orders from that sent alert: **0**
- filled: **0**
- settled: **0**

Unique opportunity view:
- qualified ticker/side/lane/price identities: **25**
- identities reaching a gate row: **24**
- alert-eligible identities: **1**
- sent identities: **1**
- attempted: **0**

What eliminates the bucket:
1. Most repeated >60c qualifications are suppressed by cooldown/event-cap policy.
2. Only one actual alert from the 250-row cohort was sent.
3. That alert never became an order.
4. The reason for the sent-alert -> no-order drop is **NOT AVAILABLE HISTORICALLY**.

Forward requirement: every alert needs a durable disposition:
`submitted | owner_skipped | approval_expired | approval_dismissed | submit_failed | superseded | other`,
with timestamp and reason.

## 3. The 9 adjudicated vs 7 unadjudicated unfilled orders

Event date is derived only from the date code already present in the logged Kalshi ticker.

### Adjudicated 9
Horizon:
- same-day: **9 / 9**

Sport:
- NCAAF 1
- NFL 1
- NPB 3
- WNBA 4

Attempted price:
- min 21c
- median 32c
- mean 33.7c
- max 44c

Execution:
- MAKE 8
- one legacy order: execution type **NOT AVAILABLE HISTORICALLY**

### Unadjudicated 7
Horizon:
- same-day: **2**
- +1 day: **4**
- +3 days: **1**

Sport:
- NFL 1
- NPB 3
- WNBA 1
- MMA 2

Attempted price:
- min 21c
- median 42c
- mean 42.1c
- max 72c

Execution:
- MAKE 7 / 7

### Representativeness conclusion

The adjudicated subset is **not representative by event horizon**:
- adjudicated: 9/9 same-day
- unadjudicated: 5/7 one to three days out

It also differs in sport mix and price distribution.

Therefore Addendum 3's 3-6 / -8.6% hypothetical gross ROI result **must not be generalized to all 16 unfilled orders, even as a directional hint**. It describes the short-horizon subset that had resolved by that cutoff.

## 4. Forward-logging additions

Add to the existing forward-logging specification only. These fields do not alter betting decisions.

### At order submission
- `execution_type_at_submission`: MAKER / TAKER
- best bid price
- best-bid depth
- best ask price
- best-ask depth
- intended order size
- source/feed quote timestamp when supplied
- PM Edge fetch timestamp
- order submission timestamp
- computed quote age at submission

### At alert time
Freeze and persist:
- proportional fair
- Power fair
- Shin fair
- active devig method
- active qualifying benchmark
- net edge under each devig using the same alert-time trade state
- alert timestamp

Never classify an alert's historical devig flip using a later rescan.

## Perspective / operating posture

Strict held-to-settlement history:
- risked: **$174.83**
- P/L: **-$51.62**
- ROI: **-29.5%**
- n=49

This is a small dollar cost for discovering major measurement and benchmark issues while stakes are intentionally tiny.

Factual precision: the audit has verified that production was not Pinnacle-anchored and had no designated sharp-book benchmark under the default US-region path. It has **not** proven that every constituent sportsbook was recreational.

Continue at the current tiny size for data generation. Do not increase size based on short-run ROI. Benchmark promotion remains CLV-based; ROI at n=250-300 is descriptive only.

## Unchanged constraints

- Production betting logic stays unchanged.
- No threshold change.
- No devig change.
- No Pinnacle production change.
- No Odds API historical spend.
- Preserve PWA/dashboard and clickable profit calendar.
- Preserve bearer-token auth, public `/health`, CORS and secure same-origin proxy.
- Preserve pandas object-dtype fix including `lane`.
- Flag inferential cells n<30 as **NOT INTERPRETABLE**.
- Report unlogged historical fields as **NOT AVAILABLE HISTORICALLY**.
- Import-test before any runtime backend deployment.
- Runtime deploy target remains `mobile-backend-live`.
- No runtime deployment is required for this documentation-only addendum.
