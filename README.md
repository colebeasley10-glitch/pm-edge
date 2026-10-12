# PM Edge

Finds Kalshi sports contracts priced below the sportsbook consensus, alerts you
on your phone, and tracks whether the alerts actually beat the market.

## How it works

1. **Every 30 min** GitHub Actions runs `pm_edge_runner_v29.py`.
2. It pulls open Kalshi game markets and sportsbook odds (The Odds API) for the
   **focus sports**: NCAAF, KBO, NPB, cricket. These are thin markets where
   Kalshi has lagged the books. Liquid markets like the NFL and MLB are skipped
   to save the free 500 monthly credits.
3. Book odds are de-vigged into a fair probability. A contract qualifies only if
   it has **3%+ net edge at the price you can buy right now**, after Kalshi's fee
   and a model/slippage buffer (the v29 taker gate).
4. Qualifying picks go to Pushover with a **Review & place bet** link. Nothing is
   bet without your tap.
5. `clv_tracker.py` records each pick's price vs the last pre-game price
   (closing-line value). Positive CLV over 20+ picks is the sign of a real edge.

## The app

`app.py` (Streamlit) has four tabs: **Picks**, **My bets**, **Results**,
**System**. Stats count from the fresh start on Oct 12, 2026
(`PM_EDGE_STATS_START`). Older history stays in the ledgers and in `archive/`.

## Knobs (workflow env)

| Setting | Default | What it does |
|---|---|---|
| `PM_EDGE_TAKER_GATE_MIN_EDGE` | `0.03` | Edge required at the live ask |
| `PM_EDGE_FOCUS_SPORTS` | `americanfootball_ncaaf,baseball_kbo,baseball_npb,cricket_` | Sports that get odds credits (empty = all) |
| `PM_EDGE_ODDS_DAILY_BUDGET` | `14` | Odds API credits per day |
| `PM_EDGE_TAKER_GATE` | `1` | `0` reverts to v28 scoring |

## Layout

- `pm_edge_runner_v29.py`: entry point. Runners v16 to v28 are earlier layers it builds on.
- `watcher.py`, `pmedge/`: market discovery, matching, de-vig, pricing.
- `clv_tracker.py`, `signal_outcome_tracker.py`, `performance_tracker.py`: results tracking.
- `app.py`, `pages/`, `dashboard_data.py`: the app and the approve-and-submit flow.
- `ncaa_beta_runner.py`: separate NCAA full-game model (beta, small stakes).
- `autotrader/`: paper-trading simulator (no real money).
