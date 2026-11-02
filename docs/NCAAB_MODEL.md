# NCAA Men's Basketball Model (`ncaab_v1`) — paper lane

Built Nov 2, 2026 for the 2026-27 season. **Paper only.** Separate ledger, bankroll and page from the
NCAA football model and every other SharpIQ lane. Nothing in it can place a bet.

**Short version.** The model gets close to the closing line on accuracy, but from 2020 on it has
almost no information the closing line doesn't already have. One pocket held up out of sample:
spreads in **neutral-court games**. That's the only thing it tags as a BET. It's unproven: the
95% interval includes losing, and the most recent season (2025-26) was slightly negative. The
point of the paper season is to find out if it's real, mainly by checking whether its bets beat
the closing line.

---

## 1. Data: what was actually obtained

| Need | Source | Status |
|---|---|---|
| Results, venue, neutral flag, conference, 2012–2026 | hoopR / sportsdataverse (ESPN), GitHub-hosted, free | ✅ 88,469 games |
| Team box scores → possessions, four factors | same | ✅ 99.8% of games |
| Player box scores → returning minutes, transfers | same | ✅ 2012–2026 |
| 2026-27 rosters (who returned, who transferred in) | ESPN roster file via hoopR | ✅ 4,588 returners, 1,922 transfers |
| Historical lines (spread, total, ML, juice) | ESPN "pickcenter" inside hoopR's raw game files (read with HTTP range requests) | ✅ 50,738 lined games: 2014–2023 and 2026. **2024 and 2025 have none.** MLs mostly 2018+ |
| Live schedule, odds (open + current), scores, box scores | ESPN public scoreboard/summary, `groups=50` (all D1) | ✅ in GitHub Actions (blocked in my sandbox, so live parsing is unit-tested, not run end to end) |
| KenPom / Bart Torvik | — | ❌ Not used. KenPom needs a paid login and its terms don't allow scraping. Torvik wasn't reachable from here. The model builds its own adjusted efficiency from box scores instead. |
| Injuries / lineups | — | ❌ No free reliable college feed. Flagged on every play. |
| Opening lines (historical) | — | ❌ Only one line per game exists in the history, and it's close to the closing line. The live lane records opening and current lines going forward. |
| Travel distance, altitude | — | ❌ Not modeled. Rest days were left out (they barely matter in college). |

**About the historical line.** ESPN's pickcenter was captured after each game. Usually that's the
consensus closing line (2014–2023; teamrankings in 2019) or DraftKings (2026). There's no timestamp, so
it's treated as the closing line. Beating a closing line is the hardest test there is. It also means
**historical CLV can't be measured**, only live CLV.

## 2. The model

**Ratings** (`ncaab_model/ratings.py`). For every D1-vs-D1 team-game, offensive efficiency is
`100·pts/poss = μ + site·HCA/2 + O[team] + D[opp]`, and pace is `poss = tempo + P[home] + P[away]`.
Possessions use `FGA − ORB + TO + 0.475·FTA`. The fit is weighted ridge regression. Games are
recency-weighted (90-day half-life), and per-game efficiency is capped at ±40 per 100 so one blowout
can't take over. **The ridge pulls each team toward its preseason prior, not toward zero.** The prior
counts as 3 games of evidence. Refits happen every game day, using only games before that day.

Projection: `pts_home = poss·(μ + HCA/2 + O_home + D_away)/100` (no home-court term on neutral
floors), and the same for the away team. Home court comes out to about 3.3 points.

**Preseason prior** (`tools/ncaab_backtest/priors.py`). Each team's end-of-season O, D and tempo
are regressed on: last season's rating, last season × returning-minutes share, last season's
conference average, returning minutes, incoming D1-transfer minutes, and transfer minutes × the
rating of the teams they came from. The fit is walk-forward, using only earlier seasons.
Out of sample (2020–22), the prior's net rating correlates 0.78–0.85 with the final rating. That's an
RMSE of 5.7–7.4 per 100 possessions, vs 7.0–7.7 for "same as last year."
The 2026-27 priors are in `ncaab_model/data/priors_2027.csv`, fit on 2014–2026.

