"""Live $1 Sharp: sizing, hard caps, idempotency, fills and settlement, with a fake Kalshi."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["LEAGUE_LEDGER_PATH"] = str(tmp / "league.csv")
os.environ["LEAGUE_STATUS_PATH"] = str(tmp / "status.json")
os.environ["LIVE_SHARP_LEDGER_PATH"] = str(tmp / "live.csv")
os.environ["LEAGUE_PUSH"] = "0"
for k in ("LIVE_SHARP_ENABLED", "LIVE_SHARP_MAX_PER_BET", "LIVE_SHARP_DAILY_BUDGET", "LIVE_SHARP_MAX_ORDERS"):
    os.environ.pop(k, None)

from league import book, live_sharp, run  # noqa: E402

# LEDGER is read at import; another test file may have imported live_sharp first.
live_sharp.LEDGER = tmp / "live.csv"

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)  # noon Eastern


class FakeClient:
    def __init__(self, fill_ratio=1.0, prior_spend=0.0):
        self.orders, self.fills, self.calls = [], [], 0
        self.fill_ratio, self.prior_spend = fill_ratio, prior_spend

    def committed_cost_for_prefix(self, prefix):
        return self.prior_spend + sum(float(o["fill_count_fp"]) * o["price"] for o in self.orders
                                      if o["client_order_id"].startswith(prefix))

    def find_order_by_client_id(self, cid):
        return next((o for o in self.orders if o["client_order_id"] == cid), None)

    def create_buy_order(self, *, ticker, outcome_side, count, max_price, execution, client_order_id):
        assert execution == "TAKE" and max_price < 1 and count >= 1
        self.calls += 1
        filled = int(count * self.fill_ratio)
        o = {"order_id": f"o{self.calls}", "client_order_id": client_order_id, "ticker": ticker,
             "count": count, "price": max_price, "fill_count_fp": str(filled)}
        self.orders.append(o)
        if filled:
            key = "yes_price_dollars" if outcome_side == "YES" else "no_price_dollars"
            self.fills.append({"order_id": o["order_id"], "count_fp": str(filled), key: str(max_price)})
        return {"order": o}

    def get_fills(self, *, ticker=None, limit=100):
        return self.fills

    def get_orders(self, *, limit=200):
        return self.orders[:limit]


def paper(i, price=0.50, side="YES", trades=50):
    return {"trade_id": f"t{i:04d}", "strategy": "sharp", "ticker": f"KXT-{i}", "event_ticker": f"KXGAME{i}",
            "title": f"Game {i}", "reason": f"$5,000 bet {side} at {price * 100:.0f}¢ ({trades} trades)",
            "side": side, "entry_price": price, "close_time": "2026-10-13T23:00:00Z"}


def test_size():
    assert live_sharp.size(0.50) == 2           # $1.00 of contracts
    assert live_sharp.size(0.33) == 3           # $0.99
    assert live_sharp.size(0.07) == 14          # $0.98
    assert live_sharp.size(0.97) == 1
    assert live_sharp.size(0.50, budget=0.60) == 1   # only room for one today
    assert live_sharp.size(0.50, budget=0.40) == 0


def test_env_cannot_raise_caps():
    os.environ.update({"LIVE_SHARP_MAX_PER_BET": "50", "LIVE_SHARP_DAILY_BUDGET": "1000",})
    try:
        assert live_sharp.per_bet() == 1.0 and live_sharp.daily_budget() == 50.0
    finally:
        for k in ("LIVE_SHARP_MAX_PER_BET", "LIVE_SHARP_DAILY_BUDGET"):
            os.environ.pop(k)


def test_daily_budget_and_order_cap():
    c, rows = FakeClient(), []
    out = live_sharp.place([paper(i) for i in range(60)], c, now=NOW, rows=rows)
    spent = sum(float(r["cost"]) + float(r["fee"]) for r in rows)
    assert spent <= 50.0 + 1e-9, spent
    assert all(o["count"] * o["price"] <= 1.0 + 1e-9 for o in c.orders)
    assert out["skipped"].get("daily_budget", 0) == 60 - out["placed"]
    # next Eastern day the budget resets
    out2 = live_sharp.place([paper(100)], c, now=NOW + timedelta(days=1), rows=rows)
    assert out2["placed"] == 1


def test_kalshi_history_counts_toward_budget():
    c = FakeClient(prior_spend=49.80)   # e.g. a ledger commit was lost
    out = live_sharp.place([paper(1)], c, now=NOW, rows=[])
    assert out["placed"] == 0 and out["skipped"] == {"daily_budget": 1}


def test_missed_orders_do_not_use_budget():
    c, rows = FakeClient(fill_ratio=0), []
    out = live_sharp.place([paper(i) for i in range(60)], c, now=NOW, rows=rows)
    assert out["placed"] == 60 and out["filled"] == 0 and out["spent_today"] == 0.0


def test_idempotent_and_no_fill():
    c, rows = FakeClient(), []
    live_sharp.place([paper(1)], c, now=NOW, rows=rows)
    live_sharp.place([paper(1)], c, now=NOW, rows=rows)
    assert c.calls == 1 and len(rows) == 1
    c2, rows2 = FakeClient(fill_ratio=0), []
    out = live_sharp.place([paper(2)], c2, now=NOW, rows=rows2)
    assert rows2[0]["status"] == "no_fill" and out["filled"] == 0
    assert live_sharp.summary(rows2, NOW)["spent_today"] == 0.0


def test_settle():
    c, rows = FakeClient(), []
    live_sharp.place([paper(1, 0.40, "YES"), paper(2, 0.40, "NO")], c, now=NOW, rows=rows)
    mk = {"KXT-1": {"result": "yes", "status": "finalized"}, "KXT-2": {"result": "yes", "status": "finalized"}}
    done = live_sharp.settle(rows, lambda t: {k: mk[k] for k in t}, now=NOW)
    assert [r["status"] for r in done] == ["won", "lost"]
    won = done[0]
    assert abs(float(won["pl"]) - (2 - float(won["cost"]) - float(won["fee"]))) < 1e-6
    s = live_sharp.summary(rows, NOW)
    assert s["won"] == 1 and s["lost"] == 1


def test_round_places_only_when_enabled():
    class FakeKalshi:
        def markets(self, tickers):
            return {t: {"ticker": t, "yes_bid": 0.48, "yes_ask": 0.50, "no_bid": 0.50, "no_ask": 0.52,
                        "status": "open", "close_time": "2026-10-13T23:00:00Z"} for t in tickers}

    class Sharp:
        NAME = "sharp"

        @staticmethod
        def run(kalshi, now, session=None):
            m = FakeKalshi().markets(["KXR-1"])["KXR-1"]
            return [book.Pick("sharp", m, "YES", None, "$9,000 bet YES at 50¢ (40 trades)")]

    st = run.run_round(NOW, strategies=[Sharp], kalshi=FakeKalshi())
    assert st["live_sharp"]["enabled"] is False and not live_sharp.LEDGER.exists()
    c = FakeClient()
    paper_row = book.load()[-1]
    out = run.run_live_sharp([paper_row], FakeKalshi(), NOW, client=c)
    assert out["placed"] == 1 and c.orders[0]["price"] == 0.50 and c.orders[0]["count"] == 2


def test_marks_open_bets_like_paper():
    c, rows = FakeClient(), []
    live_sharp.place([paper(50, 0.40)], c, now=NOW, rows=rows)
    mk = {"KXT-50": {"status": "active", "yes_bid": 0.54, "yes_ask": 0.56, "no_bid": 0.44, "no_ask": 0.46,
                     "expected_expiration_time": "2026-10-13T22:00:00Z"}}
    live_sharp.settle(rows, lambda t: {k: mk[k] for k in t if k in mk}, now=NOW)
    r = rows[0]
    assert r["status"] == "open" and r["last_mid"] == 0.55 and r["ends_at"] == "2026-10-13T22:00:00Z"
    out = live_sharp.refresh(rows, lambda t: {k: dict(mk[k], yes_bid=0.60, yes_ask=0.62) for k in t if k in mk}, now=NOW)
    assert out["marked"] == 1 and out["rows"][0]["last_mid"] == 0.61 and rows[0]["last_mid"] == 0.55
    mk["KXT-50"] = {"result": "yes", "status": "finalized"}
    live_sharp.settle(rows, lambda t: {k: mk[k] for k in t if k in mk}, now=NOW)
    assert r["status"] == "won" and r["clv_cents"] == 15.0


def test_refresh_counts_closed_matches_as_checked():
    c, rows = FakeClient(), []
    live_sharp.place([paper(60), paper(61)], c, now=NOW, rows=rows)
    mk = {"KXT-60": {"status": "closed", "result": ""},
          "KXT-61": {"status": "active", "yes_bid": 0.5, "yes_ask": 0.52, "no_bid": 0.48, "no_ask": 0.5}}
    out = live_sharp.refresh(rows, lambda t: {k: mk[k] for k in t if k in mk}, now=NOW)
    assert out["found"] == 2 and out["marked"] == 1 and out["waiting"] == 1


def test_one_real_bet_per_market():
    c, rows = FakeClient(), []
    a = paper(7)
    b = dict(paper(7), trade_id="t9999")   # same market re-picked later with a new paper id
    live_sharp.place([a], c, now=NOW, rows=rows)
    out = live_sharp.place([b], c, now=NOW + timedelta(hours=1), rows=rows)
    assert out["skipped"] == {"already_bet_game": 1} and c.calls == 1
    opp = dict(paper(7), trade_id="t8888", ticker="KXT-7B", side="YES")   # the other player, same game
    out = live_sharp.place([opp], c, now=NOW + timedelta(hours=2), rows=rows)
    assert out["skipped"] == {"already_bet_game": 1} and c.calls == 1


def test_live_copies_only_paper_picks():
    class FakeKalshi:
        def markets(self, tickers):
            return {t: {"ticker": t, "yes_bid": 0.48, "yes_ask": 0.50, "no_bid": 0.50, "no_ask": 0.52,
                        "status": "open", "close_time": "2026-10-13T23:00:00Z"} for t in tickers}

    class Sharp:
        NAME = "sharp"

        @staticmethod
        def run(kalshi, now, session=None):
            m = dict(FakeKalshi().markets(["KXCAP-1"])["KXCAP-1"])
            return [book.Pick("sharp", m, "YES", None, "$9,000 bet YES at 50¢ (40 trades)")]

    os.environ["LEAGUE_DAILY_CAP_SHARP"] = "0"
    os.environ["LIVE_SHARP_ENABLED"] = "1"
    c = FakeClient()
    orig = live_sharp.client_from_env, live_sharp.enabled
    live_sharp.client_from_env, live_sharp.enabled = (lambda: c), (lambda: True)
    try:
        st = run.run_round(NOW, strategies=[Sharp], kalshi=FakeKalshi())
    finally:
        live_sharp.client_from_env, live_sharp.enabled = orig
        os.environ.pop("LEAGUE_DAILY_CAP_SHARP")
        os.environ.pop("LIVE_SHARP_ENABLED")
    assert st["strategies"]["sharp"]["opened"] == 0 and st["strategies"]["sharp"]["skipped"] == {"daily_cap": 1}
    ls = st["live_sharp"]
    assert ls["kalshi_ok"] is True and not ls.get("placed"), ls     # over the paper cap → no real bet
    assert not any(r["ticker"] == "KXCAP-1" for r in book.load())
    assert not any(r["ticker"] == "KXCAP-1" for r in live_sharp.load())


def test_no_fill_does_not_use_order_cap():
    day = "20261013"
    rows = [{"day": day, "status": "no_fill", "cost": 0, "fee": 0} for _ in range(5)]
    rows.append({"day": day, "status": "won", "cost": 0.5, "fee": 0.02})
    spent, n = live_sharp._spent(rows, day)
    assert n == 1 and abs(spent - 0.52) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Live $1 Sharp tests: PASS")


def test_daily_breakdown():
    rows = [
        {"day": "20261012", "status": "won", "cost": "0.50", "fee": "0.02", "pl": "0.48", "settled_at": "2026-10-13T01:00:00Z"},
        {"day": "20261013", "status": "lost", "cost": "0.60", "fee": "0.02", "pl": "-0.62", "settled_at": "2026-10-13T20:00:00Z"},
        {"day": "20261013", "status": "no_fill"},
        {"day": "20261013", "status": "error", "note": "Trading_is_not_currently_allowed_in_Washington"},
        {"day": "20261013", "status": "open", "cost": "0.81", "fee": "0.01"},
    ]
    out = {d["day"]: d for d in live_sharp.daily(rows)}
    # 01:00Z Oct 13 is 9pm Oct 12 Eastern
    assert out["20261012"]["won"] == 1 and out["20261012"]["pl"] == 0.48 and out["20261012"]["filled"] == 1
    d = out["20261013"]
    assert (d["won"], d["lost"], d["pl"], d["total"]) == (0, 1, -0.62, -0.14)
    assert (d["orders"], d["filled"], d["missed"], d["blocked"]) == (4, 2, 1, 1)


def _sell(ticker, side, count, price, when="2026-10-13T20:00:00Z", fee="0.01"):
    key = "yes_price_dollars" if side == "YES" else "no_price_dollars"
    return {"ticker": ticker, "action": "sell", "side": side.lower(), "count_fp": str(count),
            key: str(price), "fee_cost": fee, "created_time": when}


def test_cash_out_counts_real_proceeds():
    c, rows = FakeClient(), []
    live_sharp.place([paper(70, 0.40, "YES")], c, now=NOW, rows=rows)
    r = rows[0]
    n = float(r["filled"])
    c.fills.append(_sell("KXT-70", "YES", n, 0.55))
    done = live_sharp.sync_sells(rows, c, now=NOW)
    assert done == [r] and r["status"] == "won" and r["result"] == "sold"
    expect = round(n * 0.55 - 0.01 - float(r["cost"]) - float(r["fee"]), 2)
    assert float(r["pl"]) == expect and "55¢" in r["note"]
    # a sold bet is finished: settlement later doesn't touch it, and it isn't counted twice
    assert live_sharp.sync_sells(rows, c, now=NOW) == []
    mk = {"KXT-70": {"result": "no", "status": "finalized"}}
    assert live_sharp.settle(rows, lambda t: {k: mk[k] for k in t if k in mk}, now=NOW) == []
    assert float(r["pl"]) == expect and live_sharp.summary(rows, NOW)["won"] == 1


def test_cash_out_at_a_loss_is_a_loss():
    c, rows = FakeClient(), []
    live_sharp.place([paper(71, 0.40, "NO")], c, now=NOW, rows=rows)
    c.fills.append(_sell("KXT-71", "NO", float(rows[0]["filled"]), 0.30))
    live_sharp.sync_sells(rows, c, now=NOW)
    assert rows[0]["status"] == "lost" and float(rows[0]["pl"]) < 0


def test_partial_cash_out_then_settle():
    c, rows = FakeClient(), []
    live_sharp.place([paper(72, 0.40, "YES")], c, now=NOW, rows=rows)
    r = rows[0]
    n = float(r["filled"])
    assert n >= 2
    c.fills.append(_sell("KXT-72", "YES", 1, 0.60))
    assert live_sharp.sync_sells(rows, c, now=NOW) == [] and r["status"] == "open" and float(r["sold"]) == 1
    mk = {"KXT-72": {"result": "yes", "status": "finalized"}}
    live_sharp.settle(rows, lambda t: {k: mk[k] for k in t if k in mk}, now=NOW)
    assert r["status"] == "won"
    assert abs(float(r["pl"]) - round((n - 1) + 0.60 - 0.01 - float(r["cost"]) - float(r["fee"]), 2)) < 1e-9


def test_ignores_other_side_and_earlier_sells():
    c, rows = FakeClient(), []
    live_sharp.place([paper(73, 0.40, "YES")], c, now=NOW, rows=rows)
    n = float(rows[0]["filled"])
    c.fills.append(_sell("KXT-73", "NO", n, 0.70))
    c.fills.append(_sell("KXT-73", "YES", n, 0.70, when="2020-01-01T00:00:00Z"))
    assert live_sharp.sync_sells(rows, c, now=NOW) == [] and rows[0]["status"] == "open" and not rows[0].get("sold")


def test_by_sport_groups_live_bets():
    from league import live_sharp
    rows = [
        {"status": "won", "event_ticker": "KXATPMATCH-26OCT13A", "pl": "0.40", "cost": "0.58", "fee": "0.02"},
        {"status": "lost", "event_ticker": "", "ticker": "KXWTAMATCH-26OCT13B-X", "pl": "-0.62", "cost": "0.60", "fee": "0.02"},
        {"status": "open", "event_ticker": "KXUCLGAME-26OCT14ASKLFC", "cost": "0.72", "fee": "0.01"},
        {"status": "no_fill", "event_ticker": "KXNFLGAME-26OCT19X"},
    ]
    out = {g["group"]: g for g in live_sharp.by_sport(rows)}
    assert set(out) == {"Tennis", "Soccer"}
    assert (out["Tennis"]["won"], out["Tennis"]["lost"], out["Tennis"]["pl"]) == (1, 1, -0.22)
    assert out["Soccer"]["open"] == 1 and out["Soccer"]["bets"] == 0 and out["Soccer"]["roi"] is None


def test_skips_thin_flow(monkeypatch):
    from league import live_sharp
    assert live_sharp.flow_trades({"reason": "$1,922 bet YES at 72¢ (6 trades)"}) == 6
    assert live_sharp.flow_trades({"reason": "$90 bet NO at 40¢ (1 trade)"}) == 1
    assert live_sharp.flow_trades({"reason": ""}) is None
    c = FakeClient()
    out = live_sharp.place([paper(1, trades=6), paper(2, trades=20), paper(3, trades=300)], c, now=NOW, rows=[])
    assert out["skipped"].get("thin_flow") == 1 and out["placed"] == 2
    monkeypatch.setenv("LIVE_SHARP_MIN_TRADES", "0")
    out = live_sharp.place([paper(4, trades=2)], c, now=NOW, rows=[])
    assert out["placed"] == 1
