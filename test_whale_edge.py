from __future__ import annotations

import whale_edge


def main() -> None:
    assert whale_edge._is_football("KXNFLGAME-TEST", "Team A vs Team B")
    assert whale_edge._is_football("ABC", "College football championship")
    assert not whale_edge._is_football("KXMLBGAME-TEST", "Baseball game")

    trade = {
        "trade_id": "t1",
        "ticker": "KXNFLGAME-TEST",
        "count_fp": "300.00",
        "yes_price_dollars": "0.6100",
        "no_price_dollars": "0.3900",
        "taker_outcome_side": "yes",
        "created_time": "2026-09-23T14:00:00Z",
        "is_block_trade": False,
    }
    assert whale_edge._trade_side(trade) == "YES"
    assert abs(whale_edge._trade_price(trade, "YES") - 0.61) < 1e-9
    assert whale_edge._trade_count(trade) == 300

    flows = whale_edge._aggregate([trade])
    assert len(flows) == 1
    assert flows[0]["side"] == "YES"
    assert flows[0]["flow_contracts"] == 300
    assert flows[0]["reference_price_cents"] == 61

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

    wait = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.70,
        model_fair_side=0.72,
    )
    assert wait["eligible"] is False
    assert wait["reason"] == "wait_for_better_price"

    state = {"sent": {"TEST|YES": {"target_price": 0.46}}}
    assert whale_edge._repeat_price_suppressed(state, "TEST", "YES", {"target": 0.76}) is True
    assert whale_edge._repeat_price_suppressed(state, "TEST", "YES", {"target": 0.46}) is False
    assert whale_edge._repeat_price_suppressed(state, "TEST", "YES", {"target": 0.44}) is False

    now = whale_edge.datetime(2026, 9, 23, 18, 46, tzinfo=whale_edge.timezone.utc)
    # MLB ticker embeds 13:10 Eastern; by 14:46 Eastern it is live and blocked.
    assert whale_edge._late_entry_reason("KXMLBGAME-26SEP231310WSHDET-DET", "NO", now) == "live_or_within_5m_of_start"
    # Tennis ticker has only the date; a same-day unverified start is blocked.
    assert whale_edge._late_entry_reason("KXITFMATCH-26SEP23THONOV-THO", "YES", now) == "same_day_start_time_unverified"
    # A future date-only event is still eligible for normal Whale evaluation.
    assert whale_edge._late_entry_reason("KXITFMATCH-26SEP24ABCDEF-ABC", "YES", now) is None

    oppose = whale_edge._evaluate_price(
        reference_cents=61,
        current_ask=0.60,
        model_fair_side=0.50,
    )
    assert oppose["eligible"] is False
    assert oppose["reason"] == "pm_edge_model_disagrees"

    print("PM Edge Whale Flow tests: PASS")


if __name__ == "__main__":
    main()
