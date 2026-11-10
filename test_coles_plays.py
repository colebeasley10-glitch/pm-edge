"""Cole's Plays: pick'em apps vs Pinnacle fair (paper)."""
from datetime import datetime, timedelta, timezone

from research_lab import coles_plays as cp

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=6)).isoformat().replace("+00:00", "Z")


def board(key="101", player="Jalen Brunson", stat="Points", line=25.5, p_over=0.62, league="NBA", starts=START):
    return {"key": key, "league": league, "player": player, "stat": stat, "game": "Bucks at Knicks",
            "starts": starts, "line": line, "p_over": p_over, "limit": 500}


def app(name, rows):
    def fn(session, now, base, info):
        info["sample"] = rows[:1]
        return [{"app": name, **r} for r in rows]
    return fn


def test_stat_names_match_across_apps():
    for a, b in (("Pts & Rebs & Asts", "pts_reb_ast"), ("Pts & Rebs & Asts", "Pts+Rebs+Asts"),
                 ("Threes Made", "3-PT Made"), ("Shots On Goal", "shots_on_goal"), ("Receiving Yards", "rec_yd"),
                 ("Passing Yards", "Pass Yards"), ("Saves", "Goalie Saves"), ("Points", "Hockey Points")):
        assert cp.canon_stat(a) == cp.canon_stat(b) is not None, (a, b)
    for skip in ("Fantasy Score", "1H Points", "1Q Rebounds", "anytime_touchdowns", "double_double"):
        assert cp.canon_stat(skip) is None


def test_edge_needs_two_percent_and_a_number_at_least_as_good():
    rows = [board()]
    props = [
        {"app": "PrizePicks", "player": "Jalen Brunson", "stat": "Points", "line": 25.5, "over": 1.732, "under": 1.732},
        {"app": "Underdog", "player": "Jalen  Brunson", "stat": "points", "line": 26.5, "over": 1.9, "under": 1.8},
        {"app": "Sleeper", "player": "Jalen Brunson", "stat": "points", "line": 24.5, "over": 1.70, "under": 2.5},
    ]
    found, match = cp.edges(props, rows, NOW)
    assert match["matched"] == 3
    assert len(found) == 1
    e = found[0]
    # Over 25.5 at 1.732 vs fair .62 -> +7.4%; Underdog Over 26.5 is a worse number -> skipped;
    # Sleeper Over 24.5 at 1.70 -> +5.4% (better number, Pinnacle prob is a floor) but PrizePicks pays more
    assert e["app"] == "PrizePicks" and e["side"] == "over" and abs(e["ev"] - (0.62 * 1.732 - 1)) < 1e-9
    assert e["also_at"] == "Sleeper 24.5 -143"


def test_started_or_far_off_games_skipped():
    late = board(starts=(NOW + timedelta(minutes=5)).isoformat())
    far = board(key="102", starts=(NOW + timedelta(days=3)).isoformat())
    props = [{"app": "PrizePicks", "player": "Jalen Brunson", "stat": "Points", "line": 25.5, "over": 1.8,
              "under": 1.8}]
    assert cp.edges(props, [late, far], NOW)[0] == []


def test_update_logs_once_tracks_clv_and_grades(tmp_path):
    apps = {"PrizePicks": app("PrizePicks", [{"player": "Jalen Brunson", "stat": "Points", "line": 25.5,
                                             "over": 1.732, "under": 1.732}]),
            "Broken": lambda *a: (_ for _ in ()).throw(RuntimeError("403"))}
    info = cp.update([board()], NOW, tmp_path, apps=apps)
    assert info["new_plays"] == 1 and info["apps"]["Broken"]["error"].startswith("RuntimeError")
    # Pinnacle moves toward us; the other side now also "clears" but a prop is only ever played one way
    info = cp.update([board(p_over=0.66)], NOW + timedelta(minutes=15), tmp_path,
                     apps={"PrizePicks": app("PrizePicks", [{"player": "Jalen Brunson", "stat": "Points",
                                                             "line": 25.5, "over": 1.732, "under": 3.0}])})
    rows = cp.load(tmp_path)
    assert info["new_plays"] == 0 and len(rows) == 1
    assert rows[0]["clv"] == "0.04" and rows[0]["pick"] == "Jalen Brunson Over 25.5 Points"
    later = NOW + timedelta(hours=9)
    cp.update([], later, tmp_path, apps={}, box=lambda play: 31.0)
    r = cp.load(tmp_path)[0]
    assert r["status"] == "won" and float(r["pl"]) == round(10 * (1.732 - 1), 2)


def test_underdog_and_prizepicks_parsers():
    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    ud = {"players": [{"id": "p1", "first_name": "Jalen", "last_name": "Brunson"}],
          "appearances": [{"id": "a1", "player_id": "p1"}],
          "over_under_lines": [{"stat_value": "25.5", "status": "active",
                                "over_under": {"appearance_stat": {"appearance_id": "a1", "display_stat": "Points"}},
                                "options": [{"choice": "higher", "american_price": "-120"},
                                            {"choice": "lower", "payout_multiplier": "1.1"}]}]}
    pp = {"data": [{"attributes": {"line_score": 25.5, "stat_type": "Points", "odds_type": "standard",
                                   "status": "pre_game"},
                    "relationships": {"new_player": {"data": {"id": "9", "type": "new_player"}}}},
                   {"attributes": {"line_score": 30.5, "stat_type": "Points", "odds_type": "demon"},
                    "relationships": {"new_player": {"data": {"id": "9", "type": "new_player"}}}}],
          "included": [{"type": "new_player", "id": "9", "attributes": {"display_name": "Jalen Brunson"}}]}

    class S:
        def get(self, url, **k):
            return Resp(ud if "underdog" in url else pp)

    info = {}
    u = cp.underdog_lines(S(), NOW, None, info)
    assert u == [{"app": "Underdog", "player": "Jalen Brunson", "stat": "Points", "line": 25.5,
                  "over": round(1 + 100 / 120, 4), "under": round(cp.UNDERDOG_PICK * 1.1, 4)}]
    p = cp.prizepicks_lines(S(), NOW, None, {})
    assert len(p) == 1 and p[0]["over"] == round(cp.PRIZEPICKS_PICK, 4)


def test_bet_queue_takes_coles_plays_when_earned(tmp_path):
    from research_lab import bet_queue as bq
    root, lab = tmp_path / "root", tmp_path / "lab"
    lab.mkdir(parents=True)
    cp.update([board()], NOW, lab, apps={"PrizePicks": app("PrizePicks", [
        {"player": "Jalen Brunson", "stat": "Points", "line": 25.5, "over": 1.732, "under": 1.732}])})
    lanes = [{"name": "Cole's Plays", "n": 100, "roi": 0.08, "verdict": "Too early", "reference": False,
              "clv": 0.01, "clv_n": 50}]
    assert bq.update(NOW, lab, root=root, lanes=lanes)["queued"] == 1
    q = bq.load(lab)[0]
    assert q["lane"] == "Cole's Plays" and q["pick"] == "Jalen Brunson Over 25.5 Points (PrizePicks)"
    rows = cp.load(lab)
    rows[0].update(status="won", clv="0.03")
    cp._save(rows, lab)
    assert bq.update(NOW + timedelta(hours=9), lab, root=root, lanes=lanes)["settled"] == 1
    assert bq.load(lab)[0]["status"] == "won"
