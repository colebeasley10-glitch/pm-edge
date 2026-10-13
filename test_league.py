"""League tests: paper book math, strategy pricing, and a full round with fakes."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["LEAGUE_LEDGER_PATH"] = str(tmp / "league.csv")
os.environ["LEAGUE_STATUS_PATH"] = str(tmp / "status.json")
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


def test_benched_strategy_opens_nothing():
    os.environ.pop("LEAGUE_BENCHED", None)
    assert "crypto" in run.benched()

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


if __name__ == "__main__":
    test_book()
    test_weather()
    test_polymarket()
    test_crypto()
    test_round()
    test_benched_strategy_opens_nothing()
    print("League tests: PASS")


def test_sharp_daily_cap_is_35_others_default(monkeypatch):
    from league import run
    monkeypatch.delenv("LEAGUE_DAILY_CAP_SHARP", raising=False)
    assert run.daily_cap("sharp") == 35
    assert run.daily_cap("weather") == run.DAILY_CAP
    monkeypatch.setenv("LEAGUE_DAILY_CAP_SHARP", "5")
    assert run.daily_cap("sharp") == 5
