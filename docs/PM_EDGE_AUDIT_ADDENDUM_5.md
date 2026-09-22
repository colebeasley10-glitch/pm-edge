# PM EDGE — ADDENDUM 5: COOLDOWN KEYING AND TEST POWER

**Audit date:** 2026-09-22  
**Mode:** Ledger/repository audit only. No production betting-logic changes. No Odds API credits. Stakes remain unchanged for data generation.  
**Pinned candidate ledger:** `34988c3be90657260147f8633c3ee95c709591b2`  
**Pinned signal ledger:** `6396fe93bed7f665aba6892ac543cfe4f97c32ed`

## 0. Accepted correction and Addendum 4 tables re-issued as plain text

No embedded-image tables are used below.

The accepted taker win-deficit check is:
- mean actual taker entry = **41.08%**
- entry-price-implied expected wins = **9.86**
- observed wins = **4**
- summed Bernoulli win-count SD = **2.325**
- standardized deficit = **-2.52 SD**

The earlier rough **2.9 SD** estimate is **WITHDRAWN / SUPERSEDED**.

### Maker/taker historical baseline

```text
Execution   Strict n   Record    Risk      P/L        Dollar-weighted ROI   Interpretation
MAKER       25         10-15     $86.34    +$1.21     +1.4%                 NOT INTERPRETABLE (n<30)
TAKER       24          4-20     $88.49    -$52.83    -59.7%                NOT INTERPRETABLE (n<30)
```

### Frozen maker/taker test parameters

```text
Field                             Frozen value
Decision point                    60 cumulative strict settled TAKER bets
Primary metric                    Delta_ROI = ROI_TAKER - ROI_MAKER
ROI definition                    Dollar-weighted strict-settlement ROI
MAKER comparison arm              All strict MAKER settlements through the same cutoff
Planning per-bet ROI SD           117.1 percentage points
Planning MAKER n                  Approximately 25-40
SE at MAKER n=25                  27.9pp
SE at MAKER n=30                  26.2pp
SE at MAKER n=40                  23.9pp
Expected SE range                 23.9-27.9pp
60pp effect                       2.15-2.51 SE
Two-sided 95% 1.96-SE resolution  46.8-54.6pp
80%-power MDE, alpha=0.05         67.0-78.1pp across MAKER n=40 down to n=25
Interpretation below MDE_80       INCONCLUSIVE for claims that taker execution is acceptable
Operational sign trigger          Delta_ROI < 0; restriction still requires explicit owner approval
Promotion basis                   CLV; ROI at n=250-300 remains descriptive only
```

At the n=60 cutoff, recompute the standard error using the actual strict MAKER count through the same cutoff and the frozen 117.1pp planning SD:

`SE(Delta_ROI) = 117.1pp * sqrt(1/60 + 1/n_MAKER)`

Define the pre-registered conventional 80%-power MDE as:

`MDE_80 = (1.96 + 0.8416) * SE(Delta_ROI)`

Any observed absolute ROI difference smaller than the applicable `MDE_80` is **INCONCLUSIVE** for claims that taker execution is acceptable. A result such as -20pp at TAKER n=60 is therefore not evidence that taker execution is acceptable.

The historical ~60pp gap is roughly a two-SE effect at this sample scale. It is near the ordinary 95% signal-resolution range, but it is not an 80%-power effect across the full planned MAKER-count range.

The earlier approximately **26,900-settlement** figure targeted a **2pp** ROI effect at alpha=0.05 and 80% power. It is not contradicted by the much larger detectable-effect scale of this n=60 test.

### >60c funnel, same pinned cohort

