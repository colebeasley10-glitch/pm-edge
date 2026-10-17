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
