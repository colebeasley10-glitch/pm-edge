from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import approval_guardrails
import watcher
from pmedge.settings import Settings


NOW = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)


def order(ticker, side, cost=2.0):
    price = 0.5
    count = cost / price
    return {
        "client_order_id": "pmedge-test",
        "ticker": ticker,
        "outcome_side": side.lower(),
        "initial_count_fp": str(count),
        "yes_price_dollars": str(price if side == "YES" else 1 - price),
        "no_price_dollars": str(price if side == "NO" else 1 - price),
        "created_time": "2026-09-21T14:00:00Z",
    }


def pos(ticker, side):
    return {"ticker": ticker, "position_fp": "2.00" if side == "YES" else "-2.00"}


def settings():
    return Settings()


def main():
    assert approval_guardrails.event_key("KXMLBGAME-26SEP171235MILPIT-MIL") == "26SEP171235MILPIT"
    assert approval_guardrails.event_key("KXMLBSPREAD-26SEP171235MILPIT-MIL3") == "26SEP171235MILPIT"

    p = [pos("KXMLBSPREAD-26SEP171235MILPIT-MIL3", "YES")]
    g = approval_guardrails.evaluate_position_guard(
        ticker="KXMLBSPREAD-26SEP171235MILPIT-MIL5", side="YES", candidate_risk=2,
        positions=p, orders=[order(p[0]["ticker"], "YES")], now=NOW,
    )
    assert g["blocked"] and g["reason"] == "second_ladder_rung"

    same = approval_guardrails.evaluate_position_guard(
        ticker=p[0]["ticker"], side="YES", candidate_risk=2,
        positions=p, orders=[order(p[0]["ticker"], "YES")], now=NOW,
    )
    assert same["blocked"] is False

    sport_positions = [
        pos("KXNFLGAME-E1-A", "YES"), pos("KXNFLGAME-E2-A", "YES"), pos("KXNFLGAME-E3-A", "YES")
    ]
    sport_orders = [order(x["ticker"], "YES", 2) for x in sport_positions]
    no_count_cap = approval_guardrails.evaluate_position_guard(
        ticker="KXNFLGAME-E4-A", side="YES", candidate_risk=2,
        positions=sport_positions, orders=sport_orders, now=NOW,
    )
    assert no_count_cap["blocked"] is False
    assert no_count_cap["sport_open_positions"] == 3

    # Per-sport position count and dollar exposure are intentionally uncapped.
    # The separate $75 total open-risk cap still applies across PM Edge.
    one_sport_position = [pos("KXNFLGAME-E1-A", "YES")]
    one_sport_orders = [order("KXNFLGAME-E1-A", "YES", 9.5)]
    no_sport_dollar_cap = approval_guardrails.evaluate_position_guard(
        ticker="KXNFLGAME-E2-A", side="YES", candidate_risk=9.52,
        positions=one_sport_position, orders=one_sport_orders, now=NOW,
    )
    assert no_sport_dollar_cap["blocked"] is False

    daily_cap = approval_guardrails.evaluate_position_guard(
        ticker="KXNFLGAME-E5-A", side="YES", candidate_risk=70.0,
        positions=sport_positions, orders=sport_orders, now=NOW,
    )
    assert daily_cap["blocked"] is True
    assert daily_cap["reason"] == "daily_open_risk_cap"

    market = {"ticker":"T","yes_bid":0.20,"yes_ask":0.21,"no_bid":0.79,"no_ask":0.80}
    fair = {"fair_yes_prob":0.50,"n_books":4}
    q = watcher.score_market(market, fair, settings())
    assert q.qualifies is False and q.reason == "implausible_edge"

    market = {"ticker":"T","yes_bid":None,"yes_ask":0.45,"no_bid":0.55,"no_ask":0.56}
    q = watcher.score_market(market, fair, settings())
    assert q.qualifies is False and q.reason == "degenerate_book"

    market = {"ticker":"T","yes_bid":0.30,"yes_ask":0.45,"no_bid":0.55,"no_ask":0.70}
    wide_fair = {"fair_yes_prob":0.42,"n_books":4}
    q = watcher.score_market(market, wide_fair, settings())
    assert q.qualifies is False and q.reason == "wide_spread"

    with TemporaryDirectory() as td:
        hist = Path(td) / "history.json"
        hist.write_text(json.dumps({"version":1,"records":[
            {"net_pl":-7.0,"filled_shares":1,"recorded_at":f"2026-09-{i:02d}T12:00:00Z"}
            for i in range(1,21)
        ]}), encoding="utf-8")
        state = {}
        assert watcher.drawdown_circuit_active(state, history_path=hist, daily_risk_budget=40.0) is True
        assert state["drawdown_circuit"]["active"] is True
        hist.write_text(json.dumps({"version":1,"records":[]}), encoding="utf-8")
        assert watcher.drawdown_circuit_active(state, history_path=hist, daily_risk_budget=40.0) is True

    print("PM Edge Commit 5 guard tests: PASS")


if __name__ == "__main__":
    main()
