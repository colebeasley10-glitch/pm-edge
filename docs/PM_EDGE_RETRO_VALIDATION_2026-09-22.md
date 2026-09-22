# PM Edge — Retroactive Validation of Reconstructed Figures

**Validation date:** 2026-09-22  
**Scope:** Ledger-derived reconstructions in the Track A preliminary, immediate follow-up and Addendum 3 reports.

## Method

The candidate ledger is moving. Validation uses the original snapshot used by the reports, not today's later file.

Pinned states:
- candidate blob: `34988c3be90657260147f8633c3ee95c709591b2`
- signal ledger: `6f71d18287e6956064a23aedb0150d048e7a87a6`
- auto/order ledger: `af73f767058579123effe9683fcd482390dd05bb`
- fill ledger: `c312086e5b65df210a9c18d52c6ae915fa7926c0`
- signal outcomes: `08c944193d3ae082e36cfa8bcbfe59a3ebe36687`
- CLV ledger: `a6c29d0d36c0ea8152037fd9ef14ad813e2967e2`

**EXACT** means the figure recomputes to reported rounding.  
**SUPERSEDED** means the reconstruction/denominator was wrong.  
External Odds API pricing/plan statements are not ledger reconstructions and are not part of this table.

# A. Track A preliminary

Population:
- candidate rows 4,950 — **EXACT**
- score rows 3,855 — **EXACT**
- all-three-devig rows 3,738 — **EXACT**
- all-three-devig score rows 2,960 — **EXACT**
- unique usable ticker/side/lane identities 309 — **EXACT**
- unique qualifying identities 126 — **SUPERSEDED; correct 124**
  - 126 = unique identities with nonblank recommended limit
  - 124 = unique actually Power-qualified / decision=QUALIFIED identities

Unique sport counts:
- MLB 143 — **EXACT**
- NPB 65 — **EXACT**
- NFL 55 — **EXACT**
- WNBA 25 — **EXACT**
- KBO 21 — **EXACT**

Unique market counts:
- h2h 179 — **EXACT**
- total 74 — **EXACT**
- spread 56 — **EXACT**

Devig shifts:
- Power-Prop mean +0.26pp — **EXACT**
- median +0.17pp — **EXACT**
- p10 -1.54pp — **EXACT**
- p90 +2.11pp — **EXACT**
- mean absolute 1.31pp — **EXACT**
- max absolute 13.40pp — **EXACT**
- Shin-Power mean -0.07pp — **EXACT**
- median -0.05pp — **EXACT**
- p10 -0.74pp — **EXACT**
- p90 +0.78pp — **EXACT**
- mean absolute 0.59pp — **EXACT**
- max absolute 9.89pp — **EXACT**

Favorite/underdog:
- favorites n=1,679 — **EXACT**
- favorite Power-Prop +1.30pp — **EXACT**
- favorite Shin-Power -0.48pp — **EXACT**
- favorite mean abs Power-Prop 1.35pp — **EXACT**
- underdogs n=1,281 — **EXACT**
- underdog Power-Prop -1.10pp — **EXACT**
- underdog Shin-Power +0.47pp — **EXACT**
- underdog mean abs Power-Prop 1.24pp — **EXACT**

Price-bucket shift rows:
- <=30c n150, -0.67pp / +0.30pp — **EXACT**
- 31-45c n322, +0.29pp / -0.03pp — **EXACT**
- 46-60c n288, +1.05pp / -0.47pp — **EXACT**
- >60c n250, +2.27pp / -0.82pp — **EXACT**

Outcome/CLV:
- signal outcomes 16 — **EXACT**
- sent rows with all three devigs 14 — **EXACT**
- matched settled rows 6 — **EXACT**
- Brier Prop 0.2300 / Power 0.2199 / Shin 0.2283 — **EXACT**
- log loss Prop 0.6533 / Power 0.6331 / Shin 0.6499 — **EXACT**
- Power-Prop shift vs win +0.649 — **EXACT**
- Shin-Power shift vs win -0.647 — **EXACT**
- CLV rows 10 — **EXACT**
- matched CLV rows 3 — **EXACT**
- Power-Prop vs CLV +0.539 — **EXACT**
- Shin-Power vs CLV -0.537 — **EXACT**

