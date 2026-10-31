"""NBA Game Model: ESPN parsing, ratings (no look-ahead, back-to-backs), and the live log/close/settle loop."""
from datetime import date, datetime, timedelta, timezone

from espn_nba import parse_event, season_start_year
from nba_grader import core
from nba_ratings import NBARatings


def ev(gid, start, home, away, hs="0", as_="0", completed=False, spread="-5.5", total="224.5", ml=("-220", "+180")):
    o = {"provider": {"name": "DraftKings"},
         "pointSpread": {"home": {"close": {"line": spread, "odds": "-110"}, "open": {"line": spread, "odds": "-110"}},
                         "away": {"close": {"line": "+5.5", "odds": "-110"}}},
         "total": {"over": {"close": {"line": "o" + total, "odds": "-110"}},
                   "under": {"close": {"line": "u" + total, "odds": "-110"}}},
         "moneyline": {"home": {"close": {"odds": ml[0]}}, "away": {"close": {"odds": ml[1]}}}}
    return {"id": str(gid), "date": start, "season": {"year": 2027, "type": 2},
            "competitions": [{"status": {"type": {"completed": completed}},
                              "competitors": [
                                  {"homeAway": "home", "score": hs, "team": {"location": "Los Angeles", "displayName": home}},
                                  {"homeAway": "away", "score": as_, "team": {"location": "Somewhere", "displayName": away}}],
                              "odds": [o]}]}


def g(gid, day, home, away, hp, ap, season=2027):
    return {"id": str(gid), "season": season, "week": day, "startDate": day + "T23:30Z", "completed": True,
            "homeTeam": home, "awayTeam": away, "homePoints": hp, "awayPoints": ap, "neutralSite": False}


def test_parse_uses_full_name_and_et_date():
    game, gl = parse_event(ev(1, "2026-10-31T02:00Z", "Golden State Warriors", "Los Angeles Lakers"))
    assert game["homeTeam"] == "Golden State Warriors"      # not the ambiguous city
    assert game["week"] == "2026-10-30"                      # 10 PM ET tip belongs to the 30th
    assert gl["lines"][0]["spread"] == -5.5 and gl["lines"][0]["overUnder"] == 224.5


def test_final_games_carry_no_line():
    game, gl = parse_event(ev(2, "2026-10-29T23:30Z", "A", "B", "110", "100", completed=True))
    assert game["homePoints"] == 110 and gl is None


def test_season_start_year():
    assert season_start_year(date(2026, 10, 30)) == 2026 and season_start_year(date(2027, 3, 1)) == 2026


def _league():
    # A beats B by ~12 every time, B and C are even, A beats C by ~12.
    games, gid, d = [], 0, date(2026, 10, 20)
    for i in range(8):
        for h, a, hp, ap in (("A", "B", 118, 106), ("B", "C", 110, 108), ("C", "A", 104, 114)):
            gid += 1
            games.append(g(gid, (d + timedelta(days=2 * i)).isoformat(), h, a, hp, ap))
    return games


def test_ratings_rank_teams_and_ignore_future_games():
    games = _league()
    as_of = date(2026, 11, 10)
    r = NBARatings(games, as_of=as_of, season=2027)
    p = r.project("A", "B", on=as_of)
    assert p and p.home_margin > 6 and p.home_win_prob > 0.7
    # A huge future B blowout must not change a projection made before it
    future = games + [g(999, "2026-11-20", "B", "A", 160, 80)]
    assert NBARatings(future, as_of=as_of, season=2027).project("A", "B", on=as_of).home_margin == p.home_margin


def test_back_to_back_costs_the_tired_team():
    games = _league()
    on = date(2026, 11, 10)
    fresh = NBARatings(games, as_of=on, season=2027).project("A", "B", on=on)
    tired = NBARatings(games + [g(500, "2026-11-09", "A", "C", 110, 105)], as_of=on, season=2027).project("A", "B", on=on)
    assert tired.home_margin < fresh.home_margin


def test_live_logs_freezes_closes_and_settles():
    hist = _league()
    tip = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
    up = {"id": "77", "season": 2027, "week": "2026-11-10", "startDate": "2026-11-10T23:30Z", "completed": False,
          "homeTeam": "A", "awayTeam": "B", "homePoints": None, "awayPoints": None, "neutralSite": False}
    line = lambda s: {"77": {"id": "77", "lines": [{"spread": s, "overUnder": 224.5, "homeMoneyline": -200,
                                                    "awayMoneyline": 170, "homeSpreadOdds": -110,
                                                    "awaySpreadOdds": -110, "overOdds": -110, "underOdds": -110}]}}
    proj = lambda game: NBARatings(hist, as_of=date(2026, 11, 10), season=2027).project(
        game["homeTeam"], game["awayTeam"], on=date(2026, 11, 10))

    out = core.run_live(games=hist + [up], lines=line(-3.5), project=proj, now=tip - timedelta(hours=8), rows=[])
    sp = next(r for r in out["rows"] if r["market"] == "SPREAD")
    assert out["logged"] == 3 and sp["pick_team"] == "A" and sp["line"] == -3.5 and sp["tier"] == "STRONG"

    # Line moves to -6.5: pick stays frozen at -3.5, CLV is +3
    out = core.run_live(games=hist + [up], lines=line(-6.5), project=proj, now=tip - timedelta(hours=1), rows=out["rows"])
    sp = next(r for r in out["rows"] if r["market"] == "SPREAD")
    assert out["logged"] == 0 and sp["line"] == -3.5 and sp["clv"] == 3.0

    final = {**up, "completed": True, "homePoints": 120, "awayPoints": 110}
    out = core.run_live(games=hist + [final], lines={}, project=proj, now=tip + timedelta(hours=4), rows=out["rows"])
    sp = next(r for r in out["rows"] if r["market"] == "SPREAD")
    assert out["settled"] == 3 and sp["result"] == "W" and abs(sp["units"] - 0.9091) < 1e-3
