"""Research Lab tests. Isolated temp data only; production ledgers are never touched."""
from __future__ import annotations

import csv
import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pmedge.devig import devig_power, devig_proportional, devig_shin
from research_lab import config, metrics, portfolio
from research_lab.context import RunContext
from research_lab.fills import contracts_for_stake, simulate_buy, taker_fee
from research_lab.flow import aggregate
from research_lab.ledger import LedgerError, Store, iso, run_lock
from research_lab.public_data import Book, PublicDataError
from research_lab.run import reconcile, run_round
from research_lab.settle import settle_store
from research_lab.snapshot import FairQuote, load_snapshot
from research_lab.strategies import structural, value

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(hours=3)


# ---------------------------------------------------------------- fakes

def market(ticker="KXNCAAFGAME-26OCT17UGAGT-UGA", title="Georgia at Georgia Tech: Georgia wins", yes_ask=0.50,
           yes_bid=0.48, event="KXNCAAFGAME-26OCT17UGAGT", **kw):
    m = {"ticker": ticker, "event_ticker": event, "title": title, "subtitle": "", "yes_sub_title": "Georgia",
         "rules_primary": "If Georgia wins the game, including overtime, resolves Yes.", "rules_secondary": "",
         "close_time": iso(KICK + timedelta(days=2)), "occurrence_datetime": iso(KICK), "status": "active",
         "result": "", "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": round(1 - yes_ask, 4),
         "no_ask": round(1 - yes_bid, 4), "expected_expiration_time": iso(KICK + timedelta(hours=4))}
    m.update(kw)
    return m


def book(yes_bids=((0.48, 100),), no_bids=((0.50, 100),), at=NOW):
    return Book(list(yes_bids), list(no_bids), at)


def quote(ticker="KXNCAAFGAME-26OCT17UGAGT-UGA", fair_yes=0.60, **kw):
    q = dict(ticker=ticker, title="Georgia at Georgia Tech: Georgia wins", event_id="e1", commence_time=KICK,
             sport_key="americanfootball_ncaaf", sport_title="NCAAF", market_type="h2h", selection="Georgia",
             point=None, fair_yes_power=fair_yes, fair_yes_prop=fair_yes - 0.005, fair_yes_shin=fair_yes + 0.003,
             books=5, dispersion=0.01, fair_at=NOW - timedelta(minutes=20), evaluated_at=NOW - timedelta(minutes=5),
             matcher_pass=True, matcher_reason="ok")
    q.update(kw)
    return FairQuote(**q)


class FakeKalshi:
    def __init__(self, markets=None, books=None, trades=None, events=None, fail_books=False):
        self._markets = markets or {}
        self._books = books or {}
        self._trades = trades or []
        self._events = events or []
        self.fail_books = fail_books
        self.failures = []
        self.book_calls = 0

    def markets(self, tickers):
        return {t: self._markets[t] for t in tickers if t in self._markets}

    def orderbook(self, ticker, depth=20):
        self.book_calls += 1
        if self.fail_books or ticker not in self._books:
            raise PublicDataError("no book")
        return self._books[ticker]

    def trades(self, *, min_ts, page_cap=10):
        return self._trades, True

    def open_events(self, max_events=400):
        return self._events


def ctx_for(q, m, b=None, **kw):
    k = FakeKalshi(markets={m["ticker"]: m}, books={m["ticker"]: b} if b else {})
    return RunContext(now=NOW, kalshi=k, quotes=[q], markets={m["ticker"]: m}, **kw)


# ---------------------------------------------------------------- de-vig / fair value

def test_devig_methods_sum_to_one_and_order_sensibly():
    outcomes = [{"name": "A", "price": 1.6}, {"name": "B", "price": 2.5}]
    for fn in (devig_power, devig_proportional, devig_shin):
        p = fn(outcomes)
        assert abs(sum(p.values()) - 1.0) < 1e-9
        assert p["A"] > p["B"]
    # power and Shin shift probability toward the favourite vs proportional
    assert devig_power(outcomes)["A"] >= devig_proportional(outcomes)["A"] - 1e-9


def test_fair_side_probability():
    q = quote(fair_yes=0.62)
    assert abs(q.fair_side("YES") - 0.62) < 1e-9 and abs(q.fair_side("NO") - 0.38) < 1e-9


# ---------------------------------------------------------------- fees and fills

def test_fee_rounding_is_ceil_to_cent():
    assert taker_fee(0.50, 1) == 0.02          # 0.0175 -> 0.02
    assert taker_fee(0.50, 20) == 0.35         # 0.35 exactly
    assert taker_fee(0.10, 1) == 0.01          # 0.0063 -> 0.01
    assert contracts_for_stake(0.33, 10) == 30


def test_fill_walks_book_and_never_beats_the_ask():
    fill = simulate_buy([(0.50, 5), (0.51, 10)], 12, max_price=0.51)
    assert fill.status == "filled" and fill.contracts == 12
    assert fill.levels == [(0.50, 5), (0.51, 7)]
    assert fill.avg_price >= 0.50


def test_partial_and_unavailable_fills():
    assert simulate_buy([(0.50, 3)], 10, 0.55).status == "partial"
    assert simulate_buy([(0.60, 30)], 10, 0.55).status == "no_fill"
    assert simulate_buy([], 10, 0.55).reason == "no_book_depth"


# ---------------------------------------------------------------- value safeguards

P = config.VALUE_ONLY.params


def test_value_qualifies_with_clean_inputs():
    m = market()
    d = value.evaluate(quote(), ctx_for(quote(), m, book()), P)
    assert d["ok"] and d["side"] == "YES" and d["net_edge"] >= P["min_net_edge"]


def test_wrong_team_match_rejected():
    q = quote(matcher_pass=False, matcher_reason="qualifier_token_mismatch:tech")
    assert value.evaluate(q, ctx_for(q, market(), book()), P)["reason"] == "match_unverified"


def test_spread_and_total_line_mismatch_rejected():
    m = market(title="Georgia at Georgia Tech: Total Points", ticker="KXT-1", yes_sub_title="Over 52.5 points scored",
               rules_primary="Includes overtime.")
    q = quote(ticker="KXT-1", market_type="total", point=49.5)
    assert value.evaluate(q, ctx_for(q, m, book()), P)["reason"] == "line_mismatch"


