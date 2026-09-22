# PM Edge — Fill-Rate Investigation (Addendum 3 Results)

**Cutoff:** 2026-09-22  
**Mode:** Ledger/commit-history audit only. No production changes. No Odds API credits. No new logging.

## Executive conclusion

The prior statement that the recent low fill rate is the primary defect to fix is **reversed**.

The existing data does **not** support increasing fill rate.

Key evidence:
- Of 16 unfilled orders, only 9 are currently adjudicable from existing ledgers.
- Those 9 would have gone **3–6**, risking $25.17 for **-$2.17 gross P/L (-8.6% gross ROI)** at the exact attempted prices.
- Restricting to the eight adjudicable post-v28/current-regime unfilled orders gives **2–6, -$7.21 on $21.21 risked (-34.0% gross ROI)**.
- Seven unfilled orders are unresolved or lack historical outcomes in the ledger.
- The modern matched cohort is dominated by passive maker execution: 15 MAKE attempts, 0 fills; 2 TAKE attempts, 2 fills, and both TAKE fills lost.
- Across the 49 strict settlements, maker fills were approximately break-even/slightly positive (+1.4% dollar-weighted ROI, n=25), while taker fills were strongly negative (-59.7%, n=24). Both n<30 cells are NOT INTERPRETABLE statistically, but the descriptive split does **not** support "maker fills are the historical loss source."

Therefore low fill rate may currently be protective. Do not loosen limits or chase the ask based on fill rate alone.

# 1. The 16 unfilled orders

## Outcome availability

Total unfilled orders: **16**

Currently adjudicable from ledger evidence: **9**
- 8 have exact-market outcomes in `pm_edge_signal_outcomes.csv`.
- 1 additional order (Akron/Minnesota spread) can be adjudicated because an opposite-side order on the exact same Kalshi ticker settled as a loss, which implies the unfilled YES side won.

Not currently adjudicable: **7**
- future/not-yet-settled markets and/or no exact historical result preserved in the current ledger.

Do not infer those seven.

## Adjudicable unfilled counterfactual

Hypothetical P/L uses:
- requested shares,
- exact attempted limit,
- binary $1 payout,
- **gross** P/L before hypothetical fees.

Unfilled orders incurred no actual fee, and an exact counterfactual fee is not stored. Therefore net-after-fee hypothetical P/L is NOT AVAILABLE HISTORICALLY.

| Order | Price | Shares | Result | Hypothetical P/L |
|---|---:|---:|---|---:|
| Akron/Minnesota spread YES | 44c | 9 | WIN | +$5.04 |
| NYG/LAR game NO | 21c | 7 | LOSS | -$1.47 |
| Hiroshima game YES | 32c | 8 | LOSS | -$2.56 |
| Yokohama + spread YES | 30c | 8 | LOSS | -$2.40 |
| Orix/Hokkaido total NO | 42c | 9 | LOSS | -$3.78 |
| Atlanta/NY WNBA game NO | 39c | 9 | LOSS | -$3.51 |
| Dallas/Phoenix game NO | 27c | 7 | WIN | +$5.11 |
| Dallas/Phoenix game NO | 26c | 7 | WIN | +$5.18 |
| Atlanta/NY WNBA spread NO | 42c | 9 | LOSS | -$3.78 |

Totals:
- n = **9**
- hypothetical record = **3–6**
- hypothetical risk = **$25.17**
- hypothetical gross P/L = **-$2.17**
- hypothetical gross ROI = **-8.6%**

Current-regime/post-v28 adjudicable subset:
- n = **8**
- record = **2–6**
- risk = **$21.21**
- gross P/L = **-$7.21**
- gross ROI = **-34.0%**

### CLV

Only **1 of the 16** unfilled orders currently has a closing-price/CLV row:
- Orix/Chiba NPB game NO at 40c
- closing midpoint 46c
- CLV **+6.0c**
- outcome not yet adjudicated in the current ledger at this cutoff.

The nine adjudicated unfilled orders do not have exact-price CLV rows in the current CLV ledger.

### Item 1 conclusion

The available data leans toward:

> **Recent non-fills saved money rather than left obvious profitable trades behind.**

But n=9 is too small to resolve the question statistically, and 7/16 remain unresolved.

Status: **NOT INTERPRETABLE for production action.**

No fill-rate remediation is justified.

# 2. Cause of the regime change

## Cohort correction

The earlier "old cohort = 77.1% fill rate" wording mixed old and new orders.

Using September 21 (v28 deployment date) as the regime split:

### Pre-v28: Sep 17–20
- attempts: **53**
- fills: **52**
- unfilled: **1**
- fill rate: **98.1%**
- strict settlements: 47
- strict-settlement risk: $169.71
- strict-settlement P/L: -$46.26
- strict-settlement ROI: **-27.3%**

