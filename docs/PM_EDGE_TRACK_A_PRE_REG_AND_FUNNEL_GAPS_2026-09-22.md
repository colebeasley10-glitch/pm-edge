# PM Edge — Addendum 4 Results: Pre-registration and Funnel Gaps

**Audit date:** 2026-09-22  
**Scope:** Ledger + repository history only. No production logic changes. No Odds API credits.

## 0. Retroactive validation of prior reconstructed figures

Validation is pinned to the ledger snapshot used by the earlier Track A reports. For candidate-ledger calculations, the pinned blob is `34988c3be90657260147f8633c3ee95c709591b2`, which contains 4,950 rows and the original 2,960 three-devig scoring observations.

### Preliminary Track A report

| Figure checked | Prior value | Re-run | Status |
|---|---:|---:|---|
| candidate rows | 4,950 | 4,950 | EXACT |
| score rows | 3,855 | 3,855 | EXACT |
| rows with all 3 devigs | 3,738 | 3,738 | EXACT |
| score rows with all 3 devigs | 2,960 | 2,960 | EXACT |
| unique usable ticker/side/lane | 309 | 309 | EXACT |
| **unique qualifying ticker/side/lane** | **126** | **124** | **SUPERSEDED** |
| identities with nonblank recommended limit | not separately labeled | 126 | EXACT clarification |
| MLB unique usable identities | 143 | 143 | EXACT |
| NPB unique usable identities | 65 | 65 | EXACT |
| NFL unique usable identities | 55 | 55 | EXACT |
| WNBA unique usable identities | 25 | 25 | EXACT |
| KBO unique usable identities | 21 | 21 | EXACT |
| h2h unique usable identities | 179 | 179 | EXACT |
| total unique usable identities | 74 | 74 | EXACT |
| spread unique usable identities | 56 | 56 | EXACT |
| mean Power-Prop shift | +0.26pp | +0.260pp | EXACT |
| median Power-Prop shift | +0.17pp | +0.174pp | EXACT |
| Power-Prop p10 | -1.54pp | -1.543pp | EXACT |
| Power-Prop p90 | +2.11pp | +2.107pp | EXACT |
| mean abs Power-Prop | 1.31pp | 1.307pp | EXACT |
| max abs Power-Prop | 13.40pp | 13.396pp | EXACT |
| mean Shin-Power shift | -0.07pp | -0.069pp | EXACT |
| median Shin-Power | -0.05pp | -0.053pp | EXACT |
| Shin-Power p10 | -0.74pp | -0.744pp | EXACT |
| Shin-Power p90 | +0.78pp | +0.784pp | EXACT |
| mean abs Shin-Power | 0.59pp | 0.588pp | EXACT |
| max abs Shin-Power | 9.89pp | 9.892pp | EXACT |
| underdog observations | 1,281 | 1,281 | EXACT |
| underdog Power-Prop | -1.10pp | -1.098pp | EXACT |
| underdog Shin-Power | +0.47pp | +0.468pp | EXACT |
| underdog mean abs Power-Prop | 1.24pp | 1.245pp | EXACT |
| favorite observations | 1,679 | 1,679 | EXACT |
| favorite Power-Prop | +1.30pp | +1.296pp | EXACT |
| favorite Shin-Power | -0.48pp | -0.479pp | EXACT |
| favorite mean abs Power-Prop | 1.35pp | 1.355pp | EXACT |
| <=30c rows / shifts | 150 / -0.67 / +0.30pp | 150 / -0.667 / +0.296pp | EXACT |
| 31–45c rows / shifts | 322 / +0.29 / -0.03pp | 322 / +0.290 / -0.035pp | EXACT |
| 46–60c rows / shifts | 288 / +1.05 / -0.47pp | 288 / +1.046 / -0.473pp | EXACT |
| >60c rows / shifts | 250 / +2.27 / -0.82pp | 250 / +2.268 / -0.824pp | EXACT |
| settled signal outcomes | 16 | 16 | EXACT |
| sent rows with 3 devigs | 14 | 14 | EXACT |
| settled matched to 3 devigs | 6 | 6 | EXACT |
| Prop Brier / log loss | .2300 / .6533 | .229991 / .653333 | EXACT |
| Power Brier / log loss | .2199 / .6331 | .219941 / .633145 | EXACT |
| Shin Brier / log loss | .2283 / .6499 | .228268 / .649944 | EXACT |
| Power-Prop vs win correlation | +.649 | +.6489 | EXACT |
| Shin-Power vs win correlation | -.647 | -.6467 | EXACT |
| CLV rows | 10 | 10 | EXACT |
| devig-comparable matched CLV | 3 | 3 | EXACT |
| Power-Prop vs CLV correlation | +.539 | +.5387 | EXACT |
| Shin-Power vs CLV correlation | -.537 | -.5372 | EXACT |

