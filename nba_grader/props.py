"""NBA player props (PAPER ONLY): our projection vs Pinnacle's fair price.

For every NBA player prop on the Prop Board (Pinnacle, free PinnWire feed):

1. PROJECT. The player's per-minute rate for the stat (recent games weigh more; last
   season's late games at reduced weight) x projected minutes (recent minutes, a little
   less on a back-to-back) x a small pace nudge from the game model's projected total.
2. BLEND + PRICE. Our mean is blended with the mean Pinnacle's price implies; our share of the
   vote grows with the player's games this season (27% at 5 games, ~55% over a full season).
   A negative-binomial count around the blend (spread set per stat) gives P(over) / P(under)
   at Pinnacle's line. Edge = our side's chance - Pinnacle's no-vig chance.
3. LOG + FREEZE. Every prop gets our side, frozen with the line. STRONG / LEAN are the
   recommendations; NO_EDGE is tracked for the honest big-sample test.
4. CLOSE + SETTLE. Keep reading Pinnacle until tip (closing price -> CLV, converted to our
   line when Pinnacle moves the number), then grade from the ESPN box score. DNP = void.

Paper price: Pinnacle's fair chance with typical book vig (4.5%) added, i.e. what a normal book
would charge. "Bet at" on a play is that number: take it or better. Nothing here places a bet.
"""
from __future__ import annotations

import csv
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nba_grader.core import _env_float, _parse_dt, fmt_odds

DATA_DIR = Path(__file__).resolve().parent / "data"
PROPS_PATH = DATA_DIR / "props.csv"
MODEL = "nba_props_v1"

LEAN = _env_float("NBA_PROPS_LEAN", 0.04)        # our chance minus Pinnacle's no-vig chance
STRONG = _env_float("NBA_PROPS_STRONG", 0.07)
# Pinnacle is the sharpest prop market there is. Our projection only gets a share of the vote,
# growing with the player's games this season: weight = MAX * games / (games + HALF).
# 5 games -> 27%, 15 games -> 43%, a full season -> ~55%.
MODEL_WEIGHT_MAX = _env_float("NBA_PROPS_MODEL_WEIGHT_MAX", 0.6)
MODEL_WEIGHT_HALF = _env_float("NBA_PROPS_MODEL_WEIGHT_HALF", 6)
BOOK_VIG = 1.045
MIN_GAMES = 3                 # this-season games played
MIN_WEIGHTED_MIN = 90.0       # weighted minutes behind the rate
MIN_PROJ_MIN = 12.0           # skip deep bench guys
CUR_DECAY, PRIOR_DECAY, PRIOR_WEIGHT = 0.88, 0.96, 0.35
B2B_MIN_FACTOR = 0.97
MIN_LEAD = timedelta(minutes=10)
SETTLE_AFTER = timedelta(hours=3)

# stat -> box-score columns summed, and variance / mean (overdispersion). Rebounds, assists and threes
# are close to Poisson; points come in 2s and 3s, so their spread is much wider.
STATS = {"points": (("pts",), 2.3), "rebounds": (("reb",), 1.35), "assists": (("ast",), 1.35),
         "threes made": (("fg3m",), 1.05), "steals": (("stl",), 1.1), "blocks": (("blk",), 1.2),
         "pts & rebs & asts": (("pts", "reb", "ast"), 2.1), "pts & rebs": (("pts", "reb"), 2.2),
         "pts & asts": (("pts", "ast"), 2.2), "rebs & asts": (("reb", "ast"), 1.5)}
PACE_STATS = {"pts", "fg3m", "ast"}

FIELDS = ["pick_id", "source", "model", "logged_at", "game_day", "kickoff", "key", "home", "away", "player",
          "team", "stat", "market", "side", "direction", "line", "odds", "proj", "proj_min", "blend", "weight", "model_prob",
          "market_prob", "edge_prob", "tier", "limit", "close_line", "close_prob", "clv", "actual",
          "result", "units", "settled_at"]


# ------------------------------------------------------------------ helpers
def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return " ".join(s.split())


def stat_spec(stat: str):
    return STATS.get(str(stat or "").strip().lower())


def american(prob: float) -> float:
    dec = 1 / prob
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def profit(odds: float) -> float:
    return odds / 100 if odds > 0 else 100 / abs(odds)


def p_over(mean: float, line: float, phi: float) -> float:
    """P(X > line) for a count with this mean and variance = phi * mean (NB; Poisson if phi <= 1)."""
    k = math.floor(line)
    if mean <= 0:
        return 0.0
    if phi <= 1.0001:
        lp, cdf = -mean, 0.0
        for x in range(k + 1):
            if x:
                lp += math.log(mean) - math.log(x)
            cdf += math.exp(lp)
        return max(0.0, min(1.0, 1 - cdf))
    r, p = mean / (phi - 1), 1 / phi            # NB(r, p): mean r(1-p)/p, var mean/p
    lp = r * math.log(p)                         # pmf(0)
    cdf = 0.0
    for x in range(k + 1):
        if x:
            lp += math.log(x - 1 + r) - math.log(x) + math.log(1 - p)
        cdf += math.exp(lp)
    return max(0.0, min(1.0, 1 - cdf))