### v28/current: Sep 21–22
- attempts: **17**
- fills: **2**
- unfilled: **15**
- fill rate: **11.8%**
- strict settlements so far: 2
- both lost

This is a real execution-regime break.

## Direct code/config changes

### Sep 17, 2026 — approval-gated TAKE/MAKE execution
Commit `6c7f004d45825921b05a2290c10ffdf2a30ffe21`  
"Add approval-gated Kalshi submit client"

Introduced:
- TAKE -> immediate-or-cancel marketable limit
- MAKE -> good-till-canceled, post-only limit

This created fundamentally different fill mechanics.

### Sep 18, 2026 — adverse-selection/quality guard
Commit `7c610d184f2e1881d4a6d5327833fe835d7a031b`  
"Add PM Edge v26 quality and correlation guards"

Added:
- exact-target revalidation for old Price Fish limits,
- bookmaker-dispersion guard,
- minimum book count for Price Fish,
- fair-value age guard,
- one-active-entry-per-event guard.

The stated purpose in code is explicitly to close an adverse-selection hole where an old resting price remained active after fair value moved against it.

### Sep 19, 2026 — maker order lifetime
Commit `f9ddb75d82b3db7e5d76a8e0f06291c41b5ed30d` briefly set maker orders to a five-minute expiry.

Two minutes later, commit `626aec97a5b1519ec60eeaa422e4118394c4fa1a` changed:
- pregame MAKE orders -> expire at event start,
- live MAKE orders -> short TTL (default 180 seconds).

### Sep 20, 2026 — v27 restricted weak buckets
Commit `b89550d3ff3a3329eb9d8cbecb67216b5e74ce98`

Changed:
- MLB and NFL team totals -> Primary only, 7.5%+ edge, 4+ books, half size
- KBO -> Primary only, 6.5%+ edge, 4+ books, half size
- kept v26 adverse-selection controls.

This changed market composition but did not itself make maker prices more aggressive.

### Sep 21, 2026 — v28 Price Fish broadening
Commit `9a174ac80941d6b6edb049ffcc072f60cf9f24ee`  
"Deploy PM Edge v28 broader coverage"

Changed Price Fish from:
- **1–5c below ask** -> **2–8c below ask**
- minimum target **25c -> 20c**
- minimum modeled side probability **30% -> 25%**
- max Price Fish alerts/run increased.

This is the clearest direct explanation for the fill-rate collapse:
- it deliberately moved passive Price Fish limits **farther below the live ask**,
- and increased the share of opportunities using that passive lane.

## Modern execution composition

Among the 17 current signal-linked attempts:
- MAKE: **15**, filled **0**
- TAKE: **2**, filled **2**
- Price Fish: **14**, filled **0**
- Primary: 3 orders, including the two TAKE fills and one MAKE unfilled

The two TAKE orders were duplicate exposure on the same NFL spread opportunity; both settled as losses.

Thus the current 11.8% fill rate is primarily a consequence of **post-only maker/Price Fish composition**, not the same execution style suddenly failing to fill.

## Historical data limits

### Order size versus book depth
**NOT AVAILABLE HISTORICALLY.**
The ledgers store requested shares/price but not order-book depth at submission.

### Kalshi quote age at submission
**NOT AVAILABLE HISTORICALLY.**
The historical ledgers do not preserve both:
- the source quote timestamp used to price the order, and
- the exact order-submission timestamp for every unfilled order.

Fair-value fetch time is not equivalent to Kalshi quote age.

### Exact ask-to-limit gap for every old submitted order
Not available for the old cohort from the order ledger alone. v28 config proves the permitted envelope changed, but exact historical ask at submission was not preserved.

# 3. Variance and sizing diagnostics

Population: **49 strict held-to-settlement orders** (14W / 35L).

## Return definitions

Dollar-weighted/aggregate ROI:
- total risk = **$174.83**
- total P/L = **-$51.62**
- dollar-weighted ROI = **-29.5%**

Unweighted per-bet ROI:
- mean per-bet ROI = **-36.0%**
- median per-bet ROI ≈ **-100.9%**

The unweighted number is economically less useful because a $0.02 position and a $9.84 position receive equal weight.

## Entry-price buckets

| Entry price | n | W-L | Risk | P/L | Dollar-weighted ROI | Interpretation |
|---|---:|---:|---:|---:|---:|---|
| <=30c | 12 | 2-10 | $19.22 | -$5.57 | -29.0% | NOT INTERPRETABLE |
| 31–45c | 18 | 5-13 | $60.29 | -$16.26 | -27.0% | NOT INTERPRETABLE |
| 46–60c | 18 | 6-12 | $85.48 | -$31.92 | -37.3% | NOT INTERPRETABLE |
| >60c | 1 | 1-0 | $9.84 | +$2.13 | +21.6% | NOT INTERPRETABLE |

