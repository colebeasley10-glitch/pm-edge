# PM EDGE — ADDENDUM 3: FILL-RATE INVESTIGATION

**Mode:** Ledger only. No production changes. No Odds API credits. No new logging.

Acknowledged correction: September 19, 2026 was a winning realized day (+$11.37, +10.6% ROI). Earlier "no winning days" framing is retracted.

## Core interpretation guard

Do **not** assume a low fill rate is a defect.

A high fill rate can be harmful if fills are adversely selected or if marketable/taker execution is systematically buying bad prices. Before any fill-rate remediation, adjudicate the unfilled orders at the exact attempted price and determine whether non-fills saved or cost money.

No production fill-rate, threshold, devig, or Pinnacle change is authorized by this addendum.

## 1. Unfilled-order adjudication first

For every unfilled order that is outcome-resolved in the existing ledgers:
- use the actual attempted limit price,
- use the actual requested share count,
- compute hypothetical W/L, gross P/L, gross ROI,
- attach CLV only when a closing-price row actually exists.

Do not use midpoint or last trade.

If some unfilled orders are not yet settled or have no historical outcome in the ledger, report them as unresolved / NOT AVAILABLE HISTORICALLY. Do not infer results from unrelated markets.

## 2. Regime-change investigation

Diff the order ledger and commit history for:
- max-buy-price / price-guard changes,
- TAKE versus MAKE behavior,
- order lifetime behavior,
- order size versus available depth,
- quote age at order submission,
- threshold/lane changes that changed market composition.

For any item not preserved by the historical ledger, state **NOT AVAILABLE HISTORICALLY**.

## 3. Variance and sizing diagnostics

For strict held-to-settlement positions:
- report dollar-weighted ROI,
- mean/median per-bet ROI separately,
- entry-price buckets <=30c / 31–45c / 46–60c / >60c,
- n, W/L, risk, P/L, ROI per bucket,
- stake min/median/max,
- share of total risk in largest five positions.

Flag cells n<30 as **NOT INTERPRETABLE**.

## 4. Devig flip versus fill data

Use the production threshold logic actually active in code/workflow.

For attempted orders, classify Power-over-Proportional flip status only when the alternative devig values existed **before the alert**. Never use a later rescan to backfill a pre-alert devig value.

Report fill rate and realized result for flip versus non-flip by entry-price bucket. Flag n<30 cells as **NOT INTERPRETABLE**.

If prior threshold reconstruction is found incorrect, correct it explicitly and supersede the old numbers.

## Priority

1. Unfilled-order adjudication
2. Regime-change cause
3. Variance/sizing
4. Devig/fill cross-reference

## Unchanged constraints

Production betting logic stays as-is. Preserve the PWA/dashboard and clickable profit calendar, bearer-token auth, public /health, CORS, secure same-origin proxy, and pandas object-dtype fix including lane.

No API backfill. No production threshold change. No devig change. No Pinnacle work. Promotion decisions remain CLV-based; ROI at n=250–300 is descriptive only.
