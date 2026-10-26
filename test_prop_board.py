"""Prop Board: parse Pinnacle props, fair prices, prop steam plays, box-score grading."""
from datetime import datetime, timedelta, timezone

from research_lab import prop_board as pb

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
START = "2026-10-26T23:00:00Z"


def payload(line, over, under, limit=500, special="Josh Jacobs Total Rushing Yards", units="Rushing Yards"):
    return {5: {"events": [
        {"event_id": 1, "league_name": "NFL", "home": "Detroit Lions", "away": "Green Bay Packers", "starts": START,
         "periods": {"num_0": {}}},
        {"event_id": 2, "parent_id": 1, "league_name": "NFL", "special": special, "special_units": units,
         "special_category": "Player Props", "starts": START,
         "special_markets": {"num_0": [{"type": "total", "max_risk": limit, "prices": [
             {"name": "Over", "points": line, "price": over}, {"name": "Under", "points": line, "price": under}]}]}}]}}


def test_parses_player_stat_and_fair_prices(tmp_path):
    info = pb.update(payload(52.5, 1.91, 1.91), T0, tmp_path)
    rows = pb.load_board(tmp_path)["rows"]
    assert info["props"] == 1 and rows[0]["player"] == "Josh Jacobs" and rows[0]["stat"] == "Rushing Yards"
    assert rows[0]["fair_over"] == "+100" or rows[0]["fair_over"] == "-100"
    assert rows[0]["bet_under_at"].startswith("+") or rows[0]["bet_under_at"] == "-100"


def test_line_drop_becomes_under_play(tmp_path):
    pushes = []
    pb.update(payload(56.5, 1.91, 1.91), T0, tmp_path)
    info = pb.update(payload(51.5, 1.91, 1.91), T0 + timedelta(hours=3), tmp_path,
                     push=lambda t, m: pushes.append((t, m)) or True)
    plays = pb.load(tmp_path)
    assert info["new_plays"] == 1 and plays[0]["pick"] == "Josh Jacobs Under 51.5 Rushing Yards"
    assert "56.5" in pushes[0][1] and "P limit $500" in pushes[0][1] and "or better" in pushes[0][1]


def test_small_moves_low_limits_and_too_soon_skip(tmp_path):
    pb.update(payload(52.5, 1.91, 1.91), T0, tmp_path)
    assert pb.update(payload(51.5, 1.91, 1.91), T0 + timedelta(hours=3), tmp_path)["new_plays"] == 0   # 1 yd
    pb.update(payload(56.5, 1.91, 1.91, limit=50), T0, tmp_path / "b")
    assert pb.update(payload(50.5, 1.91, 1.91, limit=50), T0 + timedelta(hours=3), tmp_path / "b")["new_plays"] == 0
    pb.update(payload(56.5, 1.91, 1.91), T0, tmp_path / "c")
    assert pb.update(payload(50.5, 1.91, 1.91), T0 + timedelta(minutes=30), tmp_path / "c")["new_plays"] == 0


def test_price_move_at_same_line_counts_for_small_stats(tmp_path):
    p1 = payload(7.5, 1.91, 1.91, special="Luka Doncic Total Assists", units="Assists")
    p2 = payload(7.5, 2.25, 1.65, special="Luka Doncic Total Assists", units="Assists")
    pb.update(p1, T0, tmp_path)
    pb.update(p2, T0 + timedelta(hours=3), tmp_path)
    assert pb.load(tmp_path)[0]["side"] == "under"


def test_grades_from_box_score(tmp_path):
    pb.update(payload(56.5, 1.91, 1.91), T0, tmp_path)
    pb.update(payload(51.5, 1.91, 1.91), T0 + timedelta(hours=3), tmp_path)
    pb.update({}, T0 + timedelta(hours=16), tmp_path, box=lambda play: 38.0)
    r = pb.load(tmp_path)[0]
    assert r["status"] == "won" and float(r["pl"]) > 0


def test_stat_value_reads_espn_boxes():
    nba = {"": {"PTS": "31", "REB": "8", "AST": "9", "3PT": "4-11"}}
    assert pb.stat_value("Assists", nba) == 9 and pb.stat_value("Threes Made", nba) == 4
    assert pb.stat_value("Pts+Rebs+Asts", nba) == 48
    nfl = {"rushing": {"CAR": "18", "YDS": "74"}, "receiving": {"REC": "3", "YDS": "22"}}
    assert pb.stat_value("Rushing Yards", nfl) == 74 and pb.stat_value("Receptions", nfl) == 3


def test_more_stats():
    nfl = {"passing": {"C/ATT": "21/33", "YDS": "240", "TD": "2", "INT": "1"},
           "rushing": {"CAR": "17", "YDS": "80", "TD": "1"}, "receiving": {"REC": "2", "TD": "1"}}
    assert pb.stat_value("Rush Attempts", nfl) == 17 and pb.stat_value("Pass Completions", nfl) == 21
    assert pb.stat_value("Pass Attempts", nfl) == 33 and pb.stat_value("Interceptions", nfl) == 1
    assert pb.stat_value("Touchdowns", nfl) == 2
    nhl = {"forwards": {"G": "1", "A": "2", "S": "4"}}
    assert pb.stat_value("Goals", nhl) == 1 and pb.stat_value("Points", nhl) == 3
    assert pb.stat_value("Shots On Goal", nhl) == 4
