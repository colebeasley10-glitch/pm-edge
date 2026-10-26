"""Sleeper pick'em vs Pinnacle: parsing, matching, conservative line handling, Odds Fam flow."""
from datetime import datetime, timedelta, timezone

from research_lab import odds_fam as of
from research_lab import sleeper as sl

NOW = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=8)).isoformat()


def line(pid, stat, val, over, under, sport="nfl"):
    opts = [{"status": "active", "outcome": "over", "outcome_value": val, "payout_multiplier": str(over)},
            {"status": "active", "outcome": "under", "outcome_value": val, "payout_multiplier": str(under)}]
    return {"status": "active", "sport": sport, "subject_id": pid, "subject_type": "player",
            "outcome_type": "over_under", "line_type": "normal", "game_status": "pre_game", "wager_type": stat,
            "options": opts}


def board(player, stat, ln, p_over):
    return {"player": player, "stat": stat, "line": ln, "p_over": p_over, "league": "NFL",
            "game": "Green Bay Packers at Detroit Lions", "starts": START, "limit": 1000}


NAMES = {"nfl": {"1": "Josh Jacobs", "2": "CeeDee Lamb"}}


def test_flags_multiplier_that_beats_fair():
    props = sl.sleeper_props([line("1", "rushing_yards", 52.5, 1.75, 2.0)], NAMES)
    # Pinnacle: Under 52.5 fair 54% -> Sleeper Under 2.0x = +8% EV
    e = sl.edges(props, [board("Josh Jacobs", "Rushing Yards", 52.5, 0.46)], NOW)
    assert len(e) == 1 and e[0]["side"] == "under" and abs(e[0]["ev"] - 0.08) < 1e-9


def test_better_number_uses_pinnacle_as_floor_worse_number_skipped():
    props = sl.sleeper_props([line("1", "rushing_yards", 53.5, 1.85, 1.95)], NAMES)
    e = sl.edges(props, [board("Josh Jacobs", "Rushing Yards", 51.5, 0.46)], NOW)
    assert len(e) == 1 and e[0]["side"] == "under"           # Under 53.5 >= Pinnacle Under 51.5
    props = sl.sleeper_props([line("2", "receiving_yards", 74.5, 2.4, 1.5)], NAMES)
    e = sl.edges(props, [board("CeeDee Lamb", "Receiving Yards", 78.5, 0.45)], NOW)
    assert e and e[0]["side"] == "over"                      # Over 74.5 <= Pinnacle Over 78.5


def test_no_edge_no_match():
    props = sl.sleeper_props([line("1", "rushing_yards", 52.5, 1.8, 1.8)], NAMES)
    assert sl.edges(props, [board("Josh Jacobs", "Rushing Yards", 52.5, 0.5)], NOW) == []
    assert sl.edges(props, [board("Someone Else", "Rushing Yards", 52.5, 0.3)], NOW) == []


def test_stat_names_line_up():
    assert sl.norm_stat("rushing_yards") == sl.norm_stat("Rushing Yards")
    assert sl.norm_stat("receptions") == sl.norm_stat("Receptions")
    assert sl.norm_stat("threes_made") == sl.norm_stat("Threes Made")
    assert sl.norm_name("Kenneth Walker III") == sl.norm_name("Kenneth Walker")


def test_odds_fam_ranks_pickem_first_and_grades_from_box(tmp_path):
    props = sl.sleeper_props([line("1", "rushing_yards", 52.5, 1.75, 2.0)], NAMES)
    e = sl.edges(props, [board("Josh Jacobs", "Rushing Yards", 52.5, 0.46)], NOW)
    pushes = []
    of.update(NOW, tmp_path, pickem=e, push=lambda t, m, u: pushes.append((t, m)) or True)
    row = of.load(tmp_path)[0]
    assert row["kind"] == "Pick'em gap" and row["pick"] == "Josh Jacobs Under 52.5 Rushing Yards"
    assert "Sleeper 2.00x" in pushes[0][1] and "🔥" in pushes[0][0]
    of.update(NOW + timedelta(hours=14), tmp_path, box=lambda play: 41.0)
    row = of.load(tmp_path)[0]
    assert row["status"] == "won" and abs(float(row["pl"]) - 10.0) < 1e-9