def test_wrong_event_date_rejected():
    m = market(occurrence_datetime=iso(KICK + timedelta(days=7)))
    assert value.evaluate(quote(), ctx_for(quote(), m, book()), P)["reason"] == "event_date_mismatch"


def test_regulation_only_settlement_rejected():
    m = market(rules_primary="Resolves Yes if Georgia leads at the end of regulation.")
    assert value.evaluate(quote(), ctx_for(quote(), m, book()), P)["reason"] == "settlement_rules_differ"


def test_stale_sportsbook_prices_rejected():
    q = quote(fair_at=NOW - timedelta(hours=3))
    assert value.evaluate(q, ctx_for(q, market(), book()), P)["reason"] == "stale_sportsbook_prices"


def test_kickoff_passed_and_unknown_rejected():
    q = quote(commence_time=NOW - timedelta(minutes=1))
    assert value.evaluate(q, ctx_for(q, market(), book()), P)["reason"] == "kickoff_passed"
    q = quote(commence_time=None)
    assert value.evaluate(q, ctx_for(q, market(), book()), P)["reason"] == "kickoff_unknown"


def test_missing_book_depth_and_unavailable_book_rejected():
    m = market()
    assert value.evaluate(quote(), ctx_for(quote(), m, book(no_bids=())), P)["reason"] == "no_book_depth"
    assert value.evaluate(quote(), ctx_for(quote(), m, None), P)["reason"] == "order_book_unavailable"


def test_suspicious_edge_rejected_and_small_edge_rejected():
    assert value.evaluate(quote(fair_yes=0.90), ctx_for(quote(fair_yes=0.90), market(), book()),
                          P)["reason"] == "suspicious_edge_review"
    assert value.evaluate(quote(fair_yes=0.52), ctx_for(quote(fair_yes=0.52), market(), book()),
                          P)["reason"] == "edge_below_min"


def test_hybrid_needs_flow_on_same_side():
    m = market()
    trades = [{"ticker": m["ticker"], "taker_side": "yes", "count_fp": "100", "yes_price_dollars": "0.50",
               "created_time": iso(NOW - timedelta(minutes=10))} for _ in range(6)]
    c = ctx_for(quote(), m, book())
    ok, why, _ = value.flow_check(c, m["ticker"], "YES", config.HYBRID_SHARP_VALUE.params)
    assert not ok and why == "no_recent_flow"
    c.flows = aggregate(trades, NOW - timedelta(minutes=45))
    ok, why, info = value.flow_check(c, m["ticker"], "YES", config.HYBRID_SHARP_VALUE.params)
    assert ok and info["flow_trades"] == 6 and info["flow_bucket"] == "5-9"
    ok, why, _ = value.flow_check(c, m["ticker"], "NO", config.HYBRID_SHARP_VALUE.params)
    assert not ok


# ---------------------------------------------------------------- portfolio limits / duplicates

def test_bankroll_and_exposure_limits(tmp_path):
    st = Store(config.VALUE_ONLY.key, tmp_path)
    ok, _ = portfolio.check_limits(st, event="E", ticker="T", committed=9.0, now=NOW)
    assert ok
    st.positions.append({"position_id": "x", "ticker": "T0", "event_ticker": "E", "status": "open",
                         "committed": 10.0, "opened_at": iso(NOW)})
    assert portfolio.check_limits(st, event="E", ticker="T", committed=5, now=NOW)[1] == "already_holding_event"
    assert portfolio.check_limits(st, event="F", ticker="T0", committed=5, now=NOW)[1] == "already_holding_market"
    for i in range(4):
        st.positions.append({"position_id": f"d{i}", "ticker": f"D{i}", "event_ticker": f"DE{i}",
                             "status": "won", "committed": 10.0, "pl": 0.0, "opened_at": iso(NOW)})
    assert portfolio.check_limits(st, event="G", ticker="T9", committed=5.0, now=NOW)[1] == "daily_exposure_cap"
    st2 = Store(config.HYBRID_SHARP_VALUE.key, tmp_path)
    st2.positions.append({"position_id": "big", "ticker": "B", "event_ticker": "BE", "status": "lost",
                          "committed": 495.0, "pl": -495.0, "opened_at": iso(NOW - timedelta(days=3))})
    assert portfolio.check_limits(st2, event="G", ticker="T9", committed=9.0, now=NOW)[1] == "bankroll_insufficient"


def test_full_value_round_opens_once_and_no_duplicates(tmp_path):
    m = market()
    k = FakeKalshi(markets={m["ticker"]: m}, books={m["ticker"]: book()})
    snap = _write_snapshot(tmp_path, [quote()])
    out = run_round(now=NOW, kalshi=k, base=tmp_path / "data", snapshot_path=snap, send_report=False)
    assert out["strategies"][config.VALUE_ONLY.key]["filled"] == 1
    # a re-run in the same bucket (e.g. a retried job) cannot double it
    out2 = run_round(now=NOW + timedelta(minutes=2), kalshi=k, base=tmp_path / "data", snapshot_path=snap,
                     send_report=False)
    assert out2["strategies"][config.VALUE_ONLY.key]["filled"] == 0
    st = Store(config.VALUE_ONLY.key, tmp_path / "data")
    assert len(st.positions) == 1 and st.positions[0]["status"] == "open"
    assert out2["reconciled"] is True
    # hybrid had no flow: nothing opened, rejection recorded
    hy = Store(config.HYBRID_SHARP_VALUE.key, tmp_path / "data")
    assert hy.positions == [] and hy.stats["rejections"].get("no_recent_flow")


# ---------------------------------------------------------------- settlement

def _open_pos(**kw):
    p = {"position_id": "p1", "ticker": "T", "event_ticker": "E", "side": "YES", "contracts": 20,
         "entry_price": 0.5, "cost": 10.0, "fee": 0.35, "committed": 10.35, "status": "open",
         "commence_time": iso(KICK), "opened_at": iso(NOW), "legs": ""}
    p.update(kw)
    return p


