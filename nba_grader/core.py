"""NBA Game Model (PAPER ONLY) — same honest grading as the NCAA Model Grader.

Every run (see .github/workflows/nba-model.yml):

1. LOG. For every NBA game tipping off in the next LOOKAHEAD_HOURS that has a line,
   log the model's side of the spread, the total and the moneyline. The line and
   price are FROZEN at that moment and never touched again.
2. TIER. STRONG / LEAN are the real recommendations; NO_EDGE is tracked anyway
   (the big-sample test of the model); LONG_SHOT flags ML dogs longer than +250.
3. CLOSE + SETTLE. Keep re-reading the line until tip-off (closing line -> CLV),
   then grade W/L/P at 1 unit flat after the final.

Grading, CLV and reporting are shared with ncaa_grader.core; only the NBA
thresholds, the game window and the model call (which needs the game date for
back-to-backs) live here. Nothing in this package can place a bet.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ncaa_grader.core import (RECOMMENDED, by_market, cdf, clv, consensus, devig, fmt_line, fmt_odds,  # noqa: F401
                              grade, implied, load_ledger as _load, save_ledger as _save, summarize, write_status as _ws,
                              _parse_dt)

DATA_DIR = Path(__file__).resolve().parent / "data"
LEDGER_PATH = DATA_DIR / "picks.csv"
STATUS_PATH = DATA_DIR / "status.json"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# NBA numbers are tighter than college: a 2.5-pt disagreement with an NBA closing line is big.
SPREAD_LEAN = _env_float("NBA_MODEL_SPREAD_LEAN", 2.5)
SPREAD_STRONG = _env_float("NBA_MODEL_SPREAD_STRONG", 5.0)
TOTAL_LEAN = _env_float("NBA_MODEL_TOTAL_LEAN", 4.0)
TOTAL_STRONG = _env_float("NBA_MODEL_TOTAL_STRONG", 7.0)
ML_LEAN = _env_float("NBA_MODEL_ML_LEAN", 0.04)
ML_STRONG = _env_float("NBA_MODEL_ML_STRONG", 0.08)
LONG_SHOT_ODDS = _env_float("NBA_MODEL_LONG_SHOT_ODDS", 250)
MARGIN_SD = _env_float("NBA_MODEL_MARGIN_SD", 12.0)
TOTAL_SD = _env_float("NBA_MODEL_TOTAL_SD", 18.0)
DEFAULT_ODDS = -110
LOOKAHEAD = timedelta(hours=_env_float("NBA_MODEL_LOOKAHEAD_HOURS", 30))
MIN_LEAD = timedelta(minutes=10)
SETTLE_AFTER = timedelta(hours=3)

FIELDS = ["pick_id", "source", "model", "logged_at", "season", "week", "game_id", "kickoff",
          "home", "away", "market", "side", "pick_team", "direction", "line", "odds",
          "model_prob", "market_prob", "edge_pts", "edge_prob", "tier", "proj",
          "close_line", "close_odds", "clv", "home_pts", "away_pts", "result", "units", "settled_at"]


def load_ledger(path: Path | None = None) -> list[dict]:
    return _load(path or LEDGER_PATH)


def save_ledger(rows: list[dict], path: Path | None = None) -> None:
    _save(rows, path or LEDGER_PATH)


def write_status(summary: dict, path: Path | None = None) -> None:
    _ws(summary, path or STATUS_PATH)


def _tier(edge: float, lean: float, strong: float) -> str:
    return "STRONG" if edge >= strong else "LEAN" if edge >= lean else "NO_EDGE"


def make_picks(*, game: dict, market: dict, home_margin: float, total: float, home_win_prob: float,
               model: str, source: str, logged_at: str) -> list[dict]:
    """The model's side of every market for one game (one pick per market)."""
    home, away, gid = game["homeTeam"], game["awayTeam"], game.get("id")
    base = {"source": source, "model": model, "logged_at": logged_at, "season": game.get("season"),
            "week": game.get("week"), "game_id": gid, "kickoff": game.get("startDate") or "",
            "home": home, "away": away,
            "proj": f"{away} {total / 2 - home_margin / 2:.1f} - {home} {total / 2 + home_margin / 2:.1f}"}
    out = []

    def add(mkt: str, **row):
        out.append({**base, **row, "market": mkt, "pick_id": f"{source}:{model}:{gid}:{mkt}"})

    s = market.get("spread")
    if s is not None:
        edge = home_margin + s
        team, line = (home, s) if edge >= 0 else (away, -s)
        odds = market.get("home_spread_odds" if team == home else "away_spread_odds") or DEFAULT_ODDS
        prob = cdf(abs(edge) / MARGIN_SD)
        add("SPREAD", side=f"{team} {fmt_line(line)}", pick_team=team, direction="", line=line, odds=odds,
            model_prob=round(prob, 4), market_prob="", edge_pts=round(abs(edge), 1),
            edge_prob=round(prob - implied(odds), 4), tier=_tier(abs(edge), SPREAD_LEAN, SPREAD_STRONG))

    t = market.get("total")
    if t is not None:
        diff = total - t
        direction = "OVER" if diff >= 0 else "UNDER"
        odds = market.get("over_odds" if direction == "OVER" else "under_odds") or DEFAULT_ODDS
        prob = cdf(abs(diff) / TOTAL_SD)
        add("TOTAL", side=f"{direction} {t:g}", pick_team="", direction=direction, line=t, odds=odds,
            model_prob=round(prob, 4), market_prob="", edge_pts=round(abs(diff), 1),
            edge_prob=round(prob - implied(odds), 4), tier=_tier(abs(diff), TOTAL_LEAN, TOTAL_STRONG))

    hm, am = market.get("home_ml"), market.get("away_ml")
    if hm is not None and am is not None:
        fair_h, fair_a = devig(hm, am)
        if home_win_prob - fair_h >= 0:
            team, odds, prob, fair = home, hm, home_win_prob, fair_h
        else:
            team, odds, prob, fair = away, am, 1 - home_win_prob, fair_a
        edge = prob - fair
        tier = "LONG_SHOT" if edge >= ML_LEAN and odds > LONG_SHOT_ODDS else _tier(edge, ML_LEAN, ML_STRONG)
        add("ML", side=f"{team} ML {fmt_odds(odds)}", pick_team=team, direction="", line="", odds=odds,
            model_prob=round(prob, 4), market_prob=round(fair, 4), edge_pts="", edge_prob=round(edge, 4), tier=tier)
    return out


