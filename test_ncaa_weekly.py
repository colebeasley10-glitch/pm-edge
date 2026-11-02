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
    assert {"CPM v1 replay", "Your picks"} <= sources
    rows = [{"rank": 1, "key": "Y", "result": "W"}, {"rank": 5, "key": "N", "result": "L"}]
    assert wk.points(rows) == 2 and wk.money(rows) == round(10 * 100 / 110 - 5, 2)


class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


class _GH:
    def __init__(self):
        self.files, self.calls = {}, []

    def get(self, url, **k):
        return _Resp(200, {"sha": "abc"}) if url in self.files else _Resp(404)

    def put(self, url, json, **k):
        import base64
        self.files[url] = base64.b64decode(json["content"]).decode()
        self.calls.append(("put", url))
        return _Resp(201)

    def delete(self, url, json, **k):
        self.files.pop(url, None)
        self.calls.append(("delete", url))
        return _Resp(200)


def test_lock_and_unlock_my_picks(monkeypatch):
    import bet_log
    monkeypatch.setattr(bet_log, "token", lambda: "t")
    gh = _GH()
    rows = [{"pool": "clay", "rank": 1, "key": "Y", "pick": "Iowa +2.5", "kind": "spread", "side": "away",
             "away": "Iowa", "home": "Washington", "line": 2.5, "prob": 0.6}]
    locked = wk.lock_picks("clay", 2026, 10, rows, now=datetime(2026, 11, 7, 1, 3, tzinfo=UTC), session=gh)
    assert locked[0]["locked_at"] == "2026-11-07T01:03Z" and locked[0]["frozen"] == "Y"
    url = next(iter(gh.files))
    assert url.endswith("ncaa_pool/data/my_picks/clay_2026_w10.csv") and "Iowa +2.5" in gh.files[url]
    wk.unlock_picks("clay", 2026, 10, session=gh)
    assert gh.calls[-1][0] == "delete" and not gh.files


def test_locked_picks_graded_and_in_history(tmp_path, monkeypatch):
    monkeypatch.setattr(wk, "SLATES", tmp_path / "slates")
    monkeypatch.setattr(wk, "CARDS", tmp_path / "cards")
    monkeypatch.setattr(wk, "MINE", tmp_path / "mine")
    wk.MINE.mkdir()
    rows = [{"pool": "clay", "season": 2026, "week": 10, "rank": 1, "key": "Y", "pick": "Away0 +3", "kind": "spread",
             "side": "away", "away": "Away0", "home": "Home0", "line": 3, "prob": 0.6, "frozen": "Y"}]
    (wk.MINE / "clay_2026_w10.csv").write_text(wk.render_card(rows, "2026-11-07T01:03Z"))
    feed = _feed(home_pts=12.0)                    # Home0 12, Away0 10 -> Away0 +3 covers
    assert wk.snapshot(datetime(2026, 11, 8, tzinfo=UTC), feed)["graded"] == 1
    mine = [e for e in wk.history() if e["source"] == "Your picks" and e["week"] == 10]
    assert mine and mine[0]["rows"][0]["result"] == "W" and wk.points(mine[0]["rows"]) == 2
