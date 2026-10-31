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


def test_offseason_does_not_age_last_season():
    last = [g(i, "2026-04-0%d" % (1 + i % 9), "A", "B", 120, 100, season=2026) for i in range(10)]
    first_week = [g(100, "2026-10-21", "C", "D", 110, 110)]
    r = NBARatings(last + first_week, as_of=date(2026, 10, 25), season=2027)
    assert r.w["A"] > 2.0   # ~10 games x 0.35 prior weight, barely decayed


def test_heavy_ml_favorite_never_recommended():
    game = {"id": "1", "homeTeam": "A", "awayTeam": "B", "startDate": "x"}
    mkt = {"spread": None, "total": None, "home_ml": -440, "away_ml": 340}
    ml = core.make_picks(game=game, market=mkt, home_margin=14, total=230, home_win_prob=0.92,
                         model="m", source="live", logged_at="x")[0]
    assert ml["pick_team"] == "A" and ml["tier"] == "NO_EDGE"


# ------------------------------------------------------------------ player props
from espn_nba import parse_box
from nba_grader import props


def test_negative_binomial_and_line_moves():
    assert abs(props.p_over(20.0, 19.5, 2.3) - 0.5) < 0.06
    assert props.p_over(25, 19.5, 2.3) > 0.75 and props.p_over(3, 2.5, 1.05) > 0.5
    m = props.mean_for(0.6, 22.5, 2.3)
    assert abs(props.p_over(m, 22.5, 2.3) - 0.6) < 1e-3


def test_parse_box_reads_threes_and_dnp():
    summ = {"boxscore": {"players": [{"team": {"displayName": "A"}, "statistics": [{
        "labels": ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
        "athletes": [{"athlete": {"displayName": "Luka Dončić"}, "stats": ["36", "11-22", "4-10", "5-6", "1", "8", "9",
                                                                           "8", "1", "0", "4", "2", "+6", "31"]},
                     {"athlete": {"displayName": "Bench Guy"}, "stats": [], "didNotPlay": True}]}]}]}}
    rows = parse_box(summ)
    assert rows[0]["pts"] == 31 and rows[0]["fg3m"] == 4 and rows[0]["reb"] == 9 and not rows[0]["dnp"]
    assert rows[1]["dnp"] and props.norm_name(rows[0]["player"]) == "luka doncic"


def _prop_world():
    games, boxes = [], {}
    for i in range(6):
        d = f"2026-10-{20 + i * 2}"
        gid = f"g{i}"
        games.append({"id": gid, "season": 2027, "week": d, "completed": True, "homeTeam": "A", "awayTeam": "B",
                      "homePoints": 110, "awayPoints": 100})
        boxes[gid] = [{"player": "Luka Doncic", "team": "A", "dnp": False, "min": 36.0, "pts": 32.0, "reb": 9.0,
                       "ast": 8.0, "fg3m": 4.0, "stl": 1.0, "blk": 0.0, "tov": 3.0}]
    return games, boxes


def test_prop_logs_over_when_projection_beats_pinnacle_then_settles():
    games, boxes = _prop_world()
    tip = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)   # Oct 31 ET
    board = [{"key": "p1", "league": "NBA", "player": "Luka Doncic", "stat": "Points", "home": "A", "away": "B",
              "starts": "2026-11-01T00:00:00Z", "line": 27.5, "p_over": 0.5, "limit": 250}]
    out = props.run(board=board, games=games, boxes=boxes, season=2027, now=tip - timedelta(hours=6), rows=[])
    r = out["rows"][0]
    assert out["logged"] == 1 and r["direction"] == "OVER" and r["tier"] == "STRONG" and abs(r["proj"] - 32) < 0.5
    # Pinnacle moves to 29.5: frozen at 27.5, CLV positive after converting the close to our line
    board[0].update(line=29.5, p_over=0.52)
    out = props.run(board=board, games=games, boxes=boxes, season=2027, now=tip - timedelta(hours=1), rows=out["rows"])
    r = out["rows"][0]
    assert out["logged"] == 0 and r["line"] == 27.5 and r["clv"] > 0.05
    final = {"id": "g9", "season": 2027, "week": "2026-10-31", "completed": True, "homeTeam": "A", "awayTeam": "B",
             "homePoints": 120, "awayPoints": 111}
    boxes["g9"] = [{**boxes["g0"][0], "pts": 30.0}]
    out = props.run(board=[], games=games + [final], boxes=boxes, season=2027, now=tip + timedelta(hours=4),
                    rows=out["rows"])
    r = out["rows"][0]
    assert r["result"] == "W" and r["actual"] == 30.0 and float(r["units"]) > 0.8


def test_prop_dnp_voids_and_thin_history_skips():
    games, boxes = _prop_world()
    board = [{"key": "p2", "league": "NBA", "player": "Nobody New", "stat": "Points", "home": "A", "away": "B",
              "starts": "2026-11-01T00:00:00Z", "line": 10.5, "p_over": 0.5, "limit": 250}]
    out = props.run(board=board, games=games, boxes=boxes, season=2027,
                    now=datetime(2026, 10, 31, 12, tzinfo=timezone.utc), rows=[])
    assert out["logged"] == 0 and out["no_log"] == 1
    row = {"pick_id": "x", "key": "p3", "player": "Luka Doncic", "stat": "Points", "direction": "OVER", "line": 27.5,
           "odds": -110, "market_prob": 0.5, "home": "A", "away": "B", "game_day": "2026-10-31",
           "kickoff": "2026-11-01T00:00:00Z"}
    final = {"id": "g9", "season": 2027, "week": "2026-10-31", "completed": True, "homeTeam": "A", "awayTeam": "B"}
    boxes["g9"] = [{**boxes["g0"][0], "dnp": True, "min": 0.0}]
    out = props.run(board=[], games=games + [final], boxes=boxes, season=2027,
                    now=datetime(2026, 11, 1, 5, tzinfo=timezone.utc), rows=[row])
    assert out["rows"][0]["result"] == "V" and out["rows"][0]["units"] == 0.0
