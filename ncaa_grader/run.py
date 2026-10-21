"""Entry point (PAPER ONLY).

  python -m ncaa_grader.run              # log upcoming picks + settle finished games
  python -m ncaa_grader.run --backtest   # replay this season's played weeks (no look-ahead)

Live picks use ESPN's free scoreboard. ESPN drops odds once a game is final, so the
BACKTEST needs historical lines from CollegeFootballData (~10 calls, once). A normal
run starts the backtest by itself the first time CFBD answers.
  python -m ncaa_grader.run --report     # just print the tables

Schedule, scores and odds come from ESPN's free scoreboard (no key, no quota).
Models:
  scores_v1  opponent-adjusted ratings fit on ESPN final scores (always available)
  core_v1    CFBD CORE ratings, added only when CollegeFootballData answers
  workbook   your own numbers from ncaa_grader/data/my_model.csv, if present
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from espn_cfb import ESPNCFB
from ncaa_grader import core
from score_ratings import ScoreRatings

MODEL_ORDER = ("scores_v1", "core_v1", "workbook")


def _print_tables(rows):
    for source in ("live", "backtest"):
        for model in MODEL_ORDER:
            if any(r.get("source") == source and r.get("model") == model for r in rows):
                print(core.format_table(rows, source=source, model=model))
                print()


def _core_rows(year, status):
    """CFBD CORE ratings, or [] if CFBD is out of quota / unreachable (never fatal)."""
    try:
        from cfbd_client import CachedCFBD
        c = CachedCFBD()
        rows = c.core(year, ttl=24 * 3600)
        c.save()
        status["cfbd_calls"] = c.calls
        return rows
    except Exception as exc:
        status["cfbd_unavailable"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return []


def _cfbd_history(season, status):
    """CFBD games + lines for every played week, or None if CFBD is unavailable."""
    try:
        from cfbd_client import CachedCFBD
        c = CachedCFBD()
        games = c.games(season)
        weeks = sorted({int(g["week"]) for g in games if g.get("completed") and g.get("week") is not None})
        lines = {}
        for w in weeks:
            for gl in c.lines(season, w, ttl=7 * 86400):
                lines[str(gl.get("id") or gl.get("gameId"))] = gl
        c.save()
        status["cfbd_history_calls"] = c.calls
        return games, lines
    except Exception as exc:
        status["backtest_waiting"] = f"CFBD history unavailable: {type(exc).__name__}: {str(exc)[:160]}"
        return None


def _backtest(season, rows, core_rows, status):
    hist = _cfbd_history(season, status)
    if hist is None:
        return None
    games, lines = hist
    builders = {"scores_v1": lambda w, prior: _scores_projector(prior)}
    if core_rows:
        builders["core_v1"] = lambda w, prior: _core_projector(core_rows, through_week=w)
    out = core.run_backtest(season=season, games=games, lines=lines, model_builders=builders, rows=rows)
    status["backtest"] = {"graded": out["graded"], "weeks": out["weeks"], "skipped_weeks": out["skipped_weeks"]}
    if "core_v1" in out["skipped_weeks"] and len(out["skipped_weeks"]["core_v1"]) == len(out["weeks"]):
        print("::notice title=core_v1 not backtested::CFBD CORE has no week-by-week snapshots, so it "
              "can't be replayed without look-ahead. It's graded live only.")
    return out


def _core_projector(core_rows, through_week=None):
    from ncaa_predictive import project, ratings_map
    aliases = core._aliases()
    key = lambda n: core.team_key(n, aliases)
    rm = {key(t): r for t, r in ratings_map(core_rows, through_week=through_week).items()}
    if not rm:
        return None
    return lambda h, a, neutral: project(key(h), key(a), rm, neutral=neutral)


def _scores_projector(games):
    aliases = core._aliases()
    if not any(g.get("completed") for g in games):
        return None
    return ScoreRatings(games, key=lambda n: core.team_key(n, aliases)).project


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    rows = core.load_ledger()
    try:
        prev = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        prev = {}
    if args.report:
        _print_tables(rows)
        return

    now = datetime.now(timezone.utc)
    espn = ESPNCFB()
    season, week = espn.current()
    games, lines = espn.season(season, min(week + 1, 16))
    espn.save()
    status = {"at": now.isoformat(timespec="seconds"), "season": season, "current_week": week,
              "espn_calls": espn.calls, "espn_errors": espn.errors, "games": len(games), "lines": len(lines)}
    core_rows = _core_rows(season, status)

    if args.backtest:
        status["mode"] = "backtest"
        out = _backtest(season, rows, core_rows, status)
        if out is not None:
            status["backtest_done_season"] = season
        else:
            print(f"::warning title=Backtest waiting on CFBD::{status['backtest_waiting']}")
            out = {"rows": rows}
    else:
        models = [("scores_v1", _scores_projector(games))]
        cp = _core_projector(core_rows) if core_rows else None
        if cp:
            models.append(("core_v1", cp))
        models = [(n, f) for n, f in models if f]
        out = core.run_live(games=games, lines=lines, models=models, now=now, rows=rows)
        status.update(mode="live", logged=out["logged"], settled=out["settled"], models=[n for n, _ in models],
                      upcoming_games=out["upcoming_games"], games_with_lines=out["games_with_lines"],
                      close_updates=out["close_updates"], not_projected=out["not_projected"],
                      workbook_games=out["workbook_games"])
        if str(prev.get("backtest_done_season")) != str(season):
            bt = _backtest(season, out["rows"], core_rows, status)   # runs once, when CFBD answers
            if bt is not None:
                out = bt
                status["backtest_done_season"] = season

    core.save_ledger(out["rows"])
    prev[status["mode"]] = status
    if status.get("backtest_done_season"):
        prev["backtest_done_season"] = status["backtest_done_season"]
    core.write_status(prev)
    print(json.dumps(status, indent=2, default=str))
    print()
    _print_tables(out["rows"])


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        if "unavailable and nothing cached" not in str(exc):
            raise
        print(f"::warning title=NCAA grader skipped::{str(exc)[:400]}")