def test_settlement_win_loss_void_unknown(tmp_path):
    st = Store("S", tmp_path)
    st.positions = [_open_pos(position_id="a", ticker="A"), _open_pos(position_id="b", ticker="B", side="NO"),
                    _open_pos(position_id="c", ticker="C"), _open_pos(position_id="d", ticker="D")]
    mk = {"A": {"result": "yes", "status": "settled"}, "B": {"result": "yes", "status": "settled"},
          "C": {"result": "", "status": "voided"}, "D": {"result": "", "status": "closed"}}
    settle_store(st, mk, NOW)
    by = {p["position_id"]: p for p in st.positions}
    assert by["a"]["status"] == "won" and by["a"]["pl"] == round(20 - 10.35, 2)
    assert by["b"]["status"] == "lost" and by["b"]["pl"] == -10.35
    assert by["c"]["status"] == "void" and by["c"]["pl"] == 0.0
    assert by["d"]["status"] == "open"  # unknown result: pending, never guessed


def test_closing_line_only_marked_before_kickoff(tmp_path):
    st = Store("S", tmp_path)
    st.positions = [_open_pos()]
    m = {"T": {"yes_bid": 0.55, "yes_ask": 0.57, "status": "active", "result": ""}}
    settle_store(st, m, NOW)
    assert st.positions[0]["last_mid"] == 0.56
    m["T"].update({"yes_bid": 0.90, "yes_ask": 0.92})
    settle_store(st, m, KICK + timedelta(minutes=30))
    assert st.positions[0]["last_mid"] == 0.56  # in-game prices are not a closing line


# ---------------------------------------------------------------- structural arbitrage

def ev_ladder(lo_yes_ask=0.36, hi_yes_bid=0.45):
    lo = market(ticker="KXHIGH-A-T80", title="High above 80", event="KXHIGH-A", strike_type="greater",
                floor_strike=80, yes_ask=lo_yes_ask, yes_bid=lo_yes_ask - 0.02)
    hi = market(ticker="KXHIGH-A-T85", title="High above 85", event="KXHIGH-A", strike_type="greater",
                floor_strike=85, yes_ask=hi_yes_bid + 0.02, yes_bid=hi_yes_bid)
    return {"event_ticker": "KXHIGH-A", "title": "High temp", "category": "Weather", "mutually_exclusive": False,
            "markets": [lo, hi]}


def test_ladder_relationship_and_locked_profit():
    ev = ev_ladder()
    pairs = structural.ladder_pairs(ev)
    assert len(pairs) == 1
    legs = [(m["ticker"], s) for m, s in pairs[0]["legs"]]
    assert legs == [("KXHIGH-A-T80", "YES"), ("KXHIGH-A-T85", "NO")]
    assert structural.quote_profit(pairs[0]) > 0.02   # 1 - 0.36 - 0.55 - fees
    assert structural.quote_profit(structural.ladder_pairs(ev_ladder(0.50, 0.45))[0]) < 0


def test_ladder_needs_same_expiry():
    ev = ev_ladder()
    ev["markets"][1]["expected_expiration_time"] = iso(KICK + timedelta(days=1))
    assert structural.ladder_pairs(ev) == []


def test_no_basket_requires_mutual_exclusivity():
    ms = [market(ticker=f"KXW-{i}", event="KXW", yes_ask=0.30, yes_bid=0.28) for i in range(3)]
    ev = {"event_ticker": "KXW", "title": "Winner", "markets": ms, "mutually_exclusive": False}
    assert structural.no_basket(ev, 8) is None
    ev["mutually_exclusive"] = True
    c = structural.no_basket(ev, 8)
    assert c["guaranteed"] == 2 and len(c["legs"]) == 3


def test_multi_leg_fill_needs_every_leg_and_same_snapshot():
    ev = ev_ladder()
    c = structural.ladder_pairs(ev)[0]
    books_ok = {"KXHIGH-A-T80": book(no_bids=((0.64, 50),)), "KXHIGH-A-T85": book(yes_bids=((0.45, 50),))}
    ctx = RunContext(now=NOW, kalshi=FakeKalshi(books=books_ok))
    decision, reason, info = structural.execute(c, ctx, 0.02)
    assert decision == "FILLED" and info["units"] >= 1
    # one leg has no depth -> no completed arbitrage
    thin = {"KXHIGH-A-T80": book(no_bids=((0.64, 50),)), "KXHIGH-A-T85": book(yes_bids=())}
    decision, reason, _ = structural.execute(c, RunContext(now=NOW, kalshi=FakeKalshi(books=thin)), 0.02)
    assert decision == "NO_FILL" and reason == "leg_missing_ask"
    # books fetched too far apart -> not simultaneous
    apart = {"KXHIGH-A-T80": book(no_bids=((0.64, 50),)),
             "KXHIGH-A-T85": book(yes_bids=((0.45, 50),), at=NOW + timedelta(minutes=2))}
    decision, reason, _ = structural.execute(c, RunContext(now=NOW, kalshi=FakeKalshi(books=apart)), 0.02)
    assert decision == "NO_FILL" and reason == "legs_not_simultaneous"


def test_multi_leg_settlement_with_void_leg(tmp_path):
    st = Store("S", tmp_path)
    legs = [{"ticker": "L1", "side": "YES", "contracts": 10, "cost": 4.0, "fee": 0.17, "outcome": None},
            {"ticker": "L2", "side": "NO", "contracts": 10, "cost": 5.5, "fee": 0.18, "outcome": None}]
    st.positions = [_open_pos(ticker="L1|L2", cost=9.5, fee=0.35, committed=9.85, legs=json.dumps(legs))]
    settle_store(st, {"L1": {"result": "yes", "status": "settled"}}, NOW)
    assert st.positions[0]["status"] == "open"  # one leg unknown -> pending
    settle_store(st, {"L1": {"result": "yes"}, "L2": {"result": "", "status": "voided"}}, NOW)
    p = st.positions[0]
    assert p["payout"] == round(10 + 5.5 + 0.18, 2) and p["status"] == "won"


# ---------------------------------------------------------------- concurrency, recovery, metrics

def _hold_lock(base, q):
    with run_lock(Path(base)):
        q.put("held")
        import time
        time.sleep(1.5)


def test_concurrent_runs_are_blocked(tmp_path):
    q = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_hold_lock, args=(str(tmp_path), q))
    proc.start()
    assert q.get(timeout=5) == "held"
    with pytest.raises(LedgerError):
        with run_lock(tmp_path):
            pass
    proc.join()


