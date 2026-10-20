"""League tests: paper book math, strategy pricing, and a full round with fakes."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["LEAGUE_LEDGER_PATH"] = str(tmp / "league.csv")
os.environ["LEAGUE_STATUS_PATH"] = str(tmp / "status.json")
os.environ["LIVE_SHARP_LEDGER_PATH"] = str(tmp / "live.csv")
os.environ["LEAGUE_PUSH"] = "0"

from league import book, run  # noqa: E402
from league.strategies import crypto, polymarket, weather  # noqa: E402

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
FIX = Path("tests/fixtures/league")


def mk(ticker="KXTEST-1", yb=0.40, ya=0.42, status="open", result="", close=NOW + timedelta(hours=6)):
    return {"ticker": ticker, "event_ticker": "KXTEST", "title": "Test", "yes_bid": yb, "yes_ask": ya,
            "no_bid": round(1 - ya, 4), "no_ask": round(1 - yb, 4), "status": status, "result": result,
            "close_time": book.iso(close)}


def test_book():
    assert book.taker_fee(0.5, 20) == 0.35  # 0.07*20*0.25 = 0.35
    rows = []
    good = book.Pick("t", mk(), "YES", 0.50, "x")
    assert book.gate(good, rows, now=NOW) == (True, "ok")
    assert book.gate(book.Pick("t", mk(), "YES", 0.43, "x"), rows, now=NOW)[1] == "edge_below_min"
    assert book.gate(book.Pick("t", mk(yb=0.02, ya=0.03), "YES", 0.2, "x"), rows, now=NOW)[1] == "price_out_of_range"
    assert book.gate(book.Pick("t", mk(yb=0.20, ya=0.42), "YES", 0.6, "x"), rows, now=NOW)[1] == "wide_spread"
    r = book.open_trade(good, rows, now=NOW)
    assert r["contracts"] == 23 and abs(r["cost"] - 9.66) < 1e-9
    assert book.gate(good, rows, now=NOW)[1] == "already_holding"

    # mark, then settle a win: closing line is the last pre-close mid
    book.update(rows, lambda t: {"KXTEST-1": mk(yb=0.47, ya=0.49)}, now=NOW + timedelta(hours=1))
    assert rows[0]["last_mid"] == 0.48
    book.update(rows, lambda t: {"KXTEST-1": mk(status="finalized", result="yes")}, now=NOW + timedelta(hours=8))
    assert rows[0]["status"] == "won" and rows[0]["clv_cents"] == 6.0
    assert abs(rows[0]["pl"] - (23 - 9.66 - r["fee"])) < 0.011
    st = book.standings(rows)[0]
    assert st["won"] == 1 and st["clv"] == 6.0


def test_weather():
    wx = json.loads((FIX / "p2_kalshi_weather.json").read_text())["KXHIGHNY"]
    from pmedge.kalshi import normalize_market
    ms = []
    for raw in wx["markets"]:
        m = {k: raw.get(k) for k in ("ticker", "strike_type", "floor_strike", "cap_strike", "yes_sub_title")}
        m.update({"yes_bid": float(raw["yes_bid_dollars"]), "yes_ask": float(raw["yes_ask_dollars"]),
                  "rules_primary": wx["rules"], "status": "open"})
        ms.append(m)
    ev = {"event_ticker": wx["event"], "series": "KXHIGHNY", "title": wx["title"], "markets": ms}

    class W(weather.Weather):
        def forecast(self, station, day, kind):
            assert station == "CLINYC" and kind == "max"
            return 71.0, 2.0

        def observed_extreme(self, *a, **k):
            return 70.4  # already 70 by midday

        def remaining_extreme(self, *a, **k):
            return 71.0, 1.0, 6  # models: rest of today peaks near 71

    os.environ.pop("LEAGUE_WEATHER_SAME_DAY", None)
    assert W().price_event(ev, NOW) == []          # same-day off by default (bad obs source)
    os.environ["LEAGUE_WEATHER_SAME_DAY"] = "1"
    try:
        priced = W().price_event(ev, NOW)
    finally:
        os.environ.pop("LEAGUE_WEATHER_SAME_DAY", None)
    probs = {m["yes_sub_title"]: p for m, p, _ in priced}
    assert len(probs) == 6
    assert probs["68° or below"] == 0.03          # can't finish below what's already happened (clamped)
    assert probs["73° to 74°"] < probs["71° to 72°"] and probs["69° to 70°"] > 0.3
    assert 0.97 <= sum(probs.values()) <= 1.08


def test_polymarket():
    games = polymarket.poly_games(json.loads((FIX / "p2_poly_sports.json").read_text()), NOW)
    k = json.loads((FIX / "p2_kalshi_sports_crypto.json").read_text())
    ev = next(e for e in k["KXMLBGAME"] if e["event"] == "KXMLBGAME-26OCT132000CLETB")
    assert polymarket.match_game({"event_ticker": ev["event"], "title": ev["title"]}, games) is None  # game 2 != game 1
    ev = next(e for e in k["KXNFLGAME"] if e["event"] == "KXNFLGAME-26OCT12BUFLAR")
    e2 = {"event_ticker": ev["event"], "title": ev["title"]}
    g = polymarket.match_game(e2, games)
    assert g and g["title"] == "Bills vs. Rams"
    probs = {m["yes_sub_title"]: polymarket.team_prob({"ticker": m["ticker"], "yes_sub_title": m["yes_sub_title"]}, e2, g)
             for m in ev["markets"]}
    assert probs == {"Los Angeles R": 0.615, "Buffalo": 0.385}


def test_crypto():
    assert abs(crypto.prob_above(100, 100, 4, 0.01) - 0.5) < 0.02
    assert crypto.prob_above(102, 100, 1, 0.005) > 0.95
    candles = [[i * 3600, 0, 0, 0, 100 * (1.001 if i % 2 else 0.999), 0] for i in range(80)]
    assert 0.001 < crypto.hourly_vol(candles) < 0.01


def test_round():
    class FakeKalshi:
        def markets(self, tickers):
            return {t: mk(t) for t in tickers}

    class S:
        NAME = "fake"

        @staticmethod
        def run(kalshi, now, session=None):
            return [book.Pick("fake", mk("KXFAKE-1"), "YES", 0.55, "because"),
                    book.Pick("fake", mk("KXFAKE-2"), "YES", 0.40, "no edge")]

    st = run.run_round(NOW, strategies=[S], kalshi=FakeKalshi())
    assert st["strategies"]["fake"]["opened"] == 1
    assert st["strategies"]["fake"]["skipped"] == {"edge_below_min": 1}
    st = run.run_round(NOW + timedelta(minutes=30), strategies=[S], kalshi=FakeKalshi())
    assert st["strategies"]["fake"]["opened"] == 0  # already holding


def test_benched_picks_are_graded_on_the_side():
    """A benched strategy's would-be pick goes to the shadow ledger and settles there,
    never into the paper book (so it can't feed Live $2 or the standings)."""
    os.environ.pop("LEAGUE_BENCHED", None)
    state = {"result": ""}

    class FakeKalshi:
        def markets(self, tickers):
            return {t: mk(t, status="settled" if state["result"] else "open", result=state["result"])
                    for t in tickers}

    class W:
        NAME = "weather"

        @staticmethod
        def run(kalshi, now, session=None):
            return [book.Pick("weather", mk("KXSHADOW-1"), "YES", 0.60, "model says so")]

    before = len([r for r in book.load() if r.get("ticker") == "KXSHADOW-1"])
    st = run.run_round(NOW, strategies=[W], kalshi=FakeKalshi())
    assert st["strategies"]["weather"]["shadow_opened"] == 1
    assert len([r for r in book.load() if r.get("ticker") == "KXSHADOW-1"]) == before
    sh = [r for r in book.load(book.SHADOW_LEDGER) if r.get("ticker") == "KXSHADOW-1"]
    assert len(sh) == 1 and sh[0]["status"] == "open"
    # next round: no duplicate; then the market settles YES and the shadow bet is graded
    st = run.run_round(NOW + timedelta(minutes=30), strategies=[W], kalshi=FakeKalshi())
    assert st["strategies"]["weather"]["shadow_opened"] == 0
    state["result"] = "yes"
    st = run.run_round(NOW + timedelta(hours=1), strategies=[W], kalshi=FakeKalshi())
    sh = [r for r in book.load(book.SHADOW_LEDGER) if r.get("ticker") == "KXSHADOW-1"]
    assert sh[0]["status"] == "won"
    assert any(s["strategy"] == "weather" and s["won"] >= 1 for s in st["shadow_standings"])


def test_low_pool_alert_once_a_day():
    sent = {}
    low = [{"pool": 3, "name": "Tennis, Baseball, Basketball", "cash": 4.0}]
    assert len(run.alert_low_pools(low, sent, NOW)) == 1
    assert run.alert_low_pools(low, sent, NOW + timedelta(hours=2)) == []
    assert len(run.alert_low_pools(low, sent, NOW + timedelta(days=1))) == 1


def test_benched_strategy_opens_nothing():
    os.environ.pop("LEAGUE_BENCHED", None)
    assert {"crypto", "polymarket"} <= run.benched()

    class FakeKalshi:
        def markets(self, tickers):
            return {t: mk(t) for t in tickers}

    class C:
        NAME = "crypto"

        @staticmethod
        def run(kalshi, now, session=None):
            return [book.Pick("crypto", mk("KXBENCH-1"), "YES", 0.60, "would pick")]

    st = run.run_round(NOW, strategies=[C], kalshi=FakeKalshi())["strategies"]["crypto"]
    assert st["benched"] and st["opened"] == 0 and st["picks_found"] == 1
    assert st["shadow"][0]["ticker"] == "KXBENCH-1"
    os.environ["LEAGUE_BENCHED"] = ""
    try:
        assert run.benched() == set()
    finally:
        os.environ.pop("LEAGUE_BENCHED", None)


def test_sharp_one_bet_per_game():
    rows = []
    m1 = dict(mk("KXATPMATCH-26OCT13WANPOH-WAN"), event_ticker="KXATPMATCH-26OCT13WANPOH")
    m2 = dict(mk("KXATPMATCH-26OCT13WANPOH-POH"), event_ticker="KXATPMATCH-26OCT13WANPOH")
    book.open_trade(book.Pick("sharp", m1, "YES", None, "big trade"), rows, now=NOW)
    assert book.gate(book.Pick("sharp", m2, "YES", None, "big trade"), rows, now=NOW) == (False, "same_game")
    # the rule is per strategy: Sharp holding a game doesn't block Polymarket
    assert book.gate(book.Pick("polymarket", m2, "YES", 0.6, "gap"), rows, now=NOW)[0]
    # Polymarket can't double up on one result worded two ways (A wins YES + B wins NO)
    prow = []
    book.open_trade(book.Pick("polymarket", m1, "YES", 0.6, "gap"), prow, now=NOW)
    assert book.gate(book.Pick("polymarket", m2, "NO", None, "gap"), prow, now=NOW) == (False, "same_game")
    assert book.event_of({"ticker": "KXNFLGAME-26OCT12BUFLAR-BUF"}) == "KXNFLGAME-26OCT12BUFLAR"


if __name__ == "__main__":
    test_book()
    test_weather()
    test_polymarket()
    test_crypto()
    test_round()
    test_sharp_one_bet_per_game()
    test_benched_strategy_opens_nothing()
    print("League tests: PASS")


def test_sharp_daily_cap_matches_live_others_default(monkeypatch):
    from league import run
    monkeypatch.delenv("LEAGUE_DAILY_CAP_SHARP", raising=False)
    assert run.daily_cap("sharp") == run.UNCAPPED
    assert run.daily_cap("weather") == run.DAILY_CAP
    monkeypatch.setenv("LEAGUE_DAILY_CAP_SHARP", "5")
    assert run.daily_cap("sharp") == 5


def test_sharp_skips_markets_that_settle_far_out():
    from datetime import datetime, timezone
    from league.strategies import sharp
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    game = {"expected_expiration_time": "2026-10-14T22:00:00Z", "close_time": "2026-10-28T10:00:00Z"}
    election = {"expected_expiration_time": "2027-02-26T15:00:00Z", "close_time": "2027-11-28T15:00:00Z"}
    fed = {"close_time": "2026-10-28T17:59:00Z"}
    assert sharp.settles_soon(game, now)
    assert not sharp.settles_soon(election, now)
    assert not sharp.settles_soon(fed, now)
    assert not sharp.settles_soon({}, now)


def test_weather_skips_yes_on_narrow_brackets(monkeypatch):
    from datetime import datetime, timezone
    from league.strategies import weather
    monkeypatch.delenv("LEAGUE_WEATHER_YES_BRACKETS", raising=False)
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    m = {"ticker": "KXHIGHNY-26OCT15-B64.5", "strike_type": "between", "floor_strike": 64, "cap_strike": 65,
         "status": "active", "yes_bid_dollars": "0.08", "yes_ask_dollars": "0.10",
         "no_bid_dollars": "0.90", "no_ask_dollars": "0.92", "close_time": "2026-10-16T04:00:00Z"}

    class FakeWx:
        def __init__(self, *_a, **_k):
            pass

        def price_event(self, ev, now):
            return [(m, 0.30, "test")]  # model says YES is worth 30c vs a 10c ask

    class FakeKalshi:
        def series(self, _c):
            return [{"ticker": "KXHIGHNY"}]

        def open_events(self, _s, limit=3):
            return [{"event_ticker": "KXHIGHNY-26OCT15", "title": "NYC high"}]

    monkeypatch.setattr(weather, "Weather", FakeWx)
    picks = weather.run(FakeKalshi(), now)
    assert all(p.side != "YES" for p in picks)
    assert weather.DIAG.get("skipped_yes_bracket", 0) >= 1


def test_weather_closing_mark_freezes_at_day_start():
    from datetime import datetime, timezone
    from league import book
    row = {"strategy": "weather", "event_ticker": "KXHIGHNY-26OCT15", "ticker": "KXHIGHNY-26OCT15-T70",
           "side": "NO", "status": "open", "entry_price": "0.60", "contracts": "16", "cost": "9.6", "fee": "0.27",
           "last_mid": "0.65", "last_mid_at": "2026-10-15T03:00:00Z", "close_time": "2026-10-16T04:00:00Z"}
    assert book.mark_cutoff(row) == datetime(2026, 10, 15, 5, tzinfo=timezone.utc)
    late = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)
    mkt = {"ticker": row["ticker"], "status": "active", "no_bid_dollars": "0.98", "no_ask_dollars": "0.99"}
    book.update([row], lambda t: {row["ticker"]: mkt}, now=late)
    assert row["last_mid"] == "0.65"  # not moved after the cutoff
    settled = dict(mkt, result="no", status="finalized")
    book.update([row], lambda t: {row["ticker"]: settled}, now=late)
    assert row["status"] == "won" and row["clv_cents"] == 5.0


def test_low_total_alert_once_a_day():
    sent = {}
    assert run.alert_low_total(80.0, sent, NOW) is True
    assert run.alert_low_total(70.0, sent, NOW + timedelta(hours=2)) is False
    assert run.alert_low_total(500.0, {}, NOW) is False
    assert run.alert_low_total(None, {}, NOW) is False
    assert run.alert_low_total(80.0, sent, NOW + timedelta(days=1)) is True
