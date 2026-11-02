"""NCAA Pools page logic (ncaa_pool/weekly.py), Nov 1, 2026."""
from datetime import datetime, timezone

from ncaa_pool import weekly as wk

UTC = timezone.utc


def _feed(home_pts=None):
    games = {}
    for i in range(14):
        g = wk.GameInfo(kickoff=datetime(2026, 11, 7, 17 + (i % 5), tzinfo=UTC), spread=-3.0 - i, total=50.0,
                        first_spread=-3.0 - i + (1.5 if i % 2 else -1.0))
        if home_pts is not None:
            g.home_pts, g.away_pts = home_pts, 10.0
        games[(f"Away{i}", f"Home{i}")] = g
    return {(2026, 10): games}


def _slate(games):
    return [{"away": a, "home": h, "home_spread": g.first_spread, "total": ""} for (a, h), g in games.items()]


def test_parse_slate_formats():
    rows, bad = wk.parse_slate("Ohio at Akron -3.5\nMissouri @ Texas A&M +7, 52.5\nKansas State,Baylor,PK\nnonsense")
    assert [(r["away"], r["home"], r["home_spread"]) for r in rows] == [
        ("Ohio", "Akron", -3.5), ("Missouri", "Texas A&M", 7.0), ("Kansas State", "Baylor", 0.0)]
    assert rows[1]["total"] == 52.5 and bad == ["nonsense"]


def test_grade_spread_from_home_and_away():
    r = {"kind": "spread", "side": "home", "line": -7}
    assert wk.grade(r, 28, 20) == "W" and wk.grade(r, 27, 20) == "P" and wk.grade(r, 26, 20) == "L"
    a = {"kind": "spread", "side": "away", "line": 7}
    assert wk.grade(a, 28, 20) == "L" and wk.grade(a, 20, 28) == "W"


def test_card_has_keys_first_and_right_size():
    feed = _feed()
    games = feed[(2026, 10)]
    now = datetime(2026, 11, 4, tzinfo=UTC)
    c = wk.build("clay", _slate(games), games, now=now, aliases={})
    rows = c["rows"]
    assert len(rows) == 12 and [r["key"] for r in rows[:3]] == ["Y"] * 3 and all(r["key"] == "N" for r in rows[3:])
    assert [r["rank"] for r in rows] == list(range(1, 13))
    assert wk.build("chappy", _slate(games), games, now=now, aliases={})["rows"][0]["key"] == "Y"


def test_snapshot_refreshes_then_freezes_and_grades(tmp_path, monkeypatch):
    monkeypatch.setattr(wk, "SLATES", tmp_path / "slates")
    monkeypatch.setattr(wk, "CARDS", tmp_path / "cards")
    monkeypatch.setattr(wk, "_aliases", lambda: {})
    feed = _feed()
    games = feed[(2026, 10)]
    wk.SLATES.mkdir()
    (wk.SLATES / "clay_2026_w10.csv").write_text(wk.render_slate(_slate(games)))
    before = datetime(2026, 11, 5, tzinfo=UTC)
    assert wk.snapshot(before, feed)["rebuilt"] == 1
    card = wk._csv(wk.card_path("clay", 2026, 10))
    assert len(card) == 12 and not any(r["frozen"] for r in card)
    # after Saturday noon ET the card is frozen even if the market moves
    for g in games.values():
        g.spread = (g.spread or 0) + 10
    after = datetime(2026, 11, 7, 18, tzinfo=UTC)
    info = wk.snapshot(after, feed)
    assert info["rebuilt"] == 0 and info["frozen"] == 12
    assert [r["pick"] for r in wk._csv(wk.card_path("clay", 2026, 10))] == [r["pick"] for r in card]
    # finals come in: every pick graded
    final = _feed(home_pts=40.0)
    for k, g in final[(2026, 10)].items():
        g.spread = games[k].spread
    assert wk.snapshot(datetime(2026, 11, 8, 12, tzinfo=UTC), final)["graded"] == 12


def test_history_and_money():
    h = wk.history()
    sources = {e["source"] for e in h}
    assert {"CPM v1 replay", "Your v51 picks"} <= sources
    rows = [{"rank": 1, "key": "Y", "result": "W"}, {"rank": 5, "key": "N", "result": "L"}]
    assert wk.points(rows) == 2 and wk.money(rows) == round(10 * 100 / 110 - 5, 2)
