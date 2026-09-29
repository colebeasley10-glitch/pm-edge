# PM Edge AutoTrader — Phase 1 (paper only)

Status: design specification, NOT deployed and NOT authorized for real-money trading.

## Objective
Evaluate whether a sports-only Kalshi strategy earns positive out-of-sample returns after fees, spread, adverse selection, slippage, latency and unfilled orders. Existing Primary, Fish and Whale lanes remain unchanged. Do not treat projected edge as realized income.

## Scope
- Sports-only, initially pregame, liquid binary win markets. Exclude crypto, intraday commodity direction and other ultra-short markets.
- Record exchange timestamps, local receipt timestamps, event-state timestamps, order-book depth and quote age.
- Model probabilities from independently timestamped game/event data; reject absent or stale data. Never use an LLM's unsupported numerical probability as fair value.
- Every candidate gets a stable ID and immutable decision ledger, including rejects, missing data, price changes, partial fills and expiry.
- Compute expected value net of the current fee schedule and conservative executable depth. Do not assume top-of-book prices remain available.
- Paper orders use queue-position-aware or trade-through fill modeling. A touch of the ask alone is NOT proof that a resting bid filled.
- Simulate exits and settlement separately. Account for both entry and exit fees, realized and unrealized P/L, bankroll, exposure and drawdown.
- Reject stale event data, markets at/after kickoff for initial phase, halted/closed markets, thin books, poor spread and quotes outside guarded price.
- Run a strict shadow mode: NO authenticated POST/DELETE requests, NO live orders and NO changes to existing execution paths.

## Risk and validation gates
- Explicit configuration for simulated bankroll, per-position and per-event exposure, correlated exposure, daily loss stop and total drawdown stop. Default to zero live buying power.
- Unit tests for no-live-order guarantee, stale-data rejection, opposing positions, duplicate alerts, order expiry, partial fills, fee math, sell accounting and emergency stop.
- Replay timestamped historical data and then forward-test on unseen live events. Compare to a no-trade baseline and hold-to-settlement; report sample size, net ROI, max drawdown, turnover, fees and missed fills. Do not optimize on the test set.
- Live execution requires a separate explicit user approval and a new deployment review; this document grants none.

## Existing code audit (2026-09-29)
- `pmedge/kalshi.py` provides public market/order-book reads and an authenticated client. The latter must not be used in Phase 1.
- `pmedge/risk.py` provides fractional-Kelly position sizing but is not a portfolio-wide correlated-exposure or daily-loss manager.
- `pmedge/paper.py` currently treats ask touching a paper limit as a fill; this is optimistic and must be replaced before any performance claims.
- `execution_guard.py` is approval-first and intentionally refuses autonomous live submission. Preserve that behavior.

## Implementation sequence
1. Isolated `autotrader/` paper-only package and tests.
2. Timestamped sports feed plus Kalshi order-book capture and immutable ledger.
3. Conservative execution simulator and portfolio/risk accounting.
4. Baseline pregame probability model, then out-of-sample forward testing.
5. Read-only portfolio dashboard and weekly audit reports.
6. Only after robust validation: separately design a supervised, explicitly approved small-bankroll live pilot.
