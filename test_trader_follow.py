from datetime import datetime, timezone

from trader_follow import core

W = "0xf0318c32136c2db7fec88b84869aee6a1106c80c"
T = core.Trader("BreakTheBank", W)

CELTICS = "0xceltics"
BILLS = "0xbills"
POLITICS = "0xpolitics"
BEARS = "0xbears"

MARKETS = {
    CELTICS: {"question": "Celtics vs. Pistons", "gameStartTime": "2026-10-20 23:30:00+00",
              "sportsMarketType": "moneyline", "bestAsk": "0.53", "bestBid": "0.52",
              "outcomes": '["Celtics", "Pistons"]', "outcomePrices": '["0.525", "0.475"]', "closed": False},
    BILLS: {"question": "Bills vs. Raiders", "gameStartTime": "2026-10-18 20:25:00+00",
            "sportsMarketType": "moneyline", "outcomePrices": '["0.7", "0.3"]', "closed": False},
    POLITICS: {"question": "Balance of Power", "outcomePrices": '["0.01", "0.99"]', "closed": False},
    BEARS: {"question": "Bears vs. Falcons", "gameStartTime": "2026-10-20 23:00:00+00",
            "sportsMarketType": "moneyline", "bestAsk": "0.80", "bestBid": "0.79",
            "outcomePrices": '["0.8", "0.2"]', "closed": False},
}


def ts(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def trade(cid, size, price, when, outcome="Celtics", idx=0, side="BUY", tx=None, slug="nba-bos-det-2026-10-20"):
    return {"conditionId": cid, "size": size, "price": price, "timestamp": ts(when), "outcome": outcome,
            "outcomeIndex": idx, "side": side, "transactionHash": tx or f"{cid}{size}{when}",
            "asset": f"{cid}-{idx}", "title": MARKETS[cid]["question"], "eventSlug": slug}


def run(tmp_path, trades_by_round, now, markets=MARKETS):
    pushes = []
    feed = {"t": []}
    out = None
    for trades in trades_by_round:
        feed["t"] = trades
        out = core.run_round(traders=[T], now=now, get_trades=lambda w: feed["t"],
                             get_market=lambda c: markets.get(c), push=lambda a, b, c: pushes.append((a, b)) or True,
                             state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.csv")
    return out, pushes, core.load_ledger(tmp_path / "l.csv")


NOW = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)


def test_first_look_only_records_history(tmp_path):
    old = [trade(CELTICS, 50000, 0.52, "2026-10-20T15:57:00")]
    _, pushes, ledger = run(tmp_path, [old], NOW)
    assert pushes == [] and ledger == []


def test_big_pregame_bet_alerts_once(tmp_path):
    first = [trade(CELTICS, 10, 0.5, "2026-10-19T12:00:00")]
    big = first + [trade(CELTICS, 43285.75, 0.52, "2026-10-20T15:57:35")]
    out, pushes, ledger = run(tmp_path, [first, big, big], NOW)
    assert len(pushes) == 1 and len(ledger) == 1
    row = ledger[0]
    assert row["outcome"] == "Celtics" and row["in_band"] == "True" and row["sport"] == "NBA"
    assert float(row["copy_price"]) == 0.53
    assert "52¢" in pushes[0][1] and "54¢" in pushes[0][1]


def test_split_fills_add_up(tmp_path):
    seed = [trade(CELTICS, 1, 0.5, "2026-10-19T12:00:00")]
    a = seed + [trade(CELTICS, 25000, 0.5, "2026-10-20T15:00:00")]
    b = a + [trade(CELTICS, 20000, 0.52, "2026-10-20T15:10:00")]
    _, pushes, ledger = run(tmp_path, [seed, a, b], NOW)
    assert len(pushes) == 1
    assert 0.5 < float(ledger[0]["his_price"]) < 0.52


def test_live_ingame_buys_never_count(tmp_path):
    seed = [trade(BILLS, 1, 0.5, "2026-10-17T12:00:00", "Bills", slug="nfl-buf-lv-2026-10-18")]
    live = seed + [trade(BILLS, 216757, 0.73, "2026-10-18T21:23:23", "Bills", slug="nfl-buf-lv-2026-10-18")]
    _, pushes, ledger = run(tmp_path, [seed, live], datetime(2026, 10, 18, 21, 25, tzinfo=timezone.utc))
    assert pushes == [] and ledger == []


def test_politics_and_sells_ignored(tmp_path):
    seed = [trade(CELTICS, 1, 0.5, "2026-10-19T12:00:00")]
    later = seed + [trade(POLITICS, 143915, 0.4, "2026-10-20T15:00:00", "Yes", slug="balance-of-power"),
                    trade(CELTICS, 90000, 0.5, "2026-10-20T15:01:00", side="SELL")]
    _, pushes, ledger = run(tmp_path, [seed, later], NOW)
    assert pushes == [] and ledger == []


def test_out_of_band_logged_not_pushed(tmp_path):
    seed = [trade(BEARS, 1, 0.5, "2026-10-19T12:00:00", "Bears", slug="nfl-chi-atl-2026-10-20")]
    fav = seed + [trade(BEARS, 100000, 0.80, "2026-10-20T15:00:00", "Bears", slug="nfl-chi-atl-2026-10-20")]
    _, pushes, ledger = run(tmp_path, [seed, fav], NOW)
    assert pushes == [] and len(ledger) == 1 and ledger[0]["in_band"] == "False"


def test_settles_from_final_prices(tmp_path):
    seed = [trade(CELTICS, 1, 0.5, "2026-10-19T12:00:00")]
    big = seed + [trade(CELTICS, 50000, 0.5, "2026-10-20T15:00:00")]
    run(tmp_path, [seed, big], NOW)
    final = dict(MARKETS)
    final[CELTICS] = {**MARKETS[CELTICS], "closed": True, "outcomePrices": '["1", "0"]'}
    _, _, ledger = run(tmp_path, [big], datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc), final)
    assert ledger[0]["status"] == "won"
    assert float(ledger[0]["pl_copy"]) == round(10 * (1 / 0.53 - 1), 2)
    s = core.summarize(ledger)
    assert any(x["trader"] == "BreakTheBank" and x["wins"] == 1 for x in s)


