from __future__ import annotations

import whale_edge


def main() -> None:
    assert whale_edge._is_football("KXNFLGAME-TEST", "Team A vs Team B")
    assert whale_edge._is_football("ABC", "College football championship")
    assert not whale_edge._is_football("KXMLBGAME-TEST", "Baseball game")

    assert whale_edge._trade_age_minutes("2m ago") == 2
    assert whale_edge._trade_age_minutes("3 hours ago") == 180
    assert whale_edge._trade_age_minutes("30 seconds ago") == 0.5

    trade = whale_edge._exact_trade(
        [
            {"ticker": "ABC", "side": "NO", "market_closed": False, "id": "x"},
            {"ticker": "ABC", "side": "YES", "market_closed": False, "id": "y"},
        ],
        ticker="ABC",
        side="YES",
    )
    assert trade and trade["id"] == "y"

    take = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.58,
        model_fair_side=0.64,
    )
    assert take["eligible"] is True
    assert take["execution"] == "TAKE"
    assert take["target_price_cents"] == 58
    assert take["price_improvement_cents"] == 3

    make = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.64,
        model_fair_side=0.65,
    )
    assert make["eligible"] is True
    assert make["execution"] == "MAKE"
    assert make["target_price_cents"] == 60
    assert make["price_improvement_cents"] == 1

    wait = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.70,
        model_fair_side=0.72,
    )
    assert wait["eligible"] is False
    assert wait["reason"] == "wait_for_better_price"

    oppose = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.60,
        model_fair_side=0.50,
    )
    assert oppose["eligible"] is False
    assert oppose["reason"] == "pm_edge_model_disagrees"

    print("PM Edge Whale Edge tests: PASS")


if __name__ == "__main__":
    main()
