"""Entry point (PAPER ONLY).

  python -m ncaa_grader.run                 # log this week's picks + settle finished games
  python -m ncaa_grader.run --backtest 2026 # one-time replay of weeks already played
  python -m ncaa_grader.run --report        # just print the tables
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from ncaa_grader import core


def _print_tables(rows):
    for source in ("live", "backtest"):
        for model in ("core_v1", "workbook"):
            if any(r.get("source") == source and r.get("model") == model for r in rows):
                print(core.format_table(rows, source=source, model=model))
                print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", type=int, metavar="SEASON")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    rows = core.load_ledger()
    if args.report:
        _print_tables(rows)
        return

    from cfbd_client import CachedCFBD  # shares the NCAA beta's cache file to save CFBD calls
    cfbd = CachedCFBD()
    now = datetime.now(timezone.utc)
    if args.backtest:
        out = core.run_backtest(cfbd, args.backtest, rows)
        if out["leak_guard_tripped"]:
            print(f"::warning title=Backtest skipped::CFBD CORE has no week-by-week rating snapshots for "
                  f"{args.backtest}, so old games would be graded with ratings that already knew the results.")
        summary = {"mode": "backtest", "season": args.backtest, "graded": out["graded"],
                   "weeks": out["weeks"], "skipped_weeks": out["skipped_weeks"]}
    else:
        out = core.run_live(cfbd, now, rows)
        summary = {"mode": "live", "logged": out["logged"], "settled": out["settled"],
                   "upcoming_games": out["upcoming_games"], "games_with_lines": out["games_with_lines"],
                   "workbook_games": out["workbook_games"]}
    cfbd.save()
    summary.update({"at": now.isoformat(timespec="seconds"), "cfbd_calls": cfbd.calls, "cfbd_errors": cfbd.errors})
    core.save_ledger(out["rows"])
    prev = {}
    try:
        import json
        prev = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        pass
    prev[summary["mode"]] = summary
    core.write_status(prev)
    print(summary)
    print()
    _print_tables(out["rows"])


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        if "CFBD unavailable" not in str(exc):
            raise
        print(f"::warning title=NCAA grader skipped::{str(exc)[:400]}")
