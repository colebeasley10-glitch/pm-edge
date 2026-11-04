"""NCAA basketball paper lane: ratings recover known strengths, pricing math, tiers, staking,
ledger freeze/close/settle, ESPN parsing. No network."""
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from espn_ncaab import parse_event, parse_possessions, season_year
from ncaab_grader import core, report
from ncaab_model.ratings import Proj, Ratings

CARD = {"version": "test_v1", "params": {},
        "calibration": {"b_s": 0.5, "sd_s": 10.0, "a_t": 0.0, "b_t": 0.5, "sd_t": 15.0},
        "rules": {"SPREAD": {"enabled": True, "min_ev": 0.03}, "TOTAL": {"enabled": False, "min_ev": 0.03},
                  "ML": {"enabled": True, "min_ev": 0.03, "min_odds": -250, "max_odds": 250}},
        "staking": {"start_bankroll": 500.0, "kelly_fraction": 0.25, "max_stake_pct": 0.02,
                    "max_daily_exposure_pct": 0.08, "flat_stake": 5.0, "min_stake": 1.0},
        "flags": {"early_season_games": 4}}


def synthetic(n_teams=40, days=60, seed=1):
    rnd = random.Random(seed)
    O = {f"T{i}": rnd.gauss(0, 6) for i in range(n_teams)}
    D = {t: rnd.gauss(0, 6) for t in O}
    teams = list(O)
    games, start = [], date(2026, 11, 3)
    for d in range(days):
        rnd.shuffle(teams)
        for h, a in zip(teams[::2], teams[1::2]):
            poss = 68 + rnd.gauss(0, 3)
            eh = 104 + 2.5 + O[h] + D[a] + rnd.gauss(0, 8)
            ea = 104 - 2.5 + O[a] + D[h] + rnd.gauss(0, 8)
            games.append({"day": start + timedelta(days=d), "home": h, "away": a, "home_pts": poss * eh / 100,
                          "away_pts": poss * ea / 100, "poss": poss, "neutral": False})
    return games, O, D, start + timedelta(days=days)


def test_ratings_recover_true_strengths_and_home_court():
    games, O, D, end = synthetic()
    r = Ratings(games, as_of=end, prior={}, league=(104, 68), prior_games=1, half_life=1000)
    import numpy as np
    est = np.array([r.O[r.idx[t]] for t in O]); true = np.array(list(O.values()))
    assert np.corrcoef(est, true)[0, 1] > 0.9
    assert 3.0 < r.hca < 7.0                     # simulated home edge is 5 pts/100
    p = r.project("T0", "T1")
    assert isinstance(p, Proj) and 100 < p.total < 180


def test_no_lookahead_and_prior_dominates_day_one():
    games, O, D, end = synthetic()
    prior = {t: (10.0 if t == "T0" else 0.0, 0.0, 0.0) for t in O}
    r = Ratings(games, as_of=games[0]["day"], prior=prior, league=(104, 68))
    assert r.eff_obs == 0 and abs(r.O[r.idx["T0"]] - 10.0) < 0.5     # nothing seen yet -> the prior
    later = Ratings(games, as_of=games[100]["day"], prior=prior, league=(104, 68))
    assert later.eff_obs == 2 * sum(1 for g in games if g["day"] < games[100]["day"])


def test_price_game_math_and_min_odds():
    proj = Proj(home_pts=80.0, away_pts=70.0, poss=68.0)       # model: home by 10, total 150
    mkt = {"spread": -4.0, "total": 140.0, "home_ml": -200, "away_ml": 170,
           "home_spread_odds": -110, "away_spread_odds": -110, "over_odds": -110, "under_odds": -110}
    best = {o["market"]: o for o in core.price_game(proj=proj, market=mkt, card=CARD)}
    sp = best["SPREAD"]
    assert sp["team_side"] == "home" and sp["edge_pts"] == pytest.approx(6.0)
    assert sp["p"] == pytest.approx(core.cdf(0.5 * 6 / 10))   # calibrated shrink b_s = 0.5
    assert sp["ev"] == pytest.approx(sp["p"] * (1 + 100 / 110) - 1)
    assert best["TOTAL"]["direction"] == "OVER"
    mo = core.min_odds(sp["p"], 0.03)
    assert core.decimal(mo) * sp["p"] - 1 == pytest.approx(0.03, abs=0.01)
    assert core.tier_for(sp, CARD) == "BET"
    assert core.tier_for(best["TOTAL"], CARD) in ("WATCH", "PASS")   # TOTAL not enabled on this card


