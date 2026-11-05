"""Settle-only round: grades finished plays between full rounds, writes nothing when idle."""
from datetime import timedelta

from research_lab import prop_board as pb, settle_only
from test_prop_board import T0, payload


def _play(tmp_path):
    pb.update(payload(56.5, 1.91, 1.91), T0, tmp_path)
    pb.update(payload(51.5, 1.91, 1.91), T0 + timedelta(hours=3), tmp_path)
    return pb.load(tmp_path)[0]


def test_settles_prop_once_final(tmp_path):
    r = _play(tmp_path)
    start = pb.parse_dt(r["game_start"])
    kw = dict(scores=lambda row: None, kalshi=None)
    # too early to be over: not even checked
    out = settle_only.run(start + timedelta(minutes=60), tmp_path, box=lambda p: 38.0, **kw)
    assert out["prop_board"] == 0 and pb.load(tmp_path)[0]["status"] == "open"
    # ESPN not final yet (box returns None): stays open
    out = settle_only.run(start + timedelta(minutes=100), tmp_path, box=lambda p: None, **kw)
    assert out["prop_board"] == 0 and pb.load(tmp_path)[0]["status"] == "open"
    # final: graded 100 minutes after start instead of waiting 4 hours
    out = settle_only.run(start + timedelta(minutes=100), tmp_path, box=lambda p: 38.0, **kw)
    assert out["prop_board"] == 1 and pb.load(tmp_path)[0]["status"] == "won"


def test_idle_run_writes_nothing(tmp_path):
    _play(tmp_path)
    f = tmp_path / "PROP_BOARD" / "plays.csv"
    before = f.read_text() if f.exists() else None
    settle_only.run(T0 + timedelta(hours=4), tmp_path, box=lambda p: None, scores=lambda row: None)
    assert (f.read_text() if f.exists() else None) == before
