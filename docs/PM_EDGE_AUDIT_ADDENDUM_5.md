# PM EDGE — ADDENDUM 5: COOLDOWN KEYING AND TEST POWER

**Audit date:** 2026-09-22  
**Mode:** Ledger/repository audit only. No production betting-logic changes. No Odds API credits. Stakes remain unchanged for data generation.  
**Pinned candidate ledger:** `34988c3be90657260147f8633c3ee95c709591b2`  
**Pinned signal ledger:** `6396fe93bed7f665aba6892ac543cfe4f97c32ed`

## 0. Addendum 4 tables re-issued as plain text

No embedded-image tables are used below.

### Maker/taker historical baseline

```text
Execution   Strict n   Record    Dollar-weighted ROI   Interpretation
MAKER       25         10-15     +1.4%                 NOT INTERPRETABLE (n<30)
TAKER       24         4-20      -59.7%                NOT INTERPRETABLE (n<30)
Delta       49 total   —         -61.1pp TAKER-MAKER   NOT INTERPRETABLE; both arms n<30
```

The accepted taker win-deficit check remains: mean entry 41.08%, expected wins 9.86, observed wins 4, win-count SD 2.325, z = -2.52. The earlier 2.9-SD estimate is withdrawn.

### Frozen maker/taker test parameters

```text
Field                         Frozen value
Decision point                60 cumulative strict settled TAKER bets
Primary metric                Delta_ROI = ROI_TAKER - ROI_MAKER
ROI definition                Dollar-weighted strict-settlement ROI
Maker comparison arm          All strict MAKER settlements through the same cutoff
Current strict TAKER n        24 — NOT INTERPRETABLE (n<30)
Current strict MAKER n        25 — NOT INTERPRETABLE (n<30)
Planning per-bet ROI SD       117pp
Planning MAKER n at cutoff    Approximately 25-40
Planning SE of Delta_ROI      23.9-27.9pp; use 25-30pp as conservative shorthand
Two-SE discernibility band    47.8-55.7pp
Formal 80%-power MDE         67.0-78.1pp for MAKER n=40 down to n=25
Result below applicable MDE   INCONCLUSIVE
Operational sign trigger      Delta_ROI < 0; restriction still requires explicit owner approval
Promotion basis               CLV; ROI at n=250-300 remains descriptive only
```

The currently observed ~60pp gap is a useful two-standard-error-scale sanity check, not the formal minimum detectable effect at 80% power. Under a conventional two-sided alpha=0.05 / 80%-power normal approximation, the MDE is **67.0-78.1pp** for a 40-to-25 settlement maker arm. At the TAKER n=60 cutoff, recompute the standard error with the actual strict MAKER count and set `MDE_80 = (1.96 + 0.8416) * SE(Delta_ROI)`.

Therefore a result such as -20pp at TAKER n=60 is **INCONCLUSIVE**, not evidence that taker execution is acceptable. More generally, any observed absolute difference smaller than the applicable `MDE_80` is **INCONCLUSIVE** for that claim. The earlier ~26,900-bet calculation addressed a 2pp effect and is not contradicted by this much larger detectable-effect scale.

### >60c funnel, same pinned cohort

```text
Stage / disposition                              Count   Interpretation
Power-qualified >60c score observations          250     Descriptive funnel count
Matching gate rows                               249     Descriptive funnel count
Qualified observation with no gate row             1     NOT INTERPRETABLE (n<30); reason NOT AVAILABLE HISTORICALLY
ALERT_SUPPRESSED                                  248     Descriptive funnel count
  ledger label: cooldown_active                   214     CAUSAL LABEL SUPERSEDED; see Section 1
  ledger label: event_cap                          34     Descriptive funnel count
ALERT_ELIGIBLE                                      1     NOT INTERPRETABLE (n<30)
Sent alerts                                         1     NOT INTERPRETABLE (n<30)
Attempted orders                                    0     NOT INTERPRETABLE (n<30)
Filled orders                                       0     NOT INTERPRETABLE (n<30)
Settled orders                                      0     NOT INTERPRETABLE (n<30)

Unique ticker/side/lane/price identities           25     NOT INTERPRETABLE (n<30)
Unique identities reaching a gate                  24     NOT INTERPRETABLE (n<30)
Unique sent identities                              1     NOT INTERPRETABLE (n<30)
```

The raw 250/249/248/214/34/1/1/0 funnel counts still reconcile exactly. What changes in Addendum 5 is the interpretation of the 214 `cooldown_active` label.