def test_restart_recovers_from_saved_files_and_rejects_corrupt_ledger(tmp_path):
    st = Store("S", tmp_path)
    st.positions = [_open_pos()]
    st.save(NOW)
    assert not list((tmp_path / "S").glob("*.tmp"))
    assert Store("S", tmp_path).positions[0]["position_id"] == "p1"
    (tmp_path / "S" / "stats.json").write_text("{not json")
    with pytest.raises(LedgerError):
        Store("S", tmp_path)


def test_metrics_common_definitions():
    rows = [{"status": "won", "cost": 5, "fee": 0.2, "committed": 5.2, "pl": 4.8, "fair": 0.6,
             "settled_at": "2026-10-17T01:00:00Z", "entry_price": 0.5, "net_edge": 0.04},
            {"status": "lost", "cost": 5, "fee": 0.2, "committed": 5.2, "pl": -5.2, "fair": 0.6,
             "settled_at": "2026-10-17T02:00:00Z", "entry_price": 0.5, "net_edge": 0.04},
            {"status": "open", "cost": 5, "fee": 0.2, "committed": 5.2}]
    s = metrics.summarize(rows)
    assert (s["won"], s["lost"], s["open"]) == (1, 1, 1)
    assert s["net_pl"] == -0.4 and abs(s["roi"] - (-0.4 / 10.4)) < 1e-9
    assert s["bankroll"] == 499.6 and s["max_drawdown"] == 5.2
    assert abs(s["brier"] - (0.16 + 0.36) / 2) < 1e-9


def test_reconcile_flags_bad_ledger(tmp_path):
    st = Store("S", tmp_path)
    st.positions = [_open_pos(), _open_pos()]
    assert any("duplicate" in x for x in reconcile(st))


def test_snapshot_reads_only_fresh_scored_rows(tmp_path):
    path = _write_snapshot(tmp_path, [quote(), quote(ticker="OLD", evaluated_at=NOW - timedelta(hours=2))])
    qs, info = load_snapshot(NOW, path)
    assert [q.ticker for q in qs] == [quote().ticker] and info["tickers"] == 1


def test_sharp_benchmark_is_read_only(tmp_path):
    path = tmp_path / "league.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["trade_id", "strategy", "status", "cost", "fee", "pl", "opened_at"])
        w.writeheader()
        w.writerow({"trade_id": "s1", "strategy": "sharp", "status": "won", "cost": 9, "fee": 0.3, "pl": 0.7,
                    "opened_at": "2026-10-16T00:00:00Z"})
        w.writerow({"trade_id": "w1", "strategy": "weather", "status": "lost", "cost": 9, "fee": 0.3, "pl": -9.3,
                    "opened_at": "2026-10-16T00:00:00Z"})
    before = path.read_bytes()
    s = metrics.sharp_summary(path)
    assert s["won"] == 1 and s["trades"] == 1 and s["bankroll"] is None
    assert path.read_bytes() == before


def _write_snapshot(tmp_path, quotes) -> Path:
    path = tmp_path / "snapshot.csv"
    fields = ["ticker", "market_title", "event_id", "commence_time", "sport_key", "sport_title", "market_type",
              "selection", "point", "fair_power", "fair_proportional", "fair_shin", "books", "dispersion",
              "fair_fetched_at", "evaluated_at", "matcher_shadow_pass", "matcher_shadow_reason", "stage", "side"]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for q in quotes:
            w.writerow({"ticker": q.ticker, "market_title": q.title, "event_id": q.event_id,
                        "commence_time": iso(q.commence_time), "sport_key": q.sport_key,
                        "sport_title": q.sport_title, "market_type": q.market_type, "selection": q.selection,
                        "point": "" if q.point is None else q.point, "fair_power": q.fair_yes_power,
                        "fair_proportional": q.fair_yes_prop, "fair_shin": q.fair_yes_shin, "books": q.books,
                        "dispersion": q.dispersion, "fair_fetched_at": iso(q.fair_at),
                        "evaluated_at": iso(q.evaluated_at), "matcher_shadow_pass": str(q.matcher_pass),
                        "matcher_shadow_reason": q.matcher_reason, "stage": "score", "side": "YES"})
    return path


def test_daily_report_timing_and_content(tmp_path):
    from research_lab import report
    assert report.due_day(datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc), None) is None   # 4pm ET
    assert report.due_day(datetime(2026, 10, 18, 3, 30, tzinfo=timezone.utc), None) == "20261017"  # 11:30pm ET
    assert report.due_day(datetime(2026, 10, 18, 3, 30, tzinfo=timezone.utc), "20261017") is None
    s = metrics.summarize([])
    title, lines = report.build("20261017", {"Value Only": s}, {}, {"snapshot": {"empty": True}, "reconciled": True})
    assert "PAPER" in title and "PAPER" in lines[0]
    assert any("EMPTY" in x for x in lines)


def test_run_with_empty_snapshot_still_settles_and_reports(tmp_path):
    k = FakeKalshi()
    out = run_round(now=NOW, kalshi=k, base=tmp_path / "data", snapshot_path=tmp_path / "missing.csv",
                    send_report=False)
    assert out["snapshot"]["empty"] and out["reconciled"]
    assert all(v.get("ok") for v in out["strategies"].values())
    assert (tmp_path / "data" / "status.json").exists()


# ---------------------------------------------------------------- Pinnacle (v2)

from research_lab import pinnacle  # noqa: E402
from pmedge.market_parse import parse_market  # noqa: E402

PIN_EVENT = {"event_id": 101, "league_name": "NCAA", "home": "Houston", "away": "Oklahoma State",
             "starts": iso(KICK), "periods": {"num_0": {
                 "money_line": {"home": 1.505, "away": 2.68},
                 "spreads": {"-4.5": {"hdp": -4.5, "home": 1.917, "away": 1.952, "max": 4000},
                             "-3.5": {"hdp": -3.5, "home": 1.8, "away": 2.08, "max": 2000}},
                 "totals": {"61": {"points": 61, "over": 1.752, "under": 2.09, "max": 1500}}}}}
PIN_PAYLOAD = {"generated_at": iso(NOW - timedelta(minutes=2)), "events": [
    PIN_EVENT, {**PIN_EVENT, "event_id": 102, "league_name": "Canadian Football", "home": "Ottawa", "away": "Toronto"}]}