def run_live(*, games: list[dict], lines: dict, project, now: datetime | None = None,
             rows: list[dict] | None = None, model: str = "nba_v1") -> dict:
    """Log new picks, refresh closing lines, settle finished games.

    project(game) -> Proj-like (home_margin, total, home_win_prob) or None. It gets the whole
    game so it can see the date (back-to-backs)."""
    now = now or datetime.now(timezone.utc)
    rows = load_ledger() if rows is None else rows
    by_id = {r["pick_id"]: r for r in rows}
    games_by_id = {str(g.get("id")): g for g in games}
    lines = {str(k): v for k, v in lines.items()}
    stamp = now.isoformat(timespec="seconds")

    upcoming = []
    for g in games:
        ko = _parse_dt(g.get("startDate") or "")
        if not g.get("completed") and ko and now + MIN_LEAD <= ko <= now + LOOKAHEAD:
            upcoming.append(g)

    logged, new, unprojected = 0, [], []
    for g in upcoming:
        gl = lines.get(str(g.get("id")))
        if not gl:
            continue
        p = project(g)
        if not p:
            unprojected.append(f'{g["awayTeam"]} @ {g["homeTeam"]}')
            continue
        for pick in make_picks(game=g, market=consensus(gl), home_margin=p.home_margin, total=p.total,
                               home_win_prob=p.home_win_prob, model=model, source="live", logged_at=stamp):
            if pick["pick_id"] not in by_id:      # frozen: never overwrite a logged pick
                by_id[pick["pick_id"]] = pick
                new.append(pick)
                logged += 1

    closed = 0
    for r in by_id.values():
        if r.get("source") != "live" or r.get("result"):
            continue
        g, gl = games_by_id.get(str(r.get("game_id"))), lines.get(str(r.get("game_id")))
        ko = _parse_dt(r.get("kickoff") or "")
        if g and gl and ko and now < ko and not g.get("completed"):
            cl, co, v = clv(r, consensus(gl))
            if v != "":
                r["close_line"], r["close_odds"], r["clv"] = cl, co, v
                closed += 1

    settled = 0
    for r in by_id.values():
        if r.get("source") != "live" or r.get("result"):
            continue
        g = games_by_id.get(str(r.get("game_id")))
        ko = _parse_dt(r.get("kickoff") or "")
        if not (g and g.get("completed") and g.get("homePoints") is not None and ko and now >= ko + SETTLE_AFTER):
            continue
        hp, ap = float(g["homePoints"]), float(g["awayPoints"])
        r["result"], r["units"] = grade(r, hp, ap)
        r["home_pts"], r["away_pts"] = int(hp), int(ap)
        r["settled_at"] = stamp
        settled += 1

    return {"rows": list(by_id.values()), "new": new, "logged": logged, "settled": settled,
            "close_updates": closed, "upcoming_games": len(upcoming),
            "games_with_lines": sum(1 for g in upcoming if str(g.get("id")) in lines),
            "not_projected": unprojected[:30]}


def format_table(rows: list[dict], *, model: str = "nba_v1") -> str:
    sel = [r for r in rows if r.get("source") == "live" and r.get("model") == model]
    graded = sum(1 for r in sel if r.get("result"))
    out = [f"[NBA {model}]  graded: {graded}   pending: {len(sel) - graded}", "",
           f"{'group':<40}{'picks':>6}  {'W-L-P':<11}{'units':>8}{'ROI':>9}{'CLV':>8}"]
    for s in summarize(rows, source="live", model=model):
        roi = "" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%"
        c = "" if s["clv_pts"] is None else f"{s['clv_pts']:+.2f}"
        out.append(f"{s['group']:<40}{s['picks']:>6}  {s['w']}-{s['l']}-{s['p']:<5}{s['units']:>+8.2f}{roi:>9}{c:>8}")
    return "\n".join(out)
