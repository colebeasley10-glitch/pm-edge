"""nba_v1: opponent-adjusted NBA offense/defense ratings from final scores, plus rest.

Each team-game is one observation:

    points = season avg + home edge/2 + team offense + opponent defense - fatigue

fit by weighted ridge regression (pure Python). Recent games count more (30-day
half-life) and last season's games count at PRIOR_WEIGHT, so October isn't built
on four games. A team on the second night of a back-to-back loses B2B_PTS.

Fit only on games before a tip-off and there's no look-ahead. What it does NOT see:
injuries and rest days announced that morning. The market does, so a big model-vs-line
gap on an injury night is usually the model being wrong. That's what the grader measures.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

HALF_LIFE_DAYS = 30.0
PRIOR_WEIGHT = 0.35       # last season's games, on top of the time decay
RIDGE = 4.0               # shrink each team toward average by ~4 full-weight games
MARGIN_CAP = 30.0         # blowouts: trim the margin, keep the total
B2B_PTS = 1.5             # points lost on the 2nd night of a back-to-back
MIN_WEIGHT = 3.0          # need ~3 full-weight games per team to project
ITERATIONS = 120
MARGIN_SD = 12.0


@dataclass
class Proj:
    home_margin: float
    total: float
    home_win_prob: float


def _day(g: dict) -> date | None:
    try:
        return date.fromisoformat(str(g.get("week"))[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(str(g.get("startDate")).replace("Z", "+00:00")).date()
        except ValueError:
            return None


class NBARatings:
    def __init__(self, games: list[dict], *, as_of: date, season: int | None = None,
                 key: Callable[[str], str] = lambda s: s, **kw):
        self.key, self.as_of = key, as_of
        self.half_life = kw.get("half_life", HALF_LIFE_DAYS)
        self.prior_weight = kw.get("prior_weight", PRIOR_WEIGHT)
        self.ridge = kw.get("ridge", RIDGE)
        self.min_weight = kw.get("min_weight", MIN_WEIGHT)
        self.b2b = kw.get("b2b_pts", B2B_PTS)
        self.season = season
        self.mu: dict = {}
        self.hfa = 2.5
        self.off: dict[str, float] = defaultdict(float)
        self.dfn: dict[str, float] = defaultdict(float)
        self.w: dict[str, float] = defaultdict(float)
        self.last_played: dict[str, date] = {}
        self._fit([g for g in games if (_day(g) or as_of) < as_of])

    def _weight(self, g: dict, d: date) -> float:
        w = 0.5 ** (max((self.as_of - d).days, 0) / self.half_life)
        if self.season is not None and str(g.get("season")) != str(self.season):
            w *= self.prior_weight
        return w

    def _fit(self, games):
        obs = []  # (team, opp, site, points, weight, season)
        played = defaultdict(set)
        for g in games:
            d = _day(g)
            if d:
                for t in (g.get("homeTeam"), g.get("awayTeam")):
                    played[self.key(t)].add(d)
        for t, days in played.items():
            self.last_played[t] = max(days)
        for g in games:
            hp, ap, d = g.get("homePoints"), g.get("awayPoints"), _day(g)
            if not g.get("completed") or hp is None or ap is None or d is None:
                continue
            hp, ap = float(hp), float(ap)
            excess = abs(hp - ap) - MARGIN_CAP
            if excess > 0:
                hp, ap = (hp - excess / 2, ap + excess / 2) if hp > ap else (hp + excess / 2, ap - excess / 2)
            h, a = self.key(g["homeTeam"]), self.key(g["awayTeam"])
            prev = d.fromordinal(d.toordinal() - 1)
            hp += self.b2b * (prev in played[h])   # add back what fatigue cost -> true strength
            ap += self.b2b * (prev in played[a])
            s = 0 if g.get("neutralSite") else 1
            w = self._weight(g, d)
            sea = str(g.get("season"))
            obs.append((h, a, s, hp, w, sea))
            obs.append((a, h, -s, ap, w, sea))
            self.w[h] += w
            self.w[a] += w
        if not obs:
            return
        by_team, by_opp, by_sea = defaultdict(list), defaultdict(list), defaultdict(list)
        for i, (t, o, _, _, _, sea) in enumerate(obs):
            by_team[t].append(i)
            by_opp[o].append(i)
            by_sea[sea].append(i)
        sited = [i for i, ob in enumerate(obs) if ob[2] != 0]
        mu = {s: 112.0 for s in by_sea}
        for _ in range(ITERATIONS):
            for s, idx in by_sea.items():
                num = sum(obs[i][4] * (obs[i][3] - obs[i][2] * self.hfa / 2 - self.off[obs[i][0]] - self.dfn[obs[i][1]])
                          for i in idx)
                mu[s] = num / sum(obs[i][4] for i in idx)
            if sited:
                num = sum(obs[i][4] * obs[i][2] * (obs[i][3] - mu[obs[i][5]] - self.off[obs[i][0]] - self.dfn[obs[i][1]])
                          for i in sited)
                self.hfa = 2 * num / sum(obs[i][4] for i in sited)
            for t, idx in by_team.items():
                r = sum(obs[i][4] * (obs[i][3] - mu[obs[i][5]] - obs[i][2] * self.hfa / 2 - self.dfn[obs[i][1]]) for i in idx)
                self.off[t] = r / (sum(obs[i][4] for i in idx) + self.ridge)
            for o, idx in by_opp.items():
                r = sum(obs[i][4] * (obs[i][3] - mu[obs[i][5]] - obs[i][2] * self.hfa / 2 - self.off[obs[i][0]]) for i in idx)
                self.dfn[o] = r / (sum(obs[i][4] for i in idx) + self.ridge)
        self.mu = mu

    def _avg(self) -> float:
        """Scoring level to project with: this season's, else the most recent season fitted."""
        if self.season is not None and str(self.season) in self.mu:
            return self.mu[str(self.season)]
        return self.mu[max(self.mu)] if self.mu else 112.0

    def project(self, home: str, away: str, neutral: bool = False, *, on: date | None = None) -> Proj | None:
        h, a = self.key(home), self.key(away)
        if self.w.get(h, 0) < self.min_weight or self.w.get(a, 0) < self.min_weight:
            return None
        on = on or self.as_of
        yday = on.fromordinal(on.toordinal() - 1)
        h_b2b = self.last_played.get(h) == yday
        a_b2b = self.last_played.get(a) == yday
        s = 0 if neutral else 1
        mu = self._avg()
        hp = mu + s * self.hfa / 2 + self.off[h] + self.dfn[a] - self.b2b * h_b2b
        ap = mu - s * self.hfa / 2 + self.off[a] + self.dfn[h] - self.b2b * a_b2b
        margin = hp - ap
        return Proj(round(margin, 1), round(hp + ap, 1),
                    round(0.5 * (1 + math.erf(margin / MARGIN_SD / math.sqrt(2))), 4))

    def ratings(self) -> list[tuple[str, float, float, float]]:
        """(team, offense, defense, net) — net > 0 is better than average, per game."""
        teams = [t for t in self.w if self.w[t] >= self.min_weight]
        return sorted(((t, self.off[t], self.dfn[t], self.off[t] - self.dfn[t]) for t in teams), key=lambda x: -x[3])