**Correction:** the preliminary report's “126 unique qualifying identities” is **SUPERSEDED**. The ledger has **124** identities with `decision=QUALIFIED`; 126 is the count with a nonblank recommended limit.

### Immediate-follow-up report — funnel and realized history

| Figure checked | Prior value | Re-run | Status |
|---|---:|---:|---|
| scored observations | 3,855 | 3,855 | EXACT |
| qualified observations | 1,321 | 1,321 | EXACT |
| unique scored identities | 389 | 389 | EXACT |
| unique qualified identities | 178 | 178 | EXACT |
| unique sent identities | 23 | 23 | EXACT |
| candidate sent observations | 25 | 25 | EXACT |
| ALERT_ELIGIBLE observations | 27 | 27 | EXACT |
| suppressed gate observations | 1,043 | 1,043 | EXACT |
| score reject: audit restriction/threshold | 1,597 | 1,597 | EXACT |
| score reject: lane threshold/price guard | 937 | 937 | EXACT |
| cooldown suppressions | 558 | 558 | EXACT |
| duplicate/correlation/position suppressions | 295 | 295 | EXACT |
| event-cap suppressions | 188 | 188 | EXACT |
| run-cap suppressions | 2 | 2 | EXACT |
| signal records | 31 | 31 | EXACT |
| distinct signal keys | 30 | 30 | EXACT |
| signal records matching an order | 16 | 16 | EXACT |
| matched distinct signal keys | 16 | 16 | EXACT |
| unmatched distinct signal keys | 14 | 14 | EXACT |
| signal-linked attempted orders | 17 | 17 | EXACT |
| signal-linked fills | 2 | 2 | EXACT |
| signal-linked unfilled | 15 | 15 | EXACT |
| all attempted orders | 70 | 70 | EXACT |
| all filled orders | 54 | 54 | EXACT |
| strict settlements | 49 | 49 | EXACT |
| closed early | 5 | 5 | EXACT |
| total realized | 54 | 54 | EXACT |
| open filled | 0 | 0 | EXACT |
| unfilled | 16 | 16 | EXACT |
| all-history fill rate | 77.1% | 54/70 = 77.1% | EXACT |
| **calling 77.1% the “old cohort” fill rate** | 77.1% | pre-v28 = 52/53 = 98.1% | **SUPERSEDED interpretation** |
| realized risk | $194.90 | $194.9001 | EXACT |
| realized P/L | -$28.03 | -$28.0346 | EXACT |
| realized ROI | -14.38% | -14.384% | EXACT |
| strict risk | $174.83 | $174.8301 | EXACT |
| strict P/L | -$51.62 | -$51.6225 | EXACT |
| strict ROI | -29.5% | -29.527% | EXACT |
| Sep 19 P/L | +$11.37 | +$11.37 | EXACT |
| “no winning days” | false after correction | false | EXACT correction |
| approximate 95% realized-ROI CI | -44.4% to +15.8% | reproduces to rounding under stated iid approximation | EXACT to rounding |

### Immediate-follow-up report — old threshold-flip section

The entire old threshold reconstruction is **SUPERSEDED** except where the scalar happened to remain unchanged.

| Figure | Old | Correct | Status |
|---|---:|---:|---|
| Power qualified | 1,006 | 1,006 | EXACT |
| Proportional qualified | 905 | 669 | SUPERSEDED |
| Shin qualified | 969 | 842 | SUPERSEDED |
| Power not Proportional | 103 | 339 | SUPERSEDED |
| Proportional not Power | 2 | 2 | EXACT |
| Power not Shin | 37 | 164 | SUPERSEDED |
| Shin not Power | 0 | 0 | EXACT |
| Shin not Proportional | 66 | 206 | SUPERSEDED |
| Proportional not Shin | 2 | 33 | SUPERSEDED |
| favorite Power qualified | 760 | 760 | EXACT |
| favorite Prop qualified | 657 | 437 | SUPERSEDED |
| favorite Shin qualified | 723 | 597 | SUPERSEDED |
| favorite Power-not-Prop | 103 | 323 | SUPERSEDED |
| favorite Power-not-Shin | 37 | 163 | SUPERSEDED |
| underdog Power qualified | 246 | 246 | EXACT |
| underdog Prop qualified | 248 | 232 | SUPERSEDED |
| underdog Shin qualified | 246 | 245 | SUPERSEDED |
| underdog Power-not-Prop | 0 | 16 | SUPERSEDED |
| underdog Prop-not-Power | 2 | 2 | EXACT |
| >60c Power qualified | 250 | 250 | EXACT |
| >60c Prop qualified | 170 | 13 | SUPERSEDED |
| >60c Shin qualified | 235 | 142 | SUPERSEDED |
| >60c Power-not-Prop | 80 | 237 | SUPERSEDED |
| >60c Power-not-Shin | 15 | 108 | SUPERSEDED |

