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

    # A full manual cash-out before settlement is finalized immediately from
    # opposite-direction fills and does not wait for the game result.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"
        early_intent = {
            "intent_id": "early-1",
            "created_at": "2026-09-19T14:00:00Z",
            "trade_date": "2026-09-19",
            "ticker": "TEST-EARLY",
            "market_title": "Test early exit",
            "side": "YES",
            "lane": "primary",
            "limit_price": 0.40,
            "shares": 10,
            "max_loss": 4.00,
            "fair_probability": 0.58,
            "net_edge": 0.10,
            "books": 5,
            "status": "filled",
            "kalshi_order_id": "entry-early",
            "filled_shares": 10.0,
            "average_fill_price": 0.40,
        }
        write_queue(queue_path, [early_intent])
        client = FakeClient([
            {
                "fill_id": "entry-fill",
                "ticker": "TEST-EARLY",
                "order_id": "entry-early",
                "outcome_side": "yes",
                "count_fp": "10.00",
                "yes_price_dollars": "0.40",
                "no_price_dollars": "0.60",
                "fee_cost": "0.05",
                "created_time": "2026-09-19T14:01:00Z",
            },
            {
                "fill_id": "exit-fill",
                "ticker": "TEST-EARLY",
                "order_id": "manual-exit",
                "outcome_side": "no",
                "count_fp": "10.00",
                "yes_price_dollars": "0.70",
                "no_price_dollars": "0.30",
                "fee_cost": "0.05",
                "created_time": "2026-09-19T15:00:00Z",
            },
        ])
        result = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            market_loader=lambda _ticker: None,
        )
        assert result["finalized"] == 1
        assert result["summary"]["realized_exits"] == 1
        assert abs(result["summary"]["realized_exit_pl"] - 2.90) < 1e-9
        history = json.loads(history_path.read_text(encoding="utf-8"))
        row = history["records"][0]
        assert row["status"] == "closed_early_profit"
        assert row["pnl_method"] == "pm_edge_realized_exit_fills"
        assert abs(row["net_pl"] - 2.90) < 1e-9
        assert abs(row["average_exit_price"] - 0.70) < 1e-9

    # Existing held-to-settlement history is repaired when fill history proves
    # the position was already closed before the market settled.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"
        write_queue(queue_path, [])
        history_path.write_text(json.dumps({
            "version": 1,
            "records": [{
                "intent_id": "retro-1",
                "trade_date": "2026-09-19",
                "ticker": "TEST-RETRO",
                "market_title": "Retro exit",
                "side": "YES",
                "lane": "primary",
                "limit_price": 0.40,
                "requested_shares": 5,
                "kalshi_order_id": "entry-retro",
                "filled_shares": 5.0,
                "average_fill_price": 0.40,
                "entry_cost": 2.00,
                "entry_fees": 0.02,
                "status": "settled_loss",
                "outcome": "LOSS",
                "net_pl": -2.02,
                "settled_at": "2026-09-19T18:00:00Z",
                "pnl_method": "pm_edge_order_fills_held_to_settlement",
            }],
        }), encoding="utf-8")
        client = FakeClient([
            {
                "fill_id": "retro-entry-fill",
                "ticker": "TEST-RETRO",
                "order_id": "entry-retro",
                "outcome_side": "yes",
                "count_fp": "5.00",
                "yes_price_dollars": "0.40",
                "no_price_dollars": "0.60",
                "fee_cost": "0.02",
                "created_time": "2026-09-19T14:00:00Z",
            },
            {
                "fill_id": "retro-exit-fill",
                "ticker": "TEST-RETRO",
                "order_id": "manual-retro-exit",
                "outcome_side": "no",
                "count_fp": "5.00",
                "yes_price_dollars": "0.65",
                "no_price_dollars": "0.35",
                "fee_cost": "0.02",
                "created_time": "2026-09-19T16:00:00Z",
            },
        ])
        result = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            market_loader=lambda _ticker: None,
        )
        assert result["retro_early_exit_corrections"] == 1
        history = json.loads(history_path.read_text(encoding="utf-8"))
        row = history["records"][0]
        assert row["status"] == "closed_early_profit"
        assert abs(row["net_pl"] - 1.21) < 1e-9
        assert row["market_settled_at"] == "2026-09-19T18:00:00Z"

    # A fill reported by the orders endpoint must not be lost just because
    # Kalshi's fills endpoint is temporarily behind. The limit price is a
    # conservative fallback until exact fill rows arrive.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"
        lagged_intent = {
            "intent_id": "lagged-1",
            "created_at": "2026-09-23T08:00:00Z",
            "trade_date": "2026-09-23",
            "ticker": "TEST-LAGGED",
            "market_title": "Lagged fill",
            "side": "NO",
            "lane": "primary",
            "limit_price": 0.40,
            "shares": 5,
            "status": "filled",
            "kalshi_order_id": "order-lagged",
            "filled_shares": 5.0,
            "average_fill_price": None,
        }
        write_queue(queue_path, [lagged_intent])
        client = FakeClient([])
        settlement = {
            "market_result": "yes",
            "settlement_value_yes": 1.0,
            "settled_time": "2026-09-23T09:00:00Z",
        }
        result = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            market_loader=lambda _ticker: settlement,
        )
        history = json.loads(history_path.read_text(encoding="utf-8"))
        row = history["records"][0]
        assert row["status"] == "settled_loss"
        assert row["filled_shares"] == 5.0
        assert row["average_fill_price"] == 0.40
        assert abs(row["net_pl"] + 2.0) < 1e-9

    # If an order was prematurely finalized as unfilled, later fill evidence
    # in the reconciled queue repairs the historical record exactly once.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"
        repaired_intent = {
            "intent_id": "repair-1",
            "created_at": "2026-09-23T08:00:00Z",
            "trade_date": "2026-09-23",
            "ticker": "TEST-REPAIR",
            "market_title": "Repair fill",
            "side": "NO",
            "lane": "primary",
            "limit_price": 0.40,
            "shares": 5,
            "status": "filled",
            "kalshi_order_id": "order-repair",
            "filled_shares": 5.0,
            "average_fill_price": None,
        }
        write_queue(queue_path, [repaired_intent])
        history_path.write_text(json.dumps({
            "version": 1,
            "records": [{
                "intent_id": "repair-1",
                "trade_date": "2026-09-23",
                "ticker": "TEST-REPAIR",
                "market_title": "Repair fill",
                "side": "NO",
                "lane": "primary",
                "limit_price": 0.40,
                "requested_shares": 5,
                "kalshi_order_id": "order-repair",
                "filled_shares": 0.0,
                "average_fill_price": None,
                "entry_cost": 0.0,
                "entry_fees": 0.0,
                "status": "unfilled",
                "outcome": "UNFILLED",
                "net_pl": 0.0,
                "settled_at": "2026-09-23T09:00:00Z",
            }],
        }), encoding="utf-8")
        result = performance_tracker.update_performance(
            FakeClient([]),
            queue_path=queue_path,
            history_path=history_path,
            market_loader=lambda _ticker: settlement,
        )
        assert result["retro_unfilled_corrections"] == 1
        history = json.loads(history_path.read_text(encoding="utf-8"))
        row = history["records"][0]
        assert row["status"] == "settled_loss"
        assert row["filled_shares"] == 5.0
        assert abs(row["net_pl"] + 2.0) < 1e-9


    # A PM Edge signal can be alerted the prior calendar day and actually
    # filled the next morning through a stale/manual approval path. Recover the
    # real fill without leaving a duplicate UNFILLED history row.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        queue_path = root / "queue.json"
        history_path = root / "history.json"
        signal_path = root / "signals.csv"
        signal_id = "abcdef1234567890abcd"
        original_intent = {
            "intent_id": "crossday-1",
            "client_order_id": f"pmedge-{signal_id}-12345678",
            "signal_id": signal_id,
            "created_at": "2026-09-22T00:20:00Z",
            "trade_date": "2026-09-22",
            "ticker": "TEST-CROSSDAY",
            "market_title": "Cross-day recovery",
            "side": "NO",
            "lane": "primary",
            "limit_price": 0.40,
            "shares": 9,
            "max_loss": 3.60,
            "status": "canceled",
            "kalshi_order_id": "old-unfilled-order",
            "filled_shares": 0.0,
        }
        write_queue(queue_path, [original_intent])
        history_path.write_text(json.dumps({
            "version": 1,
            "records": [{
                "intent_id": "crossday-1",
                "client_order_id": f"pmedge-{signal_id}-12345678",
                "signal_id": signal_id,
                "trade_date": "2026-09-22",
                "ticker": "TEST-CROSSDAY",
                "market_title": "Cross-day recovery",
                "side": "NO",
                "lane": "primary",
                "limit_price": 0.40,
                "requested_shares": 9,
                "kalshi_order_id": "old-unfilled-order",
                "filled_shares": 0.0,
                "entry_cost": 0.0,
                "entry_fees": 0.0,
                "status": "unfilled",
                "outcome": "UNFILLED",
                "net_pl": 0.0,
            }],
        }), encoding="utf-8")
        signal_path.write_text(
            "record_type,signal_id,alerted_at,trade_date,ticker,event_id,market_title,sport_key,sport_title,market_type,side,lane,execution,recommended_limit,hard_max,fair_probability,net_edge,books,dispersion,fair_fetched_at,commence_time,risk_multiplier,audit_bucket,source_version,client_order_id,submitted_at,kalshi_order_id\n"
            f"signal,{signal_id},2026-09-22T00:17:00Z,2026-09-22,TEST-CROSSDAY,event-1,Cross-day recovery,baseball_npb,NPB,h2h,NO,primary,MAKE,0.40,0.40,0.55,0.10,9,0.01,2026-09-22T00:16:00Z,2026-09-23T08:00:00Z,1.0,,28,,,\n",
            encoding="utf-8",
        )
        client = FakeClient([{
            "fill_id": "crossday-fill",
            "order_id": "actual-filled-order",
            "ticker": "TEST-CROSSDAY",
            "outcome_side": "no",
            "count_fp": "9.00",
            "yes_price_dollars": "0.61",
            "no_price_dollars": "0.39",
            "fee_cost": "0.00",
            "created_time": "2026-09-23T07:00:00Z",
        }])
        result = performance_tracker.update_performance(
            client,
            queue_path=queue_path,
            history_path=history_path,
            signal_ledger_path=signal_path,
            market_loader=lambda _ticker: {
                "market_result": "yes",
                "settlement_value_yes": 1.0,
                "settled_time": "2026-09-23T10:00:00Z",
            },
        )
        assert result["discovered_orders"] == 1
        assert result["retro_unfilled_corrections"] == 1
        history = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(history["records"]) == 1
        row = history["records"][0]
        assert row["trade_date"] == "2026-09-23"
        assert row["kalshi_order_id"] == "actual-filled-order"
        assert row["status"] == "settled_loss"
        assert row["outcome"] == "LOSS"
        assert row["filled_shares"] == 9.0
        assert abs(row["average_fill_price"] - 0.39) < 1e-9
        assert abs(row["net_pl"] + 3.51) < 1e-9

    print("PM Edge performance tracker tests: PASS")


if __name__ == "__main__":
    main()