```text
Stage / disposition                              Count   Interpretation
Power-qualified >60c score observations          250     Descriptive funnel count
Matching gate rows                               249     Descriptive funnel count
Qualified observation with no gate row             1     Reason NOT AVAILABLE HISTORICALLY
ALERT_SUPPRESSED                                  248     Descriptive funnel count
  ledger label: cooldown_active                   214     CAUSAL INTERPRETATION SUPERSEDED
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

The raw 250 / 249 / 248 / 214 / 34 / 1 / 1 / 0 funnel counts still reconcile exactly. What changes in Addendum 5 is the interpretation of the 214 `cooldown_active` labels.

## 1. Cooldown keying — primary audit item

### 1.1 Of the 248 suppressions: already alerted vs never alerted

For this audit, a full opportunity identity is:

`ticker / side / lane / recommended_limit`

Across the **248 suppressed observations**:

```text
Suppression class                                      Observations   Unique full identities
Full identity had already been alerted                 36             1
Full identity had never been alerted                   212            23
TOTAL                                                   248            24
```

The 36 already-alerted observations are rescans of the one sent >60c identity:

`KXNFLGAME-26SEP24ATLGB-GB | YES | price_fish | 68c`

It was alerted at **2026-09-21 18:14:22Z**. Its 36 later suppressed observations run from 2026-09-21 18:45Z through 2026-09-22 13:13Z.

The **212 never-alerted suppressions** split exactly as:

```text
Recorded gate label             Rows on never-alerted identities
cooldown_active                 178
event_cap                        34
TOTAL                            212
```

Therefore:
- **214 rows labeled `cooldown_active`** is EXACT as a ledger-label count;
- interpreting all 214 as genuine cooldown rescans is **SUPERSEDED**.

### 1.2 Why the 214 label is contaminated

The v28 gate logger records the upstream reason using:

`reason or "cooldown_active"`

Several upstream guards return `False, ""`, so a blank non-cooldown reason is rewritten as `cooldown_active` in the candidate ledger.

For the 178 never-alerted rows carrying that label:

```text
What can be established from the pinned candidate ledger       Count
Price Fish rows with books < 3                                 102
Books >= 3 and dispersion <= 5.5%                               76
TOTAL                                                           178
Maximum dispersion anywhere in these 178 rows                  2.164%
```

All 178 are Price Fish.

The **102 rows with books < 3 are conclusively the v26 Price Fish minimum-book guard**, because that guard returns `False, ""` before the base cooldown code can run. They were mis-recorded as `cooldown_active` by the v28 fallback.

For the remaining **76**, the exact historical blank-reason guard cannot be separated from the persisted candidate fields. Possible blank-reason code paths include stale fair value, an existing submitted/open order or position, and quiet-time deferral after earlier guards pass. The exact subreason is therefore **NOT AVAILABLE HISTORICALLY**.

Do not reconstruct those 76 from later state.

### 1.3 The 25 identities span only 5 events

```text
Event                                      Qualified obs   Full identities   Suppressed   Event-cap
NY Giants at LA Rams                      121             18                121          14
ATL Falcons at GB Packers                  74              2                 73           3
Hanshin Tigers vs Tokyo Yakult Swallows    32              2                 32          17
ATL Dream at NY Liberty                    22              2                 22           0
Washington vs Detroit                       1              1                  0           0
TOTAL                                      250             25                248          34
```

The 34 event-cap suppressions cover **11 distinct full identities across 3 events**.

The event concentration makes the event-cap count structurally plausible. v26 keys the correlation cap on `event_id`, and production is configured for **one active alert per event**.

This does not validate the 178 never-alerted rows mislabeled as cooldown.

### 1.4 Exact cooldown key from code

The base watcher constructs:

`key = f"{market['ticker']}|{q.side}{suffix}"`

where the effective suffix is:
- Primary: empty
- Standard / legacy Value: `|VALUE`
- Price Fish: `|SPECULATIVE`

Therefore cooldown is keyed on:

**ticker + side + effective lane/tier**

It is **not** keyed on:
- event
- recommended price
- the full ticker/side/lane/price audit identity

Price is instead a re-alert condition. A recommended-price move of at least **2c** bypasses the ordinary cooldown as `PRICE`:

`abs(new_recommended_limit - old_entry) >= 0.02`

An edge improvement of at least 2pp, an inactive prior alert, or the reminder-age condition can also re-open alert eligibility.

### 1.5 How much did the coarser key suppress?

The 25 full ticker/side/lane/price identities collapse to **20 ticker/side/lane cooldown-key identities**. Five coarse keys contain two distinct price-specific identities, so the key structure exposes **5 additional price identities** to shared-key behavior.

Those five extra price identities appear historically as:
- 4 with the overloaded ledger label `cooldown_active`
- 1 with `event_cap`

However, none of those five full identities had been alerted, and the pinned signal history contains no earlier alert on the same cooldown key at another price. The pre-cohort watcher state also does not supply a matching recent active alert that would establish true cooldown for them.

Therefore:

```text
Confirmed distinct full identities suppressed by actual cooldown
solely because price is omitted from the cooldown key:             0