def kmarket(ticker, title, yes_sub="", **kw):
    return market(ticker=ticker, title=title, yes_sub_title=yes_sub, event="KXNCAAFGAME-26OCT17OKSTHOU",
                  rules_primary="Includes overtime.", occurrence_datetime=iso(KICK), **kw)


def pin_event():
    return pinnacle.events_from(PIN_PAYLOAD, {"NCAA", "NFL"}, NOW)


def test_pinnacle_league_filter_and_parse():
    evs = pin_event()
    assert [e.home for e in evs] == ["Houston"]


def test_pinnacle_moneyline_fair_is_devigged_and_side_correct():
    ev = pin_event()[0]
    spec = parse_market(kmarket("KXNCAAFGAME-X-HOU", "Oklahoma State at Houston: Houston wins", "Houston"))
    probs, why = pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10)
    raw = (1 / 1.505) / (1 / 1.505 + 1 / 2.68)
    assert why == "ok" and abs(probs["proportional"] - raw) < 1e-9 and 0.6 < probs["power"] < 0.7
    spec = parse_market(kmarket("KXNCAAFGAME-X-OKST", "Oklahoma State at Houston: Oklahoma State wins",
                                "Oklahoma State"))
    probs2, _ = pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10)
    assert abs(probs["power"] + probs2["power"] - 1) < 1e-9


def test_pinnacle_spread_uses_exact_line_and_rejects_missing_line():
    ev = pin_event()[0]
    spec = parse_market(kmarket("KXNCAAFSPREAD-X-HOU4", "Oklahoma State at Houston: Houston wins by over 4.5 points"))
    assert spec.market_type == "spread" and spec.point == -4.5
    probs, why = pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10)
    assert why == "ok" and abs(probs["proportional"] - (1 / 1.917) / (1 / 1.917 + 1 / 1.952)) < 1e-9
    spec = parse_market(kmarket("KXNCAAFSPREAD-X-HOU10", "Oklahoma State at Houston: Houston wins by over 10.5 points"))
    assert pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10) == (None, "pinnacle_line_not_offered")
    # away team covering +3.5 is Houston -3.5 from the home side
    spec = parse_market(kmarket("KXNCAAFSPREAD-X-OKST", "Oklahoma State at Houston: Oklahoma State wins by over 3.5 points"))
    assert pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10)[1] == "pinnacle_line_not_offered"


def test_pinnacle_total_and_bad_margin():
    ev = pin_event()[0]
    spec = parse_market(kmarket("KXNCAAFTOTAL-X-61", "Oklahoma State at Houston: Total Points",
                                "Over 61 points scored"))
    probs, why = pinnacle.fair_for(spec, ev, pinnacle.lab_aliases(), 1.10)
    assert why == "ok" and probs["power"] < 0.6
    assert pinnacle.two_way(1.5, 1.5, 1.10) is None   # 133% book: broken data, rejected


def test_pinnacle_fetch_respects_budget_and_refetch_gap(tmp_path):
    class Fake:
        key, calls = "k", 0

        def fixtures(self, sid):
            Fake.calls += 1
            return PIN_PAYLOAD
    out, info = pinnacle.fetch_due(Fake(), NOW, tmp_path)
    assert set(out) == {5, 3, 4} and info["requests_today"] == 3          # max 3 sports per round
    out, info = pinnacle.fetch_due(Fake(), NOW + timedelta(minutes=10), tmp_path)
    assert set(out) == {6, 1, 2}                                           # the rest go next round
    out, info = pinnacle.fetch_due(Fake(), NOW + timedelta(minutes=20), tmp_path)
    assert out == {} and Fake.calls == 6                                   # nothing due yet
    out, info = pinnacle.fetch_due(Fake(), NOW + timedelta(minutes=50), tmp_path)
    assert set(out) == {5}                                                 # football every 45 min
    u = pinnacle.load_usage(NOW, tmp_path)
    u["requests"] = pinnacle.DAILY_BUDGET
    pinnacle.save_usage(u, tmp_path)
    out, info = pinnacle.fetch_due(Fake(), NOW + timedelta(hours=3), tmp_path)
    assert out == {} and any("budget" in x for x in info["skipped"])
    out, info = pinnacle.fetch_due(type("NoKey", (), {"key": ""})(), NOW, tmp_path / "nk")
    assert out == {} and info["errors"] == ["PINNWIRE_KEY is not set"]


def test_daily_schedule_fits_free_plan():
    per_day = sum(24 * 60 / spec[3] for spec in pinnacle.SPORTS.values())
    assert per_day <= 100


def test_v2_round_end_to_end_with_pinnacle(tmp_path):
    hou = kmarket("KXNCAAFGAME-26OCT17OKSTHOU-HOU", "Oklahoma State at Houston: Houston wins", "Houston",
                  yes_ask=0.58, yes_bid=0.56)

    class FakeSeriesKalshi(FakeKalshi):
        def series_events(self, series):
            return [{"event_ticker": hou["event_ticker"], "markets": [hou]}] if series == "KXNCAAFGAME" else []

    class FakePin:
        key = "k"

        def fixtures(self, sid):
            return PIN_PAYLOAD if sid == 5 else {"events": []}
    k = FakeSeriesKalshi(markets={hou["ticker"]: hou},
                         books={hou["ticker"]: book(yes_bids=((0.56, 100),), no_bids=((0.42, 100),))})
    out = run_round(now=NOW, kalshi=k, base=tmp_path / "data", snapshot_path=tmp_path / "none.csv",
                    send_report=False, pinn=FakePin())
    assert out["pinnacle"]["matched"] >= 1
    v2 = out["strategies"][config.VALUE_ONLY_V2.key]
    assert v2["filled"] == 1, out
    st = Store(config.VALUE_ONLY_V2.key, tmp_path / "data")
    p = st.positions[0]
    assert p["side"] == "YES" and p["category"] == "NCAA" and float(p["fair"]) > 0.62
    # v1 (scanner-priced) saw nothing and opened nothing: ledgers stay separate
    assert Store(config.VALUE_ONLY.key, tmp_path / "data").positions == []
    assert out["reconciled"]


