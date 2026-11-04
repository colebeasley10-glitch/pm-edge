"""ncaab_v1 ratings: opponent-adjusted, possession-based efficiency with a preseason prior.

For every team-game with a possession count we observe points per 100 possessions:

    eff(team vs opp) = mu + site * hca / 2 + O[team] + D[opp]          (site = +1 home, -1 away, 0 neutral)
    poss(game)       = tempo + P[home] + P[away]

O, D and P are pulled toward each team's PRESEASON PRIOR (not toward zero) by a ridge penalty
worth `prior_games` games of evidence, so early-November ratings are mostly last season's
team adjusted for who came back, and by February they are mostly this season's results.
Games are weighted by recency (half-life `half_life` days). Every fit only sees games played
strictly before `as_of`, so a backtest built on it has no look-ahead.

Projection for home vs away:
    pts_home = poss * (mu + s*hca/2 + O[home] + D[away]) / 100
    pts_away = poss * (mu - s*hca/2 + O[away] + D[home]) / 100

Pure numpy; the normal equations are accumulated directly (no design matrix), ~5 ms per fit.
All tuning constants were chosen on 2014-2019 seasons only (see tools/ncaab_backtest.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

# Tuned on the TRAIN seasons (2014-2019) by tools/ncaab_backtest.py; never on the test seasons.
DEFAULTS = dict(
    half_life=90.0,       # days; recency weighting inside the season
    prior_games=3.0,      # ridge strength of the preseason prior, in full-weight games
    tempo_prior_games=3.0,
    eff_cap=40.0,         # cap |eff - league| per team-game (pts/100) so one blowout can't dominate
    hca_prior=4.5,        # pts/100 poss; ridge target for home-court edge
    hca_games=200.0,      # how firmly hca is held near hca_prior
    hca_pts=None,         # if set: home-court edge FIXED at this many points per game (overrides hca_prior)
    min_games=0,          # with a prior every D1 team can be projected from game 1
)


@dataclass
class Proj:
    home_pts: float
    away_pts: float
    poss: float

    @property
    def home_margin(self) -> float:
        return self.home_pts - self.away_pts

    @property
    def total(self) -> float:
        return self.home_pts + self.away_pts


class Ratings:
    """Fit once per day. `games`: iterable of dicts with keys
    day (date), home, away, home_pts, away_pts, poss (float or None), neutral (bool).
    `prior`: {team: (O0, D0, P0)} in pts/100 and possessions relative to average.
    `league`: (mu0, tempo0) priors for the season's scoring level and pace."""

    def __init__(self, games, *, as_of: date, prior: dict, league=(103.0, 68.5), **kw):
        p = {**DEFAULTS, **kw}
        self.p, self.as_of, self.prior = p, as_of, prior
        self.mu0, self.tempo0 = league
        gs = [g for g in games if g["day"] < as_of]
        teams = set(prior)
        for g in gs:
            teams.add(g["home"]); teams.add(g["away"])
        self.teams = sorted(teams, key=str)
        self.idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        h = np.array([self.idx[g["home"]] for g in gs], dtype=int)
        a = np.array([self.idx[g["away"]] for g in gs], dtype=int)
        self.n_games = dict(zip(self.teams, (np.bincount(h, minlength=n) + np.bincount(a, minlength=n)).tolist()))
        age = np.array([(as_of - g["day"]).days for g in gs], dtype=float)
        w = 0.5 ** (np.maximum(age, 0) / p["half_life"])
        hp = np.array([float(g["home_pts"]) for g in gs]); ap = np.array([float(g["away_pts"]) for g in gs])
        poss = np.array([float(g.get("poss") or 0.0) for g in gs])
        site = np.array([0.0 if g.get("neutral") else 1.0 for g in gs])
        self._fit_tempo(h, a, poss, w)                      # tempo first: a fixed hca_pts needs it
        self._fit_eff(h, a, hp, ap, poss, site, w)

    # ------------------------------------------------------------------ fitting
    def _prior_vec(self, k: int) -> np.ndarray:
        return np.array([self.prior.get(t, (0.0, 0.0, 0.0))[k] for t in self.teams], dtype=float)

    @staticmethod
    def _normal_eq(N, cols, vals, y, w):
        """A = X'WX, b = X'Wy for a sparse design given as parallel (column index, value) lists."""
        A = np.zeros((N, N)); b = np.zeros(N)
        for ci, vi in zip(cols, vals):
            np.add.at(b, ci, w * vi * y)
            for cj, vj in zip(cols, vals):
                np.add.at(A, (ci, cj), w * vi * vj)
        return A, b

    def _fit_eff(self, h, a, hp, ap, poss, site, w):
        n = len(self.teams)
        N = 2 + 2 * n                                     # mu, hca, O[n], D[n]
        ok = poss > 0
        t = np.concatenate([h[ok], a[ok]]); o = np.concatenate([a[ok], h[ok]])
        s = np.concatenate([site[ok], -site[ok]])
        y = 100.0 * np.concatenate([hp[ok], ap[ok]]) / np.concatenate([poss[ok], poss[ok]])
        y = self.mu0 + np.clip(y - self.mu0, -self.p["eff_cap"], self.p["eff_cap"])
        ww = np.concatenate([w[ok], w[ok]])
        one = np.ones_like(s)
        A, b = self._normal_eq(N, [np.zeros_like(t), np.ones_like(t), 2 + t, 2 + n + o], [one, s / 2.0, one, one], y, ww)
        lam = self.p["prior_games"] * 2.0               # ~one O row and one D row per team-game
        A[2:2 + n, 2:2 + n] += np.eye(n) * lam; b[2:2 + n] += lam * self._prior_vec(0)
        A[2 + n:, 2 + n:] += np.eye(n) * lam; b[2 + n:] += lam * self._prior_vec(1)
        if self.p.get("hca_pts") is not None:
            # Fixed home court (pts/game -> pts/100 poss at this season's tempo), held hard so a small
            # or lopsided sample (November buy games) can't drag it around.
            A[1, 1] += 1e7; b[1] += 1e7 * 100.0 * float(self.p["hca_pts"]) / self.tempo
        else:
            A[1, 1] += self.p["hca_games"]; b[1] += self.p["hca_games"] * self.p["hca_prior"]
        A[0, 0] += 50.0; b[0] += 50.0 * self.mu0           # scoring level: weak pull to the prior season
        for lo, hi in ((2, 2 + n), (2 + n, N)):            # identify: mean O = mean D = 0 (soft)
            c = np.zeros(N); c[lo:hi] = 1.0
            A += 1e3 * np.outer(c, c) / n
        x = np.linalg.solve(A, b)
        self.mu, self.hca = float(x[0]), float(x[1])
        self.O, self.D = x[2:2 + n], x[2 + n:]
        self.eff_obs = int(len(y))

    def _fit_tempo(self, h, a, poss, w):
        n = len(self.teams)
        ok = (poss >= 45) & (poss <= 100)
        hh, aa, y, ww = h[ok], a[ok], poss[ok], w[ok]
        one = np.ones_like(y)
        A, b = self._normal_eq(n + 1, [np.zeros_like(hh), 1 + hh, 1 + aa], [one, one, one], y, ww)
        lam = self.p["tempo_prior_games"]
        A[1:, 1:] += np.eye(n) * lam; b[1:] += lam * self._prior_vec(2)
        A[0, 0] += 50.0; b[0] += 50.0 * self.tempo0
        c = np.zeros(n + 1); c[1:] = 1.0
        A += 1e3 * np.outer(c, c) / n
        x = np.linalg.solve(A, b)
        self.tempo, self.P = float(x[0]), x[1:]

    # --------------------------------------------------------------- projecting
    def has(self, team: str) -> bool:
        return team in self.idx and (team in self.prior or self.n_games.get(team, 0) >= 5)

    def project(self, home: str, away: str, neutral: bool = False) -> Proj | None:
        if not (self.has(home) and self.has(away)):
            return None
        h, a = self.idx[home], self.idx[away]
        s = 0 if neutral else 1
        poss = self.tempo + self.P[h] + self.P[a]
        eh = self.mu + s * self.hca / 2 + self.O[h] + self.D[a]
        ea = self.mu - s * self.hca / 2 + self.O[a] + self.D[h]
        return Proj(poss * eh / 100.0, poss * ea / 100.0, poss)

    def table(self) -> list[dict]:
        """One row per team: adjusted offense/defense (pts/100 vs average D1), net, tempo."""
        out = []
        for t, i in self.idx.items():
            out.append({"team": t, "adj_o": round(self.mu + self.O[i], 1), "adj_d": round(self.mu + self.D[i], 1),
                        "net": round(self.O[i] - self.D[i], 1), "tempo": round(self.tempo + self.P[i], 1),
                        "games": self.n_games.get(t, 0)})
        return sorted(out, key=lambda r: -r["net"])


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
