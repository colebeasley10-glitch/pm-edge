from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone

import clv_tracker
import performance_tracker
from pmedge.fair_value import fair_from_event
from pmedge.market_parse import MarketSpec


class Resp:
    def raise_for_status(self):
        pass
    def json(self):
        return {"market": {"yes_bid_dollars": "0.54", "yes_ask_dollars": "0.56"}}


def getter(*args, **kwargs):
    return Resp()


class FillClient:
    def get_fills(self, *, ticker=None, limit=200):
        return [{
            "fill_id": "fill-1",
            "order_id": "order-1",
            "ticker": "TEST",
            "outcome_side": "yes",
            "count_fp": "2.00",
            "yes_price_dollars": "0.40",
            "fee_cost": "0.03",
            "is_taker": True,
            "created_time": "2026-09-21T14:01:00Z",
        }]


def main():
    spec = MarketSpec(
        ticker="TEST-H2H",
        event_name="Team A vs Team B",
        participant_a="Team A",
        participant_b="Team B",
        market_type="h2h",
        selection="Team A",
        point=None,
        parse_confidence=1.0,
        reason="test",
    )
    event = {"bookmakers": [
        {"key": "good", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Team A", "price": 1.8},{"name": "Team B", "price": 2.1}
        ]}]},
        {"key": "drop", "markets": []},
    ]}
    consensus, diag = fair_from_event(spec, event, {}, 1, 1.0, return_diagnostics=True)
    assert consensus is not None
    assert diag["books_available"] == 2
    assert diag["books_matched"] == 1
    assert any("drop:" in x for x in diag["book_drop_reasons"])

    with TemporaryDirectory() as td:
        root = Path(td)
        fill_ledger = root / "fills.csv"
        summary = performance_tracker._fill_summary(
            FillClient(),
            {"ticker":"TEST","kalshi_order_id":"order-1","side":"YES","client_order_id":"cid","signal_id":"sid"},
            fill_ledger_path=fill_ledger,
        )
        assert summary["entry_fees"] == 0.03
        with fill_ledger.open("r", encoding="utf-8", newline="") as fh:
            row = next(csv.DictReader(fh))
        assert row["fill_type"] == "taker"
        assert row["realized_fee"] == "0.03"

        signals = root / "signals.csv"
        candidates = root / "candidates.csv"
        clv = root / "clv.csv"
        signals.write_text(
            "record_type,signal_id,alerted_at,ticker,side,recommended_limit,commence_time\n"
            "signal,s1,2026-09-21T12:00:00Z,T1,YES,0.50,2026-09-21T15:00:00Z\n"
            "signal,s2,2026-09-21T12:00:00Z,T2,NO,0.50,2026-09-21T15:00:00Z\n",
            encoding="utf-8",
        )
        candidates.write_text("ticker,evaluated_at,fair_probability\n", encoding="utf-8")
        old_sig, old_cand, old_clv = clv_tracker.SIGNAL_LEDGER, clv_tracker.CANDIDATE_LEDGER, clv_tracker.CLV_LEDGER
        clv_tracker.SIGNAL_LEDGER, clv_tracker.CANDIDATE_LEDGER, clv_tracker.CLV_LEDGER = signals, candidates, clv
        try:
            result = clv_tracker.capture_clv(now=datetime(2026,9,21,15,5,tzinfo=timezone.utc), getter=getter)
        finally:
            clv_tracker.SIGNAL_LEDGER, clv_tracker.CANDIDATE_LEDGER, clv_tracker.CLV_LEDGER = old_sig, old_cand, old_clv
        assert result["captured"] == 2
        with clv.open("r", encoding="utf-8", newline="") as fh:
            rows = {r["signal_id"]:r for r in csv.DictReader(fh)}
        assert float(rows["s1"]["clv_cents"]) == 5.0
        assert float(rows["s2"]["clv_cents"]) == -5.0

    print("PM Edge Commit 4 instrumentation tests: PASS")


if __name__ == "__main__":
    main()