def test_current_kalshi_title_format_gets_matchup_and_matches_pinnacle():
    from research_lab.public_data import with_matchup
    t = with_matchup("Oklahoma St. vs Houston", "Houston wins")
    assert t == "Oklahoma St. vs Houston: Houston wins"
    assert with_matchup("A vs B", "A vs B: A wins") == "A vs B: A wins"
    m = kmarket("KXNCAAFGAME-26OCT17OKSTHOU-HOU", t, "Houston")
    ev = pin_event()[0]
    spec = parse_market(m)
    assert spec.market_type == "h2h" and spec.participant_a and spec.participant_b
    quotes, _, info = pinnacle.build_quotes({5: PIN_PAYLOAD}, {5: [{"markets": [m]}]}, NOW)
    assert info["matched"] == 1 and quotes[0].fair_side("YES") > 0.6
    sp = kmarket("KXNCAAFSPREAD-26OCT17OKSTHOU-HOU4", with_matchup("Oklahoma St. vs Houston",
                 "Houston wins by over 4.5 points"))
    quotes, _, info = pinnacle.build_quotes({5: PIN_PAYLOAD}, {5: [{"markets": [sp]}]}, NOW)
    assert info["matched"] == 1 and quotes[0].market_type == "spread" and quotes[0].point == -4.5


# ---------------------------------------------------------------- +EV Finder

from research_lab import ev_data  # noqa: E402
from research_lab.strategies.ev_finder import run_ev_finder  # noqa: E402


def _games():
    evs = pinnacle.events_from(PIN_PAYLOAD, {"NCAA"}, NOW)
    return ev_data.games_from_pinnacle({5: evs}, NOW, 1.10)


def test_american_odds_format():
    assert ev_data.american(1.909) == "-110" and ev_data.american(2.5) == "+150"


def test_ev_game_fair_line_from_pinnacle():
    g = _games()[0]
    assert abs(g.fair["home"] + g.fair["away"] - 1) < 1e-9 and g.fair["home"] > 0.6


def test_books_and_polymarket_attach_to_right_team():
    games = _games()
    snap = {"americanfootball_ncaaf": [{"home_team": "Houston Cougars", "away_team": "Oklahoma State Cowboys",
                                        "commence_time": iso(KICK), "bookmakers": [
        {"key": "draftkings", "last_update": iso(NOW - timedelta(minutes=10)), "markets": [
            {"key": "h2h", "outcomes": [{"name": "Houston Cougars", "price": 1.80},
                                        {"name": "Oklahoma State Cowboys", "price": 2.05}]}]},
        {"key": "williamhill_us", "markets": []}]}]}
    assert ev_data.add_books(games, snap, NOW) == 2
    poly = [{"markets": [{"sportsMarketType": "moneyline", "outcomes": '["Oklahoma State", "Houston"]',
                          "bestBid": "0.36", "bestAsk": "0.37", "gameStartTime": iso(KICK)}]}]
    assert ev_data.add_polymarket(games, poly, NOW, 0.0) == 2
    g = games[0]
    dk_home = [o for o in g.offers if o.venue == "DraftKings" and o.side == "home"][0]
    assert dk_home.decimal == 1.80 and dk_home.shown == "-125"
    poly_away = [o for o in g.offers if o.venue == "Polymarket" and o.side == "away"][0]
    assert abs(poly_away.decimal - 1 / 0.37) < 1e-9
    poly_home = [o for o in g.offers if o.venue == "Polymarket" and o.side == "home"][0]
    assert abs(poly_home.decimal - 1 / 0.64) < 1e-9      # Houston ask = 1 - 0.36 bid


def test_ev_finder_paper_bets_best_price_and_settles_from_kalshi(tmp_path):
    games = _games()
    g = games[0]
    g.settle = {"home": "KXNCAAFGAME-X-HOU", "away": "KXNCAAFGAME-X-OKST"}
    fair_away = g.fair["away"]
    big = (1 + 0.06) / fair_away                     # 6% EV on Oklahoma State
    g.offers = [ev_data.Offer("DraftKings", "away", big, ev_data.american(big), NOW),
                ev_data.Offer("Bovada", "away", 1.5, "-200", NOW),
                ev_data.Offer("Fliff", "home", 1.40, "-250", NOW)]
    st = Store(config.EV_FINDER.key, tmp_path)
    ctx = RunContext(now=NOW, kalshi=FakeKalshi(), ev_games=games)
    res = run_ev_finder(config.EV_FINDER, st, ctx)
    assert res["filled"] == 1 and res["qualified"] == 1
    p = st.positions[0]
    assert p["ticker"] == "KXNCAAFGAME-X-OKST" and "DraftKings" in p["reason"]
    assert abs(float(p["net_edge"]) - 0.06) < 1e-3 and p["committed"] == 10.0
    settle_store(st, {"KXNCAAFGAME-X-OKST": {"result": "yes", "status": "settled"}}, NOW + timedelta(days=1))
    assert st.positions[0]["status"] == "won" and abs(st.positions[0]["pl"] - round(10 * big - 10, 2)) < 0.01
    assert reconcile(st) == []


def test_ev_finder_rejects_stale_and_suspicious_prices(tmp_path):
    g = _games()[0]
    g.settle = {"away": "T"}
    g.offers = [ev_data.Offer("Bovada", "away", 1.06 / g.fair["away"], "+x", NOW - timedelta(hours=5))]
    st = Store(config.EV_FINDER.key, tmp_path)
    res = run_ev_finder(config.EV_FINDER, st, RunContext(now=NOW, kalshi=FakeKalshi(), ev_games=[g]))
    assert res["filled"] == 0 and res["qualified"] == 0          # 5h-old price ignored
    g.offers = [ev_data.Offer("Bovada", "away", 1.50 / g.fair["away"], "+x", NOW)]
    res = run_ev_finder(config.EV_FINDER, st, RunContext(now=NOW, kalshi=FakeKalshi(), ev_games=[g]))
    assert res["filled"] == 0 and st.stats["rejections"]["suspicious_ev_review"] == 1


def test_arbitrage_detected_across_venues(tmp_path):
    g = _games()[0]
    g.offers = [ev_data.Offer("Kalshi", "home", 1.75, "57¢", NOW), ev_data.Offer("Fliff", "away", 2.70, "+170", NOW)]
    st = Store(config.EV_FINDER.key, tmp_path)
    res = run_ev_finder(config.EV_FINDER, st, RunContext(now=NOW, kalshi=FakeKalshi(), ev_games=[g]))
    assert res["arbs"] == 1 and any(a.startswith("ARB") for a in res["alerts"])
    # same venue both sides is not an arbitrage we'd count
    g.offers = [ev_data.Offer("Fliff", "home", 1.75, "x", NOW), ev_data.Offer("Fliff", "away", 2.70, "x", NOW)]
    assert run_ev_finder(config.EV_FINDER, st, RunContext(now=NOW, kalshi=FakeKalshi(), ev_games=[g]))["arbs"] == 0


