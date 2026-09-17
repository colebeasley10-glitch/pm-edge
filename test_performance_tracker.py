from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import execution_guard
import performance_tracker


class FakeClient:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, *, ticker=None, limit=200):
        rows = self.fills
        if ticker:
            rows = [x for x in rows if x.get("ticker") == ticker]
        return rows[:limit]


def write_queue(path: Path, intents: list[dict]) -> None:
    path.write_text(json.dumps({"version": 1, "intents": intents}), encoding="utf-8")


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"

        win_intent = {
            "intent_id": "win-1",
            "created_at": "2026-09-17T20:00:00Z",
            "trade_date": "2026-09-17",
            "ticker": "TEST-WIN",
            "market_title": "Test winner",
            "side": "YES",
            "lane": "primary",
            "limit_price": 0.38,
            "shares": 9,
            "max_loss": 3.42,
            "fair_probability": 0.558,
            "net_edge": 0.149,
            "books": 6,
            "status": "filled",
            "kalshi_order_id": "order-win",
            "filled_shares": 9.0,
            "average_fill_price": 0.38,
        }
        unfilled_intent = {
            "intent_id": "unfilled-1",
            "created_at": "2026-09-17T20:05:00Z",
            "trade_date": "2026-09-17",
            "ticker": "TEST-NOFILL",
            "market_title": "Test unfilled",
            "side": "NO",
            "lane": "price_fish",
            "limit_price": 0.22,
            "shares": 10,
            "max_loss": 2.20,
            "fair_probability": 0.31,
            "net_edge": 0.05,
            "books": 5,
            "status": "canceled",
            "kalshi_order_id": "order-no-fill",
            "filled_shares": 0.0,
        }
        write_queue(queue_path, [win_intent, unfilled_intent])

        fills = [
            {
                "ticker": "TEST-WIN",
                "order_id": "order-win",
                "outcome_side": "yes",
                "count_fp": "4.00",
                "yes_price_dollars": "0.37",
                "no_price_dollars": "0.63",
                "fee_cost": "0.04",
            },
            {
                "ticker": "TEST-WIN",
                "order_id": "order-win",
                "outcome_side": "yes",
                "count_fp": "5.00",
                "yes_price_dollars": "0.38",
                "no_price_dollars": "0.62",
                "fee_cost": "0.06",
            },
        ]
        client = FakeClient(fills)

        def market_loader(ticker: str):
            if ticker == "TEST-WIN":
                return {
                    "market_result": "yes",
                    "settlement_value_yes": 1.0,
                    "settled_time": "2026-09-17T23:00:00Z",
                }
            raise AssertionError("Canceled unfilled orders should not need market settlement")

        result = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            market_loader=market_loader,
        )
        assert result["finalized"] == 2
        summary = result["summary"]
        assert summary["tracked_orders"] == 2
        assert summary["filled_orders"] == 1
        assert summary["unfilled_orders"] == 1
        assert summary["fill_rate"] == 0.5
        assert summary["wins"] == 1
        assert summary["losses"] == 0
        assert summary["record"] == "1-0"
        # Entry = 4*.37 + 5*.38 = 3.38; fees=.10; payout=9; net=5.52
        assert abs(summary["entry_cost"] - 3.38) < 1e-9
        assert abs(summary["fees"] - 0.10) < 1e-9
        assert abs(summary["net_pl"] - 5.52) < 1e-9

        history = json.loads(history_path.read_text(encoding="utf-8"))
        rows = {r["intent_id"]: r for r in history["records"]}
        assert rows["win-1"]["status"] == "settled_win"
        assert rows["win-1"]["outcome"] == "WIN"
        assert abs(rows["win-1"]["net_pl"] - 5.52) < 1e-9
        assert rows["win-1"]["fees_complete"] is True
        assert rows["unfilled-1"]["status"] == "unfilled"
        assert rows["unfilled-1"]["net_pl"] == 0.0

        # Idempotency: the same intents cannot be counted twice.
        again = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            market_loader=market_loader,
        )
        assert again["finalized"] == 0
        assert again["summary"]["tracked_orders"] == 2

    print("PM Edge performance tracker tests: PASS")


if __name__ == "__main__":
    main()
