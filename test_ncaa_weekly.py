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
    monkeypatch.setattr(wk, "VARIANTS", tmp_path / "variants")
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
    monkeypatch.setattr(wk, "VARIANTS", tmp_path / "variants")
    monkeypatch.setattr(wk, "MINE", tmp_path / "mine")
    wk.MINE.mkdir()
    rows = [{"pool": "clay", "season": 2026, "week": 10, "rank": 1, "key": "Y", "pick": "Away0 +3", "kind": "spread",
             "side": "away", "away": "Away0", "home": "Home0", "line": 3, "prob": 0.6, "frozen": "Y"}]
    (wk.MINE / "clay_2026_w10.csv").write_text(wk.render_card(rows, "2026-11-07T01:03Z"))
    feed = _feed(home_pts=12.0)                    # Home0 12, Away0 10 -> Away0 +3 covers
    assert wk.snapshot(datetime(2026, 11, 8, tzinfo=UTC), feed)["graded"] == 1
    mine = [e for e in wk.history() if e["source"] == "Your picks" and e["week"] == 10]
    assert mine and mine[0]["rows"][0]["result"] == "W" and wk.points(mine[0]["rows"]) == 2


# ---------------------------------------------------------------- Nov 4, 2026: Pinnacle line + shadow variants
import json  # noqa: E402
from datetime import timedelta  # noqa: E402

from ncaa_pool import recheck  # noqa: E402


def test_pinnacle_used_when_fresh_espn_when_stale():
    now = datetime(2026, 11, 6, tzinfo=UTC)
    g = wk.GameInfo(spread=-3.0, total=50.0, pinn_spread=-4.5, pinn_total=52.0, pinn_at=now - timedelta(hours=1))
    assert wk.market_line(g, "pinnacle", now) == (-4.5, 52.0, "pinnacle")
    assert wk.market_line(g, "espn", now) == (-3.0, 50.0, "espn")
    g.pinn_at = now - timedelta(hours=7)
    assert wk.market_line(g, "pinnacle", now) == (-3.0, 50.0, "espn")


def test_attach_pinnacle_matches_flipped_and_skips_ambiguous(tmp_path):
    kick = datetime(2026, 11, 7, 17, tzinfo=UTC)
    feed = {(2026, 10): {("Ohio State", "Michigan"): wk.GameInfo(kickoff=kick),
                         ("Miami", "Duke"): wk.GameInfo(kickoff=kick)}}
    at = "2026-11-06T20:00:00Z"
    games = {"1": {"home": "Ohio State", "away": "Michigan", "starts": "2026-11-07T17:00:00Z", "spread": -7.5,
                   "total": 44.5, "at": at},                     # Pinnacle lists the teams the other way round
             "2": {"home": "Duke", "away": "Miami", "starts": "2026-11-07T17:00:00Z", "spread": 3.0,
                   "total": None, "at": at},
             "3": {"home": "Duke", "away": "Miami", "starts": "2026-11-07T18:00:00Z", "spread": 2.5,
                   "total": None, "at": at}}                     # two candidates: leave ESPN alone
    p = tmp_path / "lines.json"
    p.write_text(json.dumps({"games": games}))
    assert wk.attach_pinnacle(feed, p, aliases={}) == 1
    g = feed[(2026, 10)][("Ohio State", "Michigan")]
    assert g.pinn_spread == 7.5 and g.pinn_total == 44.5
    assert feed[(2026, 10)][("Miami", "Duke")].pinn_spread is None


def _variant_setup(tmp_path, monkeypatch):
    for name in ("SLATES", "CARDS", "VARIANTS", "MINE"):
        monkeypatch.setattr(wk, name, tmp_path / name.lower())
    monkeypatch.setattr(wk, "_aliases", lambda: {})
    feed = _feed()
    games = feed[(2026, 10)]
    wk.SLATES.mkdir()
    (wk.SLATES / "clay_2026_w10.csv").write_text(wk.render_slate(_slate(games)))
    return feed, games


