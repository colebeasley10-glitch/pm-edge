"""Claude NCAA Pool Model (CPM v1, Nov 1, 2026) -- picks for Clay's pool (12 picks, 3 double-point keys)
and Chappy's pool (6 picks, 1 key).

Why it works the way it does (evidence: CFBD 2022-2026, ~4,300 FBS games with lines; see the "NCAA Pool Model — Audit & Rebuild" doc):
  * Public power ratings (FPI, Elo, our own opponent-adjusted rating) beat the line only ~50% of the time,
    and bigger rating-vs-line gaps do NOT cover more often. So the model does not trust its own rating
    against the market.
  * The market's current number is the best fair line available. Clay's pool line is frozen on Tuesday;
    picks lock Saturday noon. Betting the frozen number in the direction the market has since moved
    won 55-58% in every season, and the win rate tracks Phi(gap / 15.1) closely.
  * Final margins pile up on 3 and 7 (about 3x and 2.5x a smooth curve), so half a point across those
    numbers is worth far more than elsewhere. The margin model below carries that.

Inputs per game: the pool line (home spread, and total if offered) and the CURRENT market line
(median of books; a sharp book like Pinnacle when available), treated as the 50/50 point. Our own rating
projection can be blended in (RATING_WEIGHT) but is off by default: it added nothing in backtests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SPREAD_SD = 15.1        # SD of final margin around the closing spread, FBS 2022-26
TOTAL_SD = 15.6         # SD of total points around the closing total
RATING_WEIGHT = 0.0     # our own rating adds nothing over the market in backtests (0.1 tested: no gain); kept as an option
MAX_MARGIN = 100

# How often each final margin happens vs a smooth normal curve (FBS 2022-26). Shrunk toward 1 past 21.
_KEY = {1: .89, 2: .81, 3: 2.74, 4: 1.01, 5: .56, 6: .87, 7: 2.29, 8: .81, 9: .32, 10: 1.31, 11: .69, 12: .45,
        13: .66, 14: 1.40, 15: .54, 16: .45, 17: 1.54, 18: .88, 19: .65, 20: .67, 21: 1.61, 22: .84, 23: .78,
        24: 1.21, 25: .97, 26: .80, 27: .98, 28: 1.50}


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def margin_pmf(fair_home_margin: float, sd: float = SPREAD_SD) -> dict[int, float]:
    """Probability of each final home margin. No ties in college football; key numbers boosted."""
    pmf = {}
    for m in range(-MAX_MARGIN, MAX_MARGIN + 1):
        if m == 0:
            continue
        p = _phi((m + 0.5 - fair_home_margin) / sd) - _phi((m - 0.5 - fair_home_margin) / sd)
        pmf[m] = p * _KEY.get(abs(m), 1.0)
    s = sum(pmf.values())
    return {m: p / s for m, p in pmf.items()}


def spread_cover(fair_home_margin: float, home_line: float, side: str) -> tuple[float, float]:
    """(win, push) probability for taking `side` ('home'/'away') at the pool's home spread."""
    pmf = margin_pmf(fair_home_margin)
    win = push = 0.0
    for m, p in pmf.items():
        x = m + home_line                     # home covers if > 0
        if x == 0:
            push += p
        elif (x > 0) == (side == "home"):
            win += p
    return win, push


def total_cover(fair_total: float, line: float, side: str) -> float:
    z = (line - fair_total) / TOTAL_SD
    return 1 - _phi(z) if side == "over" else _phi(z)


@dataclass
class Game:
    away: str
    home: str
    pool_spread: float | None            # pool's HOME spread (home -7.5 -> -7.5)
    market_spread: float | None          # current market HOME spread
    pool_total: float | None = None
    market_total: float | None = None
    rating_margin: float | None = None   # our projected home margin (optional)
    note: str = ""


@dataclass
class Option:
    game: Game
    kind: str          # spread / total
    side: str          # home / away / over / under
    label: str
    prob: float        # chance this pick scores (pushes count as not scoring)
    gap: float         # points of value vs the market (pool line better than market by this much)
    extra: dict = field(default_factory=dict)


def center_for_line(home_spread: float) -> float:
    """Location of the margin curve at which the market's own line is a coin flip.

    The key-number bumps skew the curve, so its mean is not where the line splits 50/50; we treat the
    market line as the median (equal win chance for both sides) and solve for that location."""
    lo, hi = -home_spread - 15, -home_spread + 15
    for _ in range(40):
        mid = (lo + hi) / 2
        h, _p = spread_cover(mid, home_spread, "home")
        a, _p = spread_cover(mid, home_spread, "away")
        lo, hi = (mid, hi) if h < a else (lo, mid)
    return (lo + hi) / 2