# B. Immediate follow-up report

Funnel:
- score 3,855 — **EXACT**
- qualified 1,321 — **EXACT**
- unique scored 389 — **EXACT**
- unique qualified 178 — **EXACT**
- unique candidate-sent 23 — **EXACT**
- candidate sent observations 25 — **EXACT**
- ALERT_ELIGIBLE 27 — **EXACT**
- suppressed gates 1,043 — **EXACT**

Score reasons:
- audit_restriction_or_threshold 1,597 observations / 169 identities — **EXACT**
- below_lane_threshold_or_price_guard 937 / 67 — **EXACT**

Gate reasons:
- cooldown 558 / 62 — **EXACT**
- duplicate/correlation/position 295 / 54 — **EXACT**
- event cap 188 / 66 — **EXACT**
- run cap 2 / 2 — **EXACT**

Signal/order join:
- signal records 31 — **EXACT**
- distinct signal keys 30 — **EXACT**
- signal records with matching order 16 — **EXACT**
- matched distinct keys 16 — **EXACT**
- unmatched distinct keys 14 — **EXACT**
- linked attempts 17 — **EXACT**
- linked fills 2 — **EXACT**
- linked unfilled 15 — **EXACT**

All order history:
- attempted 70 — **EXACT**
- filled 54 — **EXACT**
- unfilled 16 — **EXACT**
- strict settlements 49 — **EXACT**
- closed early 5 — **EXACT**
- total realized 54 — **EXACT**
- open filled 0 — **EXACT**
- strict record 14W-35L — **EXACT**
- strict risk $174.83 — **EXACT**
- strict P/L -$51.62 — **EXACT**
- strict ROI -29.5% — **EXACT**
- strict approximate 95% CI -60.2% to +1.2% — **EXACT**
- total realized risk $194.90 — **EXACT**
- total realized P/L -$28.03 — **EXACT**
- total realized ROI -14.38% — **EXACT**
- realized approximate 95% CI -44.5% to +15.7% — **EXACT**
- Sep 19 realized +$11.37 — **EXACT**

Threshold-flip Section 3:
- old Proportional qualified 905 — **SUPERSEDED; correct 669**
- old Shin qualified 969 — **SUPERSEDED; correct 842**
- old Power-not-Proportional 103 — **SUPERSEDED; correct 339**
- old Power-not-Shin 37 — **SUPERSEDED; correct 164**
- old >60c Power-not-Proportional 80 — **SUPERSEDED; correct 237**
- old favorite/underdog and price-bucket flip counts/percentages derived from those floors — **SUPERSEDED**
- Power-qualified 1,006 — **EXACT**, but by itself did not validate the alternative-method reconstruction

Corrected snapshot:
- Power 1,006 — **EXACT**
- Proportional 669 — **EXACT**
- Shin 842 — **EXACT**
- Power-not-Proportional 339 — **EXACT**
- Proportional-not-Power 2 — **EXACT**
- Power-not-Shin 164 — **EXACT**
- Shin-not-Power 0 — **EXACT**
- favorite Power-not-Proportional 323 — **EXACT**
- underdog Power-not-Proportional 16 — **EXACT**
- >60c Power 250 / Proportional 13 / Power-not-Proportional 237 — **EXACT**

Power analysis:
- CLV n10, mean +4.25c, SD 4.39c — **EXACT**
- 8c planning SD — **ASSUMPTION, not ledger reconstruction**
- MDE n250 1.42c — **EXACT calculation**
- MDE n300 1.29c — **EXACT calculation**
- observed-SD reference MDE 0.78c / 0.71c — **EXACT**
- realized per-order ROI SD 117.1pp — **EXACT**
- 2pp ROI difference requires ~26,900 bets — **EXACT calculation; 26,905**
- with 100pp SD ~19,600 — **EXACT calculation; 19,623**

