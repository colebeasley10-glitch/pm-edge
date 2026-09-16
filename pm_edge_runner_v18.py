"""PM Edge v18 launcher.

Adds PRICE FISH alerts without weakening PM Edge's existing bet-quality bar.

A Price Fish is a market that does NOT qualify at the current quote, but would
qualify at PM Edge's normal primary net-edge threshold if a lower passive buy
limit were filled. The watcher can therefore surface more opportunities while
keeping the instruction explicit: leave the order resting and never chase the
current market price.

Implementation notes:
* Primary BET NOW / PLACE LIMIT alerts are unchanged.
* The old lower-edge "speculative" path is repurposed internally as Price Fish
  plumbing, but v18 scores it at the NORMAL primary threshold, not 2%.
* Price Fish searches 2c to 8c below the current ask by default and chooses the
  closest qualifying passive limit, favoring fillability over a fantasy price.
* At most 4 Price Fish pushes per run and 12 per day by default. Reminders are
  suppressed; a fish can re-alert only when its target price or edge changes
  enough to satisfy the watcher's existing change rules.
* This launcher is alert-only. It never places a real-money order.
"""
from __future__ import annotations

import json
import math
import os
from types import SimpleNamespace

import watcher
import pm_edge_runner_v17 as v17

PRICE_FISH_MIN_GAP = float(os.getenv("PM_EDGE_PRICE_FISH_MIN_GAP", "0.02"))
PRICE_FISH_MAX_GAP = float(os.getenv("PM_EDGE_PRICE_FISH_MAX_GAP", "0.08"))
PRICE_FISH_MAX_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_MAX_PRICE_FISH_ALERTS_PER_RUN", "4"))
PRICE_FISH_DAILY_CAP = int(os.getenv("PM_EDGE_PRICE_FISH_DAILY_CAP", "12"))
PRICE_FISH_STATE_VERSION = 1

V18_STATS = {
    "price_fish_candidates_built": 0,
    "price_fish_daily_cap_blocks": 0,
    "price_fish_reminders_suppressed": 0,
    "legacy_speculative_alerts_cleared": 0,
}


def _safe_float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _tick_for_side(market: dict, side: str, current_ask: float) -> float:
    """Return the market tick, falling back conservatively to one cent."""
    try:
        tick = float(watcher.tick_size_for_price(market, current_ask))
    except Exception:
        tick = 0.01
    if not math.isfinite(tick) or tick <= 0:
        tick = 0.01
    # Sports contracts are normally penny-like. This guard prevents malformed
    # metadata from producing huge search steps or an excessive loop.
    return min(max(tick, 0.001), 0.05)


def _resting_quote_scenario(market: dict, side: str, target: float, tick: float) -> dict:
    """Model the book after price moves down enough for our passive target.

    We leave the current ask untouched and move only the selected side's best
    bid to one tick below the proposed target. PM Edge's existing trade engine
    can then evaluate target = bid + one tick as a maker order using its normal
    maker fees, model buffer, slippage buffer, and book-count edge threshold.
    """
    fake = dict(market)
    bid_key = "yes_bid" if side == "YES" else "no_bid"
    fake[bid_key] = max(tick, target - tick)
    return fake


def find_price_fish(
    primary_scorer,
    market: dict,
    fair_entry: dict,
    settings,
    min_gap: float = PRICE_FISH_MIN_GAP,
    max_gap: float = PRICE_FISH_MAX_GAP,
):
    """Return the closest qualifying passive-limit trade, or None.

    `primary_scorer` must apply the normal PM Edge threshold when called with
    base_min_edge=None. We intentionally do not call the old 2% speculative
    threshold anywhere in this function.
    """
    candidates = []

    for side, ask_key in (("YES", "yes_ask"), ("NO", "no_ask")):
        current_ask = _safe_float(market.get(ask_key))
        if current_ask is None or not (0.0 < current_ask < 1.0):
            continue

        tick = _tick_for_side(market, side, current_ask)
        first_step = max(1, int(math.ceil((min_gap - 1e-12) / tick)))
        last_step = max(first_step, int(math.floor((max_gap + 1e-12) / tick)))

        side_candidate = None
        for steps in range(first_step, last_step + 1):
            target = current_ask - steps * tick
            if target <= tick or target >= current_ask:
                continue
            target = round(target, 6)

            fake_market = _resting_quote_scenario(market, side, target, tick)
            try:
                q = primary_scorer(fake_market, fair_entry, settings, base_min_edge=None)
            except Exception:
                continue

            if not getattr(q, "qualifies", False):
                continue
            if str(getattr(q, "side", "")).upper() != side:
                continue
            if str(getattr(q, "execution", "")).upper() != "MAKE":
                continue

            rec = _safe_float(getattr(q, "recommended_limit", None))
            edge = _safe_float(getattr(q, "net_edge", None))
            if rec is None or edge is None:
                continue

            gap = current_ask - rec
            if gap + 1e-9 < min_gap or gap - 1e-9 > max_gap:
                continue

            # For a Price Fish, the recommended limit itself is the no-chase
            # ceiling. `best_ask` is restored to the REAL current market ask so
            # the push shows exactly how far away the desired fill is.
            side_candidate = (
                gap,
                SimpleNamespace(
                    qualifies=True,
                    side=side,
                    recommended_limit=rec,
                    hard_max=rec,
                    net_edge=edge,
                    best_ask=current_ask,
                    execution="MAKE",
                ),
            )
            break  # closest / most fillable qualifying price for this side

        if side_candidate is not None:
            candidates.append(side_candidate)

    if not candidates:
        return None

    # Fillability first: choose the candidate requiring the smallest move from
    # the current ask. Break ties with the larger edge at the proposed fill.
    candidates.sort(key=lambda item: (item[0], -float(item[1].net_edge)))
    return candidates[0][1]