def test_market_error_retries_next_round(tmp_path):
    seed = [trade(CELTICS, 1, 0.5, "2026-10-19T12:00:00")]
    big = seed + [trade(CELTICS, 50000, 0.5, "2026-10-20T15:00:00")]
    run(tmp_path, [seed], NOW)
    run(tmp_path, [big], NOW, markets={})          # Polymarket hiccup
    _, pushes, ledger = run(tmp_path, [big], NOW)  # next round works
    assert len(pushes) == 1 and len(ledger) == 1


def test_american_odds():
    assert core.american(0.40) == "+150"
    assert core.american(2 / 3) == "-200"
    assert core.in_band(0.52) and not core.in_band(0.80) and not core.in_band(0.30)


def test_by_sport_groups_soccer_and_ranks_by_sample():
    rows = [
        {"trader": "A", "sport": "NFL", "in_band": "True", "status": "won", "pl_copy": "9", "pl_his": "10"},
        {"trader": "A", "sport": "NFL", "in_band": "True", "status": "lost", "pl_copy": "-10", "pl_his": "-10"},
        {"trader": "A", "sport": "EPL", "in_band": "True", "status": "won", "pl_copy": "8", "pl_his": "9"},
        {"trader": "A", "sport": "UCL", "in_band": "True", "status": "open", "pl_copy": "", "pl_his": ""},
        {"trader": "A", "sport": "NBA", "in_band": "False", "status": "won", "pl_copy": "5", "pl_his": "5"},
    ]
    out = core.summarize_by_sport(rows)
    assert [(x["sport"], x["wins"], x["losses"], x["signals"]) for x in out] == [("NFL", 1, 1, 2), ("Soccer", 1, 0, 2)]
    assert out[0]["roi_copy"] == -0.05


def test_sized_lane_scales_flat_result_and_caps():
    from trader_follow import core
    rows = [{"status": "won", "pl_copy": 10.0, "his_usdc": 25000, "trader": "X"},     # 0.5u -> +5
            {"status": "lost", "pl_copy": -10.0, "his_usdc": 400000, "trader": "X"}]  # capped 3u -> -30
    s = core._stats(rows)
    assert s["pl_sized"] == -25.0 and s["staked_sized"] == 35.0
    assert core.sized_units({"his_usdc": 100000}) == 2.0