## 1. Cooldown keying — primary audit item

### 1.1 Already-alerted identity versus never-alerted identity

Opportunity identity for this audit is the full:
`ticker / side / lane / recommended_limit`.

Across the **248 suppressed observations**:

```text
Suppression class                                      Observations   Unique full identities
Full identity had already been alerted                 36             1  — NOT INTERPRETABLE (n<30)
Full identity had never been alerted                   212            23 — NOT INTERPRETABLE (n<30)
TOTAL                                                   248            24 — NOT INTERPRETABLE (n<30)
```

The 36 already-alerted observations are rescans of the one sent >60c identity:
NFL ATL/GB, Green Bay side, Price Fish, YES, 68c.

The 212 never-alerted observations split as:
- 178 rows carrying the ledger label `cooldown_active`;
- 34 `event_cap` rows.

This reconciles exactly to 248.

### 1.2 The 25 identities span only 5 events

```text
Event                                      Qualified obs   Full identities   Suppressed   Event-cap
NY Giants at LA Rams                      121             18                121          14
ATL Falcons at GB Packers                  74              2                 73           3
Hanshin Tigers vs Tokyo Yakult Swallows    32              2                 32          17
ATL Dream at NY Liberty                    22              2                 22           0
Washington vs Detroit                       1              1                  0           0
TOTAL                                      250             25                248          34
```

All event-level identity counts above are n<30 and therefore **NOT INTERPRETABLE** inferentially; they are exact descriptive audit counts.

The concentration into five events makes the 34 event-cap suppressions legitimate in structure. v26 applies the correlation cap on `event_id`, not on the full opportunity identity, and the configured cap is one active alert per event.

### 1.3 Exact cooldown key from code

The base watcher constructs:

`key = ticker + "|" + side + tier_suffix`

where:
- PRIMARY -> `ticker|side`
- Price Fish / speculative -> `ticker|side|SPECULATIVE`
- VALUE, which v28 logs as the standard lane -> `ticker|side|VALUE`

Therefore cooldown is keyed on **ticker + side + alert tier/lane class**.

It is:
- not keyed on event;
- not keyed on price;
- not the full ticker/side/lane/price audit identity.

The code separately allows a new alert when price moves by at least 2c, edge improves by at least 2pp, the prior alert becomes inactive, or the normal reminder condition is reached. Price is a re-alert condition, not part of the storage key.

### 1.4 Why “214 cooldowns” is not a valid causal count

v26 returns `(False, "")` for several non-cooldown guards:
- excessive dispersion;
- Price Fish insufficient books;
- Price Fish stale fair value;
- an existing submitted/partially-filled/filled position or order.

v28 then records every blank gate reason as:

`reason = reason or "cooldown_active"`

That makes `cooldown_active` an overloaded fallback label rather than a specific historical disposition.

Reconciliation of the 214 rows labeled `cooldown_active`:

```text
What can be established from pinned data                    Count
Identity had previously been alerted                        36
Identity had never been alerted                            178
Of the 178 never-alerted: Price Fish books < 3             102
Of the 178 never-alerted: books >= 3 and dispersion OK      76
```

All 178 never-alerted rows are Price Fish. All 178 have dispersion <= 2.17%, below the 5.5% dispersion guard. The 102 rows with fewer than three books therefore reconcile directly to the v26 Price Fish minimum-book guard and were **mis-recorded as `cooldown_active` by v28**.

For the remaining 76, the exact blank v26 disposition cannot be separated between stale-fair and existing-position/order guards from the fields frozen in the candidate ledger. Report the exact subreason as **NOT AVAILABLE HISTORICALLY**. Do not reconstruct it from later state.

This means Addendum 4's statement that the bucket was eliminated “mostly by cooldown/event-cap policy” is **SUPERSEDED**. The correct statement is:
- 34 event-cap suppressions are specifically logged;
- at least 102 supposed cooldown rows are actually the Price Fish minimum-book gate;
- the remaining overloaded rows need better gate-disposition logging before causal attribution.

### 1.5 Does the coarse cooldown key suppress distinct price opportunities?

The 25 full ticker/side/lane/price identities collapse to **20** ticker/side/lane cooldown-key identities. Five coarse keys each contain two price-specific identities, so **5 additional price identities structurally share a cooldown key**.

Of those five extra price identities:
- 4 appear in rows labeled `cooldown_active`;
- 1 appears under `event_cap`.

