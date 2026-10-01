from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from ledger_isolation import CommittedLedgerGuard, isolate_runtime_ledgers  # noqa: E402

# Must run before importing modules that bind ledger paths at import time.
isolate_runtime_ledgers()

import approval_links  # noqa: E402
import performance_tracker  # noqa: E402
import signal_tracker  # noqa: E402


class FakeClient:
    def __init__(self, order, fills):
        self.order = order
        self.fills = fills

    def get_orders(self, limit=200):
        return [self.order]

    def get_fills(self, *, ticker=None, limit=200):
        rows = self.fills
        if ticker:
            rows = [row for row in rows if row.get("ticker") == ticker]
        return rows[:limit]


def main() -> None:
    os.environ["PM_EDGE_APPROVAL_SECRET"] = "commit3-test-secret"
    signal_id = "a" * 20
    token = approval_links.create_approval_token(
        ticker="TEST-PROV",
        side="YES",
        lane="primary",
        execution="TAKE",
        limit_price=0.40,
        shares=5,
        max_loss=2.00,
        fair_probability=0.61,
        net_edge=0.08,
        books=5,
        dispersion=0.012,
        source_version=28,
        fair_fetched_at="2026-09-21T15:00:00Z",
        signal_id=signal_id,
        now=1_000_000,
    )
    trade = approval_links.verify_approval_token(token, now=1_000_001)
    assert trade["signal_id"] == signal_id
    assert trade["fair_probability"] == 0.61
    assert trade["net_edge"] == 0.08
    assert trade["books"] == 5
    assert trade["dispersion"] == 0.012
    assert trade["source_version"] == 28
    assert trade["fair_fetched_at"] == "2026-09-21T15:00:00Z"

    client_id = approval_links.client_order_id(signal_id, token)
    assert client_id.startswith(f"pmedge-{signal_id}-")
    assert performance_tracker._signal_id_from_client_order_id(client_id) == signal_id
    assert performance_tracker._signal_id_from_client_order_id("pmedge-legacydigest") is None

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "signals.csv"
        queue = root / "queue.json"
        history = root / "history.json"
        queue.write_text(json.dumps({"version": 1, "intents": []}), encoding="utf-8")

        market = {"ticker": "TEST-PROV", "title": "Provenance test"}
        fair = {
            "event_id": "evt-prov",
            "sport_key": "test_sport",
            "sport_title": "Test",
            "market_type": "h2h",
            "fair_yes_prob": 0.61,
            "n_books": 5,
            "dispersion": 0.012,
            "fetched_at": "2026-09-21T15:00:00Z",
        }
        q = SimpleNamespace(
            side="YES",
            execution="TAKE",
            recommended_limit=0.40,
            hard_max=0.43,
            net_edge=0.08,
            pmedge_risk_multiplier=1.0,
            pmedge_audit_bucket="",
        )
        result = signal_tracker.record_signal(
            market,
            fair,
            q,
            lane="primary",
            source_version=28,
            path=ledger,
            alerted_at="2026-09-21T15:01:00Z",
            signal_id=signal_id,
        )
        assert result["recorded"] is True

        submission = signal_tracker.record_submission(
            trade,
            client_order_id=client_id,
            order_result={"order_id": "order-prov"},
            path=ledger,
            submitted_at="2026-09-21T15:02:00Z",
        )
        assert submission["recorded"] is True
        with ledger.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[1]["record_type"] == "submission"
        assert rows[1]["client_order_id"] == client_id

        order = {
            "client_order_id": client_id,
            "order_id": "order-prov",
            "ticker": "TEST-PROV",
            "outcome_side": "yes",
            "initial_count_fp": "5.00",
            "fill_count_fp": "5.00",
            "yes_price_dollars": "0.400000",
            "status": "executed",
            "created_time": "2026-09-21T15:02:00Z",
        }
        fills = [{
            "fill_id": "fill-prov",
            "ticker": "TEST-PROV",
            "order_id": "order-prov",
            "outcome_side": "yes",
            "count_fp": "5.00",
            "yes_price_dollars": "0.40",
            "no_price_dollars": "0.60",
            "fee_cost": "0.05",
            "created_time": "2026-09-21T15:03:00Z",
        }]
        client = FakeClient(order, fills)

        history.write_text(json.dumps({
            "version": 1,
            "records": [{
                "intent_id": "historical-unrecoverable",
                "trade_date": "2026-09-01",
                "ticker": "LEGACY",
                "fair_probability": None,
                "status": "settled_loss",
                "outcome": "LOSS",
                "filled_shares": 1.0,
                "entry_cost": 0.5,
                "entry_fees": 0.0,
                "net_pl": -0.5,
            }],
        }), encoding="utf-8")

        updated = performance_tracker.update_performance(
            client,
            queue_path=queue,
            history_path=history,
            signal_ledger_path=ledger,
            market_loader=lambda _ticker: {
                "market_result": "yes",
                "settlement_value_yes": 1.0,
                "settled_time": "2026-09-21T18:00:00Z",
            },
        )
        assert updated["discovered_orders"] == 1
        assert updated["finalized"] == 1

        payload = json.loads(history.read_text(encoding="utf-8"))
        legacy = next(row for row in payload["records"] if row["intent_id"] == "historical-unrecoverable")
        assert legacy["fair_probability"] is None
        new_row = next(row for row in payload["records"] if row["ticker"] == "TEST-PROV")
        assert new_row["signal_id"] == signal_id
        assert new_row["fair_probability"] is not None
        assert abs(float(new_row["fair_probability"]) - 0.61) < 1e-9

    print("commit3 provenance tests passed")


if __name__ == "__main__":
    with CommittedLedgerGuard():
        main()
