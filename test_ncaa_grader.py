"""NCAA grader: freezing, tiers, grading, CLV, leak guard, report math."""
from datetime import datetime, timedelta, timezone

import pytest

from ncaa_grader import core

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
CORE_ROWS = [  # two weekly snapshots per team
    {"team": "Alabama", "throughWeek": 7, "offense": 20, "defense": -5, "offensePlays": 400},
    {"team": "Alabama", "throughWeek": 8, "offense": 25, "defense": -8, "offensePlays": 460},
    {"team": "Vanderbilt", "throughWeek": 7, "offense": -5, "defense": 10, "offensePlays": 400},
    {"team": "Vanderbilt", "throughWeek": 8, "offense": -8, "defense": 12, "offensePlays": 460},
]


def line_row(spread, total, hml=None, aml=None, s_open=None, t_open=None):
    return {"provider": "x", "spread": spread, "overUnder": total, "homeMoneyline": hml, "awayMoneyline": aml,
            "spreadOpen": s_open, "overUnderOpen": t_open}


from ncaa_predictive import project, ratings_map


def core_model(through_week=None):
    rm = ratings_map(CORE_ROWS, through_week=through_week)
    return lambda h, a, n: project(h, a, rm, neutral=n)


MODELS = [("core_v1", core_model())]


def flat(lines):  # {week: [gl]} -> {id: gl}
    return {str(gl["id"]): gl for gls in lines.values() for gl in gls}


def live(games, lines, now, rows, workbook=None, models=MODELS):
    return core.run_live(games=games, lines=flat(lines), models=models, now=now, rows=rows,
                         workbook=workbook or {})


def backtest(games, lines, rows, core_rows=CORE_ROWS):
    def build(w, prior):
        rm = ratings_map(core_rows, through_week=w)
        return (lambda h, a, n: project(h, a, rm, neutral=n)) if rm else None
    return core.run_backtest(season=2026, games=games, lines=flat(lines), model_builders={"core_v1": build},
                             rows=rows)


def game(gid, week, ko, completed=False, hp=None, ap=None):
    return {"id": gid, "season": 2026, "week": week, "startDate": ko.isoformat(), "completed": completed,
            "homeTeam": "Alabama", "awayTeam": "Vanderbilt", "neutralSite": False,
            "homeClassification": "fbs", "awayClassification": "fbs", "homePoints": hp, "awayPoints": ap}


def test_live_logs_every_market_and_freezes_the_line():
    ko = NOW + timedelta(days=3)
    lines = {9: [{"id": 1, "lines": [line_row(-20.5, 50.5, -2000, 1100)]}]}
    out = live([game(1, 9, ko)], lines, NOW, [])
    picks = {r["market"]: r for r in out["rows"]}
    assert set(picks) == {"SPREAD", "TOTAL", "ML"} and out["logged"] == 3
    assert picks["SPREAD"]["line"] in (-20.5, 20.5)
    # the line moves; a second run must NOT overwrite the frozen pick
    lines[9][0]["lines"] = [line_row(-27.5, 44.5, -5000, 1800)]
    out2 = live([game(1, 9, ko)], lines, NOW + timedelta(hours=6), out["rows"])
    again = {r["market"]: r for r in out2["rows"]}
    assert out2["logged"] == 0
    assert again["SPREAD"]["line"] == picks["SPREAD"]["line"] and again["TOTAL"]["line"] == 50.5


def test_settles_after_final_with_clv():
    ko = NOW + timedelta(days=1)
    lines = {9: [{"id": 1, "lines": [line_row(-20.5, 50.5, -2000, 1100)]}]}
    rows = live([game(1, 9, ko)], lines, NOW, [])["rows"]
    lines[9][0]["lines"] = [line_row(-17.5, 52.5, -1200, 800)]   # closing numbers
    later = ko + timedelta(hours=5)
    out = live([game(1, 9, ko, True, 35, 10)], lines, later, rows)
    by = {r["market"]: r for r in out["rows"]}
    assert out["settled"] == 3
    sp = by["SPREAD"]
    team_close = -17.5 if sp["pick_team"] == "Alabama" else 17.5
    assert sp["close_line"] == team_close
    assert sp["clv"] == round(float(sp["line"]) - team_close, 2)
    assert by["TOTAL"]["result"] in ("W", "L")   # 45 vs 50.5
    assert by["TOTAL"]["result"] == ("W" if by["TOTAL"]["direction"] == "UNDER" else "L")


def test_not_settled_until_closing_line_window():
    ko = NOW + timedelta(days=1)
    lines = {9: [{"id": 1, "lines": [line_row(-20.5, 50.5)]}]}
    rows = live([game(1, 9, ko)], lines, NOW, [])["rows"]
    out = live([game(1, 9, ko, True, 35, 10)], lines, ko + timedelta(hours=2), rows)
    assert out["settled"] == 0


