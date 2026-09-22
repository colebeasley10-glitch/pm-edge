> **CORRECTION / SUPERSEDED SECTION:** The threshold-flip counts in Section 3 below were reconstructed with an incorrect Price Fish edge floor. Addendum 3 re-ran the calculation against the current code/workflow and reproduced the ledger's 1,006 Power-qualified rows exactly. The corrected Power-vs-Proportional count is 339 Power-only qualifiers (not 103), including 237 in the >60c bucket. Use `PM_EDGE_TRACK_A_FILL_RATE_INVESTIGATION_2026-09-22.md` for the corrected threshold-flip analysis. Sections 1, 2, and 4 remain unchanged.

# PM Edge — Immediate Follow-ups to Track A Preliminary

**Cutoff:** 2026-09-22 13:13 UTC  
**Mode:** Audit only. No production logic changes. No Odds API credits spent. No new logging added.

## 1. Funnel counts

### Important instrumentation caveat

The ledgers do not all start on the same date:

- order ledger: 2026-09-17 through 2026-09-22
- signal ledger: 2026-09-20 through 2026-09-22
- candidate/score ledger: 2026-09-21 00:45 UTC through 2026-09-22 13:13 UTC

Therefore a literal all-time stage-to-stage funnel is not cohort-consistent. All available records are within the last 30 days, so **all-time and last-30-day raw totals are identical**. The report gives both the raw ledger totals and the comparable modern signal-linked cohort.

### Raw ledger totals — all-time = last 30 days

| Stage | Raw count | Notes |
|---|---:|---|
| scored observations | 3,855 | full candidate score-stage ledger |
| three-devig-comparable score observations | 2,960 | Track A subset only |
| qualified score observations | 1,321 | repeated rescans included |
| actual signal records / alerts | 31 | unique signal_id records |
| attempted orders | 70 | unique client_order_id values |
| filled orders | 54 | filled_shares > 0 |
| held-to-settlement | 49 | settled_win / settled_loss |
| closed early | 5 | realized before settlement |
| total realized/closed | 54 | 49 settled + 5 closed early |
| open filled positions | 0 | none currently open |
| unfilled orders | 16 | status=unfilled |

Because the alert and order instrumentation were introduced at different times, the 31 alerts and 70 attempted orders must **not** be interpreted as a single monotonic historical funnel.

### Candidate-era unique identities

Repeated 30-minute rescans substantially inflate raw score counts. Using unique ticker + side + lane identities:

| Stage | Unique identities | Drop |
|---|---:|---:|
| scored | 389 | — |
| qualified | 178 | -211 (-54.2%) |
| sent/alerted in candidate ledger | 23 | -155 (-87.1%) |

The qualified-to-alert drop is largely explained by recorded policy gates rather than threshold failure.

### Score-stage rejection reasons

Raw rejected score observations: 2,534.

| Reason | Observations | Unique candidate identities |
|---|---:|---:|
| audit_restriction_or_threshold | 1,597 | 169 |
| below_lane_threshold_or_price_guard | 937 | 67 |

These identity counts can overlap across rescans/reasons and should not be summed as independent opportunities.

### Alert-gate suppression reasons

Raw suppressed gate observations: 1,043.

| Reason | Observations | Unique candidate identities |
|---|---:|---:|
| cooldown_active | 558 | 62 |
| policy_duplicate_correlation_or_position_guard | 295 | 54 |
| event_cap | 188 | 66 |
| run_cap | 2 | 2 |

There were 27 ALERT_ELIGIBLE observations and 25 candidate-ledger SENT observations, but both represent 23 unique ticker/side/lane identities. The two-observation raw difference is rescanning/duplication, not evidence of two lost unique alerts.

### Alert -> attempted — modern trackable cohort

Best-effort existing-ledger join using ticker + side + recommended/limit price:

- signal records: 31
- distinct signal keys: 30
- signal records with a matching order: 16
- distinct signal keys with a matching order: 16
- distinct signal keys without a matching order: 14

Approximate unique-key alert-to-attempt drop:
**14 / 30 = 46.7%**

There is no recorded failure/rejection reason for alerts that never became an order. Existing data cannot distinguish owner skip, expired approval, deliberate pass, or another manual-execution reason.

### Attempted -> filled — signal-linked cohort

Orders matching a recorded signal by ticker + side + price:

- attempted: 17
- filled: 2
- unfilled: 15
- fill rate: **11.8%**
- attempted-to-filled drop: **88.2%**
- both filled orders subsequently settled

