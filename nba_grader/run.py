"""Entry point (PAPER ONLY).

  python -m nba_grader.run            # log today's/tomorrow's picks, refresh closing lines, settle finals
  python -m nba_grader.run --report   # just print the table

Data: ESPN's free NBA scoreboard (no key, no quota). The model is fit on this season's
finals plus the last ~8 weeks of last season at reduced weight, using only games
before the day it projects. New STRONG picks go to your phone if the Pushover
secrets are set. Nothing here can place a bet.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone

import requests

from espn_nba import ET, BoxCache, ESPNNBA, season_start_year
from nba_grader import core, props
from nba_ratings import NBARatings, _day


def _window(today: date) -> tuple[date, date, date, date]:
    y = season_start_year(today)
    prior = (date(y, 2, 20), date(y, 4, 15))            # end of last regular season
    current = (date(y, 10, 1), today + timedelta(days=1))
    return prior[0], prior[1], current[0], current[1]


def _projector(games: list[dict], season):
    """project(game): fit on everything before that game's day (cached per day)."""
    fits: dict = {}

    def project(g):
        d = _day(g)
        if d is None:
            return None
        if d not in fits:
            fits[d] = NBARatings(games, as_of=d, season=season)
        return fits[d].project(g["homeTeam"], g["awayTeam"], bool(g.get("neutralSite")), on=d)
    return project, fits


def _board() -> list[dict]:
    """NBA rows from the Prop Board (Pinnacle props, refreshed by the Research Lab workflow)."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "research_lab" / "data" / "PROP_BOARD" / "board.json"
    try:
        b = json.loads(p.read_text())
    except Exception:
        return []
    return [r for r in b.get("rows") or [] if r.get("league") == "NBA"]


def _b2b_teams(games: list[dict]) -> set:
    days = {}
    for g in games:
        for t in (g.get("homeTeam"), g.get("awayTeam")):
            days.setdefault(t, set()).add(str(g.get("week")))
    out = set()
    for t, ds in days.items():
        for d in ds:
            try:
                prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            except ValueError:
                continue
            if prev in ds:
                out.add((t, d))
    return out


def _notify(new: list[dict]) -> int:
    token, user = os.getenv("PUSHOVER_APP_TOKEN", "").strip(), os.getenv("PUSHOVER_USER_KEY", "").strip()
    strong = [r for r in new if r.get("tier") == "STRONG"]
    if not (token and user and strong):
        return 0
    lines = []
    for r in sorted(strong, key=lambda r: r.get("kickoff") or ""):
        try:
            tip = datetime.fromisoformat(r["kickoff"].replace("Z", "+00:00")).astimezone(ET).strftime("%-I:%M")
        except ValueError:
            tip = ""
        edge = f"edge {float(r['edge_pts']):.1f} pts" if r.get("edge_pts") not in ("", None) \
            else f"edge {float(r['edge_prob']) * 100:.1f}%"
        if r.get("market") == "PROP":
            edge = f"proj {r['proj']} vs {float(r['line']):g}"
        lines.append(f"{r['side']} ({core.fmt_odds(float(r['odds']))}) · {r['away']} @ {r['home']} {tip} · {edge}")
    payload = {"token": token, "user": user,
               "title": f"🏀 NBA model: {len(strong)} STRONG play{'s' if len(strong) != 1 else ''} (paper)",
               "message": ("\n".join(lines) + "\nPaper lane. Check injuries before betting any of these.")[:1024]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip().rstrip("/")
    if url:
        payload.update({"url": url + "/Research_Lab_NBA_Model", "url_title": "Open NBA Game Model"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    except Exception:
        return 0
    return len(strong)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    rows = core.load_ledger()
    if args.report:
        print(core.format_table(rows))
        return

    now = datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    p0, p1, c0, c1 = _window(today)
    espn = ESPNNBA()
    prior_games, _ = espn.window(p0, p1, today)
    games, lines = espn.window(c0, c1, today)
    espn.save()
    if not games:
        print(f"::warning title=NBA model skipped::ESPN returned no games. {'; '.join(espn.errors[-3:])}")
        return
    season = max((g.get("season") for g in games if g.get("season")), default=None)
    project, fits = _projector(prior_games + games, season)

    out = core.run_live(games=games, lines=lines, project=project, now=now, rows=rows)
    core.save_ledger(out["rows"])
    latest = NBARatings(prior_games + games, as_of=today + timedelta(days=1), season=season)

    # ---- player props: box scores for every final (fetched once, cached), then price the board
    box = BoxCache()
    finals = sorted((g for g in prior_games + games if g.get("completed")), key=lambda g: str(g.get("week")), reverse=True)
    boxes = {}
    for g in finals:
        b = box.get(g["id"])
        if b:
            boxes[str(g["id"])] = b
    box.save()
    league_total = 2 * latest._avg()
    upcoming = {(g["homeTeam"], g["awayTeam"]): g for g in games if not g.get("completed")}

    def pace_for(home, away):
        g = upcoming.get((home, away))
        p = project(g) if g else None
        return max(0.95, min(1.05, (p.total / league_total) ** 0.5)) if p else 1.0

    pr = props.run(board=_board(), games=prior_games + games, boxes=boxes, season=season, now=now,
                   pace_for=pace_for, b2b_teams=_b2b_teams(games))
    props.save(pr["rows"])
    pushed = _notify(out["new"] + pr["new"])
    status = {"at": now.isoformat(timespec="seconds"), "season": season, "espn_calls": espn.calls,
              "espn_errors": espn.errors[-5:], "games_this_season": len(games),
              "finals_this_season": sum(1 for g in games if g.get("completed")),
              "prior_season_games": len(prior_games), "lines": len(lines),
              "logged": out["logged"], "settled": out["settled"], "close_updates": out["close_updates"],
              "upcoming_games": out["upcoming_games"], "games_with_lines": out["games_with_lines"],
              "not_projected": out["not_projected"], "strong_pushed": pushed,
              "props": {k: v for k, v in pr.items() if k not in ("rows", "new")},
              "box_scores": len(boxes), "box_calls": box.calls, "box_errors": box.errors[-3:],
              "home_court": round(latest.hfa, 2),
              "ratings": [{"team": t, "off": round(o, 1), "def": round(d, 1), "net": round(n, 1)}
                          for t, o, d, n in latest.ratings()]}
    core.write_status(status)
    print(json.dumps({k: v for k, v in status.items() if k != "ratings"}, indent=2, default=str))
    print()
    print(core.format_table(out["rows"]))
    print("props:", props.stats(pr["rows"], ("STRONG", "LEAN")))


if __name__ == "__main__":
    main()