# C. Addendum 3

Unfilled:
- total 16 — **EXACT**
- adjudicable 9 / unadjudicable 7 — **EXACT**
- 3-6 — **EXACT**
- risk $25.17 — **EXACT**
- hypothetical gross P/L -$2.17 — **EXACT**
- gross ROI -8.6% — **EXACT**
- post-v28 adjudicable n8, 2-6, $21.21 risk, -$7.21, -34.0% — **EXACT**
- one unfilled CLV row +6.0c — **EXACT**

Regime:
- Sep17-20 attempts 53 / fills 52 / unfilled 1 / fill 98.1% — **EXACT**
- strict n47 / $169.71 risk / -$46.26 / -27.3% — **EXACT**
- Sep21-22 attempts 17 / fills 2 / unfilled 15 / fill 11.8% — **EXACT**
- strict n2, both losses — **EXACT**

Modern composition:
- MAKE 15 / fills 0 — **EXACT**
- TAKE 2 / fills 2 — **EXACT**
- Price Fish 14 / fills 0 — **EXACT**
- Primary 3 — **EXACT**

Strict sizing/price:
- n49, 14W-35L, $174.83, -$51.62, -29.5% — **EXACT**
- mean per-bet ROI -36.0% — **EXACT**
- median per-bet ROI about -100.9% — **EXACT**
- <=30c n12, 2-10, $19.22, -$5.57, -29.0% — **EXACT**
- 31-45c n18, 5-13, $60.29, -$16.26, -27.0% — **EXACT**
- 46-60c n18, 6-12, $85.48, -$31.92, -37.3% — **EXACT**
- >60c n1, 1-0, $9.84, +$2.13, +21.6% — **EXACT**
- stake min $0.02 / median $3.78 / mean $3.57 / max $9.84 — **EXACT**
- top-five risk $30.75 / 17.6% — **EXACT**

Maker/taker:
- strict maker n25, 10-15, $86.34, +$1.21, +1.4% — **EXACT**
- strict taker n24, 4-20, $88.49, -$52.83, -59.7% — **EXACT**
- including early exits maker n27 +11.9% / taker n27 -38.4% — **EXACT**

Corrected flip/fill:
- classifiable pre-alert attempts 5 — **EXACT**
- unclassifiable 12 — **EXACT**
- classifiable flip attempts 0 — **EXACT**
- non-flip attempts 5 / fills 2 / realized 2 / 0-2 — **EXACT**
- $5.12 risk / -$5.36 / -104.8% including fees — **EXACT**
- all inferential cells n<30 — **NOT INTERPRETABLE**

# D. Newly reconciled >60c funnel

At the original 2,960-row snapshot:
- Power-qualified >60c 250 — **EXACT**
- Power-not-Proportional 237 — **EXACT**
- gate rows 249 — **EXACT**
- suppressed 248 — **EXACT**
  - cooldown 214 — **EXACT**
  - event cap 34 — **EXACT**
- alert eligible 1 — **EXACT**
- sent 1 — **EXACT**
- attempted from that sent cohort 0 — **EXACT**
- filled 0 — **EXACT**
- settled 0 — **EXACT**
- one qualifying observation has no gate row — **EXACT; reason NOT AVAILABLE HISTORICALLY**

The one historical >60c strict settlement is from an older execution cohort.

Therefore "250 >60c qualified -> 1 settled" as a same-cohort funnel is **SUPERSEDED**.

## Final status

Material supersessions:
1. **126 -> 124** unique qualifying identities.
2. Immediate follow-up threshold-flip Section 3 — **SUPERSEDED IN FULL**.
3. "250 >60c qualified -> 1 settled" same-cohort framing — **SUPERSEDED**.

All other core ledger-derived figures enumerated above reconcile to the pinned snapshots within reported rounding.