def test_kelly_caps_and_zero_for_no_edge():
    st = CARD["staking"]
    assert core.kelly_stake(0.50, -110, 500, st) == 0.0
    assert core.kelly_stake(0.70, -110, 500, st) == 10.0       # capped at 2% of $500
    assert 0 < core.kelly_stake(0.55, -110, 500, st) < 10.0


def _game(gid, start, done=False, hp=None, ap=None):
    return {"id": gid, "season": 2027, "week": start[:10], "startDate": start, "completed": done, "state": "post" if done else "pre",
            "homeTeam": "Home U", "awayTeam": "Away St", "homeId": "1", "awayId": "2", "homePoints": hp,
            "awayPoints": ap, "neutralSite": False}


def test_run_live_freezes_closes_and_settles():
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    g = _game("9", "2026-11-11T00:00Z")
    lines = {"9": {"lines": [{"spread": -4.0, "overUnder": 140.0, "homeMoneyline": -200, "awayMoneyline": 170,
                              "homeSpreadOdds": -110, "awaySpreadOdds": -110}]}}
    proj = lambda game: (Proj(80.0, 70.0, 68.0), {"home_games": 5, "away_games": 5})
    out = core.run_live(games=[g], lines=lines, project=proj, card=CARD, now=now, rows=[])
    sp = [r for r in out["rows"] if r["market"] == "SPREAD"][0]
    assert sp["tier"] == "BET" and sp["line"] == -4.0 and sp["stake"] == 5.0 and 0 < sp["stake_kelly"] <= 10
    assert sp["logged_at"].startswith("2026-11-10") and sp["model"] == "test_v1"
    # line moves to -6 before tip: frozen pick keeps -4, CLV = +2
    lines["9"]["lines"][0]["spread"] = -6.0
    out2 = core.run_live(games=[g], lines=lines, project=proj, card=CARD, now=now + timedelta(hours=2), rows=out["rows"])
    sp = [r for r in out2["rows"] if r["market"] == "SPREAD"][0]
    assert out2["logged"] == 0 and sp["line"] == -4.0 and sp["clv"] == 2.0
    # final: home by 5 -> covers -4
    fin = _game("9", "2026-11-11T00:00Z", done=True, hp=75, ap=70)
    out3 = core.run_live(games=[fin], lines={}, project=proj, card=CARD, now=now + timedelta(hours=14), rows=out2["rows"])
    sp = [r for r in out3["rows"] if r["market"] == "SPREAD"][0]
    assert sp["result"] == "W" and sp["pnl"] == pytest.approx(sp["stake"] * 100 / 110, abs=0.01)
    b = core.bankroll(out3["rows"], CARD)
    assert b["balance"] == pytest.approx(500 + sum(float(r.get("pnl") or 0) for r in out3["rows"]), abs=0.01)


def test_neutral_only_rule_and_edge_cap():
    card = json.loads(json.dumps(CARD))
    card["rules"]["SPREAD"].update({"neutral_only": True, "max_edge_pts": 8.0})
    o = {"market": "SPREAD", "ev": 0.05, "edge_pts": 5.0, "odds": -110}
    assert core.tier_for(o, card, neutral=False) == "WATCH"
    assert core.tier_for(o, card, neutral=True) == "BET"
    assert core.tier_for({**o, "edge_pts": 9.5}, card, neutral=True) == "WATCH"
    card["calibration"].update({"b_s_neutral": 0.3, "b_s": 0.05})
    proj = Proj(80.0, 70.0, 68.0)
    m = {"spread": -4.0}
    p_home = core.price_game(proj=proj, market=m, card=card)[0]["p"]
    p_neut = core.price_game(proj=proj, market=m, card=card, neutral=True)[0]["p"]
    assert p_neut > p_home > 0.5


