"""Maker Bot: pricing, kickoff-safe expiry, money limits, fills, settlement."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from maker_bot import core

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)


def cand(ticker="KXNCAAFGAME-X-ALA", side="YES", fair=0.60, bid=0.50, ask=0.58, starts=NOW + timedelta(hours=3),
         event="E1", sharp=None, label="Alabama ML"):
    return {"ticker": ticker, "side": side, "event": event, "label": label, "game": "g", "sport": "football",
            "market": "moneyline", "starts": core.iso(starts), "fair": fair, "bid": bid, "ask": ask, "sharp": sharp}


def test_prices_one_cent_over_best_bid_never_at_ask():
    p, n, e, why = core.quote(cand(fair=0.60, bid=0.50, ask=0.58), 5.0)
    assert why == "ok" and p == 0.51 and e >= core.MIN_EDGE
    p, n, e, why = core.quote(cand(fair=0.62, bid=0.56, ask=0.57), 5.0)
    assert p == 0.56                      # bid + 1 would touch the ask, so join the bid


def test_size_scales_with_edge():
    # ~1.3% edge -> about 1/6 unit; ~8% edge -> full unit
    _, n_small, e_small, _ = core.quote(cand(fair=0.5778, bid=0.55, ask=0.57), 5.0)
    _, n_big, e_big, _ = core.quote(cand(fair=0.64, bid=0.55, ask=0.60), 5.0)
    assert 0.01 <= e_small < 0.02 and 1 <= n_small <= 2
    assert e_big >= core.FULL_EDGE and n_big == 8 and n_big * 0.56 <= 5.0   # 9 would be $5.04
    assert core.stake_for(0.035, False, 5.0) == pytest.approx(5 * (1/6 + 5/6 * 0.5), abs=0.01)


def test_sharp_flow_sizes_up_but_doesnt_lower_floor():
    base = cand(fair=0.5778, bid=0.55, ask=0.57)
    _, n0, _, _ = core.quote(base, 5.0)
    _, n1, _, _ = core.quote({**base, "sharp": {"flow_trades": 40}}, 5.0)
    assert n1 > n0
    # below 1% raw edge: still skipped with Sharp
    assert core.quote({**cand(fair=0.555, bid=0.54, ask=0.56), "sharp": {"x": 1}}, 5.0)[3] == "edge_under_1pct"


def test_skips_tiny_huge_and_extreme_prices():
    assert core.quote(cand(fair=0.552, bid=0.54, ask=0.56), 5.0)[3] == "edge_under_1pct"
    assert core.quote(cand(fair=0.75, bid=0.55, ask=0.60), 5.0)[3] == "edge_too_big_check_match"
    assert core.quote(cand(fair=0.97, bid=0.93, ask=0.95), 5.0)[3] == "outside_10c_90c"
    assert core.quote(cand(fair=0.80, bid=0.75, ask=0.78), 5.0)[3] == "ok"   # no more +150/-200 band


def test_order_expires_before_kickoff_never_after():
    soon = cand(starts=NOW + timedelta(minutes=30))
    picked, _ = core.plan([soon], [], NOW, "dry")
    exp = core.parse_dt(picked[0]["expires_at"])
    assert exp == NOW + timedelta(minutes=25)                 # start - 5 min, not now + 50
    later = cand(starts=NOW + timedelta(hours=5))
    exp2 = core.parse_dt(core.plan([later], [], NOW, "dry")[0][0]["expires_at"])
    assert exp2 == NOW + core.ORDER_TTL
    too_close = cand(starts=NOW + timedelta(minutes=9))
    picked, skips = core.plan([too_close], [], NOW, "dry")
    assert not picked and skips == {"too_close_to_start": 1}


def test_one_bet_per_game_and_daily_budget(monkeypatch):
    cs = [cand(ticker=f"T{i}", event=f"E{i}") for i in range(30)] + [cand(ticker="T0b", event="E0")]
    picked, skips = core.plan(cs, [], NOW, "live")
    assert len({c["event"] for c in picked}) == len(picked) <= core.MAX_NEW_PER_RUN
    # budget: $25/day ceiling; resting orders already committing ~$24.7 today -> nothing new fits
    rows = [{**core.new_row({**cand(ticker=f"R{i}", event=f"R{i}"), "price": 0.55, "contracts": 1, "edge": 0.04,
                             "expires_at": core.iso(NOW + timedelta(minutes=40))}, NOW, "live")} for i in range(44)]
    assert 24.5 < core.committed_today(rows, core.et_day(NOW)) <= 25
    picked, skips = core.plan(cs, rows, NOW, "live")
    assert not picked and skips.get("daily_budget")


def test_env_can_lower_but_not_raise_limits(monkeypatch):
    monkeypatch.setenv("MAKER_BOT_UNIT", "50")
    monkeypatch.setenv("MAKER_BOT_DAILY_BUDGET", "1000")
    assert core.unit() == 5.0 and core.daily_budget() == 25.0
    monkeypatch.setenv("MAKER_BOT_DAILY_BUDGET", "5")
    assert core.daily_budget() == 5.0


def test_live_off_without_flag(monkeypatch):
    monkeypatch.delenv("MAKER_BOT_ENABLED", raising=False)
    assert core.live_enabled() is False


def _resting(now=NOW, **kw):
    c = {**cand(), "price": 0.55, "contracts": 1, "edge": 0.04, "expires_at": core.iso(now + timedelta(minutes=50))}
    r = core.new_row(c, now, "live")
    r.update(kw)
    return r


def test_reconcile_fill_partial_and_expiry():
    filled, empty, partial = _resting(), _resting(), _resting()
    filled["client_order_id"], empty["client_order_id"], partial["client_order_id"] = "pmmk-a", "pmmk-b", "pmmk-c"
    orders = [{"client_order_id": "pmmk-a", "order_id": "1", "status": "executed", "fill_count_fp": "1.00"},
              {"client_order_id": "pmmk-b", "order_id": "2", "status": "canceled", "fill_count_fp": "0"},
              {"client_order_id": "pmmk-c", "order_id": "3", "status": "resting", "fill_count_fp": "1.00"}]
    out = core.reconcile([filled, empty, partial], orders, NOW + timedelta(minutes=10))
    assert filled["status"] == "filled" and filled["cost"] == 0.55 and filled["fee"] == 0.01
    assert empty["status"] == "expired" and empty["pl"] == 0.0
    assert partial["status"] == "resting" and partial["filled"] == 1.0 and out["still_resting"] == 1


def test_markout_recorded_once_before_start():
    r = _resting(status="filled", filled=1.0, cost=0.55, fee=0.01)
    m = {r["ticker"]: {"yes_bid": 0.52, "yes_ask": 0.54}}
    assert core.mark([r], m, NOW + timedelta(minutes=20)) == 1 and r["markout"] == -2.0
    assert core.mark([r], {r["ticker"]: {"yes_bid": 0.7, "yes_ask": 0.72}}, NOW + timedelta(minutes=40)) == 0
    late = _resting(status="filled", filled=1.0, cost=0.55, fee=0.01)
    assert core.mark([late], m, NOW + timedelta(hours=4)) == 0     # game started: in-play mid isn't a markout


def test_settle_pl():
    r = _resting(status="filled", filled=1.0, cost=0.55, fee=0.01)
    lost = _resting(status="filled", filled=1.0, cost=0.55, fee=0.01, ticker="L")
    n = core.settle([r, lost], {r["ticker"]: {"result": "yes"}, "L": {"result": "no"}}, NOW)
    assert n == 2 and r["pl"] == 0.44 and lost["pl"] == -0.56
    s = core.summarize([r, lost])
    assert (s["won"], s["lost"], s["pl"]) == (1, 1, -0.12)


def test_expired_orders_dont_count_as_bets_or_spend():
    e = _resting(status="expired", cost=0.0, fee=0.0, pl=0.0)
    assert core.committed_today([e], core.et_day(NOW)) == 0.0
    s = core.summarize([e])
    assert s["fills"] == 0 and s["fill_rate"] == 0.0


class FakeSession:
    def __init__(self):
        self.sent = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, **kw):
        self.sent.append(json)
        return SimpleNamespace(status_code=201, ok=True, text="{}",
                               json=lambda: {"order": {"order_id": "o1", "status": "resting", "fill_count_fp": "0"}})


def test_order_sent_is_post_only_and_expires_before_start(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import kalshi_submit
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    sess = FakeSession()
    client = kalshi_submit.KalshiSubmitClient("kid", pem, session=sess)
    real_now = datetime.now(timezone.utc)
    c = core.plan([cand(starts=real_now + timedelta(minutes=40))], [], real_now, "live")[0][0]
    row = core.new_row(c, real_now, "live")
    core.place(client, c, row)
    body = sess.sent[-1]
    assert body["post_only"] is True and body["time_in_force"] == "good_till_canceled"
    assert body["expiration_time"] == int(core.parse_dt(row["expires_at"]).timestamp())
    assert body["expiration_time"] <= int((real_now + timedelta(minutes=35)).timestamp())
    assert row["order_id"] == "o1" and row["status"] == "resting"


def test_candidates_must_be_fresh(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"at": core.iso(NOW - timedelta(minutes=45)), "candidates": [cand()]}))
    assert core.load_candidates(NOW, p) == ([], f"candidates are stale ({core.iso(NOW - timedelta(minutes=45))})")


def test_lab_exporter_tags_sharp_and_keeps_pregame_only():
    from research_lab import maker_candidates as mc
    from research_lab.flow import Flow
    pk = lambda t, starts: SimpleNamespace(settle_ticker=t, settle_side="YES", starts=starts, event="E" + t,
                                           label=t, game="g", league="NCAA", sport="football",
                                           market="moneyline", fair=0.6)
    markets = {t: {"status": "active", "yes_bid": 0.5, "yes_ask": 0.58, "event_ticker": "E"} for t in "ABC"}
    flows = {("A", "YES"): Flow("A", "YES", trades=40, notional=900, market_notional=1000, latest=NOW)}
    rows = mc.build([pk("A", NOW + timedelta(hours=2)), pk("B", NOW + timedelta(minutes=5)),
                     pk("C", NOW + timedelta(hours=1))], markets, flows, NOW)
    assert [r["ticker"] for r in rows] == ["A", "C"] and rows[0]["sharp"] and rows[1]["sharp"] is None