No more specific unfilled reason is recorded in the existing order ledger; only status=unfilled is available.

### Attempted -> filled — all order history

- attempted: 70
- filled: 54
- unfilled: 16
- all-history fill rate: **77.1%**
- attempted-to-filled drop: **22.9%**

This is materially different from the modern signal-linked cohort. Fifteen of the sixteen unfilled orders in the full execution ledger belong to the current signal-linked cohort.

### Funnel conclusion

Do not infer more than the counts support:

1. Score -> qualify is not an orders-of-magnitude loss once repeated rescans are removed.
2. Qualified -> alert is a large drop, but the recorded causes are predominantly policy/cooldown/event-cap suppression rather than a model threshold failure.
3. Alert -> attempt loses roughly half of distinct modern signal keys; no reason is recorded for non-attempts.
4. **Attempt -> fill is the largest measurable bottleneck in the modern signal-linked cohort: 17 attempts -> 2 fills.**
5. The legacy all-history order cohort does not show the same fill problem, so this should be treated as a recent/current execution finding, not a timeless system property.
6. Filled -> realized currently has no attrition: all 54 filled orders in the ledger are closed/realized, with 49 held to settlement and 5 closed early.

## 2. Total settled population

Execution ledger coverage: **2026-09-17 through 2026-09-22**.

Current totals:
- attempted orders: 70
- filled: 54
- strict held-to-settlement: 49
- closed early: 5
- total realized/closed: **54**
- open filled: **0**
- unfilled: **16**

Realized results:
- settled wins: 14
- settled losses: 35
- closed-early realized positions: 5
- total amount risked across the 54 realized orders: **$194.90**
- total realized P/L: **-$28.03**
- aggregate realized ROI: **-14.38%**

Approximate 95% confidence interval for aggregate ROI:
**-44.4% to +15.8%**

Method: ratio-of-sums bootstrap / delta-method cross-check across the 54 realized orders. The two methods agree almost exactly. This interval treats orders as independent; correlated bets/events make the effective sample size smaller, so the true uncertainty can be wider.

Conclusion:
**54 realized orders is still a small statistical sample for a high-variance binary-contract strategy. The observed negative ROI is not statistically distinguishable from zero at conventional confidence. The project premise is neither validated nor disproven by this sample.**

The phrase "no winning days" does **not** match the current execution ledger. By trade date:
- 2026-09-17: -$13.41
- 2026-09-18: -$15.88
- 2026-09-19: **+$11.37**
- 2026-09-20: -$4.75
- 2026-09-21: -$5.36

There is one positive realized trade date out of five. Five daily observations are also far too few for inference.

## 3. Threshold-flip analysis

### Method

Scope: the same 2,960 three-devig-comparable scoring observations.

For each row, hold the recorded trade side/execution state constant and shift the active Power net-edge estimate by the difference between the alternative method's side-adjusted fair probability and Power's side-adjusted fair probability.

This is an **edge-threshold counterfactual**, not a full re-pricing of both YES/NO sides. The old ledger does not preserve both sides of the Kalshi order book for every score row, so exact counterfactual side-switching cannot be reconstructed.

Production lane/bucket edge floors are respected:
- unrestricted Primary: 4.0% with 4+ books; 4.5% with 3; 6.0% below 3
- Standard: 3.0% with 4+ books; 3.5% with 3
- Price Fish: 2.0% with 4+ books; 2.5% with 3; 4.0% below 3
- restricted MLB / NFL team total Primary: 7.5%, 4+ books
- restricted KBO Primary: 6.5%, 4+ books
- restricted secondary lanes and restricted rows below the minimum-book rule are structurally blocked

754 of the 2,960 observations are structurally blocked by current lane/audit rules regardless of devig method.

### Total threshold qualification counts

| Method | Threshold-qualified observations |
|---|---:|
| Power | **1,006** |
| Shin | **969** |
| Proportional | **905** |

### Pairwise flips

| Comparison | Count | Share of the method's qualified set |
|---|---:|---:|
| Power qualifies, Proportional does not | **103** | **10.24% of Power qualifiers** |
| Proportional qualifies, Power does not | **2** | **0.22% of Proportional qualifiers** |
| Power qualifies, Shin does not | **37** | **3.68% of Power qualifiers** |
| Shin qualifies, Power does not | **0** | **0.00% of Shin qualifiers** |
| Shin qualifies, Proportional does not | **66** | **6.81% of Shin qualifiers** |
| Proportional qualifies, Shin does not | **2** | **0.22% of Proportional qualifiers** |