def test_suspension_downgrades_bets():
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    lines = {"9": {"lines": [{"spread": -4.0, "homeSpreadOdds": -110, "awaySpreadOdds": -110}]}}
    proj = lambda game: (Proj(80.0, 70.0, 68.0), {"home_games": 5, "away_games": 5})
    out = core.run_live(games=[_game("9", "2026-11-11T00:00Z")], lines=lines, project=proj, card=CARD, now=now,
                        rows=[], suspended="test")
    assert all(r["tier"] != "BET" and float(r["stake"]) == 0 for r in out["rows"])


def test_daily_exposure_cap():
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    games, lines = [], {}
    for i in range(10):
        gid = str(100 + i)
        g = _game(gid, "2026-11-10T23:00Z"); g["homeId"], g["awayId"] = f"h{i}", f"a{i}"
        games.append(g)
        lines[gid] = {"lines": [{"spread": -1.0, "homeSpreadOdds": -110, "awaySpreadOdds": -110}]}
    proj = lambda game: (Proj(85.0, 70.0, 68.0), {"home_games": 9, "away_games": 9})
    out = core.run_live(games=games, lines=lines, project=proj, card=CARD, now=now, rows=[])
    assert sum(float(r["stake"]) for r in out["rows"]) <= 500 * 0.08 + 1e-6


def test_report_and_excel(tmp_path):
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    lines = {"9": {"lines": [{"spread": -4.0, "overUnder": 140.0, "homeSpreadOdds": -110, "awaySpreadOdds": -110}]}}
    proj = lambda game: (Proj(80.0, 70.0, 68.0), {"home_games": 2, "away_games": 2})
    out = core.run_live(games=[_game("9", "2026-11-11T00:00Z")], lines=lines, project=proj, card=CARD, now=now, rows=[])
    md = report.daily(out["rows"], {"at": "x"}, CARD, now=now)
    assert "RECOMMENDATION: Home U -4" in md and "BREAK-EVEN PROBABILITY: 52.38%" in md and "LOW" in md
    pytest.importorskip("openpyxl")
    p = tmp_path / "x.xlsx"
    report.export_excel(out["rows"], {"ratings": []}, CARD, path=p)
    assert p.exists()


