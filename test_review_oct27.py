"""Oct 27, 2026 review changes: flat-risk sizing, paused Today's Plays, quick bets, scorecard."""
import json

import quick_bets as qb
import scorecard as sc
from unit_sizing import contracts_for_flat_risk


def test_flat_risk_never_risks_more_than_a_unit():
    assert contracts_for_flat_risk(0.74, 5.0) == 6          # $4.44, not ~$14 to win $5
    assert contracts_for_flat_risk(0.40, 5.0) == 12
    assert contracts_for_flat_risk(0.80, 5.0) == 0          # over 75c still skipped
    for p in (0.05, 0.33, 0.5, 0.61, 0.75):
        assert contracts_for_flat_risk(p, 5.0) * p <= 5.0 + 1e-9


def test_live_plays_size_uses_flat_risk(monkeypatch):
    from live_plays import core
    monkeypatch.setenv("LIVE_PLAYS_PER_BET", "5")
    n = core.size(0.74, 50.0)
    assert n * 0.74 <= 5.0


def test_paused_places_nothing(monkeypatch, tmp_path):
    from live_plays import core, run
    monkeypatch.setenv("LIVE_PLAYS_PAUSED", "1")
    monkeypatch.setattr(core, "DATA", tmp_path)
    called = []
    monkeypatch.setattr(core, "new_picks", lambda *a, **k: called.append(1) or ([], {}))
    monkeypatch.setattr(core, "place", lambda *a, **k: called.append(2) or {})
    out = run.main()
    assert out["mode"] == "paused" and called == []
    assert json.loads((tmp_path / "status.json").read_text())["mode"] == "paused"


def test_quick_bet_grading():
    b = qb.new_bet("Hunter", "Luka o8.5 ast", "Betr", "+120", 10)
    assert b["decimal"] == 2.2 and b["status"] == "open"
    assert qb.pl_for(b, "won") == 12.0 and qb.pl_for(b, "lost") == -10.0 and qb.pl_for(b, "push") == 0.0
    rows = qb.parse(qb.render([{**b, "status": "won", "pl": 12.0}, {**qb.new_bet("Me", "X", "Rebet", "-110", 11),
                                                                     "status": "lost", "pl": -11.0}]))
    s = qb.summary(rows, "Hunter")
    assert (s["won"], s["lost"], s["pl"]) == (1, 0, 12.0)
    assert qb.american_to_decimal("-110") == 1.9091 and qb.american_to_decimal("50") is None


def test_scorecard_bar():
    win = [{"pl": "1", "cost": "10", "clv": "0.01"}] * 150
    assert sc.grade("x", win, clv="clv")["verdict"] == "Clears the bar"
    lose = [{"pl": "-1", "cost": "10", "clv": "0.01"}] * 150
    assert sc.grade("x", lose, clv="clv")["verdict"].startswith("Proven loser")
    few = [{"pl": "1", "cost": "10"}] * 20
    assert sc.grade("x", few)["verdict"] == "Too early — 130 more bets"
    open_rows = [{"pl": "", "status": "open"}]
    assert sc.grade("x", open_rows)["n"] == 0


def test_scorecard_runs_on_repo_ledgers():
    names = [l["name"] for l in sc.lanes()]
    assert "Live Sharp" in names and "Paper Sharp (benchmark)" in names