Unique candidate identities exhibiting at least one flip:
- Power-not-Proportional: 15
- Proportional-not-Power: 2
- Power-not-Shin: 7
- Shin-not-Power: 0
- Shin-not-Proportional: 9
- Proportional-not-Shin: 2

### Favorite vs underdog

Favorite/underdog is defined using the side-adjusted Power fair probability.

#### Favorites — 1,679 observations
- Power qualified: 760
- Shin qualified: 723
- Proportional qualified: 657
- Power-not-Proportional: **103**
- Power-not-Shin: **37**
- Shin-not-Proportional: **66**
- Proportional-not-Power: 0

#### Underdogs — 1,281 observations
- Power qualified: 246
- Shin qualified: 246
- Proportional qualified: 248
- Proportional-not-Power: **2**
- Proportional-not-Shin: **2**
- Power-not-Proportional: 0
- Power-not-Shin: 0

This is consistent with the expected longshot-bias correction behavior: Power moves favorite-side probabilities upward and underdog-side probabilities downward relative to Proportional.

### Kalshi price bucket

A legitimate recorded attempted/recommended price exists for 1,010 of the 2,960 observations. The remaining 1,950 cannot be assigned an actual Kalshi price bucket without inventing an unlogged price. All 1,006 Power threshold-qualified observations do have a recorded recommended limit; the two Proportional-only flips do not.

| Price bucket | n | Power q | Prop q | Shin q | P not Prop | P not Shin | Shin not Prop |
|---|---:|---:|---:|---:|---:|---:|---:|
| <=30c | 150 | 150 | 150 | 150 | 0 | 0 | 0 |
| 31–45c | 322 | 321 | 312 | 312 | 9 | 9 | 0 |
| 46–60c | 288 | 285 | 271 | 272 | 14 | 13 | 1 |
| >60c | 250 | 250 | 170 | 235 | **80** | **15** | **65** |

Power-vs-Proportional flip share among Power qualifiers:
- <=30c: 0%
- 31–45c: 2.8%
- 46–60c: 4.9%
- >60c: **32.0%**

Power-vs-Shin flip share among Power qualifiers:
- <=30c: 0%
- 31–45c: 2.8%
- 46–60c: 4.6%
- >60c: 6.0%

### Threshold-flip conclusion

Devig choice materially affects qualification near the edge floor, especially for higher-priced favorite-side contracts. However, production already uses Power. The direction of the differences is consistent with Power's expected longshot-bias correction, not indiscriminate probability inflation. Therefore devig choice is operationally important but is a weaker standalone explanation for the observed negative ROI than it first appeared.

This increases the priority of the funnel/execution finding, particularly current fillability.

## 4. Power analysis for the 250–300 shadow target

### Promotion metric

The 250–300 settled-shadow target is powered on **CLV**, not ROI.

Statistical design assumption:
- two-sided alpha = 0.05
- power = 80%
- conservative per-bet CLV standard deviation for planning = **8 cents**

Current observed CLV sample:
- n = 10
- mean = +4.25c
- observed SD = 4.39c

The observed SD is too unstable at n=10 to use as the planning assumption, so 8c is used conservatively.

Detectable mean CLV shift under the 8c assumption:
- n=250: approximately **1.42 cents**
- n=300: approximately **1.29 cents**

For reference only, if the current 4.39c observed SD were stable, the detectable shifts would be about 0.78c at n=250 and 0.71c at n=300. Do not plan around that until the CLV sample is much larger.

### ROI power

Observed standard deviation of per-order realized ROI across the current 54 realized orders:
**117.1 percentage points**.

At alpha=0.05, 80% power, and that observed volatility, the approximate independent-bet sample required to detect a **2 percentage-point mean ROI difference** is:

**~26,900 settled bets**

Correlated positions would increase the required sample further.

Therefore:
**At n=250–300, ROI is descriptive only and must not be used as the promotion criterion. Promotion of a Pinnacle/sharp benchmark at that sample size must be based on CLV, calibration, execution realism, and stability across relevant buckets.**

## Recorded context

Production already defaults to Power.

Power pushing favorite-side probabilities upward and underdog-side probabilities downward versus Proportional is consistent with the expected correction for favorite-longshot bias. PM Edge is therefore not currently using the most mechanically longshot-inflating devig option.

That weakens devig choice as a primary explanation for negative ROI while leaving it operationally important around qualification thresholds. The immediate audit priority shifts toward:
1. alert gating and manual attempt conversion,
2. and especially the recent attempted-to-filled collapse.

No production change is authorized by this report.
