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

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)  # noon Eastern


class FakeClient:
    def __init__(self, fill_ratio=1.0, prior_spend=0.0):
        self.orders, self.fills, self.calls = [], [], 0
        self.fill_ratio, self.prior_spend = fill_ratio, prior_spend

    def submitted_cost_for_prefix(self, prefix):
        return self.prior_spend + sum(o["count"] * o["price"] for o in self.orders
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


def paper(i, price=0.50, side="YES"):
    return {"trade_id": f"t{i:04d}", "strategy": "sharp", "ticker": f"KXT-{i}", "event_ticker": f"KXGAME{i}",
            "title": f"Game {i}",
            "side": side, "entry_price": price, "close_time": "2026-10-13T23:00:00Z"}


def test_size():
    assert live_sharp.size(0.50) == 2           # $1.00 of contracts
    assert live_sharp.size(0.33) == 3           # $0.99
    assert live_sharp.size(0.07) == 14          # $0.98
    assert live_sharp.size(0.97) == 1
    assert live_sharp.size(0.50, budget=0.60) == 1   # only room for one today
    assert live_sharp.size(0.50, budget=0.40) == 0


def test_env_cannot_raise_caps():
    os.environ.update({"LIVE_SHARP_MAX_PER_BET": "50", "LIVE_SHARP_DAILY_BUDGET": "1000", "LIVE_SHARP_MAX_ORDERS": "500"})
    try:
        assert live_sharp.per_bet() == 1.0 and live_sharp.daily_budget() == 30.0 and live_sharp.max_orders() == 30
    finally:
        for k in ("LIVE_SHARP_MAX_PER_BET", "LIVE_SHARP_DAILY_BUDGET", "LIVE_SHARP_MAX_ORDERS"):
            os.environ.pop(k)


def test_daily_budget_and_order_cap():
    c, rows = FakeClient(), []
    out = live_sharp.place([paper(i) for i in range(40)], c, now=NOW, rows=rows)
    spent = sum(float(r["cost"]) + float(r["fee"]) for r in rows)
    assert spent <= 30.0 + 1e-9, spent
    assert out["placed"] <= 30 and len(c.orders) <= 30
    assert all(o["count"] * o["price"] <= 1.0 + 1e-9 for o in c.orders)
    assert out["skipped"].get("daily_budget", 0) + out["skipped"].get("daily_order_cap", 0) == 40 - out["placed"]
    # next Eastern day the budget resets
    out2 = live_sharp.place([paper(100)], c, now=NOW + timedelta(days=1), rows=rows)
    assert out2["placed"] == 1


def test_kalshi_history_counts_toward_budget():
    c = FakeClient(prior_spend=29.80)   # e.g. a ledger commit was lost
    out = live_sharp.place([paper(1)], c, now=NOW, rows=[])
    assert out["placed"] == 0 and out["skipped"] == {"daily_budget": 1}


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
            return [book.Pick("sharp", m, "YES", None, "big trade")]

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


def test_paper_cap_does_not_block_live():
    class FakeKalshi:
        def markets(self, tickers):
            return {t: {"ticker": t, "yes_bid": 0.48, "yes_ask": 0.50, "no_bid": 0.50, "no_ask": 0.52,
                        "status": "open", "close_time": "2026-10-13T23:00:00Z"} for t in tickers}

    class Sharp:
        NAME = "sharp"

        @staticmethod
        def run(kalshi, now, session=None):
            m = dict(FakeKalshi().markets(["KXCAP-1"])["KXCAP-1"])
            return [book.Pick("sharp", m, "YES", None, "big trade")]

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
    assert ls["kalshi_ok"] is True and ls["placed"] == 1, ls
    assert not any(r["ticker"] == "KXCAP-1" for r in book.load())   # stays out of the paper ledger


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Live $1 Sharp tests: PASS")