Corrected threshold reconstruction matches the ledger's **1,006 Power-qualified rows exactly**.

### Power-analysis figures

| Figure | Prior | Re-run | Status |
|---|---:|---:|---|
| observed CLV n | 10 | 10 | EXACT |
| observed CLV mean | +4.25c | +4.25c | EXACT |
| observed CLV SD | 4.39c | 4.39c | EXACT |
| planning CLV SD | 8c | assumption | EXACT assumption |
| n=250 CLV MDE | 1.42c | 1.418c | EXACT |
| n=300 CLV MDE | 1.29c | 1.294c | EXACT |
| per-order ROI SD | ~117.1pp | ~117.1pp | EXACT |
| n for 2pp ROI difference | ~26,900 | ~26,900 | EXACT to rounding |

### Addendum 3 figures

| Figure checked | Prior | Re-run | Status |
|---|---:|---:|---|
| unfilled orders | 16 | 16 | EXACT |
| adjudicable unfilled | 9 | 9 | EXACT |
| adjudicated hypothetical record | 3–6 | 3–6 | EXACT |
| adjudicated hypothetical risk | $25.17 | $25.17 | EXACT |
| adjudicated hypothetical P/L | -$2.17 | -$2.17 | EXACT |
| adjudicated hypothetical ROI | -8.6% | -8.6% | EXACT |
| current-regime adjudicable n | 8 | 8 | EXACT |
| current-regime hypothetical record | 2–6 | 2–6 | EXACT |
| current-regime risk | $21.21 | $21.21 | EXACT |
| current-regime P/L | -$7.21 | -$7.21 | EXACT |
| current-regime ROI | -34.0% | -34.0% | EXACT |
| unfilled with CLV row | 1 | 1 | EXACT |
| that CLV | +6.0c | +6.0c | EXACT |
| pre-v28 attempts/fills/unfilled | 53 / 52 / 1 | 53 / 52 / 1 | EXACT |
| pre-v28 fill rate | 98.1% | 98.1% | EXACT |
| pre-v28 strict settlements | 47 | 47 | EXACT |
| pre-v28 strict risk / P&L / ROI | $169.71 / -$46.26 / -27.3% | same to rounding | EXACT |
| v28-era attempts/fills/unfilled | 17 / 2 / 15 | 17 / 2 / 15 | EXACT |
| v28-era fill rate | 11.8% | 11.8% | EXACT |
| modern MAKE attempts/fills | 15 / 0 | 15 / 0 | EXACT |
| modern TAKE attempts/fills | 2 / 2 | 2 / 2 | EXACT |
| modern Price Fish attempts/fills | 14 / 0 | 14 / 0 | EXACT |
| maker strict n / W-L | 25 / 10-15 | 25 / 10-15 | EXACT |
| maker strict risk / P&L / ROI | $86.34 / +$1.21 / +1.4% | same | EXACT |
| taker strict n / W-L | 24 / 4-20 | 24 / 4-20 | EXACT |
| taker strict risk / P&L / ROI | $88.49 / -$52.83 / -59.7% | same | EXACT |
| <=30c strict bucket | 12, 2-10, $19.22, -$5.57, -29.0% | same | EXACT |
| 31–45c strict bucket | 18, 5-13, $60.29, -$16.26, -27.0% | same | EXACT |
| 46–60c strict bucket | 18, 6-12, $85.48, -$31.92, -37.3% | same | EXACT |
| >60c strict bucket | 1, 1-0, $9.84, +$2.13, +21.6% | same | EXACT |
| stake min / median / mean / max | $.02 / $3.78 / $3.57 / $9.84 | same to rounding | EXACT |
| largest-five risk / share | $30.75 / 17.6% | same | EXACT |
| corrected Power / Prop / Shin qualified | 1,006 / 669 / 842 | same | EXACT |
| corrected Power-not-Prop | 339 | 339 | EXACT |
| >60c Power-not-Prop | 237 | 237 | EXACT |
| >60c Power-not-Prop share | 94.8% | 237/250 = 94.8% | EXACT |
| pre-alert devig-classifiable attempts | 5 | 5 | EXACT |
| classifiable flip attempts | 0 | 0 | EXACT |
| classifiable non-flip attempts/fills | 5 / 2 | 5 / 2 | EXACT |
| classifiable non-flip settled record | 0–2 | 0–2 | EXACT |