def price_fish_push_copy(title: str, message: str) -> tuple[str, str]:
    """Convert the watcher's internal speculative copy into Price Fish copy."""
    if title.startswith("🎲 PM EDGE SPECULATIVE"):
        title = title.replace("🎲 PM EDGE SPECULATIVE", "🎣 PM EDGE PRICE FISH", 1)
        message = message.replace(
            "Speculative pick: smaller edge, lower conviction. Only worth it as a passive limit order -- do not chase.",
            "PRICE FISH: current market is too expensive. This price only qualifies if your lower passive limit fills.",
        )
        message = message.replace("Est. net edge:", "Est. net edge IF FILLED:")
        message = message.replace(
            "Do not chase above MAX.",
            "Leave the limit resting. If it does not fill, skip it; do not raise the price.",
        )
    elif title == "⚠️ PM EDGE — PASS NOW" and "(speculative)" in message:
        title = "⚠️ PM EDGE PRICE FISH — CANCEL / PASS"
        message = message.replace(" (speculative)", "")
        message = message.replace(
            "Current quote no longer clears PM Edge's",
            "This resting Price Fish no longer clears PM Edge's",
        )
    elif title == "📋 PM Edge daily digest":
        message = message.replace("Speculative actionable:", "Price Fish candidates:")
        message = message.replace("Speculative alerts sent:", "Price Fish alerts sent:")
    return title, message


def _daily_bucket(state: dict) -> dict:
    today = watcher.now_utc().date().isoformat()
    bucket = state.setdefault("price_fish_daily", {"date": today, "sent": 0})
    if bucket.get("date") != today:
        bucket.clear()
        bucket.update({"date": today, "sent": 0})
    return bucket


