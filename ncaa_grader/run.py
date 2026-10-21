"""Entry point (PAPER ONLY).

  python -m ncaa_grader.run              # log upcoming picks + settle finished games
  python -m ncaa_grader.run --backtest   # replay this season's played weeks (no look-ahead)
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
        builders = {"scores_v1": lambda w, prior: _scores_projector(prior)}
        if core_rows:
            builders["core_v1"] = lambda w, prior: _core_projector(core_rows, through_week=w)
        out = core.run_backtest(season=season, games=games, lines=lines, model_builders=builders, rows=rows)
        status.update(mode="backtest", graded=out["graded"], weeks=out["weeks"], skipped_weeks=out["skipped_weeks"])
        if "core_v1" in out["skipped_weeks"] and len(out["skipped_weeks"]["core_v1"]) == len(out["weeks"]):
            print("::notice title=core_v1 not backtested::CFBD CORE has no week-by-week snapshots, so it "
                  "can't be replayed without look-ahead. It's graded live only.")
    else:
        models = [("scores_v1", _scores_projector(games))]
        cp = _core_projector(core_rows) if core_rows else None
        if cp:
            models.append(("core_v1", cp))
        models = [(n, f) for n, f in models if f]
        out = core.run_live(games=games, lines=lines, models=models, now=now, rows=rows)
        status.update(mode="live", logged=out["logged"], settled=out["settled"], models=[n for n, _ in models],
                      upcoming_games=out["upcoming_games"], games_with_lines=out["games_with_lines"],
                      not_projected=out["not_projected"], workbook_games=out["workbook_games"])

    core.save_ledger(out["rows"])
    try:
        prev = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        prev = {}
    prev[status["mode"]] = status
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