**Tuning.** half-life {45, 90, ∞} × prior strength {1.5, 2, 3, 6, 10} × cap {25, 40, 60}, scored
on margin RMSE over **2014–2019 only**. Results are in `ncaab_model/data/backtest/tune_results*.csv`.

### Model comparison (walk-forward MAE of the predicted margin, points)

| Season | **ncaab_v1** | no prior | last-yr prior | points-only (no possessions) | Elo | closing line |
|---|---|---|---|---|---|---|
| 2014–19 avg | 8.65 | 8.89 | 8.80 | 8.74 | 8.85 | 8.61 |
| 2020–22 avg | 9.00 | 9.18 | 9.19 | 9.05 | 9.17 | 8.94 |
| **2023 (test)** | **8.94** | 9.16 | 9.11 | 9.02 | 9.13 | 8.91 |
| **2026 (test)** | **8.98** | 9.38 | 9.12 | 9.04 | 9.15 | 8.86 |

v1 beats every alternative in every season, and the priors help most early on. It's still
0.03–0.12 points behind the closing line. Totals MAE: v1 13.1–13.6 vs the closing total
13.0–13.3.

## 3. From projection to probability (the key step)

Turning "model says −7, market says −4" directly into a normal-distribution probability
overstates the edge, because the market knows things the model doesn't. So the backtest measures
how much of the disagreement actually shows up in results:

`(margin + spread) = b · (model_margin + spread) + noise`

| Seasons | b (spreads) | b (totals) |
|---|---|---|
| 2014–19 (train) | 0.42 | 0.18 |
| 2020–22 (validation) | 0.08 | −0.02 |
| 2023 + 2026 (test) | 0.07 | 0.01 |
| **neutral courts**, 2020–22 → test | **0.26 → 0.34** | ~0 |

