import json
from datetime import datetime, timedelta, timezone

from live_plays import core

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)


def pos(pid, title, venue="Kalshi", shown="44¢", opened=NOW - timedelta(minutes=5), start=NOW + timedelta(hours=3),
        lane="Today's Plays", status="open", ticker="KXNBAGAME-X-BOS", cost=10.0, pl=""):
    return {"position_id": pid, "title": title, "game": core.game_of(title), "venue": venue, "shown": shown,
            "opened_at": core.iso(opened), "commence_time": core.iso(start), "lane": lane, "status": status,
            "ticker": ticker, "side": "YES", "market_type": "moneyline", "cost": cost, "pl": pl, "fair": 0.47,
            "ev": 0.03}


class FakeClient:
    def __init__(self, fill=True, cash=100.0, spent=0.0):
        self.orders, self.fill, self.cash, self.spent = [], fill, cash, spent

    def committed_cost_for_prefix(self, prefix):
        return self.spent

    def get_balance_dollars(self):
        return self.cash

    def find_order_by_client_id(self, cid):
        return None

    def create_buy_order(self, **kw):
        self.orders.append(kw)
        n = kw["count"] if self.fill else 0
        return {"order": {"order_id": "o" + str(len(self.orders)), "fill_count": n}}

    def get_fills(self, ticker=None, limit=100):
        return [{"order_id": "o" + str(i + 1), "count": o["count"], "yes_price_dollars": o["max_price"]}
                for i, o in enumerate(self.orders) if self.fill]


def test_one_bet_per_game_across_lanes():
    ps = [pos("a", "Knicks at Celtics: Boston Celtics ML"),
          pos("b", "Knicks at Celtics: Boston Celtics -3.5", lane="Today's Plays (-200 to +150)")]
    picks, skips = core.new_picks(ps, [], NOW)
    assert [p["position_id"] for p in picks] == ["a"]
    assert skips["game_already_bet"] == 1


def test_skips_old_pregolive_and_started():
    ps = [pos("old", "A at B: B ML", opened=core.GO_LIVE - timedelta(minutes=1)),
          pos("soon", "C at D: D ML", start=NOW + timedelta(minutes=2)),
          pos("stale", "E at F: F ML", opened=NOW - timedelta(hours=3))]
    picks, _ = core.new_picks(ps, [], NOW)
    assert picks == []


def test_kalshi_order_is_5_dollars_ioc_at_paper_price():
    rows = []
    c = FakeClient()
    out = core.place([pos("a", "Knicks at Celtics: Boston Celtics ML")], rows, c, NOW, live=True)
    assert out["filled"] == 1
    o = c.orders[0]
    assert o["execution"] == "TAKE" and o["max_price"] == 0.44 and o["count"] == 11   # 11 x 44¢ = $4.84
    assert o["client_order_id"].startswith("pmtp-20261024-")
    assert rows[0]["status"] == "open" and float(rows[0]["cost"]) <= 5.0


def test_daily_cap_and_kalshi_history():
    rows = []
    c = FakeClient(spent=47.0)
    out = core.place([pos("a", "A at B: B ML")], rows, c, NOW, live=True)
    assert c.orders == [] and out["skipped"]["daily_budget"] == 1


def test_polymarket_alerts_and_never_orders():
    rows, sent = [], []
    c = FakeClient()
    out = core.place([pos("p", "A at B: B ML", venue="Polymarket")], rows, c, NOW, live=True, notify=sent.extend)
    assert c.orders == [] and out["alerts"] == 1 and rows[0]["status"] == "alert" and sent


def test_dry_run_places_nothing():
    rows = []
    out = core.place([pos("a", "A at B: B ML")], rows, None, NOW, live=False)
    assert rows[0]["status"] == "dry_run" and out["placed"] == 0


def test_no_fill_frees_budget():
    rows = []
    core.place([pos("a", "A at B: B ML")], rows, FakeClient(fill=False), NOW, live=True)
    assert rows[0]["status"] == "no_fill"
    assert core.spent_today(rows, core.et_day(NOW)) == 0


def test_paper_record_dedupes_and_scales():
    ps = [pos("a", "A at B: B ML", status="won", pl=8.0),
          pos("b", "A at B: B -3.5", status="lost", pl=-10.0),       # same game: ignored
          pos("c", "C at D: D ML", venue="Polymarket", status="lost", pl=-10.0)]
    r = core.paper_record(ps)
    assert (r["all"]["won"], r["all"]["lost"], r["all"]["pl"]) == (1, 1, -1.0)
    assert r["by_venue"]["Kalshi"]["pl"] == 4.0


def test_logged_polymarket_bet_counts_in_live_record():
    rows = [{**core._base_row(pos("p", "A at B: B ML", venue="Polymarket"), NOW, "Polymarket"), "status": "alert"}]
    logged = [{"bet_id": "x", "settle_ticker": "KXNBAGAME-X-BOS", "settle_side": "YES", "stake": "5", "odds": "+120",
               "game": "A at B", "label": "B ML"}]
    poly = core.match_logged(rows, logged, {"x": {"status": "won", "pl": 6.0}})
    s = core.live_record(rows, poly)
    assert (s["won"], s["pl"], s["poly_unlogged"]) == (1, 6.0, 0)


def test_settle_from_kalshi_result():
    rows = []
    core.place([pos("a", "A at B: B ML")], rows, FakeClient(), NOW, live=True)
    done = core.settle(rows, {"KXNBAGAME-X-BOS": {"result": "yes", "status": "finalized"}}, NOW)
    assert len(done) == 1 and rows[0]["status"] == "won" and float(rows[0]["pl"]) > 0


def test_ledger_roundtrip(tmp_path):
    rows = []
    core.place([pos("a", "A at B: B ML")], rows, FakeClient(), NOW, live=True)
    core.save(rows, tmp_path)
    back = core.load(tmp_path)
    assert back[0]["paper_id"] == "a" and back[0]["venue"] == "Kalshi"
    # a second round sees the game as taken
    picks, _ = core.new_picks([pos("a2", "A at B: B -1.5")], back, NOW)
    assert picks == []


def test_real_paper_files_load():
    ps = core.paper_positions()
    assert ps and all(p["lane"] in core.LANES.values() for p in ps)
    json.dumps(core.paper_record(ps)["all"])
