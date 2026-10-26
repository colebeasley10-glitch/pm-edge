"""Kalshi Follow: side detection (taker/maker), pre-game only, thresholds, alerts, settlement."""
from datetime import datetime, timezone

from trader_follow import kalshi_follow as kf

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
ML = "KXNFLGAME-26OCT25CLETEN-CLE"
SPREAD = "KXNFLSPREAD-26OCT25CLETEN-CLE4"
MKTS = {
    ML: {"ticker": ML, "event_ticker": "KXNFLGAME-26OCT25CLETEN", "title": "Cleveland at Tennessee Winner?",
         "yes_sub_title": "Cleveland", "occurrence_datetime": "2026-10-25T17:00:00Z",
         "yes_bid_dollars": "0.52", "yes_ask_dollars": "0.53", "no_bid_dollars": "0.47", "no_ask_dollars": "0.48"},
    SPREAD: {"ticker": SPREAD, "event_ticker": "KXNFLSPREAD-26OCT25CLETEN", "title": "Cleveland wins by over 3.5 points?",
             "yes_sub_title": "Cleveland wins by over 3.5 Points", "occurrence_datetime": "2026-10-25T17:00:00Z",
             "yes_bid_dollars": "0.52", "yes_ask_dollars": "0.53", "no_bid_dollars": "0.47", "no_ask_dollars": "0.48"},
}
T = kf.KalshiTrader("BWmarkets", min_usd=1000.0)


def tr(i, ticker, side, price, count, when="2026-10-25T14:00:00Z", taker="BWmarkets", maker="", action="buy"):
    return {"trade_id": f"t{i}", "ticker": ticker, "price": price, "count_fp": str(count), "taker_side": side,
            "taker_action": action, "taker_nickname": taker, "maker_nickname": maker, "create_date": when}


def run(tmp_path, rounds, now=NOW, markets=MKTS):
    pushes, feed = [], {"t": []}
    out = None
    for trades in rounds:
        feed["t"] = trades
        out = kf.run_round(traders=[T], now=now, get_trades=lambda n: feed["t"], get_market=lambda t: markets.get(t),
                           get_event_title=lambda e: "Cleveland at Tennessee",
                           push=lambda a, b, c, url_title=None: pushes.append((a, b)) or True,
                           state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.csv")
    return out, pushes, kf.load_ledger(tmp_path / "l.csv")


def test_first_look_only_records_history(tmp_path):
    _, pushes, ledger = run(tmp_path, [[tr(1, ML, "yes", 52, 5000)]])
    assert pushes == [] and ledger == []


def test_big_pregame_buy_alerts_with_plain_label(tmp_path):
    seed = [tr(0, ML, "yes", 50, 1)]
    big = seed + [tr(1, SPREAD, "no", 47, 4704)]          # NO on Cleveland -3.5 = Tennessee +3.5
    _, pushes, ledger = run(tmp_path, [seed, big, big])
    assert len(pushes) == 1 and len(ledger) == 1
    row = ledger[0]
    assert row["bet"] == "Tennessee +3.5" and row["side"] == "no"
    assert abs(float(row["his_price"]) - 0.47) < 1e-9 and float(row["copy_price"]) == 0.48
    assert "Tennessee +3.5" in pushes[0][0]


def test_maker_fill_is_the_opposite_side(tmp_path):
    seed = [tr(0, ML, "yes", 50, 1)]
    rest = seed + [tr(1, ML, "yes", 53, 4000, taker="someone", maker="BWmarkets")]   # taker bought YES from him
    _, _, ledger = run(tmp_path, [seed, rest])
    assert ledger and ledger[0]["side"] == "no" and ledger[0]["bet"] == "Tennessee ML"


def test_ingame_buys_and_sells_never_count(tmp_path):
    seed = [tr(0, ML, "yes", 50, 1)]
    later = seed + [tr(1, ML, "yes", 52, 9000, when="2026-10-25T17:30:00Z"),
                    tr(2, ML, "yes", 52, 9000, action="sell")]
    _, pushes, ledger = run(tmp_path, [seed, later])
    assert pushes == [] and ledger == []


def test_settles_from_kalshi_result(tmp_path):
    seed = [tr(0, ML, "yes", 50, 1)]
    big = seed + [tr(1, ML, "yes", 52, 5000)]
    run(tmp_path, [seed, big])
    done = {**MKTS, ML: {**MKTS[ML], "result": "yes"}}
    _, _, ledger = run(tmp_path, [big], now=datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc), markets=done)
    assert ledger[0]["status"] == "won" and float(ledger[0]["pl_copy"]) > 0