def test_variants_freeze_friday_and_saturday(tmp_path, monkeypatch):
    feed, games = _variant_setup(tmp_path, monkeypatch)
    fri = datetime(2026, 11, 6, 22, tzinfo=UTC)                  # Fri 5pm ET
    for g in games.values():
        g.pinn_spread, g.pinn_at = g.spread - 1, fri
    info = wk.snapshot(fri, feed)
    assert info["shadow"]["rebuilt"] == 5
    assert {r["src"] for r in wk._csv(wk.card_path("clay", 2026, 10))} == {"pinnacle"}
    assert {r["src"] for r in wk._csv(wk.variant_path("clay", 2026, 10, "espn_sat"))} == {"espn"}
    sat = datetime(2026, 11, 7, 14, tzinfo=UTC)                  # Sat 9am ET: Friday cards frozen, Saturday still live
    info = wk.snapshot(sat, feed)
    assert info["shadow"]["frozen"] == 24 and info["shadow"]["rebuilt"] == 3
    final = _feed(home_pts=40.0)
    wk.snapshot(datetime(2026, 11, 8, 12, tzinfo=UTC), final)
    res = wk.variant_results()
    assert set(res["totals"]["clay"]) == set(wk.VARIANT_SPECS)
    assert all(t["weeks"] == 1 and t["w"] + t["l"] + t["p"] == 12 for t in res["totals"]["clay"].values())


def test_saturday_recheck_alerts_once_on_change(tmp_path, monkeypatch):
    feed, games = _variant_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(recheck, "STATE", tmp_path / "state.json")
    sat = datetime(2026, 11, 7, 14, 20, tzinfo=UTC)              # Sat 9:20am ET
    wk.snapshot(sat, feed)
    card = wk._csv(wk.card_path("clay", 2026, 10))
    sent = []
    send = lambda t, m: sent.append((t, m)) or True              # noqa: E731
    assert recheck.run(sat, send) == ["clay_2026_w10_reminder"] and "not locked" in sent[0][0]
    wk.MINE.mkdir()
    wk.mine_path("clay", 2026, 10).write_text(wk.render_card(card, "x"))
    assert recheck.run(sat, send) == []                          # locked = model card: no alert
    mine = [dict(r) for r in card]
    mine[-1]["pick"] = "Somebody +3"
    wk.mine_path("clay", 2026, 10).write_text(wk.render_card(mine, "x"))
    assert recheck.run(sat, send) == ["clay_2026_w10"] and "Somebody +3" in sent[-1][1]
    assert recheck.run(sat, send) == []                          # same change: don't repeat
    assert recheck.run(datetime(2026, 11, 6, 14, tzinfo=UTC), send) == []   # not Saturday


# ---- Nov 5, 2026: tiebreaker + crowd-fade shadow
def test_parse_slate_marks_tiebreaker():
    rows, bad = wk.parse_slate("Ohio at Akron -3.5\nAlabama at LSU -5.5, 52.5 tiebreaker\nA,B,3,40,TB")
    assert not bad and [r["tiebreaker"] for r in rows] == ["", "Y", "Y"]
    assert rows[1]["home"] == "LSU" and rows[1]["total"] == 52.5 and rows[2]["total"] == 40


def test_tiebreaker_uses_market_total_and_freezes(tmp_path, monkeypatch):
    feed, games = _variant_setup(tmp_path, monkeypatch)
    slate = wk._csv(wk.SLATES / "clay_2026_w10.csv")
    slate[0]["tiebreaker"] = "Y"
    (wk.SLATES / "clay_2026_w10.csv").write_text(wk.render_slate(slate))
    (away, home), g = next(iter(games.items()))
    fri = datetime(2026, 11, 6, 22, tzinfo=UTC)
    g.pinn_spread, g.pinn_total, g.pinn_at = -3.0, 52.5, fri
    wk.snapshot(fri, feed)
    tb = wk.load_tiebreaker("clay", 2026, 10)
    assert (tb["away"], tb["home"], tb["guess"], tb["src"], tb["frozen"]) == (away, home, 53, "pinnacle", "")
    g.pinn_total, g.pinn_at = 49.0, datetime(2026, 11, 7, 18, tzinfo=UTC)
    wk.snapshot(datetime(2026, 11, 7, 18, tzinfo=UTC), feed)          # after the Saturday noon lock
    tb = wk.load_tiebreaker("clay", 2026, 10)
    assert tb["guess"] == 53 and tb["frozen"] == "Y"


def test_crowd_fade_only_swaps_public_filler():
    from ncaa_pool.model import Game, build_card
    games = [Game(f"A{i}", f"H{i}", -10.5, -13.5) for i in range(3)]          # big edges -> keys
    games += [Game(f"A{i}", f"H{i}", -3.5, -3.5) for i in range(3, 12)]       # no edge: coin flips
    plain = build_card(games, 12, 3, allow_totals=False)
    faded = build_card(games, 12, 3, allow_totals=False, fade=True)
    assert [o.label for o in plain["keys"]] == [o.label for o in faded["keys"]]
    assert all(o.side == "home" for o in plain["keys"])
    flips = [o for o in faded["picks"][3:] if o.extra.get("faded")]
    assert flips and all(o.side == "away" for o in flips)                     # off the favorite
