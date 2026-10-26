"""Sharp Plays: steam detection on Pinnacle lines, alert text, CLV, settlement."""
from datetime import datetime, timedelta, timezone

from research_lab import sharp_plays as sp
from research_lab.pinnacle import PinEvent

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
START = T0 + timedelta(hours=10)


def ev(total_pts, over, under, hdp=-3.5, home=1.91, away=1.91, eid="1001", league="NCAA Division I"):
    full = {"totals": {str(total_pts): {"points": total_pts, "over": over, "under": under}},
            "spreads": {str(hdp): {"hdp": hdp, "home": home, "away": away}},
            "money_line": {"home": 1.6, "away": 2.4}}
    return PinEvent(eid, "Texas State", "Utah State", START, league, full, T0)


def rounds(tmp_path, seq, push=None, scores=None):
    out = None
    for when, events in seq:
        out = sp.update({5: events}, when, tmp_path, push=push, scores=scores)
    return out, sp.load(tmp_path)


def test_total_steam_becomes_under_play_with_target_price(tmp_path):
    pushes = []
    _, rows = rounds(tmp_path, [(T0, [ev(63.5, 1.91, 1.91)]),
                                (T0 + timedelta(hours=3), [ev(61.0, 1.95, 1.87)])],
                     push=lambda t, m: pushes.append((t, m)) or True)
    assert len(rows) == 1
    r = rows[0]
    assert r["pick"] == "Under 61" and r["market"] == "total" and r["side"] == "under"
    assert r["target_odds"].startswith("-") and float(r["target_decimal"]) > 1 / float(r["fair_prob"])
    assert "Under 61" in pushes[0][0] and "63.5 → 61" in pushes[0][1] and "or better" in pushes[0][1]


def test_small_move_and_too_soon_do_nothing(tmp_path):
    _, rows = rounds(tmp_path, [(T0, [ev(63.5, 1.91, 1.91)]),
                                (T0 + timedelta(hours=3), [ev(63.0, 1.91, 1.91)])])
    assert rows == []
    _, rows = rounds(tmp_path / "b", [(T0, [ev(63.5, 1.91, 1.91)]),
                                      (T0 + timedelta(minutes=45), [ev(60.0, 1.91, 1.91)])])
    assert rows == []                          # needs 2h of history first


def test_spread_steam_on_home_side(tmp_path):
    _, rows = rounds(tmp_path, [(T0, [ev(63.5, 1.91, 1.91, hdp=-3.5)]),
                                (T0 + timedelta(hours=3), [ev(63.5, 1.91, 1.91, hdp=-5.5)])])
    assert rows and rows[0]["pick"] == "Texas State -5.5"


def test_one_play_per_game_and_clv_tracked(tmp_path):
    seq = [(T0, [ev(63.5, 1.91, 1.91)]), (T0 + timedelta(hours=3), [ev(61.0, 1.91, 1.91, hdp=-6.5)]),
           (T0 + timedelta(hours=6), [ev(61.0, 1.91, 1.91, hdp=-6.5, home=1.80, away=2.05)])]
    _, rows = rounds(tmp_path, seq)
    # biggest move wins the game's one play (spread -3.5 -> -6.5); home kept getting bet: positive CLV
    assert len(rows) == 1 and rows[0]["pick"] == "Texas State -6.5" and float(rows[0]["clv"]) > 0


def test_settles_from_scores(tmp_path):
    rounds(tmp_path, [(T0, [ev(63.5, 1.91, 1.91)]), (T0 + timedelta(hours=3), [ev(61.0, 1.91, 1.91)])])
    _, rows = rounds(tmp_path, [(START + timedelta(hours=4), [])], scores=lambda row: "won")
    assert rows[0]["status"] == "won" and float(rows[0]["pl"]) > 0
    assert sp.espn_row(rows[0])["question"].endswith("O/U 61")
