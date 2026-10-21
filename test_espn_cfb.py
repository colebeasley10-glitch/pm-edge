"""ESPN scoreboard parsing + end-to-end grader run on a fake ESPN feed."""
import json
from datetime import datetime, timedelta, timezone

from espn_cfb import ESPNCFB, parse_event
from ncaa_grader import core
from score_ratings import ScoreRatings


def ev(gid, week, date, home, away, hs="0", as_="0", completed=False, odds=True, ml=("-300", "+240")):
    o = {"provider": {"name": "DraftKings"}, "details": f"{home} -7.5", "overUnder": 52.5, "spread": -7.5,
         "pointSpread": {"home": {"close": {"line": "-7.5", "odds": "-108"}, "open": {"line": "-6.5", "odds": "-110"}},
                         "away": {"close": {"line": "+7.5", "odds": "-112"}, "open": {"line": "+6.5", "odds": "-110"}}},
         "total": {"over": {"close": {"line": "o52.5", "odds": "-115"}, "open": {"line": "o54.5", "odds": "-110"}},
                   "under": {"close": {"line": "u52.5", "odds": "-105"}, "open": {"line": "u54.5", "odds": "-110"}}},
         "moneyline": {"home": {"close": {"odds": ml[0]}, "open": {"odds": "OFF"}},
                       "away": {"close": {"odds": ml[1]}, "open": {"odds": "OFF"}}}}
    return {"id": str(gid), "date": date, "season": {"year": 2026, "type": 2}, "week": {"number": week},
            "competitions": [{"neutralSite": False, "status": {"type": {"completed": completed}},
                              "competitors": [
                                  {"homeAway": "home", "score": hs, "team": {"location": home, "displayName": home + " X"}},
                                  {"homeAway": "away", "score": as_, "team": {"location": away, "displayName": away + " Y"}}],
                              "odds": [o] if odds else []}]}


def test_parse_event_matches_confirmed_espn_shape():
    g, gl = parse_event(ev(401858439, 2, "2026-09-12T16:00Z", "Indiana", "Howard", ml=("OFF", "OFF")))
    assert g["homeTeam"] == "Indiana" and g["week"] == 2 and g["homePoints"] is None
    line = gl["lines"][0]
    assert line["spread"] == -7.5 and line["spreadOpen"] == -6.5
    assert line["overUnder"] == 52.5 and line["overUnderOpen"] == 54.5
    assert line["homeMoneyline"] is None and line["homeSpreadOdds"] == -108 and line["overOdds"] == -115


def test_spread_pick_uses_real_juice():
    g, gl = parse_event(ev(1, 9, "2026-10-25T19:30Z", "Alabama", "Vanderbilt"))
    picks = core.make_picks(game=g, market=core.consensus(gl), home_margin=14, total=60, home_win_prob=0.8,
                            model="m", source="live", logged_at="x")
    sp = next(p for p in picks if p["market"] == "SPREAD")
    tot = next(p for p in picks if p["market"] == "TOTAL")
    assert sp["pick_team"] == "Alabama" and sp["odds"] == -108
    assert tot["direction"] == "OVER" and tot["odds"] == -115


def test_scores_model_has_no_lookahead_in_backtest():
    # A beats B in weeks 1-3; week 4 result is a huge B upset. The week-4 projection must not know it.
    games = []
    for w, (hs, as_) in enumerate([(35, 10), (31, 14), (38, 7)], 1):
        games.append({"id": str(w), "week": w, "completed": True, "homeTeam": "A", "awayTeam": "B",
                      "homePoints": hs, "awayPoints": as_, "neutralSite": False})
    w4 = {"id": "4", "week": 4, "completed": True, "homeTeam": "A", "awayTeam": "B", "homePoints": 0,
          "awayPoints": 60, "neutralSite": False, "startDate": "2026-09-27T00:00Z", "season": 2026}
    lines = {"4": {"id": "4", "lines": [{"spread": -10, "spreadOpen": -10, "overUnder": 45, "overUnderOpen": 45}]}}
    out = core.run_backtest(season=2026, games=games + [w4], lines=lines,
                            model_builders={"scores_v1": lambda w, prior: ScoreRatings(prior).project
                                            if prior else None}, rows=[])
    sp = next(r for r in out["rows"] if r["market"] == "SPREAD" and str(r["game_id"]) == "4")
    assert sp["pick_team"] == "A" and sp["result"] == "L"   # model liked A from prior weeks only


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, weeks):
        self.weeks, self.calls = weeks, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if "week" not in params:
            return FakeResp({"season": {"year": 2026, "type": 2}, "week": {"number": 9}, "events": []})
        return FakeResp({"events": self.weeks.get(int(params["week"]), [])})