def test_parse_possessions_and_event():
    box = {"boxscore": {"teams": [
        {"statistics": [{"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "25-63"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "9-12"},
                        {"name": "offensiveRebounds", "displayValue": "9"}, {"name": "totalTurnovers", "displayValue": "8"}]},
        {"statistics": [{"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "31-56"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "16-20"},
                        {"name": "offensiveRebounds", "displayValue": "8"}, {"name": "totalTurnovers", "displayValue": "11"}]}]}}
    assert parse_possessions(box) == pytest.approx(((63 - 9 + 8 + 0.475 * 12) + (56 - 8 + 11 + 0.475 * 20)) / 2, abs=0.01)
    assert parse_possessions({"boxscore": {"teams": []}}) is None
    ev = {"id": "1", "date": "2026-11-03T00:00Z", "season": {"year": 2027},
          "competitions": [{"neutralSite": True, "status": {"type": {"completed": False, "state": "pre"}},
                            "competitors": [{"homeAway": "home", "team": {"id": "57", "displayName": "Florida Gators"}},
                                            {"homeAway": "away", "team": {"id": "2", "displayName": "Auburn Tigers"}}],
                            "odds": [{"provider": {"name": "ESPN BET"}, "spread": -6.5, "overUnder": 150.5}]}]}
    g, gl = parse_event(ev)
    assert g["homeId"] == "57" and g["neutralSite"] and g["week"] == "2026-11-02"
    assert gl["lines"][0]["spread"] == -6.5
    assert season_year(date(2026, 11, 2)) == 2027


def test_shipped_card_and_priors_are_consistent():
    card = core.load_card()
    assert set(card["rules"]) == {"SPREAD", "TOTAL", "ML"}
    assert card["staking"]["start_bankroll"] == 500
    from ncaab_grader.run import load_priors
    prior, info, league = load_priors(2027)
    assert len(prior) > 350 and 95 < league[0] < 115


def test_fixed_home_court_points():
    games, O, D, end = synthetic()
    r = Ratings(games, as_of=end, prior={}, hca_pts=3.1)
    assert r.hca / 100 * r.tempo == pytest.approx(3.1, abs=0.01)
    p_home = r.project("T1", "T2")
    p_neut = r.project("T1", "T2", neutral=True)
    assert p_home.home_margin - p_neut.home_margin == pytest.approx(3.1, abs=0.01)
    free = Ratings(games, as_of=end, prior={})               # default path unchanged (fit with a prior)
    assert free.hca / 100 * free.tempo != pytest.approx(3.1, abs=0.01)


HT_CARD = {**CARD, "calibration": {**CARD["calibration"], "b_s": 0.05, "b_s_neutral": 0.3, "sd_s_neutral": 11.0},
           "home_test": {"enabled": True, "min_ev": 0.03, "max_edge_pts": 8.0, "flat_stake": 5.0, "start_bankroll": 500.0}}


def test_home_test_lane_logs_home_spreads_only_and_settles_separately():
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    home = _game("9", "2026-11-11T00:00Z")
    neut = {**_game("8", "2026-11-11T00:00Z"), "neutralSite": True}
    ln = {"spread": -4.0, "overUnder": 140.0, "homeSpreadOdds": -110, "awaySpreadOdds": -110}
    lines = {"9": {"lines": [dict(ln)]}, "8": {"lines": [dict(ln)]}}
    proj = lambda game: (Proj(80.0, 70.0, 68.0), {"home_games": 5, "away_games": 5})   # model -10 vs -4: 6-pt gap
    # main lane: home shrink 0.05 -> no BET on the home game
    live = core.run_live(games=[home, neut], lines=lines, project=proj, card=HT_CARD, now=now, rows=[])
    assert not [r for r in live["rows"] if r["game_id"] == "9" and r["tier"] == "BET"]
    ht = core.run_home_test(games=[home, neut], lines=lines, project=proj, card=HT_CARD, now=now, rows=[])
    assert ht["logged"] == 1
    r = ht["rows"][0]
    assert r["game_id"] == "9" and r["tier"] == "TEST" and r["stake"] == 5.0 and r["side"] == "Home U -4"
    assert r["ev"] >= 0.03 and r["pick_id"].startswith("home-test:")
    # frozen + settled on its own bankroll
    again = core.run_home_test(games=[home], lines=lines, project=proj, card=HT_CARD, now=now, rows=ht["rows"])
    assert again["logged"] == 0
    fin = _game("9", "2026-11-11T00:00Z", done=True, hp=75, ap=70)
    done = core.run_home_test(games=[fin], lines={}, project=proj, card=HT_CARD, now=now + timedelta(hours=14),
                              rows=ht["rows"])
    assert done["rows"][0]["result"] == "W"
    assert core.home_test_bankroll(done["rows"], HT_CARD)["balance"] == pytest.approx(504.55, abs=0.01)


def test_home_test_respects_edge_cap_and_disable():
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    g = _game("9", "2026-11-11T00:00Z")
    lines = {"9": {"lines": [{"spread": -4.0, "homeSpreadOdds": -110, "awaySpreadOdds": -110}]}}
    big = lambda game: (Proj(85.0, 70.0, 68.0), {"home_games": 5, "away_games": 5})   # 11-pt gap > 8 cap
    assert core.run_home_test(games=[g], lines=lines, project=big, card=HT_CARD, now=now, rows=[])["logged"] == 0
    off = {**HT_CARD, "home_test": {**HT_CARD["home_test"], "enabled": False}}
    ok = lambda game: (Proj(80.0, 70.0, 68.0), {"home_games": 5, "away_games": 5})
    assert core.run_home_test(games=[g], lines=lines, project=ok, card=off, now=now, rows=[])["logged"] == 0
