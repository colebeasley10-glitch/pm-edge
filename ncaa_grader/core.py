"""NCAA Model Grader (PAPER ONLY).

Answers one question: does the NCAA model actually beat the market?

Every run (see .github/workflows/ncaa-grader.yml):

1. LOG. For every FBS game kicking off in the next LOOKAHEAD_DAYS that has a
   market line, log the model's side of the spread, the total and the moneyline.
   The line and price are FROZEN at that moment and never touched again, so the
   grade can't be flattered by a better number seen later.
2. TIER. Each pick is tagged by how far the model is from the market:
     STRONG / LEAN  -> the real recommendations
     NO_EDGE        -> tracked anyway (the honest big-sample test of the model)
     LONG_SHOT      -> moneyline dogs longer than +250 with an edge (flagged, not bets)
3. SETTLE. After the final score, grade W/L/P at 1 unit flat, and record the
   closing line so we can measure closing-line value (CLV).

A BACKTEST mode replays weeks already played. Each model is rebuilt from games
before that week only (no look-ahead) and graded at the opening line.

Data: ESPN's free scoreboard (schedule, scores, odds). Nothing here can place a bet.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


DATA_DIR = Path(__file__).resolve().parent / "data"
LEDGER_PATH = DATA_DIR / "picks.csv"
STATUS_PATH = DATA_DIR / "status.json"
WORKBOOK_PATH = DATA_DIR / "my_model.csv"
ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "team_aliases.csv"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Lean thresholds match the NCAA beta alert thresholds, so "LEAN or better" is
# roughly what the beta would have pushed to your phone.
SPREAD_LEAN = _env_float("NCAA_GRADER_SPREAD_LEAN", 3.0)      # pts off the market spread
SPREAD_STRONG = _env_float("NCAA_GRADER_SPREAD_STRONG", 6.0)
TOTAL_LEAN = _env_float("NCAA_GRADER_TOTAL_LEAN", 4.0)        # pts off the market total
TOTAL_STRONG = _env_float("NCAA_GRADER_TOTAL_STRONG", 7.0)
ML_LEAN = _env_float("NCAA_GRADER_ML_LEAN", 0.04)             # win-prob points over the no-vig price
ML_STRONG = _env_float("NCAA_GRADER_ML_STRONG", 0.08)
LONG_SHOT_ODDS = _env_float("NCAA_GRADER_LONG_SHOT_ODDS", 250)  # dogs longer than +250
MARGIN_SD = _env_float("NCAA_BETA_MARGIN_SD", 16.0)
TOTAL_SD = _env_float("NCAA_BETA_TOTAL_SD", 14.0)
SPREAD_TOTAL_ODDS = -110          # CFBD has no spread/total juice; assume standard -110
LOOKAHEAD_DAYS = _env_float("NCAA_GRADER_LOOKAHEAD_DAYS", 7)
MIN_LEAD = timedelta(minutes=10)  # don't log a game that's about to kick off
SETTLE_AFTER = timedelta(hours=4)  # final scores have settled in the feed

RECOMMENDED = ("STRONG", "LEAN")
FIELDS = ["pick_id", "source", "model", "logged_at", "season", "week", "game_id", "kickoff",
          "home", "away", "market", "side", "pick_team", "direction", "line", "odds",
          "model_prob", "market_prob", "edge_pts", "edge_prob", "tier", "proj",
          "close_line", "close_odds", "clv", "home_pts", "away_pts", "result", "units", "settled_at"]


# ---------------------------------------------------------------- math helpers
def cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def profit_per_unit(odds: float) -> float:
    """Units won on a 1-unit stake at American odds."""
    odds = float(odds)
    return odds / 100 if odds > 0 else 100 / abs(odds)


def implied(odds: float) -> float:
    odds = float(odds)
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


def devig(home_odds: float, away_odds: float) -> tuple[float, float]:
    h, a = implied(home_odds), implied(away_odds)
    return h / (h + a), a / (h + a)


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _median(values: Iterable) -> float | None:
    vals = [v for v in (_num(x) for x in values) if v is not None]
    return statistics.median(vals) if vals else None


def _first(row: dict, *keys):
    for k in keys:
        if row.get(k) is not None:
            return row.get(k)
    return None


def consensus(game_lines: dict, opening: bool = False) -> dict:
    """Median market numbers across CFBD providers. CFBD spreads are the HOME line
    (negative = home favored). Handles both v1 and v2 field names."""
    rows = game_lines.get("lines") or []
    if opening:
        spread = _median(_first(r, "spreadOpen", "openingSpread") for r in rows)
        total = _median(_first(r, "overUnderOpen", "openingOverUnder") for r in rows)
    else:
        spread = _median(r.get("spread") for r in rows)
        total = _median(r.get("overUnder") for r in rows)
    pairs = [(_num(r.get("homeMoneyline")), _num(r.get("awayMoneyline"))) for r in rows]
    pairs = [(h, a) for h, a in pairs if h is not None and a is not None and h != 0 and a != 0]
    home_ml = _median(h for h, _ in pairs)
    away_ml = _median(a for _, a in pairs)
    juice = {}
    if not opening:  # actual spread/total prices when the feed has them (ESPN does), else -110
        for k, src in (("home_spread_odds", "homeSpreadOdds"), ("away_spread_odds", "awaySpreadOdds"),
                       ("over_odds", "overOdds"), ("under_odds", "underOdds")):
            v = _median(r.get(src) for r in rows)
            juice[k] = v if v is not None and abs(v) >= 100 else None
    return {"spread": spread, "total": total, "home_ml": home_ml, "away_ml": away_ml,
            "providers": len(rows), **juice}


def fmt_line(x: float) -> str:
    return f"+{x:g}" if x > 0 else ("PK" if x == 0 else f"{x:g}")


def fmt_odds(x: float) -> str:
    return f"+{x:.0f}" if x > 0 else f"{x:.0f}"


# ---------------------------------------------------------------- tiers
def tier_spread(edge_pts: float) -> str:
    return "STRONG" if edge_pts >= SPREAD_STRONG else "LEAN" if edge_pts >= SPREAD_LEAN else "NO_EDGE"


def tier_total(edge_pts: float) -> str:
    return "STRONG" if edge_pts >= TOTAL_STRONG else "LEAN" if edge_pts >= TOTAL_LEAN else "NO_EDGE"


def tier_ml(edge_prob: float, odds: float) -> str:
    if edge_prob >= ML_LEAN and odds > LONG_SHOT_ODDS:
        return "LONG_SHOT"
    return "STRONG" if edge_prob >= ML_STRONG else "LEAN" if edge_prob >= ML_LEAN else "NO_EDGE"


# ---------------------------------------------------------------- picks
def make_picks(*, game: dict, market: dict, home_margin: float, total: float, home_win_prob: float,
               model: str, source: str, logged_at: str) -> list[dict]:
    """The model's side of every market for one game (always one pick per market)."""
    home, away = game["homeTeam"], game["awayTeam"]
    season, gid = game.get("season"), game.get("id")
    base = {"source": source, "model": model, "logged_at": logged_at, "season": season,
            "week": game.get("week"), "game_id": gid, "kickoff": game.get("startDate") or "",
            "home": home, "away": away,
            "proj": f"{away} {total / 2 - home_margin / 2:.1f} - {home} {total / 2 + home_margin / 2:.1f}"}
    out = []

    def add(mkt: str, **row):
        row = {**base, **row, "market": mkt, "pick_id": f"{source}:{model}:{season}:{gid}:{mkt}"}
        out.append(row)

    s = market.get("spread")
    if s is not None:
        edge = home_margin + s                       # >0: home covers the market line
        team, line = (home, s) if edge >= 0 else (away, -s)
        odds = market.get("home_spread_odds" if team == home else "away_spread_odds") or SPREAD_TOTAL_ODDS
        prob = cdf(abs(edge) / MARGIN_SD)
        add("SPREAD", side=f"{team} {fmt_line(line)}", pick_team=team, direction="", line=line,
            odds=odds, model_prob=round(prob, 4), market_prob="",
            edge_pts=round(abs(edge), 1), edge_prob=round(prob - implied(odds), 4),
            tier=tier_spread(abs(edge)))

    t = market.get("total")
    if t is not None:
        diff = total - t
        direction = "OVER" if diff >= 0 else "UNDER"
        odds = market.get("over_odds" if direction == "OVER" else "under_odds") or SPREAD_TOTAL_ODDS
        prob = cdf(abs(diff) / TOTAL_SD)
        add("TOTAL", side=f"{direction} {t:g}", pick_team="", direction=direction, line=t,
            odds=odds, model_prob=round(prob, 4), market_prob="",
            edge_pts=round(abs(diff), 1), edge_prob=round(prob - implied(odds), 4),
            tier=tier_total(abs(diff)))

    hm, am = market.get("home_ml"), market.get("away_ml")
    if hm is not None and am is not None:
        fair_h, fair_a = devig(hm, am)
        edge_h = home_win_prob - fair_h
        if edge_h >= 0:
            team, odds, prob, fair = home, hm, home_win_prob, fair_h
        else:
            team, odds, prob, fair = away, am, 1 - home_win_prob, fair_a
        add("ML", side=f"{team} ML {fmt_odds(odds)}", pick_team=team, direction="", line="",
            odds=odds, model_prob=round(prob, 4), market_prob=round(fair, 4), edge_pts="",
            edge_prob=round(prob - fair, 4), tier=tier_ml(prob - fair, odds))
    return out