def test_end_to_end_runner_with_fake_espn(tmp_path, monkeypatch, capsys):
    teams = ["Alabama", "Vanderbilt", "Georgia", "Auburn"]
    weeks, gid = {}, 0
    pairs = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2), (0, 1), (2, 3)]
    for w, (h, a) in enumerate(pairs, 1):
        gid += 1
        weeks.setdefault(w, []).append(ev(gid, w, f"2026-09-{w + 1:02d}T19:00Z", teams[h], teams[a],
                                          str(20 + 3 * (3 - h)), str(14 + (3 - a)), completed=True))
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ")
    weeks[9] = [ev(99, 9, soon, "Georgia", "Vanderbilt")]
    monkeypatch.setattr(core, "LEDGER_PATH", tmp_path / "picks.csv")
    monkeypatch.setattr(core, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(core, "WORKBOOK_PATH", tmp_path / "none.csv")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    import espn_cfb
    from ncaa_grader import run
    sess = FakeSession(weeks)
    monkeypatch.setattr(run, "ESPNCFB", lambda: espn_cfb.ESPNCFB(path=tmp_path / "c.json", session=sess))
    monkeypatch.setattr("sys.argv", ["run"])
    run.main()
    rows = core.load_ledger(tmp_path / "picks.csv")
    assert {r["market"] for r in rows if r["game_id"] == "99"} == {"SPREAD", "TOTAL", "ML"}
    status = json.loads((tmp_path / "status.json").read_text())["live"]
    assert status["models"] == ["scores_v1"] and "cfbd_unavailable" in status
    assert "backtest_waiting" in status          # no CFBD yet -> backtest waits, nothing breaks
    # CFBD history (what the backtest uses), built from the same games with opening lines
    hist_games = [espn_cfb.parse_event(e)[0] for w in range(1, 9) for e in weeks[w]]
    hist_lines = {g["id"]: {"id": g["id"], "lines": [{"spread": -3.5, "spreadOpen": -3.0, "overUnder": 44.5,
                                                      "overUnderOpen": 45.5}]} for g in hist_games}
    monkeypatch.setattr(run, "_cfbd_history", lambda season, status: (hist_games, hist_lines))
    monkeypatch.setattr("sys.argv", ["run", "--backtest"])
    run.main()
    rows = core.load_ledger(tmp_path / "picks.csv")
    assert any(r["source"] == "backtest" and r["result"] for r in rows)
    assert any(r["source"] == "live" for r in rows)       # backtest didn't wipe live picks
    assert "Graded picks" in capsys.readouterr().out


def test_auto_backtest_runs_once(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LEDGER_PATH", tmp_path / "picks.csv")
    monkeypatch.setattr(core, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(core, "WORKBOOK_PATH", tmp_path / "none.csv")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    import espn_cfb
    from ncaa_grader import run
    sess = FakeSession({})
    monkeypatch.setattr(run, "ESPNCFB", lambda: espn_cfb.ESPNCFB(path=tmp_path / "c.json", session=sess))
    calls = []
    monkeypatch.setattr(run, "_cfbd_history", lambda season, status: calls.append(1) or ([], {}))
    monkeypatch.setattr("sys.argv", ["run"])
    run.main()
    run.main()
    assert calls == [1]


def test_blowout_cap_keeps_the_total():
    games = [{"completed": True, "homeTeam": "A", "awayTeam": "B", "homePoints": 63, "awayPoints": 0,
              "neutralSite": True} for _ in range(4)]
    r = ScoreRatings(games, ridge=0, min_games=1)
    p = r.project("A", "B", neutral=True)
    assert abs(p.total - 63) < 0.5 and abs(p.home_margin - 28) < 0.5
