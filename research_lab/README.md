# PM Edge v30: Strategy Research Lab (PAPER ONLY)

A separate, paper-only experiment that runs next to the existing PM Edge system and
compares four ways of finding Kalshi bets. Nothing here can place a real order.

| Strategy | ID | Idea |
|---|---|---|
| Existing Sharp | `SHARP_EXISTING` | Benchmark. Read-only from `league_trades.csv`; never modified. |
| Value Only | `VALUE_ONLY_v1` | Sportsbook consensus (power de-vig) beats the current Kalshi ask by 3+ pts after fees and buffers. |
| Hybrid | `HYBRID_SHARP_VALUE_v1` | Same value test **and** recent public trade flow on the same side. Flow is a filter, never a probability boost. |
| Structural Arb | `STRUCTURAL_ARB_v1` | Provable price inconsistencies between related contracts (strike ladders, NO baskets on mutually exclusive outcomes). |
| Value Only (Pinnacle) | `VALUE_ONLY_v2` | v1 rules, but fair value is Pinnacle's de-vigged line from PinnWire's free feed. |
| Hybrid (Pinnacle) | `HYBRID_SHARP_VALUE_v2` | v1 Hybrid rules on Pinnacle prices. |
| Today's Plays | `EV_FINDER_v2` | Every moneyline, spread and total across football, basketball, hockey, baseball, soccer and tennis at +1% EV or better vs Pinnacle. One ~$10 paper bet per game at the best price; quarter-Kelly size shown as a suggestion; alerts at +3%. |
| +EV Finder | `EV_FINDER_v1` | OddsJam-style: Pinnacle's fair moneyline vs Kalshi, Polymarket, DraftKings, Bovada, Fliff, Novig, ProphetX. Paper-bets the best price at 3%+ EV, alerts you to bet sportsbooks by hand, flags cross-venue arbitrage. |

## How a round works (`python -m research_lab.run`)

1. **Settle** open paper positions from Kalshi's official `result` only. Unknown results stay pending; voids refund.
2. **Sportsbook prices** come from the main scanner's `pm_edge_candidate_ledger.csv` (same run, power,
   proportional and Shin all recorded). The lab makes **zero Odds API calls**.
3. **Shared public data**, fetched once: fresh Kalshi quotes, order books (only for real candidates),
   the last 45 minutes of the public trade tape, and up to 400 open events.
4. **Each strategy decides independently** and writes only its own ledger.
5. **Reconcile** every ledger, write `research_lab/data/status.json`, send one daily summary.

It runs in `.github/workflows/research-lab.yml`, triggered after every *PM Edge Watch* run (≈30 min).
That workflow gets no Kalshi keys.

## Pinnacle prices (v2)

`research_lab/pinnacle.py` pulls PinnWire's free feed (`PINNWIRE_KEY` secret, 100 requests/day). One request
returns every upcoming game in a sport, so the lab fetches football and NBA at most once per 25 minutes and
stops at 90 requests a day. Kalshi game/spread/total markets (NCAAF, NFL, NBA) are matched to Pinnacle games
with the strict matcher; spreads and totals need Pinnacle to offer the **exact** same line. The two-way price is
de-vigged three ways (power used, proportional and Shin recorded). One sharp book replaces "3+ books agree", so
v2 instead rejects any Pinnacle market whose margin is abnormal (overround > 1.10). Team-name fixes for
Pinnacle spellings live in `research_lab/pinnacle_aliases.csv`.

## Sports rotation (free Pinnacle feed)

PinnWire's free plan is 100 requests/day; one request returns every game in a sport. Each sport refreshes on
its own schedule (football 45 min, basketball 60, hockey and baseball 120, soccer 180, tennis 360), at most 3
sports per round, capped at 90 requests/day. Strategies frozen before the extra sports were added (Value v2,
Hybrid v2, +EV Finder v1) still only see football and basketball, so their records stay comparable. Soccer
moneylines are de-vigged three ways (home/draw/away).

## +EV Finder and Promo Converter

`research_lab/ev_data.py` collects every venue's moneyline for each game Pinnacle priced that round:
Kalshi (live), Polymarket (live, public Gamma API, 1% fee allowance), and DraftKings / Bovada / Fliff / Novig /
ProphetX from The Odds API. Requesting named books costs 1 credit per sport, so a snapshot of NCAAF + NFL + NBA
is 3 credits; the lab takes one every 4 hours, max 6 credits a day, and skips if the account has < 60 left.
Sportsbook prices can be hours old, so alerts show their age and must be confirmed before betting.