def test_grading_spread_total_ml_and_push():
    base = {"home": "A", "away": "B", "odds": -110}
    assert core.grade({**base, "market": "SPREAD", "pick_team": "B", "line": 7}, 30, 24) == ("W", 0.9091)
    assert core.grade({**base, "market": "SPREAD", "pick_team": "A", "line": -6}, 30, 24) == ("P", 0.0)
    assert core.grade({**base, "market": "TOTAL", "direction": "UNDER", "line": 55}, 30, 24) == ("W", 0.9091)
    assert core.grade({**base, "market": "ML", "pick_team": "B", "line": "", "odds": 250}, 20, 24) == ("W", 2.5)
    assert core.grade({**base, "market": "ML", "pick_team": "A", "line": "", "odds": -200}, 20, 24) == ("L", -1.0)


def test_tiers():
    assert core.tier_spread(2.9) == "NO_EDGE" and core.tier_spread(3) == "LEAN" and core.tier_spread(6) == "STRONG"
    assert core.tier_total(4) == "LEAN" and core.tier_total(7.5) == "STRONG"
    assert core.tier_ml(0.05, 400) == "LONG_SHOT" and core.tier_ml(0.09, 120) == "STRONG"
    assert core.tier_ml(0.01, 400) == "NO_EDGE"


def test_backtest_uses_only_prior_ratings_and_opening_line():
    ko = NOW - timedelta(days=4)
    g = game(1, 8, ko, True, 31, 17)
    lines = {8: [{"id": 1, "lines": [line_row(-17.5, 51.5, -900, 600, s_open=-14.5, t_open=54.5)]}]}
    out = backtest([g], lines, [])
    sp = next(r for r in out["rows"] if r["market"] == "SPREAD")
    assert abs(float(sp["line"])) == 14.5          # opening line, not closing
    assert sp["result"] and out["graded"] == 3
    # week-8 games must use the through-week-7 snapshot (Alabama off 20), not week 8 (off 25)
    p7 = project("Alabama", "Vanderbilt", ratings_map(CORE_ROWS, through_week=8))
    assert sp["proj"].endswith(f"{p7.total / 2 + p7.home_margin / 2:.1f}")


def test_backtest_refuses_without_weekly_snapshots():
    flatc = [{k: v for k, v in r.items() if k != "throughWeek"} for r in CORE_ROWS]
    g = game(1, 8, NOW - timedelta(days=4), True, 31, 17)
    out = backtest([g], {8: [{'id': 1, 'lines': [line_row(-17.5, 51.5, s_open=-14.5, t_open=54.5)]}]}, [], core_rows=flatc)
    assert out["skipped_weeks"] == {"core_v1": [8]} and out["graded"] == 0


def test_backtest_rerun_replaces_old_backtest_rows_but_keeps_live():
    live_row = {"pick_id": "live:x", "source": "live", "season": "2026"}
    old = {"pick_id": "backtest:old", "source": "backtest", "season": "2026"}
    out = backtest([], {}, [live_row, old])
    ids = [r["pick_id"] for r in out["rows"]]
    assert "live:x" in ids and "backtest:old" not in ids


def test_report_roi_is_units_per_pick_like_the_screenshot():
    rows = ([{"source": "live", "model": "core_v1", "market": "ML", "tier": "LONG_SHOT",
              "result": "W", "units": 10.0}] +
            [{"source": "live", "model": "core_v1", "market": "ML", "tier": "LONG_SHOT",
              "result": "L", "units": -1.0}] * 6)
    s = next(x for x in core.summarize(rows, source="live") if x["group"].startswith("Long"))
    assert (s["w"], s["l"], s["units"]) == (1, 6, 4.0) and s["roi"] == pytest.approx(4 / 7)


def test_workbook_csv_matches_games_and_logs_separately(tmp_path):
    p = tmp_path / "my_model.csv"
    p.write_text("home,away,home_margin,total\nAlabama,Vanderbilt,24,49\n")
    wb = core.load_workbook_model(p)
    ko = NOW + timedelta(days=2)
    lines = {9: [{"id": 1, "lines": [line_row(-20.5, 50.5)]}]}
    out = live([game(1, 9, ko)], lines, NOW, [], workbook=wb)
    wbp = [r for r in out["rows"] if r["model"] == "workbook"]
    assert {r["market"] for r in wbp} == {"SPREAD", "TOTAL"}
    sp = next(r for r in wbp if r["market"] == "SPREAD")
    assert sp["pick_team"] == "Alabama" and sp["edge_pts"] == 3.5 and sp["tier"] == "LEAN"


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "picks.csv"
    core.save_ledger([{"pick_id": "a", "source": "live", "market": "ML", "units": 1.5}], p)
    assert core.load_ledger(p)[0]["units"] == "1.5"
