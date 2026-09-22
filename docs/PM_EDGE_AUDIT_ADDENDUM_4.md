# PM EDGE — ADDENDUM 4: PRE-REGISTRATION AND FUNNEL GAPS

**Date pre-registered:** 2026-09-22  
**Mode:** Ledger/audit only. No production betting-logic changes. No Odds API credits.

## 0. Retroactive validation rule

All reconstructed figures in prior Track A reports must be re-run against the same ledger snapshot/cutoff used by the report.

Status labels:
- **EXACT** — reproduces the underlying ledger and/or stated calculation to reported rounding.
- **SUPERSEDED** — does not reconcile; corrected value becomes authoritative.
- **NOT LEDGER-RECONCILABLE** — historical field was never logged and must not be reconstructed.

Moving ledgers must not be used to invalidate an earlier report. Pin the historical snapshot by commit/blob where possible.

## 1. Maker/taker test — pre-registered before further outcomes

Do not restrict taker execution now.

Keep both maker and taker execution active at the current production size and logic until the decision point below. This is an observational pre-registration, not a randomized experiment.

**Decision point:** when the cumulative strict held-to-settlement **TAKER** count reaches **n=60**.

**Primary metric:** dollar-weighted strict-settlement ROI difference:
`Delta = ROI_TAKER - ROI_MAKER`
using all strict maker settlements available through the same cutoff.

**Decision threshold:** if `Delta < 0` when the 60th strict taker settlement is recorded, record the pre-registered trigger as met and restrict taker execution only after explicit owner approval. If `Delta >= 0`, the trigger is not met.

Secondary descriptive metrics may include W/L, CLV and calibration, but they do not replace or redefine the pre-registered trigger.

Current baseline at pre-registration:
- strict taker n=24, 4W-20L, dollar-weighted ROI -59.7%
- strict maker n=25, 10W-15L, dollar-weighted ROI +1.4%
- both cells n<30: **NOT INTERPRETABLE**

Exact ledger check of the “~46%, ~2.9 sigma” framing:
- mean taker entry price = **41.1%**, not 46%
- expected wins using each taker entry price as a naive Bernoulli probability = **9.86**
- observed wins = 4
- Poisson-binomial-style SD of win count = **2.33**
- z ≈ **-2.52**, not -2.9
- n=24 remains **NOT INTERPRETABLE**

Entry price is not model fair probability, so this z-score is descriptive only.

### Multiple-comparisons caveat

The settled sample has already been sliced by sport, price bucket, devig, regime and execution type. One striking split can appear by chance under repeated slicing.

Maker/taker receives more weight than a post-hoc cut only because the maker/taker diagnostic was specified before its result was observed. It is still confounded by market selection, time, code version and execution eligibility.

## 2. >60c funnel

Trace the exact three-devig comparable >60c Power-qualified cohort from qualification through execution. Do not use an older >60c settled order as the endpoint of this cohort unless it is actually linked to one of these alerts.

## 3. Unfilled representativeness

Compare adjudicated and unadjudicated unfilled orders on:
- event date/horizon
- sport
- entry price
- order type

State explicitly whether the adjudicated subset is representative.

## 4. Forward-logging additions

Add to the forward-logging specification only; no decision logic changes:
- maker/taker tag captured **at submission**
- Kalshi order-book depth at best bid/ask **at submission**
- quote age at submission with both source/feed timestamp and PM Edge fetch timestamp
- proportional/Power/Shin values frozen **at alert time** and linked to the signal/order so later rescans cannot contaminate flip-vs-fill analysis

## Perspective / operating posture

Ledger facts:
- strict-settlement risk: **$174.83**
- strict-settlement P/L: **-$51.62**
- strict-settlement ROI: **-29.5%**

Owner perspective may treat this as low-cost learning while the system remains at small size. Factual wording must remain precise: PM Edge has been shown to lack a designated sharp benchmark and to exclude Pinnacle under the default US-region path; the audit has **not** proven that every possible sharp sportsbook was excluded.

Promotion remains CLV-based. ROI at n=250–300 remains descriptive only.

## Unchanged constraints

Production betting logic stays as-is. Preserve the PWA/dashboard and clickable profit calendar, bearer-token auth, public `/health`, CORS, secure same-origin proxy, and the pandas object-dtype fix including `lane`.

No production threshold change. No devig change. No Pinnacle production change. No historical backfill. Flag every inferential cell with n<30 as **NOT INTERPRETABLE**.