def test_book_snapshot_budget_and_cache(tmp_path):
    class Resp:
        status_code = 200
        headers = {"x-requests-last": "1", "x-requests-remaining": "400"}

        def json(self):
            return []

    class Sess:
        calls = 0

        def get(self, url, **kw):
            Sess.calls += 1
            assert kw["params"]["markets"] == "h2h" and "draftkings" in kw["params"]["bookmakers"]
            return Resp()
    snap, info = ev_data.book_snapshot(NOW, tmp_path, session=Sess(), key="k")
    assert info["fresh"] and Sess.calls == 3 and info["credits_today"] == 3
    snap, info = ev_data.book_snapshot(NOW + timedelta(hours=1), tmp_path, session=Sess(), key="k")
    assert not info["fresh"] and Sess.calls == 3                  # not due yet: cached
    snap, info = ev_data.book_snapshot(NOW + timedelta(hours=5), tmp_path, session=Sess(), key="k")
    assert info["fresh"] and Sess.calls == 6                      # second snapshot uses the 6/day budget
    snap, info = ev_data.book_snapshot(NOW + timedelta(hours=9, minutes=50), tmp_path, session=Sess(), key="k")
    assert not info["fresh"] and Sess.calls == 6 and "skipped" in info   # budget used for the day
    Resp.headers = {"x-requests-last": "1", "x-requests-remaining": "10"}
    snap, info = ev_data.book_snapshot(NOW + timedelta(days=1), tmp_path / "low", session=Sess(), key="k")
    snap, info = ev_data.book_snapshot(NOW + timedelta(days=1, hours=5), tmp_path / "low", session=Sess(), key="k")
    assert "skipped" in info                                       # account nearly empty: leave it for the scanner


def test_promo_conversion_math():
    from research_lab.dashboard import promo_conversion
    # $1 bonus at +250 hedged at -300 (decimal 1.333): 2.5 * (1 - 0.75) = 62.5%
    assert abs(promo_conversion(3.5, 4 / 3) - 0.625) < 1e-9


def test_book_snapshot_retries_next_day_after_empty_account(tmp_path):
    class Resp:
        status_code, headers = 401, {"x-requests-last": "0", "x-requests-remaining": "0"}

        def json(self):
            return {}

    class Sess:
        calls = 0

        def get(self, url, **kw):
            Sess.calls += 1
            return Resp()
    ev_data.book_snapshot(NOW, tmp_path, session=Sess(), key="k")
    ev_data.book_snapshot(NOW + timedelta(hours=5), tmp_path, session=Sess(), key="k")
    assert Sess.calls == 3                      # same day: empty account, no more tries
    ev_data.book_snapshot(NOW + timedelta(days=1), tmp_path, session=Sess(), key="k")
    assert Sess.calls == 6                      # next day: checks again in case credits reset


# ---------------------------------------------------------------- Today's Plays (EV_FINDER v2)

from research_lab.strategies.ev_finder import run_plays  # noqa: E402


def test_three_way_soccer_devig():
    ml = {"home": 2.10, "draw": 3.40, "away": 3.60}
    h = pinnacle.three_way(ml, "home", 1.12)
    a = pinnacle.three_way(ml, "away", 1.12)
    assert h and a and 0.40 < h["power"] < 0.50 and h["power"] + a["power"] < 0.80   # draw takes the rest


def test_spread_and_total_picks_have_both_sides(tmp_path):
    games = _games()
    sq = FairQuote(ticker="KXNCAAFSPREAD-X-HOU4", title="t", event_id="101", commence_time=KICK, sport_key="pinnacle_football",
                   sport_title="NCAA", market_type="spread", selection="Houston", point=-4.5, fair_yes_power=0.52,
                   fair_yes_prop=0.52, fair_yes_shin=0.52, books=1, dispersion=None, fair_at=NOW, evaluated_at=NOW,
                   matcher_pass=True, matcher_reason="ok")
    tq = FairQuote(ticker="KXNCAAFTOTAL-X-61", title="t", event_id="101", commence_time=KICK, sport_key="pinnacle_football",
                   sport_title="NCAA", market_type="total", selection="Over", point=61.0, fair_yes_power=0.47,
                   fair_yes_prop=0.47, fair_yes_shin=0.47, books=1, dispersion=None, fair_at=NOW, evaluated_at=NOW,
                   matcher_pass=True, matcher_reason="ok")
    mk = {"KXNCAAFSPREAD-X-HOU4": {"yes_ask": 0.46, "no_ask": 0.56}, "KXNCAAFTOTAL-X-61": {"yes_ask": 0.49, "no_ask": 0.53}}
    picks = ev_data.build_picks(games, [sq, tq], mk, NOW, taker_fee)
    labels = {p.label: p for p in picks}
    assert "Houston −4.5" in labels and "Oklahoma State +4.5" in labels
    assert "Over 61" in labels and "Under 61" in labels
    assert labels["Oklahoma State +4.5"].settle_side == "NO" and abs(labels["Oklahoma State +4.5"].fair - 0.48) < 1e-9
    # Houston -4.5 at 46c vs fair 52%: about +11% EV before fees -> a play; paper bet settles from Kalshi
    st = Store(config.EV_FINDER_V2.key, tmp_path)
    ctx = RunContext(now=NOW, kalshi=FakeKalshi(), picks=[labels["Houston −4.5"], labels["Under 61"]])
    res = run_plays(config.EV_FINDER_V2, st, ctx)
    assert res["filled"] == 1                       # one play per game, best first
    pos = st.positions[0]
    assert pos["side"] in {"YES", "NO"} and pos["ticker"].startswith("KXNCAAF")
    settle_store(st, {pos["ticker"]: {"result": "yes" if pos["side"] == "YES" else "no", "status": "settled"}}, NOW + timedelta(days=1))
    assert st.positions[0]["status"] == "won" and reconcile(st) == []


