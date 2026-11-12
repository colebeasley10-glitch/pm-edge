"""v2 lanes (Nov 12, 2026): each fix only counts bets logged after START, and plays only reach the
Bet Queue / Cole's Plays once a v2 lane clears the bar."""
import csv
from datetime import datetime, timedelta, timezone

import lane_v2

T = lane_v2.START + timedelta(hours=2)


def iso(d):
    return d.isoformat().replace("+00:00", "Z")


def test_prop_board_rule_needs_near_game_and_limit():
    near = {"alert_time": iso(T), "game_start": iso(T + timedelta(hours=3)), "limit": "750"}
    assert lane_v2.prop_board_ok(near)
    assert not lane_v2.prop_board_ok({**near, "game_start": iso(T + timedelta(hours=13))})   # too early
    assert not lane_v2.prop_board_ok({**near, "limit": "250"})                               # shaky price
    assert not lane_v2.prop_board_ok({**near, "game_start": iso(T - timedelta(hours=1))})    # after start


def test_sharp_rule_drops_spreads_and_long_shots():
    base = {"strategy": "sharp", "category": "Sports", "ticker": "KXNBAGAME-X", "title": "Lakers win", "entry_price": "0.55"}
    assert lane_v2.sharp_ok(base)
    assert not lane_v2.sharp_ok({**base, "entry_price": "0.18"})
    assert not lane_v2.sharp_ok({**base, "ticker": "KXNBASPREAD-X", "title": "Lakers win by over 4.5"})
    assert not lane_v2.sharp_ok({**base, "category": "Other"})


def test_model_rule_uses_calibrated_ev_band(monkeypatch):
    import fair_calibration
    row = {"source": "live", "pick_id": "x", "odds": "-110"}
    monkeypatch.setattr(fair_calibration, "calibrated", lambda src, r: 0.55)      # EV = 0.55*1.909-1 = +5%
    assert lane_v2.model_ok("ncaa_grader", row)
    monkeypatch.setattr(fair_calibration, "calibrated", lambda src, r: 0.53)      # +1.2%: not enough
    assert not lane_v2.model_ok("ncaa_grader", row)
    monkeypatch.setattr(fair_calibration, "calibrated", lambda src, r: 0.62)      # +18%: model missing something
    assert not lane_v2.model_ok("ncaa_grader", row)
    monkeypatch.setattr(fair_calibration, "calibrated", lambda src, r: 0.55)
    assert not lane_v2.model_ok("ncaa_grader", {**row, "odds": "-260"})            # outside -200..+150
    assert not lane_v2.model_ok("ncaa_grader", {**row, "source": "backtest"})
    assert not lane_v2.model_ok("ncaab_model", {"pick_id": "bt:1", "odds": "-110"})
    assert lane_v2.model_ok("ncaab_model", {"pick_id": "live:1", "odds": "-110"})


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_only_bets_after_start_count(tmp_path):
    play = {"play_id": "p", "alert_time": iso(T), "game_start": iso(T + timedelta(hours=2)), "limit": "800",
            "status": "won", "pl": "9.1", "clv": "0.01"}
    old = {**play, "play_id": "old", "alert_time": iso(lane_v2.START - timedelta(days=1)),
           "game_start": iso(lane_v2.START - timedelta(days=1) + timedelta(hours=2))}
    _write(tmp_path / "research_lab/data/PROP_BOARD/plays.csv", [play, old])
    rows = lane_v2.rows_for("Prop Board steam v2", tmp_path)
    assert [r["play_id"] for r in rows] == ["p"]


def test_v2_lanes_on_scorecard_and_feed_queue(tmp_path):
    import scorecard
    names = {l["name"] for l in scorecard.lanes()}
    for name, *_ in lane_v2.LANES:
        assert name in names and " — " not in name          # top-level lanes, not slices
    play = {"play_id": "p", "alert_time": iso(T), "game_start": iso(T + timedelta(hours=2)), "limit": "800",
            "status": "open", "pick": "Joe Over 20.5 Points", "game": "A @ B", "league": "NBA",
            "target_decimal": "1.95", "fair_prob": "0.55"}
    _write(tmp_path / "PROP_BOARD/plays.csv", [play])
    cands = lane_v2.candidates(tmp_path, tmp_path)
    assert [(c["lane"], c["source"], c["source_id"]) for c in cands] == [("Prop Board steam v2", "prop_board", "p")]