def install_v18_patch() -> None:
    # The watcher calls its secondary tier only after a primary miss. Keep that
    # control flow, but never allow the old lower-edge economics to run.
    watcher.SPECULATIVE_MIN_EDGE = watcher.MIN_EDGE
    watcher.MAX_SPECULATIVE_ALERTS_PER_RUN = PRICE_FISH_MAX_ALERTS_PER_RUN

    original_score_market = watcher.score_market

    def score_market_v18(market, fair_entry, settings, base_min_edge=None):
        if base_min_edge is None:
            return original_score_market(market, fair_entry, settings, base_min_edge=None)

        fish = find_price_fish(original_score_market, market, fair_entry, settings)
        if fish is not None:
            V18_STATS["price_fish_candidates_built"] += 1
            return fish

        # Return the normal primary result (which should be a miss because the
        # watcher only reaches this branch after a primary miss). This is the
        # critical guard that prevents the legacy 2% speculative tier from
        # leaking back into v18.
        return original_score_market(market, fair_entry, settings, base_min_edge=None)

    watcher.score_market = score_market_v18

    # One-time migration: clear old 2%-threshold speculative alert state so it
    # cannot suppress a new Price Fish on the same ticker/side. Primary alert
    # state and fair-value cache are preserved.
    original_load_state = watcher.load_state

    def load_state_v18():
        state = original_load_state()
        if int(state.get("price_fish_state_version", 0) or 0) < PRICE_FISH_STATE_VERSION:
            alerts = state.setdefault("alerts", {})
            legacy_keys = [
                key for key, value in alerts.items()
                if str(key).upper().endswith("|SPECULATIVE")
                or str((value or {}).get("tier", "")).lower() == "speculative"
            ]
            for key in legacy_keys:
                alerts.pop(key, None)
            V18_STATS["legacy_speculative_alerts_cleared"] += len(legacy_keys)
            state["price_fish_daily"] = {"date": watcher.now_utc().date().isoformat(), "sent": 0}
            state["price_fish_state_version"] = PRICE_FISH_STATE_VERSION
        return state

    watcher.load_state = load_state_v18

    # Cap Price Fish notifications separately from primary alerts and suppress
    # time-only reminders. New opportunities and meaningful price/edge changes
    # can still alert.
    original_should_alert = watcher.should_alert

    def should_alert_v18(state, market, fair_entry, q, tier="primary"):
        if tier == "speculative":
            if int(_daily_bucket(state).get("sent", 0) or 0) >= PRICE_FISH_DAILY_CAP:
                V18_STATS["price_fish_daily_cap_blocks"] += 1
                return False, ""

        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if tier == "speculative" and ok and reason == "REMINDER":
            V18_STATS["price_fish_reminders_suppressed"] += 1
            return False, ""
        return ok, reason

    watcher.should_alert = should_alert_v18

    original_record_alert = watcher.record_alert

    def record_alert_v18(state, market, fair_entry, q, tier="primary"):
        original_record_alert(state, market, fair_entry, q, tier=tier)
        if tier == "speculative":
            bucket = _daily_bucket(state)
            bucket["sent"] = int(bucket.get("sent", 0) or 0) + 1

    watcher.record_alert = record_alert_v18

    # v18 sits outside v17's execution-safety wrapper. We rewrite the semantic
    # label first, then v17 still adds SIDE TO BUY / EXACT CONTRACT and the
    # explicit limit-order safety language.
    original_send_push = watcher.send_push

    def send_push_v18(title, message, *args, **kwargs):
        if title == "✅ PM Edge Watch v15 is live":
            title = "✅ PM Edge Watch v18 Price Fish is live"
        title, message = price_fish_push_copy(title, message)
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v18


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

    # Fake scorer: YES becomes a normal-threshold maker bet only at 42c or
    # lower. With a live 47c ask, Price Fish should therefore land exactly 42c.
    def fake_primary_scorer(market, fair_entry, settings, base_min_edge=None):
        yes_bid = _safe_float(market.get("yes_bid"))
        yes_ask = _safe_float(market.get("yes_ask"))
        if yes_bid is not None and yes_ask is not None:
            rec = round(yes_bid + 0.01, 6)
            if rec <= 0.42:
                return FakeQ(True, "YES", rec, 0.061, "MAKE")
        return FakeQ(False, "YES", None, 0.0, "PASS")

    market = {"yes_bid": 0.46, "yes_ask": 0.47, "no_bid": 0.52, "no_ask": 0.53}
    fish = find_price_fish(fake_primary_scorer, market, {"fair_yes_prob": 0.50, "n_books": 4}, object())
    assert fish is not None
    assert fish.side == "YES"
    assert abs(fish.recommended_limit - 0.42) < 1e-9
    assert abs(fish.best_ask - 0.47) < 1e-9
    assert abs(fish.hard_max - 0.42) < 1e-9
    assert fish.execution == "MAKE"

    # If qualifying requires a move larger than the configured 8c search band,
    # v18 must return no fish rather than advertise a fantasy fill.
    def too_far_scorer(market, fair_entry, settings, base_min_edge=None):
        yes_bid = _safe_float(market.get("yes_bid"))
        if yes_bid is not None and yes_bid + 0.01 <= 0.30:
            return FakeQ(True, "YES", yes_bid + 0.01, 0.08, "MAKE")
        return FakeQ(False, "YES", None, 0.0, "PASS")

    assert find_price_fish(too_far_scorer, market, {}, object()) is None

    title, msg = price_fish_push_copy(
        "🎲 PM EDGE SPECULATIVE +6.1% — YES",
        "MLB\nYES — White Sox win\n\n"
        "Speculative pick: smaller edge, lower conviction. Only worth it as a passive limit order -- do not chase.\n\n"
        "PLACE LIMIT: 42.0¢\nDO NOT PAY OVER: 42.0¢\nCurrent ask: 47.0¢\n"
        "Fair chance: 50.0%\nEst. net edge: +6.1%\nBooks: 8\n\nDo not chase above MAX."
    )
    assert title.startswith("🎣 PM EDGE PRICE FISH")
    assert "current market is too expensive" in msg
    assert "Est. net edge IF FILLED: +6.1%" in msg
    assert "do not raise the price" in msg

    # Daily cap state resets cleanly when the date changes and increments only
    # through successful record_alert calls in live operation.
    state = {"price_fish_daily": {"date": "1900-01-01", "sent": 99}}
    bucket = _daily_bucket(state)
    assert bucket["date"] == watcher.now_utc().date().isoformat()
    assert bucket["sent"] == 0

    print("PM Edge v18 Price Fish self-test: PASS")


def main() -> int:
    v17.v16.install_v16_patch()
    v17.install_v17_patch()
    install_v18_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(V18_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v17.v16.self_test()
        v17.self_test()
        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