def _run_with(tmp_path, trader, rounds, now):
    pushes, feed = [], {"t": []}
    for trades in rounds:
        feed["t"] = trades
        core.run_round(traders=[trader], now=now, get_trades=lambda w: feed["t"],
                       get_market=lambda c: MARKETS.get(c), push=lambda a, b, c: pushes.append((a, b)) or True,
                       state_path=tmp_path / "s.json", ledger_path=tmp_path / "l.csv")
    return pushes, core.load_ledger(tmp_path / "l.csv")


def test_outside_his_sports_logged_not_pushed(tmp_path):
    nfl_only = core.Trader("BreakTheBank", W, sports=frozenset({"NFL", "Soccer"}))
    first = [trade(CELTICS, 10, 0.5, "2026-10-19T12:00:00")]
    big = first + [trade(CELTICS, 43285.75, 0.52, "2026-10-20T15:57:35")]
    pushes, ledger = _run_with(tmp_path, nfl_only, [first, big], NOW)
    assert pushes == [] and len(ledger) == 1 and ledger[0]["in_sports"] == "False"
    assert core._alerted(ledger, True) == []


def test_per_trader_minimum(tmp_path):
    small = core.Trader("primm", W, min_usdc=5_000.0)
    first = [trade(CELTICS, 10, 0.5, "2026-10-19T12:00:00")]
    bet = first + [trade(CELTICS, 12000, 0.52, "2026-10-20T15:57:35")]   # ~$6.2k: under 20k, over 5k
    pushes, ledger = _run_with(tmp_path, small, [first, bet], NOW)
    assert len(pushes) == 1 and len(ledger) == 1


def test_bet_label_reads_the_side_he_took():
    assert core.bet_label({"question": "Spread: Seahawks (-2.5)", "outcome": "Chiefs", "market_type": "spreads"}) == "Chiefs +2.5"
    assert core.bet_label({"question": "Spread: Thunder (-16.5)", "outcome": "Thunder", "market_type": "spreads"}) == "Thunder -16.5"
    assert core.bet_label({"question": "Chiefs vs. Seahawks", "outcome": "Chiefs", "market_type": "moneyline"}) == "Chiefs ML"


def test_breakthebank_all_sports_one_dollar_units():
    btb = next(t for t in core.TRADERS if t.name == "BreakTheBank")
    assert btb.sports == frozenset() and btb.unit == 1.0
    assert core.unit_for("BreakTheBank") == 1.0 and core.unit_for("primm") == core.PAPER_STAKE
    rows = [{"trader": "BreakTheBank", "sport": "NBA", "in_band": "True", "status": "won", "pl_copy": "10", "pl_his": "10"},
            {"trader": "BreakTheBank", "sport": "NBA", "in_band": "True", "status": "lost", "pl_copy": "-10", "pl_his": "-10"},
            {"trader": "BreakTheBank", "sport": "MLB", "in_band": "True", "status": "won", "pl_copy": "5", "pl_his": "5"}]
    s = next(x for x in core.summarize(rows) if x["trader"] == "BreakTheBank")
    assert s["units"] == 0.5 and s["pl_unit"] == 0.5


def test_huge_bets_tagged_and_tiered():
    row = {"trader": "BreakTheBank", "his_usdc": 150000, "his_price": 0.5, "copy_price": 0.5, "question": "Chiefs vs. Bills",
           "outcome": "Chiefs", "market_type": "moneyline", "sport": "nfl", "game_start": "2026-10-27T00:00:00Z"}
    title, msg = core.alert_text(row)
    assert title.startswith("🐋 HUGE") and "$1.00 (1 unit)" in msg
    rows = [dict(row, status="won", pl_copy="10", pl_his="10"),
            dict(row, his_usdc=30000, status="lost", pl_copy="-10", pl_his="-10"),
            dict(row, his_usdc=400000, status="open", pl_copy="", pl_his="")]
    tiers = {t["tier"]: t for t in core.summarize_by_stake(rows, "BreakTheBank")}
    assert tiers["$100k–250k"]["wins"] == 1 and tiers["Under $50k"]["losses"] == 1 and tiers["$250k+"]["open"] == 1
    assert [float(r["his_usdc"]) for r in core.biggest_bets(rows, "BreakTheBank")] == [400000, 150000, 30000]
