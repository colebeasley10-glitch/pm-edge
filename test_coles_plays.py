"""Cole's Plays: pick'em apps vs Pinnacle fair (paper)."""
import csv
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
    info = cp.update([board()], NOW, tmp_path, apps=apps, lanes=[])
    assert info["new_plays"] == 1 and info["apps"]["Broken"]["error"].startswith("RuntimeError")
    # Pinnacle moves toward us; the other side now also "clears" but a prop is only ever played one way
    info = cp.update([board(p_over=0.66)], NOW + timedelta(minutes=15), tmp_path, lanes=[],
                     apps={"PrizePicks": app("PrizePicks", [{"player": "Jalen Brunson", "stat": "Points",
                                                             "line": 25.5, "over": 1.732, "under": 3.0}])})
    rows = cp.load(tmp_path)
    assert info["new_plays"] == 0 and len(rows) == 1
    assert rows[0]["clv"] == "0.04" and rows[0]["pick"] == "Jalen Brunson Over 25.5 Points"
    later = NOW + timedelta(hours=9)
    cp.update([], later, tmp_path, apps={}, box=lambda play: 31.0, lanes=[])
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
                                            {"choice": "lower", "payout_multiplier": "1.1", "decimal_price": "2.39",
                                             "odds": {"fantasy": {"decimal": "2.54"}}}]}]}
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
    cp.update([board()], NOW, lab, root=root, lanes=[], apps={"PrizePicks": app("PrizePicks", [
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


# ---------------------------------------------------------------- best of SharpIQ
def lane(name, roi=0.08, n=100):
    return {"name": name, "n": n, "roi": roi, "verdict": "Too early", "reference": False, "clv": 0.01, "clv_n": 50}


def steam(i, ev_fair=0.55, dec="2.0", status="open"):
    return {"play_id": f"s{i}", "league": "NHL", "game": f"A{i} at B{i}", "pick": f"B{i} ML",
            "game_start": START, "target_decimal": dec, "fair_prob": str(ev_fair), "clv": "0.02", "status": status}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_best_of_takes_only_top_lanes_big_edges_capped_and_settles(tmp_path):
    root, lab = tmp_path / "root", tmp_path / "lab"
    write(lab / "SHARP_PLAYS/plays.csv", [steam(i) for i in range(12)] + [steam(99, ev_fair=0.505)])
    write(lab / "PROP_BOARD/plays.csv", [{"play_id": "pb1", "league": "NBA", "game": "C at D", "pick": "X Over 9.5 Points",
                                          "game_start": START, "target_decimal": "2.0", "fair_prob": "0.6",
                                          "status": "open"}])
    lanes = [lane("Sharp Plays (line steam)", 0.20), lane("A", 0.15), lane("B", 0.12), lane("Prop Board steam", 0.05),
             lane("Cole's Plays", 0.5), lane("Bet Queue", 0.5)]
    info = cp.update([], NOW, lab, root=root, apps={}, lanes=lanes)
    assert info["best_of"]["top_lanes"] == ["Sharp Plays (line steam)", "A", "B"]   # never itself / the queue
    rows = cp.load(lab)
    assert len(rows) == cp.MAX_BEST_PER_DAY and all(r["via"] == "Sharp Plays (line steam)" for r in rows)
    assert "s99" not in {r["source_id"] for r in rows}
    cp.update([], NOW + timedelta(minutes=15), lab, root=root, apps={}, lanes=lanes)
    assert len(cp.load(lab)) == cp.MAX_BEST_PER_DAY                               # daily cap holds
    write(lab / "SHARP_PLAYS/plays.csv", [steam(i, status="won") for i in range(12)])
    info = cp.update([], NOW + timedelta(hours=9), lab, root=root, apps={}, lanes=[])
    done = cp.load(lab)
    assert info["settled"] == cp.MAX_BEST_PER_DAY and all(r["status"] == "won" and float(r["pl"]) == 10.0 for r in done)


def test_bet_queue_ignores_borrowed_plays(tmp_path):
    from research_lab import bet_queue as bq
    root, lab = tmp_path / "root", tmp_path / "lab"
    write(lab / "SHARP_PLAYS/plays.csv", [steam(1)])
    cp.update([], NOW, lab, root=root, apps={}, lanes=[lane("Sharp Plays (line steam)")])
    assert len(cp.load(lab)) == 1
    assert [c["lane"] for c in bq.candidates(root, lab)] == ["Sharp Plays (line steam)"]


def test_low_limit_pinnacle_props_skipped():
    props = [{"app": "PrizePicks", "player": "Jalen Brunson", "stat": "Points", "line": 25.5, "over": 1.8,
              "under": 1.8}]
    thin = {**board(), "limit": 50}
    assert cp.edges(props, [thin], NOW)[0] == []
    assert len(cp.edges(props, [board()], NOW)[0]) == 1


def test_new_plays_push_once_each(tmp_path, monkeypatch):
    """Nov 12, 2026: Cole's Plays is the only lane that buzzes; each new play alerts once."""
    from datetime import datetime, timedelta, timezone
    from research_lab import coles_plays as cp
    now = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
    e = {"key": "nba|g|p|pts|20.5", "side": "over", "player": "Joe Star", "line": 20.5, "stat": "Points",
         "app": "sleeper", "league": "NBA", "game": "A @ B", "pin_line": 20.5, "dec": 1.95, "p": 0.55,
         "ev": 0.0725, "also_at": "", "limit": 500, "starts": (now + timedelta(hours=3)).isoformat()}
    monkeypatch.setattr(cp, "fetch_all", lambda *a, **k: ([], {}))
    monkeypatch.setattr(cp, "edges", lambda *a, **k: ([e], {"matched": 1, "closest": [], "by_app": {}}))
    monkeypatch.setattr(cp, "best_of", lambda *a, **k: {"added": 0})
    monkeypatch.setattr(cp, "grade", lambda *a, **k: 0)
    sent = []
    push = lambda t, m: sent.append((t, m)) or True
    info = cp.update([], now, tmp_path, push=push)
    assert info["pushed"] == 1 and len(sent) == 1
    assert "Cole's Plays" in sent[0][0] and "Joe Star Over 20.5 Points" in sent[0][1]
    assert "Sleeper" in sent[0][1]
    info = cp.update([], now, tmp_path, push=push)       # same play next round: no second buzz
    assert info["pushed"] == 0 and len(sent) == 1


def test_alert_gate_allows_coles_plays_and_pool(monkeypatch):
    import alert_gate
    monkeypatch.delenv("SHARPIQ_ALERTS", raising=False)
    assert alert_gate.allowed() == frozenset({"coles_plays", "ncaa_pool"})


def test_prizepicks_skips_quarter_half_and_discount_lines():
    """Nov 12, 2026: PrizePicks' NBA1Q / NBA1H boards reuse "Points"; a 3.5 first-half line looked like
    a giant edge against Pinnacle's full-game 7.5."""
    from research_lab import coles_plays as cp

    def proj(pid, line, league, **attrs):
        return {"type": "projection", "id": pid,
                "attributes": {"line_score": line, "stat_type": "Points", "odds_type": "standard",
                               "status": "pre_game", "event_type": "player", **attrs},
                "relationships": {"new_player": {"data": {"type": "new_player", "id": "p1"}},
                                  "league": {"data": {"type": "league", "id": league}},
                                  "duration": {"data": {"type": "duration", "id": "d"}}}}

    payload = {"data": [proj("full", 7.5, "7"), proj("half", 3.5, "84"), proj("promo", 6.5, "7", adjusted_odds=True),
                        proj("team", 110.5, "7", event_type="team")],
               "included": [{"type": "new_player", "id": "p1", "attributes": {"display_name": "Daniel Gafford"}},
                            {"type": "league", "id": "7", "attributes": {"name": "NBA"}},
                            {"type": "league", "id": "84", "attributes": {"name": "NBA1H"}},
                            {"type": "duration", "id": "d", "attributes": {"name": "Game"}}]}

    class R:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return payload

    class S:
        def get(self, *a, **k):
            return R()

    from datetime import datetime, timezone
    out = cp.prizepicks_lines(S(), datetime.now(timezone.utc), None, {})
    assert [o["line"] for o in out] == [7.5]


def test_far_off_app_lines_are_not_edges_and_get_voided():
    from research_lab import coles_plays as cp
    assert cp.line_gap_ok(24.5, 25.5) and cp.line_gap_ok(3.5, 4.5)
    assert not cp.line_gap_ok(3.5, 7.5) and not cp.line_gap_ok(12.5, 25.5) and not cp.line_gap_ok(1.5, 4.5)
    rows = [{"play_id": "a", "origin": "pickem", "line": "3.5", "pin_line": "7.5", "status": "open", "pl": ""},
            {"play_id": "b", "origin": "pickem", "line": "2.5", "pin_line": "9.5", "status": "won", "pl": "7.32"},
            {"play_id": "c", "origin": "pickem", "line": "33.5", "pin_line": "33.5", "status": "won", "pl": "7.32"}]
    assert cp.void_mismatched(rows) == 2
    assert [r["status"] for r in rows] == ["void", "void", "won"] and rows[1]["pl"] == ""
