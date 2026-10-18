"""Best Price check: live Sharp pick vs Polymarket (read-only)."""
from __future__ import annotations

import csv
from datetime import datetime, timezone

from research_lab import best_price

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
COLS = ["trade_id", "placed_at", "ticker", "title", "side", "limit_price", "contracts_wanted", "status",
        "filled", "avg_price", "fee"]


def _ledger(tmp_path, rows):
    p = tmp_path / "live.csv"
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return p


def _two_way(a, b, bid, ask, start="2026-10-18 20:00:00+00"):
    return {"title": f"{a} vs {b}", "slug": "x", "markets": [{
        "sportsMarketType": "moneyline", "outcomes": f'["{a}", "{b}"]', "bestBid": bid, "bestAsk": ask,
        "gameStartTime": start, "liquidityNum": 9000}]}


def test_yes_and_no_priced_from_two_way_market(tmp_path):
    src = _ledger(tmp_path, [
        {"trade_id": "a", "placed_at": "2026-10-18T17:50:00Z", "ticker": "KXATPMATCH-26OCT18SINALC-SIN",
         "title": "Jannik Sinner wins", "side": "YES", "limit_price": "0.60", "contracts_wanted": "1",
         "status": "open", "filled": "1", "avg_price": "0.60", "fee": "0.02"},
        {"trade_id": "b", "placed_at": "2026-10-18T17:55:00Z", "ticker": "KXATPMATCH-26OCT18SINALC-ALC",
         "title": "Carlos Alcaraz wins", "side": "NO", "limit_price": "0.55", "contracts_wanted": "1",
         "status": "open", "filled": "1", "avg_price": "0.55", "fee": "0.02"}])
    ev = [_two_way("Jannik Sinner", "Carlos Alcaraz", 0.55, 0.57)]
    info = best_price.record(NOW, tmp_path, src=src, poly_fetch=lambda n: ev)
    assert info["compared"] == 2
    rows = {r["trade_id"]: r for r in best_price.load(tmp_path)}
    assert float(rows["a"]["poly_ask"]) == 0.57 and rows["a"]["best_venue"] == "Polymarket"
    # NO on Alcaraz = Sinner wins = Sinner's ask
    assert float(rows["b"]["poly_ask"]) == 0.57 and rows["b"]["best_venue"] == "Kalshi"
    # running again doesn't duplicate
    best_price.record(NOW, tmp_path, src=src, poly_fetch=lambda n: ev)
    assert len(best_price.load(tmp_path)) == 2


def test_old_picks_spreads_and_misses(tmp_path):
    src = _ledger(tmp_path, [
        {"trade_id": "old", "placed_at": "2026-10-18T10:00:00Z", "ticker": "KXATPMATCH-26OCT18SINALC-SIN",
         "title": "Jannik Sinner wins", "side": "YES", "limit_price": "0.6", "status": "open"},
        {"trade_id": "sp", "placed_at": "2026-10-18T17:50:00Z", "ticker": "KXNCAAFSPREAD-26OCT18NDBYU-ND11",
         "title": "Notre Dame wins by over 10.5 points", "side": "YES", "limit_price": "0.5", "status": "no_fill"},
        {"trade_id": "miss", "placed_at": "2026-10-18T17:50:00Z", "ticker": "KXWTAMATCH-26OCT18ABCDEF-ABC",
         "title": "Nobody Known wins", "side": "YES", "limit_price": "0.5", "status": "open"}])
    info = best_price.record(NOW, tmp_path, src=src,
                             poly_fetch=lambda n: [_two_way("Jannik Sinner", "Carlos Alcaraz", 0.55, 0.57)])
    assert info["new_picks"] == 2 and info["compared"] == 0
    notes = {r["trade_id"]: r["note"] for r in best_price.load(tmp_path)}
    assert notes["sp"].startswith("not compared") and notes["miss"] == "not found on Polymarket"


def test_polymarket_outage_retries_next_round(tmp_path):
    src = _ledger(tmp_path, [
        {"trade_id": "a", "placed_at": "2026-10-18T17:50:00Z", "ticker": "KXATPMATCH-26OCT18SINALC-SIN",
         "title": "Jannik Sinner wins", "side": "YES", "limit_price": "0.6", "status": "open"}])

    def boom(n):
        raise ConnectionError

    assert "error" in best_price.record(NOW, tmp_path, src=src, poly_fetch=boom)
    assert best_price.load(tmp_path) == []