def test_plays_threshold_is_one_percent(tmp_path):
    g = _games()[0]
    g.settle = {"home": "K-HOU", "away": "K-OKST"}
    for ev, should in ((0.015, 1), (0.005, 0)):
        g.offers = [ev_data.Offer("Kalshi", "away", (1 + ev) / g.fair["away"], "x", NOW)]
        picks = ev_data.build_picks([g], [], {}, NOW, taker_fee)
        st = Store(config.EV_FINDER_V2.key, tmp_path / f"t{ev}")
        assert run_plays(config.EV_FINDER_V2, st, RunContext(now=NOW, kalshi=FakeKalshi(), picks=picks))["filled"] == should


def test_frozen_strategies_keep_original_sports():
    q = lambda sk: FairQuote(ticker="T", title="t", event_id="e", commence_time=KICK, sport_key=sk, sport_title="",
                             market_type="h2h", selection="", point=None, fair_yes_power=.5, fair_yes_prop=.5,
                             fair_yes_shin=.5, books=1, dispersion=None, fair_at=NOW, evaluated_at=NOW,
                             matcher_pass=True, matcher_reason="")
    ctx = RunContext(now=NOW, kalshi=FakeKalshi(), pinnacle_quotes=[q("pinnacle_football"), q("pinnacle_hockey")])
    assert [x.sport_key for x in ctx.quotes_for(config.VALUE_ONLY_V2.params)] == ["pinnacle_football"]


def test_kelly_suggestion_is_capped():
    assert abs(ev_data.kelly_fraction(0.55, 2.0) - 0.025) < 1e-9   # quarter of 10%
    assert ev_data.kelly_fraction(0.80, 2.0) == 0.03           # capped
    assert ev_data.kelly_fraction(0.40, 2.0) == 0.0            # negative EV -> no bet



# ---------------------------------------------------------------- your logged bets + odds band

from research_lab import my_bets  # noqa: E402


def test_american_odds_conversion():
    assert abs(my_bets.american_to_decimal("-110") - 1.9090909) < 1e-6
    assert my_bets.american_to_decimal("+150") == 2.5 and my_bets.american_to_decimal("150") == 2.5
    assert my_bets.american_to_decimal("−200") == 1.5          # unicode minus works
    assert my_bets.american_to_decimal("50") is None and my_bets.american_to_decimal("abc") is None
    assert my_bets.decimal_to_american(2.5) == "+150" and my_bets.decimal_to_american(1.5) == "-200"


def test_logged_bets_settle_from_kalshi(tmp_path):
    rows = [{"bet_id": "a", "label": "Houston ML", "stake": "20", "decimal": "1.8", "settle_ticker": "K1", "settle_side": "YES"},
            {"bet_id": "b", "label": "Under 61", "stake": "10", "decimal": "2.0", "settle_ticker": "K2", "settle_side": "NO"},
            {"bet_id": "c", "label": "Open one", "stake": "5", "decimal": "2.0", "settle_ticker": "K3", "settle_side": "YES"},
            {"bet_id": "d", "label": "Voided", "stake": "7", "decimal": "2.0", "settle_ticker": "K4", "settle_side": "YES"}]
    (tmp_path / my_bets.LOG_NAME).write_text(my_bets.render_log(rows))
    mk = {"K1": {"result": "yes"}, "K2": {"result": "yes"}, "K3": {"result": "", "status": "active"},
          "K4": {"result": "", "status": "voided"}}
    out = my_bets.settle_files(mk, NOW, tmp_path)
    res = my_bets.load_results(tmp_path)
    assert out["settled_now"] == 3 and "c" not in res
    assert res["a"]["status"] == "won" and res["a"]["pl"] == 16.0
    assert res["b"]["status"] == "lost" and res["b"]["pl"] == -10.0     # NO side lost when Kalshi says yes
    assert res["d"]["status"] == "void" and res["d"]["pl"] == 0.0
    s = my_bets.summary(my_bets.load_log(tmp_path), res)
    assert (s["won"], s["lost"], s["pl"], s["open"]) == (1, 1, 6.0, 1)
    assert my_bets.settle_files(mk, NOW, tmp_path)["settled_now"] == 0   # never settles twice
    assert my_bets.open_tickers(tmp_path) == ["K3"]


def test_v3_only_takes_prices_between_minus200_and_plus150(tmp_path):
    g = _games()[0]
    g.settle = {"home": "K-HOU", "away": "K-OKST"}
    cases = ((3.20, 0), (2.40, 1), (1.40, 0))           # +220 out, +140 in, -250 out
    for dec, should in cases:
        g.fair = {"home": 1.05 / dec, "away": 1 - 1.05 / dec}   # +5% EV at that price
        g.offers = [ev_data.Offer("Kalshi", "home", dec, "x", NOW)]
        picks = ev_data.build_picks([g], [], {}, NOW, taker_fee)
        st = Store(config.EV_FINDER_V3.key, tmp_path / f"d{dec}")
        assert run_plays(config.EV_FINDER_V3, st, RunContext(now=NOW, kalshi=FakeKalshi(), picks=picks))["filled"] == should


def test_bet_log_appends_through_github_api():
    import base64
    import bet_log

    class R:
        def __init__(self, code, data=None):
            self.status_code, self._d = code, data or {}

        def json(self):
            return self._d

    class S:
        def __init__(self):
            self.content, self.sha, self.puts = "", None, 0

        def get(self, url, **kw):
            if self.sha is None:
                return R(404)
            return R(200, {"content": base64.b64encode(self.content.encode()).decode(), "sha": self.sha})

        def put(self, url, json=None, **kw):
            self.puts += 1
            if self.puts == 1 and self.sha is None:      # first write creates the file
                pass
            self.content = base64.b64decode(json["content"]).decode()
            self.sha = f"sha{self.puts}"
            return R(201)
    import os
    os.environ["GITHUB_TOKEN"] = "t"
    try:
        s = S()
        bet_log.append_bet({"label": "A", "odds": "-110", "stake": 10}, session=s)
        bet_log.append_bet({"label": "B", "odds": "+120", "stake": 5}, session=s)
        rows = my_bets.parse_log(s.content)
        assert [r["label"] for r in rows] == ["A", "B"] and all(r["bet_id"] for r in rows)
    finally:
        os.environ.pop("GITHUB_TOKEN", None)
