"""Honest fair values for our own models (Nov 4, 2026).

The NCAA, NBA game and NCAAB models print raw win probabilities that are far too confident:
NCAA STRONG picks the model called 74% won 61% (the market said 58%), and the queue was showing
Sacramento State +26.5 at 95% / +81% EV. Checked against every settled pick, the market's own
no-vig price beat the raw model on log loss in almost every market.

So a model's fair value is now shrunk toward the market:

    fair = sigmoid( logit(market) + w * (logit(model) - logit(market)) )

w is how much of the model's disagreement with the market has actually held up. It's fitted per
model and market (ML / SPREAD / TOTAL) from that model's settled picks, then pulled toward a
prior of 1/3 until there are a few hundred results, and capped at 0.6. It refits itself as picks
settle, so a model that proves itself earns more trust automatically.

On top of that, fair is never more than MAX_EDGE (10 points) from the market. Across ~800 settled
model picks, a 25-point "edge" didn't reliably win more than a 10-point one; a huge gap from the
market almost always means the model is missing something (injury, QB change, weather).

Pinnacle-based fair values (Prop Board, Odds Fam, Trader Follow) and the NBA prop model (which
already blends with the market and checks out as calibrated) are left as they are.
"""
from __future__ import annotations

import csv
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIOR_W = 1 / 3          # trust in a model's disagreement before it has a record
PRIOR_N = 100            # how many settled picks the prior is worth
MAX_W = 0.6
VIG = 1.0476             # standard two-way -110/-110 overround, for rows with no no-vig market price
REFIT_EVERY = 600        # seconds
MAX_EDGE = 0.10          # fair is never more than 10 points away from the market

MODELS = {   # source -> ledger, result column, win/loss codes
    "ncaa_grader": ("ncaa_grader/data/picks.csv", "result", ("W",), ("L",)),
    "nba_grader": ("nba_grader/data/picks.csv", "result", ("W",), ("L",)),
    "ncaab_model": ("ncaab_grader/data/picks.csv", "result", ("W",), ("L",)),
}
_cache: dict[str, tuple[float, dict]] = {}


def _f(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _implied(american: float) -> float:
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def market_prob(row: dict) -> float | None:
    """The market's no-vig probability for this side: the ledger's own when it has one (ML vs
    Pinnacle), else the listed price with a standard two-way vig taken out."""
    m = _f(row.get("market_prob"))
    if m and 0 < m < 1:
        return m
    o = _f(row.get("odds"))
    if o is None or -100 < o < 100:
        return None
    return _implied(o) / VIG


def _blend(model: float, market: float, w: float) -> float:
    return _sig(_logit(market) + w * (_logit(model) - _logit(market)))


def fit_weight(rows: list[tuple[float, float, bool]]) -> float:
    """Best w on a 0..1 grid by log loss, shrunk toward the prior by sample size."""
    if not rows:
        return PRIOR_W
    best_w, best_ll = PRIOR_W, None
    for i in range(101):
        w = i / 100
        ll = 0.0
        for p, m, won in rows:
            q = min(max(_blend(p, m, w), 1e-6), 1 - 1e-6)
            ll -= math.log(q if won else 1 - q)
        if best_ll is None or ll < best_ll:
            best_w, best_ll = w, ll
    n = len(rows)
    return min(MAX_W, (n * best_w + PRIOR_N * PRIOR_W) / (n + PRIOR_N))


def _read(rel: str) -> list[dict]:
    p = ROOT / rel
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def weights(source: str) -> dict:
    """{market: w} for one model, refitted at most every REFIT_EVERY seconds."""
    hit = _cache.get(source)
    if hit and time.time() - hit[0] < REFIT_EVERY:
        return hit[1]
    out = {}
    if source in MODELS:
        rel, col, win, loss = MODELS[source]
        by = {}
        for r in _read(rel):
            res = r.get(col)
            p, m = _f(r.get("model_prob")), market_prob(r)
            if res in win + loss and p and m and 0 < p < 1:
                by.setdefault(str(r.get("market") or "").upper(), []).append((p, m, res in win))
        out = {mk: fit_weight(v) for mk, v in by.items()}
    _cache[source] = (time.time(), out)
    return out


def calibrated(source: str, row: dict) -> float | None:
    """Fair win probability for one of our model's picks, or the raw model if it can't be priced."""
    p = _f(row.get("model_prob"))
    if p is None or not 0 < p < 1:
        return None
    if source not in MODELS:
        return p
    m = market_prob(row)
    if m is None or not 0 < m < 1:
        return p
    w = weights(source).get(str(row.get("market") or "").upper(), PRIOR_W)
    fair = min(max(_blend(p, m, w), m - MAX_EDGE), m + MAX_EDGE)
    return round(min(max(fair, 0.01), 0.99), 4)