def _fair_margin(g: Game) -> float:
    mkt = center_for_line(g.market_spread)
    return mkt if g.rating_margin is None else (1 - RATING_WEIGHT) * mkt + RATING_WEIGHT * g.rating_margin


def _fmt(x: float) -> str:
    return f"{x:+.1f}".replace("+0.0", "PK")


def options(g: Game) -> list[Option]:
    out = []
    if g.pool_spread is not None and g.market_spread is not None:
        fair = _fair_margin(g)
        for side in ("home", "away"):
            win, push = spread_cover(fair, g.pool_spread, side)
            line = g.pool_spread if side == "home" else -g.pool_spread
            team = g.home if side == "home" else g.away
            mkt_line = g.market_spread if side == "home" else -g.market_spread
            out.append(Option(g, "spread", side, f"{team} {_fmt(line)}", win, line - mkt_line,
                              {"push": push, "market": mkt_line}))
    if g.pool_total is not None and g.market_total is not None:
        for side in ("over", "under"):
            p = total_cover(g.market_total, g.pool_total, side)
            gap = (g.market_total - g.pool_total) * (1 if side == "over" else -1)
            out.append(Option(g, "total", side, f"{g.away}/{g.home} {side.title()} {g.pool_total:g}", p, gap,
                              {"market": g.market_total}))
    return out


def poisson_binomial(probs: list[float], weights: list[int]) -> dict[int, float]:
    dist = {0: 1.0}
    for p, w in zip(probs, weights):
        nxt = {}
        for k, q in dist.items():
            nxt[k + w] = nxt.get(k + w, 0) + q * p
            nxt[k] = nxt.get(k, 0) + q * (1 - p)
        dist = nxt
    return dist


# ---- crowd-fade shadow (Nov 5, 2026). Paper only. To win a week outright you need to gain on the field, and
# the coin-flip filler picks at the bottom of the card carry no real edge either way. On those (never the keys),
# take the side the crowd is less likely to be on when it costs almost nothing in cover chance.
FADE_MAX_PROB = 0.53      # only filler with no real edge gets faded
FADE_MAX_COST = 0.015     # give up at most 1.5 points of cover chance to get off the public side
BRANDS = {"Alabama", "Georgia", "Ohio State", "Michigan", "Texas", "Notre Dame", "Oklahoma", "LSU", "Penn State",
          "Oregon", "USC", "Florida State", "Clemson", "Tennessee", "Miami", "Auburn", "Florida", "Texas A&M",
          "Ole Miss", "Nebraska", "Colorado", "Miami (FL)", "Wisconsin", "Iowa"}


def public_side(g: Game) -> str | None:
    """The side most pool players take: a big brand over a non-brand, otherwise the favorite at the pool line."""
    hb, ab = g.home in BRANDS, g.away in BRANDS
    if hb != ab:
        return "home" if hb else "away"
    if g.pool_spread is None or g.pool_spread == 0:
        return None
    return "home" if g.pool_spread < 0 else "away"


def _fade(card_picks: list[Option], n_keys: int) -> list[Option]:
    out = list(card_picks)
    for i, o in enumerate(out):
        if i < n_keys or o.kind != "spread" or o.prob >= FADE_MAX_PROB or public_side(o.game) != o.side:
            continue
        other = next((x for x in options(o.game) if x.kind == "spread" and x.side != o.side), None)
        if other and other.prob >= o.prob - FADE_MAX_COST:
            other.extra["faded"] = True
            out[i] = other
    return out


def build_card(games: list[Game], n_picks: int = 12, n_keys: int = 3, allow_totals: bool = True,
               exclude: set[str] | None = None, fade: bool = False) -> dict:
    """Best side per game, top n by chance to score, keys = the n_keys most likely to score.
    fade=True: crowd-fade shadow -- coin-flip filler swapped to the less-public side (see _fade)."""
    best = []
    for g in games:
        if exclude and (g.home in exclude or g.away in exclude):
            continue
        opts = [o for o in options(g) if allow_totals or o.kind == "spread"]
        if opts:
            best.append(max(opts, key=lambda o: (o.prob, o.gap)))
    best.sort(key=lambda o: (-o.prob, -o.gap))
    picks = best[:n_picks]
    if fade:
        picks = _fade(picks, n_keys)
    probs = [o.prob for o in picks]
    weights = [2 if i < n_keys else 1 for i in range(len(picks))]
    dist = poisson_binomial(probs, weights)
    exp_pts = sum(k * q for k, q in dist.items())
    return {"picks": picks, "keys": picks[:n_keys], "expected_points": exp_pts,
            "p_at_least": {t: sum(q for k, q in dist.items() if k >= t)
                           for t in range(max(1, sum(weights) - 5), sum(weights) - 1)},
            "max_points": sum(weights), "bench": best[n_picks:n_picks + 6]}