def grade(row: dict, home_pts: float, away_pts: float) -> tuple[str, float]:
    mkt = row["market"]
    if mkt == "TOTAL":
        diff = (home_pts + away_pts) - float(row["line"])
        if row["direction"] == "UNDER":
            diff = -diff
    else:
        margin = home_pts - away_pts
        if row["pick_team"] == row["away"]:
            margin = -margin
        diff = margin + (float(row["line"]) if mkt == "SPREAD" else 0.0)
    if diff > 0:
        return "W", round(profit_per_unit(row["odds"]), 4)
    if diff < 0:
        return "L", -1.0
    return "P", 0.0


def clv(row: dict, close: dict) -> tuple[str, str, str]:
    """(close_line, close_odds, clv). Spread/total CLV in points, ML CLV in no-vig win-prob points.
    Positive = you beat the closing number."""
    mkt = row["market"]
    if mkt == "SPREAD" and close.get("spread") is not None:
        cl = close["spread"] if row["pick_team"] == row["home"] else -close["spread"]
        return cl, "", round(float(row["line"]) - cl, 2)
    if mkt == "TOTAL" and close.get("total") is not None:
        ct = close["total"]
        v = ct - float(row["line"]) if row["direction"] == "OVER" else float(row["line"]) - ct
        return ct, "", round(v, 2)
    if mkt == "ML" and close.get("home_ml") is not None and close.get("away_ml") is not None \
            and row.get("market_prob") not in ("", None):
        fh, fa = devig(close["home_ml"], close["away_ml"])
        home_side = row["pick_team"] == row["home"]
        fair_close = fh if home_side else fa
        odds_close = close["home_ml"] if home_side else close["away_ml"]
        return "", odds_close, round(fair_close - float(row["market_prob"]), 4)
    return "", "", ""