### Validation conclusion

Three items are materially **SUPERSEDED**:
1. preliminary “126 unique qualifying identities” -> **124 actually qualified**;
2. treating 77.1% as an “old-cohort” fill rate -> **98.1% pre-v28**;
3. the entire old 103/905/969 threshold-flip reconstruction -> corrected 339/669/842 framework.

All other checked numerical figures reproduce the pinned ledgers to their reported rounding.

## 1. Maker/taker pre-registration

No execution restriction is made now.

At the registration date:
- TAKER strict settlements: **n=24**, 4W-20L, -59.7% dollar-weighted ROI
- MAKER strict settlements: **n=25**, 10W-15L, +1.4% dollar-weighted ROI
- both n<30 -> **NOT INTERPRETABLE**

The exact taker entry-price check does not reproduce the “~46%, ~2.9 SD” framing:
- mean taker entry = **41.08%**
- sum of naive entry-implied win probabilities = **9.86 expected wins**
- observed = **4 wins**
- win-count SD = **2.325**
- z = **-2.52**

This is **NOT INTERPRETABLE (n=24)** and entry price is not model fair probability.

Entry fees:
- taker strict entry fees = about **3.78% of taker risk**
- maker strict entry fees = about **0.52% of maker risk**
- historical spread/slippage counterfactual is **NOT AVAILABLE HISTORICALLY**

Thus the ledger can verify that fees are much higher for takers, but it cannot attribute the full outcome gap to costs.

### Pre-registered rule

Date: **2026-09-22**

- Keep maker and taker paths unchanged at current production size.
- Evaluate when cumulative strict TAKER settlements reach **n=60**.
- Primary metric: `ROI_TAKER - ROI_MAKER`, dollar-weighted, through the same cutoff.
- Trigger: if that value remains **< 0** at the 60th taker settlement, record the trigger as met; any production restriction still requires explicit approval.
- Do not move the decision point, metric or sign threshold after seeing intermediate results.

The 24 existing taker settlements count toward the 60, leaving **36 additional strict taker settlements** before the registered decision point.

## 2. The >60c funnel hole

This analysis uses exactly the original **250** >60c, three-devig-comparable Power-qualified score observations.

### Raw-observation funnel

| Stage | Count | Drop |
|---|---:|---:|
| Power-qualified >60c score observations | **250** | — |
| gate observations found | **249** | 1 missing gate record |
| ALERT_ELIGIBLE | **1** | — |
| ALERT_SUPPRESSED | **248** | — |
| sent alert | **1** | 249/250 did not become sent alerts |
| attempted order linked to that sent alert | **0** | 1/1 did not become an order |
| filled | **0** | — |
| settled | **0** | — |

Suppression reasons among the 248:
- **cooldown_active: 214**
- **event_cap: 34**

One qualified score observation has **no recorded gate row**. Its reason is **NOT AVAILABLE HISTORICALLY**.

Unique-opportunity view:
- 25 unique ticker/side/lane/price identities qualified
- 24 reached a gate record
- 1 unique sent alert
- 0 submitted orders

The one sent alert was:
- NFL ATL/GB market
- BUY YES
- Price Fish
- MAKE
- **68c**
- no `client_order_id` / submission record

### Critical correction

The **single >60c settled bet** in the 49-settlement historical bucket is from the older pre-candidate-instrumentation order cohort. It is **not** a descendant of these 250 score observations.

Therefore the earlier narrative “250 Power-qualified >60c observations -> 1 settled bet” is **SUPERSEDED**.

The correct current-cohort funnel is:

> **250 score observations -> 1 alert -> 0 attempts -> 0 fills -> 0 settlements.**