No bucket has n>=30.

The largest dollar loss is in 46–60c because that bucket carried the most risk, but its ROI is not dramatically separated from the <=45c buckets at this sample size. There is no statistically defensible single-price-bucket culprit yet.

## Stake distribution

Strict settlements:
- minimum risk: **$0.02**
- median risk: **$3.78**
- mean risk: **$3.57**
- maximum risk: **$9.84**
- largest five positions risked: **$30.75**
- largest five = **17.6% of total strict-settlement risk**

The result is not dominated by one or two giant bets.

## Maker versus taker — important descriptive split

Strict settlements only:

### Maker
- n=25
- 10W / 15L
- risk $86.34
- P/L **+$1.21**
- dollar-weighted ROI **+1.4%**
- **NOT INTERPRETABLE (n<30)**

### Taker
- n=24
- 4W / 20L
- risk $88.49
- P/L **-$52.83**
- dollar-weighted ROI **-59.7%**
- **NOT INTERPRETABLE (n<30)**

Including five early exits:
- maker realized ROI: +11.9%, n=27
- taker realized ROI: -38.4%, n=27
- both remain n<30.

This does not prove maker execution is superior, but it directly contradicts the simplistic claim that historical losses are the signature of maker-fill adverse selection. The historical dollar loss is descriptively concentrated in taker execution.

# 4. Devig flips versus fill data

## Correction to the prior threshold-flip report

The earlier immediate-follow-up report reconstructed Price Fish edge floors too loosely.

Current code behavior:
- Price Fish sets `SPECULATIVE_MIN_EDGE = MIN_EDGE`
- current `MIN_EDGE = 4.0%`
- book-count floors then apply.
- current Standard edge workflow is 3.0% with 4+ books / 3.5% with 3 books.

Using the corrected thresholds reproduces the candidate ledger exactly:
- actual Power-qualified rows among the 2,960 comparable observations: **1,006**
- reconstructed Power-qualified rows: **1,006**

Therefore these corrected figures supersede the old threshold-flip counts.

Corrected totals:
- Power qualified: **1,006**
- Proportional qualified: **669**
- Shin qualified: **842**
- Power qualifies / Proportional rejects: **339**
- Proportional qualifies / Power rejects: **2**
- Power qualifies / Shin rejects: **164**
- Shin qualifies / Power rejects: **0**

Power-over-Proportional flips:
- favorites: 323
- underdogs: 16

By recorded attempted-price bucket:
- <=30c: 0
- 31–45c: 25
- 46–60c: 77
- >60c: **237**

At >60c:
- Power qualifiers: 250
- Proportional qualifiers: 13
- Power-not-Proportional: **237 (94.8% of Power qualifiers)**

This is substantially larger than the prior 80/103 figure and is the correct current-threshold reconstruction.

## Can flip status be linked to actual fills?

Only attempted orders whose three devig values existed **before the alert** can be classified without look-ahead.

Of the 17 current signal-linked attempts:
- classifiable from pre-alert devig data: **5**
- not classifiable without using a later rescan: **12**

The three-devig fields began populating after many September 21 alerts were already sent.

### Classifiable attempted set

Flip population:
- attempted: **0**
- fill rate: N/A
- realized result: N/A

Non-flip population:
- attempted: **5**
- filled: **2**
- fill rate: **40%**
- realized: 2
- record: **0–2**
- risk: $5.12
- P/L: -$5.36
- ROI: -104.8% including entry fees
- **NOT INTERPRETABLE (n=5)**

By price:
- <=30c non-flip: n=1, 0 fills — NOT INTERPRETABLE
- 31–45c non-flip: n=4, 2 fills, both losses — NOT INTERPRETABLE
- no classifiable 46–60c or >60c attempts at this cutoff.

### Item 4 conclusion

The requested flip-vs-fill test is **NOT AVAILABLE HISTORICALLY for 12/17 current attempts** and **NOT INTERPRETABLE** for the five that are classifiable.

No Power-vs-Proportional flip order appears in the classifiable attempted set, so current fill data cannot tell us whether Power-only qualifiers fill or perform differently.

# Priority conclusion

1. **Do not fix the fill rate.**
2. The adjudicable unfilled subset is losing, which suggests non-fills may be protective.
3. The execution regime changed materially: v28 increased passive Price Fish distance from the ask and current attempts are overwhelmingly MAKE/post-only.
4. Historical losses are descriptively concentrated in TAKE/taker fills, not maker fills.
5. Threshold/devig choice remains operationally material—more than the prior report stated after correcting the Price Fish threshold reconstruction—but the fill cross-reference is not yet historically complete enough to adjudicate it.
6. No production change, threshold change, devig change, or Pinnacle work is authorized by this audit.