A b near zero means the closing line already has all the model's information. The market got
much sharper after 2019, or the earlier line captures were less "closing" (it can't be told which).
Either way, the model is only useful where b > 0. The probabilities shown are:

- Spread: `P(cover) = Φ(b·edge / σ)`, with b = 0.046 and σ = 11.1 on home courts, and b = 0.293 and σ = 11.5 on neutral courts (fit on 2020–2026)
- Total: `P(over) = Φ((a + b·edge)/σ)`, with b ≈ 0. Shown for research, never a BET
- ML: `P(win) = Φ((−spread + b·edge)/σ)`, compared with the actual price

EV = `p × decimal odds − 1`. Break-even = the price's implied probability. Minimum acceptable odds = the
price where EV equals the threshold.

## 4. Betting backtest (spreads/totals/ML at the closing price, real juice where recorded, −110 otherwise)

**First, the plan as pre-registered** (calibrate on 2014–19, pick thresholds on 2020–22,
untouched test on 2023 + 2026). It **failed**: spreads 32-44-1, −18.6% (77 bets);
moneylines 7-21, −33.4% (28 bets); totals were disabled after validation (−14% at +3% EV). The 2014–19
calibration was far too confident for the modern market. Full output:
`ncaab_model/data/backtest/report_v1_plan.txt`.

**Second pass, recent-era calibration only** (calibrated on 2020–22, test on 2023 + 2026,
nothing refit on test):

| Rule | Bets | W-L-P | ROI | 95% CI |
|---|---|---|---|---|
| **Neutral-court spreads, EV ≥ +2%** | 113 | 64-47-2 | **+11.0%** | −6.5% to +28.6% |
|   · 2023 | 56 | 36-18-2 | +26.6% | +3.1% to +50.0% |
|   · 2026 | 57 | 28-29 | −4.2% | −29.8% to +21.3% |
| Home-court spreads, EV ≥ +2% | 26 | 13-13 | +2.8% | −37% to +43% |
| Moneylines, EV ≥ +2% | 395 | 201-194 | −8.9% | −17.8% to 0.0% |
| Totals, EV ≥ +2% | 9 | 1-8 | — | too few |

**Be skeptical.** About 10 segments were checked (venue, month, conference tier, spread size and others),
and neutral courts was the only one with signal in both validation and test. That's a multiple-testing
risk. Within the neutral bets, underdogs were 98 of 113, and the 12 NCAA-tournament bets went 10-2.
Edges over 8 points lost in every test split (cover rate 37.5%), so those are capped to WATCH.

**Calibration (test, every game).** Spread probabilities from 50–56% landed within about 1–3 points of the
actual rate, and the few above 56% did worse than predicted. That's the reason for the large-edge cap and the
small b.

**Staking on the $500 bankroll** (the 113 test bets, in date order, 8% daily cap):
flat $5 → **$562.38** (max drawdown $56). Capped quarter Kelly → $539.44 (max drawdown $83).
Kelly bets most on the highest-EV plays, which were the least reliable. **So flat $5 is the
primary stake** and quarter Kelly is tracked next to it. Stakes never go up after losses.

## 5. The daily system (`.github/workflows/ncaab-model.yml`, every hour on the cron-job.org tick)

1. Pull the ESPN D1 scoreboard from Nov 1 through tomorrow, plus box scores for new finals (possessions).
2. Refit ratings for each game day using only earlier finals, starting from `priors_2027.csv`.
3. For each game in the next 30 hours with a line: price the spread, total and ML. Keep the better side of each.
4. Tier: **BET** = neutral-court spread, EV ≥ +2%, edge ≤ 8 points. **WATCH** = positive EV that doesn't
   qualify, or a market without a validated rule. **PASS** = no edge. All of them are logged (the
   whole-model test), each with a timestamp, the line and price frozen at first sight, the model
   version, the minimum acceptable odds and flags (early season, neutral, new D1 team,
   injuries not modeled).
5. Re-read the line until tipoff (closing line → **CLV**), grade finals, update the bankroll.
6. Write `ncaab_grader/data/daily_report.md` (the requested format), `picks.csv`, and
   `ncaab_paper_bets.xlsx` (Bets / Summary with daily, weekly and monthly P/L / Ratings).

**Automatic suspension** (BETs drop to WATCH and the page shows why): possession data for under 70% of
finals, more than 30% of lined games unprojectable, ESPN errors with no fresh data, or live Brier score
> 0.256 on 150+ settled spread/total picks.

**No phone alerts.** The workflow gets no Pushover secrets, which keeps alerts limited to the Bet Queue
and BreakTheBank NFL.

**Recalibration is scheduled, never reactive:** once in early January (about 2 months of live data) and once
after the season. Each refit gets a new version (`ncaab_v2`, …). Old picks keep the version they
were made under, so models can be compared fairly.

## 6. When this would earn real money

Neutral-court bets should **beat the closing line on average** over at least 100 bets, and still be
positive ROI with a lower 95% bound above about −5%. If CLV is ≤ 0 after 100 bets, the hypothesis is
dead whatever the W-L says.

## 7. What would make it better (not available for free here)

- **Historical opening lines.** The live lane may be beatable at early numbers even though it isn't
  at the close. Paid odds history would let that be backtested instead of waiting a season.
- **An injury/availability feed** (none is reliable for college).
- **KenPom / Torvik ratings** as an extra input, if you have a subscription with suitable terms.
- **Travel/altitude**, from venue coordinates.

## Reproducing

`tools/ncaab_backtest/` contains every script (see `download.py` for the order). Data downloads
to `ncaab_data/` (about 250 MB, not committed). Outputs are in `ncaab_model/data/backtest/`.
The live pipeline was checked against the backtest: identical projections on 300 random 2026 games
(max difference 0.0 points).
