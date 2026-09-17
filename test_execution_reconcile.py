from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import execution_guard


NOW = datetime(2026, 9, 17, 19, 30, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, orders, fills=None):
        self.orders = orders
        self.fills = fills or []

    def get_orders(self, **kwargs):
        return list(self.orders)

    def get_fills(self, **kwargs):
        return list(self.fills)


def seed(path: Path, ticker="TEST", side="YES", shares=9, price=0.38):
    result = execution_guard.stage_order_intent(
        ticker=ticker,
        market_title="Test market",
        side=side,
        lane="primary",
        limit_price=price,
        shares=shares,
        max_loss=shares * price,
        fair_probability=0.55,
        net_edge=0.10,
        books=5,
        source_version=24,
        path=path,
        now=NOW,
    )
    assert result["staged"] is True


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"
        seed(path)
        order_time = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        client = FakeClient(
            [{
                "order_id": "order-1",
                "ticker": "TEST",
                "outcome_side": "yes",
                "action": "buy",
                "status": "executed",
                "yes_price_dollars": "0.3800",
                "initial_count_fp": "9.00",
                "fill_count_fp": "9.00",
                "remaining_count_fp": "0.00",
                "created_time": order_time,
            }],
            [{
                "fill_id": "fill-1",
                "order_id": "order-1",
                "ticker": "TEST",
                "outcome_side": "yes",
                "action": "buy",
                "count_fp": "9.00",
                "yes_price_dollars": "0.3700",
                "created_time": order_time,
            }],
        )
        result = execution_guard.reconcile_order_intents(client, path=path, now=NOW + timedelta(minutes=2))
        assert result["matched"] == 1
        assert result["updated"] == 1
        payload = execution_guard._load_queue(path)
        intent = payload["intents"][0]
        assert intent["status"] == "filled"
        assert intent["kalshi_order_id"] == "order-1"
        assert intent["filled_shares"] == 9.0
        assert intent["average_fill_price"] == 0.37

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"
        seed(path, ticker="AMB", shares=2, price=0.40)
        created = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        base = {
            "ticker": "AMB",
            "outcome_side": "yes",
            "action": "buy",
            "status": "resting",
            "yes_price_dollars": "0.4000",
            "initial_count_fp": "2.00",
            "fill_count_fp": "0.00",
            "created_time": created,
        }
        result = execution_guard.reconcile_order_intents(
            FakeClient([{**base, "order_id": "a"}, {**base, "order_id": "b"}]),
            path=path,
            now=NOW + timedelta(minutes=2),
        )
        assert result["ambiguous"] == 1
        intent = execution_guard._load_queue(path)["intents"][0]
        assert intent["status"] == "awaiting_approval"
        assert intent["reconciliation_note"] == "ambiguous_multiple_matching_orders"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"
        seed(path, ticker="TOO-HIGH", shares=2, price=0.40)
        created = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        result = execution_guard.reconcile_order_intents(
            FakeClient([{
                "order_id": "bad-price",
                "ticker": "TOO-HIGH",
                "outcome_side": "yes",
                "action": "buy",
                "status": "resting",
                "yes_price_dollars": "0.4100",
                "initial_count_fp": "2.00",
                "fill_count_fp": "0.00",
                "created_time": created,
            }]),
            path=path,
            now=NOW + timedelta(minutes=2),
        )
        assert result["matched"] == 0
        assert execution_guard._load_queue(path)["intents"][0]["status"] == "awaiting_approval"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"
        seed(path, ticker="TOO-LATE", shares=2, price=0.40)
        late = (NOW + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        result = execution_guard.reconcile_order_intents(
            FakeClient([{
                "order_id": "late-order",
                "ticker": "TOO-LATE",
                "outcome_side": "yes",
                "action": "buy",
                "status": "resting",
                "yes_price_dollars": "0.4000",
                "initial_count_fp": "2.00",
                "fill_count_fp": "0.00",
                "created_time": late,
            }]),
            path=path,
            now=NOW + timedelta(hours=4, minutes=1),
        )
        assert result["matched"] == 0

    print("Execution reconciliation tests: PASS")


if __name__ == "__main__":
    main()
