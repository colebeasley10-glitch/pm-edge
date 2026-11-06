"""Nov 6, 2026: "Lock this pick" -- a pick Cole already entered stays in its spot while the card refreshes."""
from datetime import datetime, timezone

from ncaa_pool import weekly as wk
from test_ncaa_weekly import _feed, _slate

UTC = timezone.utc
NOW = datetime(2026, 11, 6, 15, tzinfo=UTC)


def _card(games, pinned=None):
    return wk.build("clay", _slate(games), games, now=NOW, aliases={}, pinned=pinned)["rows"]


def test_held_pick_keeps_rank_key_and_line_when_market_moves():
    feed = _feed()
    games = feed[(2026, 10)]
    first = _card(games)
    tcu = dict(first[4])                       # a non-key pick at #5
    key = dict(first[1])                       # a key at #2
    # market swings hard against both picks: the model alone would drop/move them
    for (a, h), g in games.items():
        if (a, h) in {(tcu["away"], tcu["home"]), (key["away"], key["home"])}:
            g.spread = g.first_spread + (8 if tcu["side"] == "home" else -8)
    held = wk.merge_held([], [tcu, key])
    rows = _card(games, held)
    by = {r["pick"]: r for r in rows}
    assert by[tcu["pick"]]["rank"] == 5 and by[tcu["pick"]]["key"] == "N"
    assert by[key["pick"]]["rank"] == 2 and by[key["pick"]]["key"] == "Y"
    assert by[tcu["pick"]]["line"] == tcu["line"]
    assert len(rows) == 12 and [r["rank"] for r in rows] == list(range(1, 13))
    assert sum(r["key"] == "Y" for r in rows) == 3
    assert [r["rank"] for r in rows if r["key"] == "Y"] == [1, 2, 3]


def test_snapshot_main_card_respects_held_but_shadows_do_not(tmp_path, monkeypatch):
    for name in ("SLATES", "CARDS", "HELD", "VARIANTS", "MISSED", "MINE"):
        monkeypatch.setattr(wk, name, tmp_path / name.lower())
    monkeypatch.setattr(wk, "_aliases", lambda: {})
    feed = _feed()
    games = feed[(2026, 10)]
    wk.SLATES.mkdir()
    (wk.SLATES / "clay_2026_w10.csv").write_text(wk.render_slate(_slate(games)))
    wk.snapshot(now=NOW, feed=feed)
    card = wk._csv(wk.card_path("clay", 2026, 10))
    pick = card[6]
    wk.HELD.mkdir()
    (wk.HELD / "clay_2026_w10.csv").write_text(wk._render_held([{**pick, "held": "Y"}]))
    g = games[(pick["away"], pick["home"])]
    g.spread = g.first_spread + (10 if pick["side"] == "home" else -10)
    wk.snapshot(now=NOW, feed=feed)
    card = wk._csv(wk.card_path("clay", 2026, 10))
    mine = next(r for r in card if r["pick"] == pick["pick"])
    assert mine["rank"] == "7" and mine["held"] == "Y" and mine["frozen"] == "Y"
    shadow = wk._csv(wk.variant_path("clay", 2026, 10, "pinn_sat"))
    assert all(not r.get("held") for r in shadow)


def test_hold_and_release_write_through_github(monkeypatch):
    writes = []
    monkeypatch.setattr(wk, "_github_write", lambda rel, text, msg, session=None: writes.append((rel, text, msg)))
    row = {"pick": "TCU +3.5", "away": "TCU", "home": "Baylor", "rank": 4, "key": "N", "line": 3.5}
    held = wk.hold_pick("clay", 2026, 11, row, held=[], now=NOW)
    assert held[0]["held"] == "Y" and "TCU +3.5" in writes[-1][2] and "TCU" in writes[-1][1]
    held = wk.release_pick("clay", 2026, 11, "TCU +3.5", held)
    assert held == [] and writes[-1][1] is None
