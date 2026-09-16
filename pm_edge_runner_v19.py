"""PM Edge v19 launcher.

Builds on v18 Price Fish and removes the ultra-long-shot fish the owner does
not want to see.

A v19 Price Fish must still satisfy every v18 rule (normal PM Edge edge
threshold, passive 2c-8c discount, event/matching safeguards) AND it must be a
realistic-priced side:

* target limit must be at least 20c by default; and
* PM Edge fair probability for the side being bought must be at least 25%.

These floors are configurable with environment variables, but the defaults are
intended to keep useful 30c-60c style opportunities while filtering 2c/5c/10c
moonshots. Primary PM Edge alerts are unchanged.

This launcher is alert-only. It never places a real-money order.
"""
from __future__ import annotations

import json
import os

import watcher
import pm_edge_runner_v18 as v18

PRICE_FISH_MIN_TARGET = float(os.getenv("PM_EDGE_PRICE_FISH_MIN_TARGET", "0.20"))
PRICE_FISH_MIN_FAIR_SIDE_PROB = float(
    os.getenv("PM_EDGE_PRICE_FISH_MIN_FAIR_SIDE_PROB", "0.25")
)

V19_STATS = {
    "price_fish_longshots_filtered": 0,
    "price_fish_target_too_low": 0,
    "price_fish_fair_prob_too_low": 0,
}

_ORIGINAL_FIND_PRICE_FISH = v18.find_price_fish


def realistic_price_fish(
    primary_scorer,
    market: dict,
    fair_entry: dict,
    settings,
    min_gap: float = v18.PRICE_FISH_MIN_GAP,
    max_gap: float = v18.PRICE_FISH_MAX_GAP,
):
    """Return a v18 Price Fish only when it is not an ultra-long-shot side."""
    fish = _ORIGINAL_FIND_PRICE_FISH(
        primary_scorer,
        market,
        fair_entry,
        settings,
        min_gap=min_gap,
        max_gap=max_gap,
    )
    if fish is None:
        return None

    rec = v18._safe_float(getattr(fish, "recommended_limit", None))
    if rec is None or rec + 1e-12 < PRICE_FISH_MIN_TARGET:
        V19_STATS["price_fish_longshots_filtered"] += 1
        V19_STATS["price_fish_target_too_low"] += 1
        return None

    fair_yes = v18._safe_float(fair_entry.get("fair_yes_prob"))
    side = str(getattr(fish, "side", "")).upper()
    if fair_yes is None or side not in {"YES", "NO"}:
        V19_STATS["price_fish_longshots_filtered"] += 1
        V19_STATS["price_fish_fair_prob_too_low"] += 1
        return None

    side_prob = fair_yes if side == "YES" else 1.0 - fair_yes
    if side_prob + 1e-12 < PRICE_FISH_MIN_FAIR_SIDE_PROB:
        V19_STATS["price_fish_longshots_filtered"] += 1
        V19_STATS["price_fish_fair_prob_too_low"] += 1
        return None

    return fish


def install_v19_patch() -> None:
    # v18's score wrapper resolves find_price_fish from the v18 module at call
    # time, so replacing that global cleanly inserts the realism filter without
    # touching primary scoring or any of v18's fill/edge math.
    v18.find_price_fish = realistic_price_fish
    watcher.WATCHER_VERSION = 19

    original_send_push = watcher.send_push

    def send_push_v19(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v18 Price Fish is live",
        }:
            title = "✅ PM Edge Watch v19 Realistic Price Fish is live"
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v19


def self_test() -> None:
    class FakeQ:
        def __init__(self, qualifies, side="YES", rec=None, edge=None, execution="MAKE"):
            self.qualifies = qualifies
            self.side = side
            self.recommended_limit = rec
            self.hard_max = rec
            self.net_edge = edge
            self.best_ask = rec
            self.execution = execution

    def scorer_42c(market, fair_entry, settings, base_min_edge=None):
        yes_bid = v18._safe_float(market.get("yes_bid"))
        yes_ask = v18._safe_float(market.get("yes_ask"))
        if yes_bid is not None and yes_ask is not None:
            rec = round(yes_bid + 0.01, 6)
            if rec <= 0.42:
                return FakeQ(True, "YES", rec, 0.061, "MAKE")
        return FakeQ(False, "YES", None, 0.0, "PASS")

    normal_market = {"yes_bid": 0.46, "yes_ask": 0.47, "no_bid": 0.52, "no_ask": 0.53}
    fish = realistic_price_fish(
        scorer_42c,
        normal_market,
        {"fair_yes_prob": 0.50, "n_books": 4},
        object(),
    )
    assert fish is not None
    assert abs(fish.recommended_limit - 0.42) < 1e-9

    # A 10c fish is exactly the behavior v19 is meant to eliminate.
    def scorer_10c(market, fair_entry, settings, base_min_edge=None):
        yes_bid = v18._safe_float(market.get("yes_bid"))
        if yes_bid is not None:
            rec = round(yes_bid + 0.01, 6)
            if rec <= 0.10:
                return FakeQ(True, "YES", rec, 0.05, "MAKE")
        return FakeQ(False, "YES", None, 0.0, "PASS")

    longshot_market = {"yes_bid": 0.14, "yes_ask": 0.15, "no_bid": 0.84, "no_ask": 0.85}
    assert realistic_price_fish(
        scorer_10c,
        longshot_market,
        {"fair_yes_prob": 0.18, "n_books": 4},
        object(),
    ) is None

    # A target at the configured price floor still needs a realistic model
    # probability for the selected side.
    class DirectFish:
        side = "YES"
        recommended_limit = PRICE_FISH_MIN_TARGET

    global _ORIGINAL_FIND_PRICE_FISH
    saved = _ORIGINAL_FIND_PRICE_FISH
    try:
        _ORIGINAL_FIND_PRICE_FISH = lambda *args, **kwargs: DirectFish()
        assert realistic_price_fish(
            scorer_42c,
            normal_market,
            {"fair_yes_prob": PRICE_FISH_MIN_FAIR_SIDE_PROB - 0.01, "n_books": 4},
            object(),
        ) is None
    finally:
        _ORIGINAL_FIND_PRICE_FISH = saved

    print("PM Edge v19 realistic Price Fish self-test: PASS")


def main() -> int:
    v18.v17.v16.install_v16_patch()
    v18.v17.install_v17_patch()
    v18.install_v18_patch()
    install_v19_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v18.v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v18.v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(v18.V18_STATS, sort_keys=True))
    print("PM_EDGE_V19_REALISTIC_FISH " + json.dumps(V19_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v18.v17.v16.self_test()
        v18.v17.self_test()
        v18.self_test()
        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