However, the four `cooldown_active` rows are part of the overloaded v28 fallback label, and those identities have no prior alert record. Therefore:

- **provable distinct price identities suppressed by actual cooldown: 0**;
- **price-specific identities structurally exposed to the coarse cooldown key: 5**;
- **extra price identities carrying the overloaded cooldown label: 4**;
- **exact historical causal count actually suppressed by cooldown: NOT AVAILABLE HISTORICALLY**.

Do not change the cooldown key or thresholds in this pass.

## 2. Detectable effect size added to the pre-registration

Planning calculation, with per-bet ROI SD = 117pp:

`SE(Delta) = 117pp * sqrt(1/60 + 1/n_MAKER)`

At n_MAKER=25:
- SE = **27.9pp**
- two-SE gap = **55.7pp**

At n_MAKER=40:
- SE = **23.9pp**
- two-SE gap = **47.8pp**

The two-standard-error scale is about **47.8-55.7pp**, so the observed ~60pp gap is roughly **2.15-2.51 SE**. For the formal pre-registration, use the conventional two-sided alpha=0.05 / 80%-power threshold:

`MDE_80 = (1.96 + 0.8416) * SE(Delta_ROI)`

That is approximately **67.0-78.1pp** for MAKER n=40 down to n=25. At the TAKER n=60 cutoff, recompute it using the actual strict MAKER count through the same cutoff. Any observed `|Delta_ROI|` smaller than the applicable `MDE_80` is **INCONCLUSIVE** and cannot be used as evidence that taker execution is acceptable.

A true 60pp gap corresponds to only roughly **58%-71% power** under this approximation. Thus n=60 can surface the current ~60pp-scale gap at roughly two standard errors, but it is not an 80%-powered test for a 60pp effect.

The old ~26,900-settlement figure addressed a 2pp effect. There is no contradiction.

## 3. Audit pattern: the system is under-observed

The apparent primary bottleneck has moved as instrumentation improved:

1. devig / fair-probability construction;
2. fill rate and execution;
3. gate / alert disposition.

Production did not need to change for these relocations to appear. Better measurement changed which layer was visible.

The audit implication is that PM Edge is an **UNDER-OBSERVED SYSTEM**, not a system with one settled, isolated failure point.

The principal deliverable from this audit cycle is therefore the forward-logging specification, not any single current bottleneck diagnosis. The current leading candidate must remain provisional.

Expect the bottleneck to move again when these fields are durably frozen at the relevant decision time:
- alert-time Proportional / Power / Shin fair values;
- active devig and benchmark;
- best bid / ask and depth;
- source quote timestamp and PM Edge fetch timestamp;
- computed quote age;
- explicit gate disposition with a nonblank enumerated reason;
- submission execution type;
- alert -> approval / skip / expiry / submit-failure disposition.

The newly discovered `reason or "cooldown_active"` collapse is itself evidence for this under-observation diagnosis.

## 4. Required forward gate-disposition logging

The forward specification must never encode an unknown/blank gate block as `cooldown_active`.

At minimum persist one explicit enum per gate evaluation:
- `cooldown_active`
- `event_cap`
- `run_cap`
- `dispersion_block`
- `insufficient_books`
- `stale_fair`
- `open_position_or_order`
- `daily_cap`
- `no_price_move`
- `other_explicit`

Also persist:
- the exact cooldown key used;
- prior alert key, prior alert time, prior entry price and prior edge when cooldown is evaluated;
- event id and active-event count when event cap is evaluated;
- book count, dispersion and fair-value age at gate time;
- immutable signal/opportunity identity including ticker, side, lane and price.

This is a logging specification only. No production betting-decision change is authorized in Addendum 5.

## 5. Unchanged constraints

- Production betting logic stays as-is.
- Stakes stay at the current tiny size; trading is for clean execution, CLV and outcome data.
- No Odds API historical spend.
- Preserve the PWA/dashboard and clickable profit calendar.
- Preserve bearer-token auth, public `/health`, CORS and the secure same-origin proxy.
- Preserve the pandas object-dtype fix including `lane`.
- Validate reconstructed figures against pinned ledgers.
- Report unlogged historical data as **NOT AVAILABLE HISTORICALLY**.
- Flag every inferential cell with n<30 as **NOT INTERPRETABLE**.
- Promotion decisions remain CLV-based; ROI at n=250-300 is descriptive only.
- Import-test before any runtime backend deployment.
- Runtime deployment target remains `mobile-backend-live`.