Distinct extra price identities structurally sharing a coarse key: 5

Extra price identities carrying overloaded cooldown label:          4
```

The correct audit conclusion is that **0 coarsening-driven losses are confirmed in this cohort**. The four overloaded rows must not be counted as cooldown losses because their true blank-reason guard is not preserved.

No cooldown logic is changed in this pass.

## 2. Detectable effect size — preregistration amendment

The maker/taker preregistration has been amended in both Addendum 4 and the Track A preregistration report.

Using the frozen 117.1pp planning SD:

```text
MAKER n   SE(Delta_ROI)   1.96-SE resolution   80%-power MDE
25        27.9pp          54.6pp                78.1pp
30        26.2pp          51.3pp                73.4pp
40        23.9pp          46.8pp                67.0pp
```

A 60pp effect is approximately **2.15-2.51 SE** across the planned maker-count range. That supports the user's two-SE sanity check, but it does not imply 80% power.

The frozen interpretation rule is:

> At TAKER n=60, compute the applicable `MDE_80` using the actual strict MAKER count through the same cutoff. Any observed `|Delta_ROI|` smaller than that threshold is **INCONCLUSIVE** for claims that taker execution is acceptable.

The original sign trigger remains separately frozen:
- `Delta_ROI < 0` records the taker-deficit trigger as met;
- any production restriction still requires explicit owner approval;
- failure to meet the sign trigger is not affirmative evidence that taker execution is acceptable when the result is inside the MDE guardrail.

## 3. Audit pattern: the system is under-observed

The apparent leading bottleneck has moved as instrumentation improved:

```text
Audit stage      Apparent leading issue
Initial          devig / fair-probability construction
Next             attempted-to-fill execution rate
Current          qualification-to-alert gate/disposition layer
```

Production did not need to change for these relocations to appear. Better measurement and cohort separation changed which layer was visible.

That is the signature of an **UNDER-OBSERVED SYSTEM**, not evidence that one isolated defect has been conclusively identified.

The principal deliverable from this audit cycle is therefore the **forward-logging specification**, not any single current bottleneck diagnosis.

Do not treat the current gate/alert finding as settled. Expect the diagnosis to move again when these fields are durably frozen at the relevant decision time:
- alert-time Proportional / Power / Shin fair values
- active devig and benchmark
- alert-time net edge under each devig
- raw gate disposition before any fallback/default label
- explicit guard name for every suppression
- best bid / ask and depth at submission
- intended size and executable depth
- source quote timestamp
- PM Edge fetch timestamp
- order-submission timestamp
- computed quote age
- execution type at submission
- durable alert disposition through submission/non-submission

The discovered `reason or "cooldown_active"` collapse is itself evidence for the under-observation diagnosis.

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
- `quiet_time_deferred`
- `daily_cap`
- `no_price_move`
- `other_explicit`

Also persist:
- exact cooldown key used
- prior alert key
- prior alert timestamp
- prior entry price
- prior edge
- event id and active-event count
- book count
- dispersion
- fair-value age at gate time
- immutable opportunity identity including ticker, side, lane and price

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

This Addendum 5 changes documentation and preregistration interpretation only. It does not change runtime betting logic and does not require a runtime deployment.
