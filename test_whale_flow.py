from __future__ import annotations

from datetime import datetime, timezone

import whale_flow


def main() -> None:
    now = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)

    assert whale_flow._is_football("KXNFLGAME-TEST")
    assert whale_flow._is_football("KXNCAAFGAME-TEST")
    assert not whale_flow._is_football("KXMLBGAME-TEST")

    yes = whale_flow._normalize_trade(
        {
            "trade_id": "a",
            "ticker": "KXNFLGAME-TEST",
            "count_fp": "600.00",
            "yes_price_dollars": "0.6100",
            "no_price_dollars": "0.3900",
            "taker_outcome_side": "yes",
            "created_time": "2026-09-23T14:55:00Z",
            "is_block_trade": False,
        },
        now=now,
    )
    assert yes is not None
    assert yes["side"] == "YES"
    assert abs(yes["contracts"] - 600) < 1e-9
    assert abs(yes["price"] - 0.61) < 1e-9
    assert abs(yes["notional"] - 366.0) < 1e-9

    no = whale_flow._normalize_trade(
        {
            "trade_id": "b",
            "ticker": "TEST",
            "count_fp": "500.00",
            "yes_price_dollars": "0.3800",
            "no_price_dollars": "0.6200",
            "taker_outcome_side": "no",
            "created_time": "2026-09-23T14:56:00Z",
        },
        now=now,
    )
    assert no is not None
    assert no["side"] == "NO"
    assert abs(no["price"] - 0.62) < 1e-9

    flows = whale_flow._aggregate_flows([yes])
    assert len(flows) == 1
    assert flows[0]["trade_count"] == 1
    assert abs(flows[0]["reference_price"] - 0.61) < 1e-9

    cluster_rows = []
    for i in range(3):
        cluster_rows.append({
            "trade_id": f"c{i}",
            "ticker": "CLUSTER",
            "side": "YES",
            "contracts": 300.0,
            "price": 0.50,
            "notional": 150.0,
            "created_time": now,
            "is_block_trade": False,
        })
    cluster = whale_flow._aggregate_flows(cluster_rows)
    assert len(cluster) == 1
    assert cluster[0]["trade_count"] == 3
    assert cluster[0]["contracts_total"] == 900

    take = whale_flow._evaluate_price(
        reference_price=0.61,
        current_ask=0.58,
        model_fair_side=0.64,
    )
    assert take["eligible"] is True
    assert take["execution"] == "TAKE"
    assert take["target_price_cents"] == 58
    assert take["price_improvement_cents"] == 3
    assert abs(take["model_edge"] - 0.06) < 1e-9

    make = whale_flow._evaluate_price(
        reference_price=0.61,
        current_ask=0.64,
        model_fair_side=0.66,
    )
    assert make["eligible"] is True
    assert make["execution"] == "MAKE"
    assert make["target_price_cents"] == 60

    no_model = whale_flow._evaluate_price(
        reference_price=0.61,
        current_ask=0.58,
        model_fair_side=None,
    )
    assert no_model["eligible"] is False
    assert no_model["reason"] == "no_pm_edge_confirmation"

    weak_model = whale_flow._evaluate_price(
        reference_price=0.61,
        current_ask=0.60,
        model_fair_side=0.61,
    )
    assert weak_model["eligible"] is False
    assert weak_model["reason"] == "insufficient_pm_edge"

    chase = whale_flow._evaluate_price(
        reference_price=0.61,
        current_ask=0.70,
        model_fair_side=0.75,
    )
    assert chase["eligible"] is False
    assert chase["reason"] == "wait_for_better_price"

    print("PM Edge free Whale Flow tests: PASS")


if __name__ == "__main__":
    main()
