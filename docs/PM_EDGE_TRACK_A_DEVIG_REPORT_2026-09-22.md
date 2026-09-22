> **ADDENDUM 4 RETRO-VALIDATION:** One preliminary count is superseded: "126 unique qualifying ticker/side/lane identities" should be **124**. The old 126 count was unique identities with a nonblank recommended limit, not unique Power-qualified identities. All other ledger-derived figures in this report reconcile to the pinned snapshot. See `PM_EDGE_RETRO_VALIDATION_2026-09-22.md`.

> **RETROVALIDATION CORRECTION (Addendum 4):** the sentence below stating “126 unique qualifying ticker/side/lane identities” is **SUPERSEDED**. The pinned ledger has **124** identities with `decision=QUALIFIED`. The value **126** is the number of usable identities with a nonblank recommended limit. All other preliminary numerical figures were re-run and reconcile to reported rounding. See `PM_EDGE_TRACK_A_PRE_REG_AND_FUNNEL_GAPS_2026-09-22.md`.

# PM Edge Track A — Devig Method Audit (Preliminary)

**Data cutoff:** 2026-09-22 13:13 UTC  
**Scope:** Audit only; no production decision changes.

## Executive finding

Power is the active production devig method and the candidate ledger confirms exact consistency between the stored active fair probability and the side-adjusted Power fair value on all usable scoring rows.

The three devig methods can materially change measured edge. Across 2,960 usable scoring rows:
- mean absolute Power-vs-Proportional shift: **1.31 percentage points**
- mean absolute Shin-vs-Power shift: **0.59 percentage points**
- maximum absolute Power-vs-Proportional shift: **13.40 points**
- maximum absolute Shin-vs-Power shift: **9.89 points**

These are deterministic row-level differences, **not 2,960 independent bets**. There are 309 unique ticker/side/lane identities in the usable scoring set and 126 unique qualifying ticker/side/lane identities.

## Data availability

Candidate ledger:
- total rows: 4,950
- score rows: 3,855
- rows with all three devig values: 3,738
- score rows with all three devig values: 2,960
- devig diagnostics begin populating around 2026-09-21 17:00 UTC

Unique usable scoring identities by sport:
- MLB: 143
- NPB: 65
- NFL: 55
- WNBA: 25
- KBO: 21

Unique usable scoring identities by market:
- h2h: 179
- total: 74
- spread: 56

## Identical-row devig shifts

For every usable scoring row, all three methods are evaluated on the same candidate row.

### Power minus Proportional

- mean shift: **+0.26 points**
- median shift: **+0.17 points**
- 10th percentile: **-1.54 points**
- 90th percentile: **+2.11 points**
- mean absolute shift: **1.31 points**
- max absolute shift: **13.40 points**

### Shin minus Power

- mean shift: **-0.07 points**
- median shift: **-0.05 points**
- 10th percentile: **-0.74 points**
- 90th percentile: **+0.78 points**
- mean absolute shift: **0.59 points**
- max absolute shift: **9.89 points**

Because execution price, fees and PM Edge buffers are unchanged within an identical row, the method-specific net-edge shift equals the side-fair-probability shift.

## Favorite vs underdog

Favorite/underdog is defined from the side-adjusted Power fair probability.

### Underdog side — Power fair < 50%
Row count: 1,281
- Power minus Proportional mean: **-1.10 points**
- Shin minus Power mean: **+0.47 points**
- mean absolute Power-vs-Proportional shift: **1.24 points**

### Favorite side — Power fair >= 50%
Row count: 1,679
- Power minus Proportional mean: **+1.30 points**
- Shin minus Power mean: **-0.48 points**
- mean absolute Power-vs-Proportional shift: **1.35 points**

Interpretation: Power systematically pushes probability farther toward the favorite than Proportional in this sample, while Shin partially pulls back toward Proportional. That is a method-behavior finding, not evidence that one method predicts outcomes better.

## Kalshi attempted-price buckets

Only scoring rows with a recorded recommended limit can be assigned an executable/attempted price bucket. The same candidate rows are used for all three devig methods.

### <= 30c
n = 150 row observations
- Power minus Proportional: **-0.67 points**
- Shin minus Power: **+0.30 points**

### 31–45c
n = 322
- Power minus Proportional: **+0.29 points**
- Shin minus Power: **-0.03 points**

### 46–60c
n = 288
- Power minus Proportional: **+1.05 points**
- Shin minus Power: **-0.47 points**

### > 60c
n = 250
- Power minus Proportional: **+2.27 points**
- Shin minus Power: **-0.82 points**

Again, these are repeated scanner observations and cannot be treated as independent settled bets.

## Realized-outcome comparison

Current unique settled data with usable devig fields is insufficient.

- total settled signal outcomes currently logged: 16
- sent rows with all three devigs: 14
- settled signals that can be matched to a sent row with all three devigs: **6**

Descriptive only:

| Method | Brier | Log loss | n | Status |
|---|---:|---:|---:|---|
| Proportional | 0.2300 | 0.6533 | 6 | NOT INTERPRETABLE |
| Power | 0.2199 | 0.6331 | 6 | NOT INTERPRETABLE |
| Shin | 0.2283 | 0.6499 | 6 | NOT INTERPRETABLE |

Power is numerically best on these six outcomes, but **n=6 is not evidence** and must not drive a production change.

Correlation of devig shift with win/loss:
- Power-minus-Proportional vs win indicator: +0.649, n=6 — **NOT INTERPRETABLE**
- Shin-minus-Power vs win indicator: -0.647, n=6 — **NOT INTERPRETABLE**

## CLV comparison

- total CLV rows: 10
- settled devig-comparable signals with matched CLV: **3**

Correlation:
- Power-minus-Proportional vs CLV: +0.539, n=3 — **NOT INTERPRETABLE**
- Shin-minus-Power vs CLV: -0.537, n=3 — **NOT INTERPRETABLE**

No devig method earns promotion/demotion from current outcome or CLV data.

## Track A conclusion

1. The methods are not interchangeable. Power changes fair probability by enough to alter edge classification near thresholds.
2. Power behaves directionally as expected in this data: more aggressive toward favorites and less aggressive toward underdogs than Proportional.
3. Shin generally sits between Power and Proportional, but exceptions can be large.
4. **There is not yet enough unique settled/CLV data to say which method is better.**
5. Production should remain unchanged.
6. Continue collecting all three devigs on every candidate and evaluate at unique signal level once n >= 30 for a cell; benchmark promotion should use the larger shadow-sample rule in Addendum 2.

## Track B status

**NOT AVAILABLE HISTORICALLY from PM Edge's existing ledger.**

Individual historical bookmaker quotes were not stored, therefore historical Pinnacle-only fair values cannot be reconstructed without an external historical-data backfill.

No backfill has been attempted and no Odds API credits were spent.

Provider documentation currently states:
- Pinnacle is in the EU bookmaker region and public-site odds may incur delay.
- Historical odds require a paid plan.
- Historical odds cost 10 credits per region/bookmaker-group per market.
- Specifying up to 10 bookmakers directly is equivalent to one region for quota purposes.
- Therefore a one-snapshot, one-market direct-Pinnacle historical request is expected to cost **10 credits** when data are returned.

The actual PM Edge Odds API subscription tier is **not verified from repository/config state**. The workflow comments/code are consistent with a 500-credit monthly budget, but that is not sufficient proof of account entitlement. Verify the account plan before any historical request.
