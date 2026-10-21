"""scores_v1: opponent-adjusted offense/defense ratings from final scores only.

Each team-game is one observation:

    points scored = league avg + home edge + team offense + opponent defense

fit by ridge regression (coordinate descent, pure Python). Offense > 0 scores more
than average, defense > 0 ALLOWS more than average. Blowout margins are capped so
cupcake games don't dominate. Fit it only on games before the week you're
projecting and there is no look-ahead, which is what makes the backtest honest.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

MARGIN_CAP = 28.0     # a 63-0 counts as 28-0 + the loser's points
RIDGE = 3.0           # shrink each team toward average by ~3 games' worth
MIN_GAMES = 3         # don't project a team with fewer games in the fit
ITERATIONS = 150
MARGIN_SD = 16.0


@dataclass
class Proj:
    home_margin: float
    total: float
    home_win_prob: float


class ScoreRatings:
    def __init__(self, games: list[dict], key: Callable[[str], str] = lambda s: s, **kw):
        self.key = key
        self.cap = kw.get("margin_cap", MARGIN_CAP)
        self.ridge = kw.get("ridge", RIDGE)
        self.min_games = kw.get("min_games", MIN_GAMES)
        self.mu, self.hfa = 27.0, 2.5
        self.off: dict[str, float] = defaultdict(float)
        self.dfn: dict[str, float] = defaultdict(float)
        self.n: dict[str, int] = defaultdict(int)
        self._fit(games)

    def _fit(self, games):
        obs = []  # (team, opp, site, points)
        for g in games:
            hp, ap = g.get("homePoints"), g.get("awayPoints")
            if not g.get("completed") or hp is None or ap is None:
                continue
            hp, ap = float(hp), float(ap)
            if hp - ap > self.cap:
                hp = ap + self.cap
            elif ap - hp > self.cap:
                ap = hp + self.cap
            h, a = self.key(g["homeTeam"]), self.key(g["awayTeam"])
            s = 0 if g.get("neutralSite") else 1
            obs.append((h, a, s, hp))
            obs.append((a, h, -s, ap))
            self.n[h] += 1
            self.n[a] += 1
        if not obs:
            return
        by_team, by_opp = defaultdict(list), defaultdict(list)
        for i, (t, o, _, _) in enumerate(obs):
            by_team[t].append(i)
            by_opp[o].append(i)
        sited = [i for i, ob in enumerate(obs) if ob[2] != 0]
        for _ in range(ITERATIONS):
            res = [y - ob_s * self.hfa / 2 - self.off[t] - self.dfn[o] for t, o, ob_s, y in obs]
            self.mu = sum(res) / len(res)
            if sited:
                num = sum(obs[i][2] * (obs[i][3] - self.mu - self.off[obs[i][0]] - self.dfn[obs[i][1]]) for i in sited)
                self.hfa = 2 * num / len(sited)
            for t, idx in by_team.items():
                r = sum(obs[i][3] - self.mu - obs[i][2] * self.hfa / 2 - self.dfn[obs[i][1]] for i in idx)
                self.off[t] = r / (len(idx) + self.ridge)
            for o, idx in by_opp.items():
                r = sum(obs[i][3] - self.mu - obs[i][2] * self.hfa / 2 - self.off[obs[i][0]] for i in idx)
                self.dfn[o] = r / (len(idx) + self.ridge)

    def project(self, home: str, away: str, neutral: bool = False) -> Proj | None:
        h, a = self.key(home), self.key(away)
        if self.n.get(h, 0) < self.min_games or self.n.get(a, 0) < self.min_games:
            return None
        s = 0 if neutral else 1
        hp = self.mu + s * self.hfa / 2 + self.off[h] + self.dfn[a]
        ap = self.mu - s * self.hfa / 2 + self.off[a] + self.dfn[h]
        margin = hp - ap
        return Proj(round(margin, 1), round(hp + ap, 1),
                    round(0.5 * (1 + math.erf(margin / MARGIN_SD / math.sqrt(2))), 4))