def mean_for(prob_over: float, line: float, phi: float) -> float:
    """The mean whose P(over line) equals prob_over (bisection) -> lets us move a price to another line."""
    lo, hi = 0.01, max(4 * line + 20, 10.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_over(mid, line, phi) < prob_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------------ player logs
def player_logs(games: list[dict], boxes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """norm player name -> [{day, season, team, dnp, min, pts, ...}] newest first."""
    out: dict[str, list[dict]] = {}
    for g in games:
        box = boxes.get(str(g.get("id")))
        if not g.get("completed") or not box:
            continue
        for r in box:
            out.setdefault(norm_name(r["player"]), []).append({**r, "day": str(g.get("week")), "season": g.get("season")})
    for logs in out.values():
        logs.sort(key=lambda r: r["day"], reverse=True)
    return out


def project(logs: list[dict], cols: tuple, *, before: str, season, b2b: bool = False, pace: float = 1.0) -> dict | None:
    """{mean, minutes, team} or None when there isn't enough of a track record."""
    logs = [r for r in logs if r["day"] < before]
    cur = [r for r in logs if str(r["season"]) == str(season) and not r["dnp"]]
    prior = [r for r in logs if str(r["season"]) != str(season) and not r["dnp"]]
    if len(cur) < MIN_GAMES:
        return None
    wsum = wmin = 0.0
    tot = {c: 0.0 for c in cols}
    for group, decay, scale in ((cur[:25], CUR_DECAY, 1.0), (prior[:30], PRIOR_DECAY, PRIOR_WEIGHT)):
        for i, r in enumerate(group):
            w = scale * decay ** i
            wmin += w * r["min"]
            for c in cols:
                tot[c] += w * r.get(c, 0.0)
            wsum += w
    if wmin < MIN_WEIGHTED_MIN:
        return None
    mw = [CUR_DECAY ** i for i in range(min(len(cur), 10))]
    minutes = sum(w * r["min"] for w, r in zip(mw, cur)) / sum(mw)
    if len(cur) < 6 and prior:            # early season: lean a bit on last year's minutes
        pm = sum(r["min"] for r in prior[:15]) / len(prior[:15])
        minutes = (minutes * len(cur) + pm * 2) / (len(cur) + 2)
    if b2b:
        minutes *= B2B_MIN_FACTOR
    if minutes < MIN_PROJ_MIN:
        return None
    mean = 0.0
    for c in cols:
        rate = tot[c] / wmin
        mean += rate * minutes * (pace if c in PACE_STATS else 1.0)
    return {"mean": mean, "minutes": minutes, "team": cur[0]["team"], "games": len(cur)}


# ------------------------------------------------------------------ ledger
def load(path: Path | None = None) -> list[dict]:
    try:
        with open(path or PROPS_PATH, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def save(rows: list[dict], path: Path | None = None) -> None:
    path = path or PROPS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (str(r.get("kickoff")), str(r.get("pick_id"))))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _tier(edge: float) -> str:
    return "STRONG" if edge >= STRONG else "LEAN" if edge >= LEAN else "NO_EDGE"


def _side_prob(row: dict, p_over_: float) -> float:
    return p_over_ if row["direction"] == "OVER" else 1 - p_over_


# ------------------------------------------------------------------ one round
def run(*, board: list[dict], games: list[dict], boxes: dict, season, now: datetime | None = None,
        rows: list[dict] | None = None, pace_for=None, b2b_teams: set | None = None) -> dict:
    """board: Prop Board rows ({key, league, player, stat, home, away, starts, line, p_over, limit}).
    pace_for(home, away) -> multiplier around 1.0 from the game model's projected total.
    b2b_teams: {(team, YYYY-MM-DD)} for teams playing the second night of a back-to-back."""
    now = now or datetime.now(timezone.utc)
    rows = load() if rows is None else rows
    by_id = {r["pick_id"]: r for r in rows}
    stamp = now.isoformat(timespec="seconds")
    logs = player_logs(games, boxes)
    b2b_teams = b2b_teams or set()
    from espn_nba import ET
    info = {"board_nba": 0, "logged": 0, "new": [], "no_log": 0, "unsupported": 0, "close_updates": 0, "settled": 0}

    live_board = {}
    for p in board:
        if p.get("league") != "NBA":
            continue
        info["board_nba"] += 1
        live_board[str(p["key"])] = p
        spec = stat_spec(p.get("stat"))
        start = _parse_dt(p.get("starts") or "")
        if not spec or not start:
            info["unsupported"] += 1
            continue
        if not (now + MIN_LEAD <= start) or f"live:{MODEL}:{p['key']}" in by_id:
            continue
        cols, phi = spec
        day = start.astimezone(ET).date().isoformat()
        hist = logs.get(norm_name(p["player"]), [])
        team = next((r["team"] for r in hist if r["day"] < day), "")
        if team and team not in (p.get("home"), p.get("away")):
            hist = []                                   # name clash / traded: don't guess
        pace = pace_for(p.get("home"), p.get("away")) if pace_for else 1.0
        pr = project(hist, cols, before=day, season=season, b2b=(team, day) in b2b_teams, pace=pace) if hist else None
        if not pr:
            info["no_log"] += 1
            continue
        line, fair_over = float(p["line"]), float(p["p_over"])
        w = MODEL_WEIGHT_MAX * pr["games"] / (pr["games"] + MODEL_WEIGHT_HALF)
        blended = w * pr["mean"] + (1 - w) * mean_for(fair_over, line, phi)
        ours_over = p_over(blended, line, phi)
        if ours_over - fair_over >= (1 - ours_over) - (1 - fair_over):
            direction, mp, fp = "OVER", ours_over, fair_over
        else:
            direction, mp, fp = "UNDER", 1 - ours_over, 1 - fair_over
        edge = mp - fp
        odds = american(min(fp * BOOK_VIG, 0.97))
        row = {"pick_id": f"live:{MODEL}:{p['key']}", "source": "live", "model": MODEL, "logged_at": stamp,
               "game_day": day, "kickoff": p["starts"], "key": str(p["key"]), "home": p.get("home", ""),
               "away": p.get("away", ""), "player": p["player"], "team": pr["team"], "stat": p["stat"],
               "market": "PROP", "side": f"{p['player']} {direction.title()} {line:g} {p['stat']}",
               "direction": direction, "line": line, "odds": odds, "proj": round(pr["mean"], 1),
               "proj_min": round(pr["minutes"], 1), "blend": round(blended, 1), "weight": round(w, 3), "model_prob": round(mp, 4), "market_prob": round(fp, 4),
               "edge_prob": round(edge, 4), "tier": _tier(edge), "limit": p.get("limit", "")}
        by_id[row["pick_id"]] = row
        info["new"].append(row)
        info["logged"] += 1

    # Closing price: last Pinnacle read before tip. If the line moved, move the close to our line.
    for r in by_id.values():
        if r.get("result"):
            continue
        p = live_board.get(str(r.get("key")))
        ko = _parse_dt(r.get("kickoff") or "")
        spec = stat_spec(r.get("stat"))
        if not p or not ko or now >= ko or not spec:
            continue
        line, close_line, cpo = float(r["line"]), float(p["line"]), float(p["p_over"])
        if close_line != line:
            cpo = p_over(mean_for(cpo, close_line, spec[1]), line, spec[1])
        r["close_line"], r["close_prob"] = close_line, round(_side_prob(r, cpo), 4)
        r["clv"] = round(float(r["close_prob"]) - float(r["market_prob"]), 4)
        info["close_updates"] += 1

    # Settle from the box score.
    finals = {}
    for g in games:
        if g.get("completed"):
            for t in (g.get("homeTeam"), g.get("awayTeam")):
                finals[(t, str(g.get("week")))] = g
    for r in by_id.values():
        if r.get("result"):
            continue
        ko = _parse_dt(r.get("kickoff") or "")
        g = finals.get((r.get("home"), r.get("game_day"))) or finals.get((r.get("away"), r.get("game_day")))
        if not g or not ko or now < ko + SETTLE_AFTER:
            continue
        box = boxes.get(str(g.get("id")))
        if not box:
            continue
        me = next((b for b in box if norm_name(b["player"]) == norm_name(r["player"])), None)
        if me is None or me["dnp"]:
            r["result"], r["units"], r["actual"] = "V", 0.0, "DNP"
        else:
            cols, _ = stat_spec(r["stat"])
            actual = sum(me.get(c, 0.0) for c in cols)
            diff = actual - float(r["line"]) if r["direction"] == "OVER" else float(r["line"]) - actual
            r["actual"] = actual
            r["result"], r["units"] = ("W", round(profit(float(r["odds"])), 4)) if diff > 0 else \
                ("L", -1.0) if diff < 0 else ("P", 0.0)
        r["settled_at"] = stamp
        info["settled"] += 1

    info["rows"] = list(by_id.values())
    return info


def stats(rows: list[dict], tiers=None) -> dict:
    sel = [r for r in rows if r.get("result") in ("W", "L", "P") and (tiers is None or r.get("tier") in tiers)]
    w = sum(r["result"] == "W" for r in sel)
    l = sum(r["result"] == "L" for r in sel)
    units = sum(float(r.get("units") or 0) for r in sel)
    clvs = [float(r["clv"]) for r in sel if r.get("clv") not in ("", None)]
    opn = [r for r in rows if not r.get("result") and (tiers is None or r.get("tier") in tiers)]
    return {"picks": len(sel), "w": w, "l": l, "p": len(sel) - w - l, "units": round(units, 2),
            "roi": units / len(sel) if sel else None, "win_pct": w / (w + l) if w + l else None,
            "clv": sum(clvs) / len(clvs) if clvs else None,
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None, "open": len(opn)}


def fmt_price(odds) -> str:
    try:
        return fmt_odds(float(odds))
    except (TypeError, ValueError):
        return ""


__all__ = ["run", "load", "save", "stats", "p_over", "mean_for", "project", "norm_name", "fmt_price", "date"]