# ---------------------------------------------------------------- ledger
def load_ledger(path: Path | None = None) -> list[dict]:
    path = path or LEDGER_PATH
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def save_ledger(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (str(r.get("source")), str(r.get("kickoff")), str(r.get("pick_id"))))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


# ---------------------------------------------------------------- team names / workbook
def canon_team(x: str) -> str:
    x = str(x or "").lower().replace("&", " and ")
    x = re.sub(r"[^a-z0-9 ]+", " ", x)
    x = re.sub(r"\b(state|st)\b", "st", x)
    return " ".join(x.split())


def _aliases() -> dict[str, str]:
    out = {}
    try:
        with open(ALIASES_PATH, newline="") as f:
            for r in csv.DictReader(f):
                out[canon_team(r.get("alias"))] = canon_team(r.get("canonical"))
    except FileNotFoundError:
        pass
    return out


def team_key(name: str, aliases: dict[str, str] | None = None) -> str:
    c = canon_team(name)
    return (aliases or {}).get(c, c)


def load_workbook_model(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """Optional: your own numbers. CSV columns: home, away, home_margin, total
    (optional home_win_prob). Paste each week's projections from the Excel model.
    Games are matched by team name (data/team_aliases.csv fixes spellings)."""
    aliases = _aliases()
    out = {}
    for r in load_ledger(path or WORKBOOK_PATH):
        hm, tot = _num(r.get("home_margin")), _num(r.get("total"))
        if hm is None or tot is None:
            continue
        wp = _num(r.get("home_win_prob"))
        if wp is not None and wp > 1:
            wp /= 100
        out[(team_key(r.get("home"), aliases), team_key(r.get("away"), aliases))] = {
            "home_margin": hm, "total": tot, "home_win_prob": wp if wp is not None else cdf(hm / MARGIN_SD)}
    return out


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_fbs_game(g: dict) -> bool:
    hc, ac = g.get("homeClassification"), g.get("awayClassification")
    return not hc or not ac or (hc == "fbs" and ac == "fbs")


# ---------------------------------------------------------------- live round
def run_live(*, games: list[dict], lines: dict, models: list, now: datetime | None = None,
             rows: list[dict] | None = None, workbook: dict | None = None) -> dict:
    """Log new picks and settle finished ones.

    games:  every game of the season so far (ESPN/CFBD shape)
    lines:  {game_id: {"lines": [...]}} current market (closing line once the game is over)
    models: [(name, project(home, away, neutral) -> obj with home_margin/total/home_win_prob or None)]
    """
    now = now or datetime.now(timezone.utc)
    rows = load_ledger() if rows is None else rows
    workbook = load_workbook_model() if workbook is None else workbook
    aliases = _aliases()
    by_id = {r["pick_id"]: r for r in rows}
    games_by_id = {str(g.get("id")): g for g in games}
    lines = {str(k): v for k, v in lines.items()}

    upcoming = []
    for g in games:
        if g.get("completed") or not _is_fbs_game(g):
            continue
        ko = _parse_dt(g.get("startDate") or "")
        if ko and now + MIN_LEAD <= ko <= now + timedelta(days=LOOKAHEAD_DAYS):
            upcoming.append(g)

    logged = 0
    stamp = now.isoformat(timespec="seconds")
    unmatched = set()
    for g in upcoming:
        gl = lines.get(str(g.get("id")))
        if not gl:
            continue
        mkt = consensus(gl)
        projs = []
        for name, fn in models:
            p = fn(g["homeTeam"], g["awayTeam"], bool(g.get("neutralSite")))
            if p:
                projs.append((name, p.home_margin, p.total, p.home_win_prob))
            else:
                unmatched.add(f'{name}: {g["awayTeam"]} @ {g["homeTeam"]}')
        wb = workbook.get((team_key(g["homeTeam"], aliases), team_key(g["awayTeam"], aliases)))
        if wb:
            projs.append(("workbook", wb["home_margin"], wb["total"], wb["home_win_prob"]))
        for name, hmgn, tot, wp in projs:
            for pick in make_picks(game=g, market=mkt, home_margin=hmgn, total=tot, home_win_prob=wp,
                                   model=name, source="live", logged_at=stamp):
                if pick["pick_id"] not in by_id:      # frozen: never overwrite a logged pick
                    by_id[pick["pick_id"]] = pick
                    logged += 1

    # Closing line: keep re-reading the market until kickoff. The last pre-game read is
    # the close (feeds drop odds once a game starts, and in-game lines aren't the close).
    closed = 0
    for r in by_id.values():
        if r.get("source") != "live" or r.get("result"):
            continue
        g = games_by_id.get(str(r.get("game_id")))
        ko = _parse_dt(r.get("kickoff") or "")
        gl = lines.get(str(r.get("game_id")))
        if g and gl and ko and now < ko and not g.get("completed"):
            cl, co, v = clv(r, consensus(gl))
            if v != "":
                r["close_line"], r["close_odds"], r["clv"] = cl, co, v
                closed += 1

    settled = 0
    for r in list(by_id.values()):
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

    return {"rows": list(by_id.values()), "logged": logged, "settled": settled, "close_updates": closed,
            "upcoming_games": len(upcoming),
            "games_with_lines": sum(1 for g in upcoming if str(g.get("id")) in lines),
            "not_projected": sorted(unmatched)[:40], "workbook_games": len(workbook)}


def run_backtest(*, season: int, games: list[dict], lines: dict, model_builders: dict,
                 rows: list[dict] | None = None) -> dict:
    """Replay finished weeks. For week w each model is built only from games BEFORE w
    (model_builders[name](week, prior_games) -> project fn, or None to skip that week).
    Spreads/totals are graded at the OPENING line, moneylines at the closing price.
    Replaces any earlier backtest rows for that season; live rows are untouched."""
    rows = load_ledger() if rows is None else rows
    keep = [r for r in rows if not (r.get("source") == "backtest" and str(r.get("season")) == str(season))]
    lines = {str(k): v for k, v in lines.items()}
    done = [g for g in games if g.get("completed") and g.get("homePoints") is not None and _is_fbs_game(g)]
    weeks = sorted({int(g["week"]) for g in done if g.get("week") is not None})
    new, skipped = [], {}
    for w in weeks:
        prior = [g for g in done if g.get("week") is not None and int(g["week"]) < w]
        for name, build in model_builders.items():
            fn = build(w, prior)
            if fn is None:
                skipped.setdefault(name, []).append(w)
                continue
            for g in (x for x in done if int(x.get("week") or -1) == w):
                gl = lines.get(str(g.get("id")))
                p = fn(g["homeTeam"], g["awayTeam"], bool(g.get("neutralSite"))) if gl else None
                if not p:
                    continue
                opening, closing = consensus(gl, opening=True), consensus(gl)
                for k in ("spread", "total"):     # no opening line recorded -> use the close
                    if opening[k] is None:
                        opening[k] = closing[k]
                mkt = {**opening, "home_ml": closing["home_ml"], "away_ml": closing["away_ml"]}
                g = {**g, "season": g.get("season") or season}
                for pick in make_picks(game=g, market=mkt, home_margin=p.home_margin, total=p.total,
                                       home_win_prob=p.home_win_prob, model=name, source="backtest",
                                       logged_at="backtest"):
                    hp, ap = float(g["homePoints"]), float(g["awayPoints"])
                    pick["result"], pick["units"] = grade(pick, hp, ap)
                    pick["home_pts"], pick["away_pts"] = int(hp), int(ap)
                    if pick["market"] != "ML":
                        pick["close_line"], pick["close_odds"], pick["clv"] = clv(pick, closing)
                    pick["settled_at"] = "backtest"
                    new.append(pick)
    return {"rows": keep + new, "graded": len(new), "weeks": weeks, "skipped_weeks": skipped}


# ---------------------------------------------------------------- reporting
def _stats(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("result")]
    w = sum(r["result"] == "W" for r in done)
    l = sum(r["result"] == "L" for r in done)
    p = sum(r["result"] == "P" for r in done)
    units = sum(float(r.get("units") or 0) for r in done)
    clv_pts = [float(r["clv"]) for r in done if r.get("clv") not in ("", None) and r["market"] != "ML"]
    clv_ml = [float(r["clv"]) for r in done if r.get("clv") not in ("", None) and r["market"] == "ML"]
    clvs = clv_pts + clv_ml
    return {"picks": len(done), "w": w, "l": l, "p": p, "units": round(units, 2),
            "roi": units / len(done) if done else None,
            "win_pct": w / (w + l) if (w + l) else None,
            "open": len(rows) - len(done),
            "clv_pts": statistics.mean(clv_pts) if clv_pts else None,
            "clv_ml": statistics.mean(clv_ml) if clv_ml else None,
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None}


GROUPS = (("Recommended bets (Strong + Lean)", lambda r: r.get("tier") in RECOMMENDED),
          ("  Strong", lambda r: r.get("tier") == "STRONG"),
          ("  Lean", lambda r: r.get("tier") == "LEAN"),
          ("Tracked leans (no edge, not bets)", lambda r: r.get("tier") == "NO_EDGE"),
          ("Long shots (flagged, not recommended)", lambda r: r.get("tier") == "LONG_SHOT"))


def summarize(rows: list[dict], *, source: str | None = None, model: str | None = None,
              market: str | None = None) -> list[dict]:
    rows = [r for r in rows if (source is None or r.get("source") == source)
            and (model is None or r.get("model") == model) and (market is None or r.get("market") == market)]
    return [{"group": name, **_stats([r for r in rows if fn(r)])} for name, fn in GROUPS]


def by_market(rows: list[dict], *, source: str | None = None, model: str | None = None,
              recommended_only: bool = False) -> list[dict]:
    out = []
    for m in ("SPREAD", "TOTAL", "ML"):
        sel = [r for r in rows if r.get("market") == m and (source is None or r.get("source") == source)
               and (model is None or r.get("model") == model)
               and (not recommended_only or r.get("tier") in RECOMMENDED)]
        out.append({"market": m, **_stats(sel)})
    return out


def format_table(rows: list[dict], *, source: str, model: str = "scores_v1") -> str:
    sel = [r for r in rows if r.get("source") == source and r.get("model") == model]
    graded = sum(1 for r in sel if r.get("result"))
    lines = [f"[{source} / {model}]  Graded picks: {graded}   still pending (games not played yet): {len(sel) - graded}",
             "",
             f"{'group':<40}{'picks':>6}  {'W-L-P':<11}{'units':>8}{'ROI':>9}{'CLV':>8}"]
    for s in summarize(rows, source=source, model=model):
        roi = "" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%"
        clv_txt = "" if s["clv_pts"] is None else f"{s['clv_pts']:+.2f}"
        lines.append(f"{s['group']:<40}{s['picks']:>6}  {s['w']}-{s['l']}-{s['p']:<5}"
                     f"{s['units']:>+8.2f}{roi:>9}{clv_txt:>8}")
    lines += ["", "Only Strong + Lean are real recommendations. 1 unit flat per pick at the posted price.",
              "CLV = points you beat the closing line by (spreads/totals). Under ~200 picks, trust CLV over W-L."]
    return "\n".join(lines)


def write_status(summary: dict, path: Path | None = None) -> None:
    path = path or STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