EV = fair probability x decimal odds - 1 (fees included for Kalshi/Polymarket). The best price per side at 3-20%
EV gets one ~$10 paper bet, settled from Kalshi's official game result (games without a Kalshi market alert only).
Arbitrage = best price each side at different venues summing below 99.5%; logged and alerted, not paper-traded.
Pushover alerts ("bet by hand · PAPER-tracked") are capped at 5 per run and not repeated within 6 hours.
Middles, spreads/totals and player props are not covered yet (props need paid per-game data).

The **Promo Converter** page converts a bonus bet (stake not returned) into guaranteed cash:
conversion = (bonus decimal odds - 1) x (1 - 1 / hedge decimal odds). It has a calculator plus a list of the best
current conversions from the latest board.

## Value safeguards (all value strategies)

A candidate is rejected (with the reason logged) unless all of these hold: strict team and date match;
the live contract re-parses to the same market type and line; kickoff known and ≥5 min away; Kalshi settles
after kickoff and the event date agrees; rules aren't regulation-only for sports whose lines include
overtime; sportsbook prices ≤90 min old from ≥3 books with dispersion ≤5.5%; edge 3–15 pts at the
**current ask** (a larger "edge" is treated as a data error); price 10–90¢; real order-book depth;
spread ≤6¢; Kalshi book fetched ≤10 min ago.

## Fills

Paper fills walk the real order book at the time of the round: whole contracts, never below the ask,
Kalshi's fee rounded up per price level. A thin book gives a partial fill or a logged **NO_FILL**. Arb
combinations fill only if every leg fills the same number of units from books fetched within 30 seconds of
each other; otherwise they're logged as unexecuted. Arb results are "locked unless a leg voids", never "risk-free".

## Bankroll rules (per strategy)

$500 virtual start · ~$10 per bet · $20 max per game · $50/day new exposure · one open position per game ·
no stake changes after wins or losses · no daily minimum.

## Frozen versions

Rules, thresholds and start dates live in `research_lab/config.py`. A version is never edited after it
starts. To change a strategy, add a new version (its own ledger), and keep or retire the old one.

## Storage and its limits

Files under `research_lab/data/<strategy>/`: `positions.csv` (every paper trade), `candidates.csv`
(14 days of decisions, including rejections), `stats.json` (all-time counters and run health). Writes are
atomic; a lock plus the workflow's concurrency group stop overlapping runs; ids are deterministic so a
re-run can't duplicate a trade. Only the lab job writes these files.

Trade-off: Git is not a database. If a run's commit never lands, that run's paper trades are lost (not
duplicated). Moving to a hosted database such as Supabase's free tier would close that gap; it needs a
connection secret added to the repo.

## Dashboard

Streamlit pages: **Research Lab Compare** (all four side by side, same date window), **Research Lab Value
Only**, **Research Lab Hybrid**, **Research Lab Structural Arb**. Everything is labeled PAPER.

## Known limitations

* Value and Hybrid depend on the main scanner pricing markets. When the Odds API runs out of credits,
  they see nothing (the lab page and daily report say so).
* The scanner's budget is 14 odds credits a day, so sportsbook prices are often >90 min old and rejected
  as stale. That's deliberate: stale prices create fake edges.
* "Sharp" flow is public prints only; one trader splitting an order looks like many trades.
* Closing-line value uses the last mid before kickoff, recorded only when a lab round ran pregame.
* The lab only checks ladders and NO baskets. YES baskets need proof that the outcomes are exhaustive,
  which the API doesn't give.
* Market making and new forecasting models are intentionally not built.

## Best Price (read-only)

`research_lab/best_price.py` checks every new **live** Sharp pick (from `live_sharp_trades.csv`, read-only) against
the same bet on Polymarket in the next lab round (a few minutes later), fees included, and records both prices in
`research_lab/data/BEST_PRICE/best_price.csv`. It never places or changes an order; live bets stay on Kalshi.
The **Best Price** page shows how often Polymarket was cheaper, by how much, and what buying at the cheaper venue
would have saved. Game/match winners only for now; spreads, totals and ties are logged as "not compared".
Novig isn't included: its prices only came through The Odds API, whose free credits are used up.