The primary recorded hole is **qualification -> alert**, driven mostly by cooldown/event-cap suppression of repeated observations. The only sent >60c alert then stopped at **alert -> attempt**.

## 3. Are the 9 adjudicated unfilled orders representative?

No.

### Adjudicated 9

- horizon: **9/9 same-day**
- sports: NCAAF 1, NFL 1, NPB 3, WNBA 4
- order type: MAKE 8, historical type unavailable 1
- entry-price mean: **33.7c**
- median: **32c**
- range: 21c–44c

### Unadjudicated 7

- horizon: **2 same-day, 4 one-day, 1 three-day**
- sports: NFL 1, NPB 3, MMA 2, WNBA 1
- order type: **MAKE 7/7**
- entry-price mean: **42.1c**
- median: **42c**
- range: 21c–72c

Five of seven unadjudicated orders are for events one to three days after the trade date, while all nine adjudicated orders are same-day.

The unresolved set also contains both MMA orders and has a materially higher average attempted price.

Therefore:

> The 3–6, -8.6% hypothetical result for the adjudicated nine **does not generalize even as a representative hint to all 16 unfilled orders**.

It is evidence about a short-horizon, lower-priced subset. Treat it as **NOT INTERPRETABLE**.

## 4. Forward-logging additions

The forward-logging specification is amended to require:

1. **Submission execution tag**
   - maker/taker stored on the order at submission time
   - do not reconstruct only from later fills

2. **Kalshi depth at submission**
   - best bid price and depth
   - best ask price and depth
   - intended shares
   - executable average price for intended size

3. **Quote age at submission**
   - source/feed timestamp
   - PM Edge fetch timestamp
   - order-submission timestamp
   - derived quote age

4. **Alert-time devig freeze**
   - Proportional, Power and Shin captured on the signal row at alert time
   - linked by immutable signal id to the eventual order
   - later rescans must never backfill the alert-time devig values

These are logging/audit requirements only. No betting decision changes are authorized.

## Perspective

Strict-settlement ledger:
- risked **$174.83**
- lost **$51.62**
- ROI **-29.5%**

The small dollar scale limits the financial impact of the current learning phase, but it does not make losing trades desirable. The audit posture remains: keep sizing unchanged during this pre-registered observation period and make promotion/restriction decisions from the registered evidence rather than short-run ROI.

The factual sharp-benchmark finding is narrower than “every sharp book was excluded”: the current production path lacks a designated sharp anchor and, under the default US-region configuration, does not include Pinnacle. The broader claim is not proven from the ledger.

No production change is authorized by Addendum 4.


## Addendum 5 amendment — detectable effect size and interpretation guard

This amendment is pre-registered before the TAKER n=60 decision point. It supplements the existing sign trigger and prevents a small noisy difference from being read as evidence that taker execution is acceptable.

Planning assumptions:
- per-bet ROI SD: **117pp**
- strict TAKER count at decision point: **60**
- planning strict MAKER count at the same cutoff: approximately **25-40**
- independent-bet normal approximation used only for power sanity-checking

Computed standard error:

`SE(Delta_ROI) = 117pp * sqrt(1/60 + 1/n_MAKER)`

- with n_MAKER=25: **27.9pp**
- with n_MAKER=40: **23.9pp**
- conservative shorthand: approximately **25-30pp**

Two-standard-error discernibility is therefore about **47.8-55.7pp**. Freeze the audit's minimum detectable-effect interpretation threshold at **60pp absolute Delta_ROI**.

**Pre-registered interpretation rule:**
- if `|ROI_TAKER - ROI_MAKER| < 60pp` at TAKER n=60, the result is **INCONCLUSIVE**;
- an inconclusive result is **not evidence that taker execution is acceptable**;
- the existing operational sign trigger `Delta_ROI < 0` remains recorded separately, and any production restriction still requires explicit owner approval;
- do not move the 60pp threshold after seeing the n=60 result.

For context, the conventional two-sided alpha=0.05 / 80%-power MDE under the same approximation is approximately **66.9-78.0pp** for n_MAKER=40 to 25. A true 60pp gap is about **2.15-2.51 SE**, corresponding to only roughly **58%-71% power**. Thus n=60 can surface the currently observed ~60pp-scale gap at roughly two standard errors, but it cannot resolve materially smaller effects reliably.

This does not contradict the earlier ~26,900-settlement calculation, which was sized for a **2pp** effect rather than a ~60pp effect.
