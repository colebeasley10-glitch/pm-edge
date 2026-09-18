"""PM Edge v26 launcher: quality and adverse-selection controls.

v26 keeps v25 settlement/performance tracking and every existing pricing guard,
then adds four execution-quality protections:

1. VALUE is tightened by deployment config (3.5% with 4+ books, 4.0% with 3).
2. Cross-lane event correlation is capped at one active entry idea per event.
3. Bookmaker-consensus dispersion is capped before an alert can be actionable.
4. Price Fish resting limits are revalidated at the ORIGINAL target price. If
   that exact old target no longer clears normal PM Edge economics, the watcher
   sends CANCEL / PASS even if a new lower Price Fish target would still qualify.

The launcher preserves v25's approval-first behavior and never autonomously
places, amends, or cancels a real-money order.
"""
from __future__ import annotations

import json
import math
import os

import execution_guard
import pm_edge_runner_v18 as v18
import pm_edge_runner_v22 as v22
import pm_edge_runner_v23 as v23
import pm_edge_runner_v24 as v24
import pm_edge_runner_v25 as v25
import watcher

MAX_BOOK_DISPERSION = float(os.getenv("PM_EDGE_MAX_BOOK_DISPERSION", "0.055"))
MAX_ACTIVE_ALERTS_PER_EVENT = max(
    1, int(os.getenv("PM_EDGE_MAX_ACTIVE_ALERTS_PER_EVENT", "1"))
)
PRICE_FISH_MIN_BOOKS = max(
    2, int(os.getenv("PM_EDGE_PRICE_FISH_MIN_BOOKS", "3"))
)
PRICE_FISH_MAX_FAIR_AGE_MINUTES = max(
    15.0, float(os.getenv("PM_EDGE_PRICE_FISH_MAX_FAIR_AGE_MINUTES", "60"))
)

# Process-local reservations prevent two correlated candidates from both passing
# should_alert during the same pre-send ranking pass before either is recorded.
_RUN_EVENT_RESERVATIONS: dict[str, set[str]] = {}

V26_STATS = {
    "dispersion_blocks": 0,
    "correlation_blocks": 0,
    "open_position_blocks": 0,
    "price_fish_book_blocks": 0,
    "price_fish_stale_fair_blocks": 0,
    "stale_price_fish_cancels": 0,
    "alert_event_ids_recorded": 0,
    "intent_event_ids_recorded": 0,
}


def _safe_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _event_id(fair_entry: dict) -> str:
    return str(fair_entry.get("event_id") or "").strip()


def _lane(tier: str, q) -> str:
    return v23._effective_lane(tier, q)


def _state_tier(lane: str) -> str:
    if lane == "value":
        return "value"
    if lane == "price_fish":
        return "speculative"
    return "primary"


def _alert_key(market: dict, q, lane: str) -> str:
    tier = _state_tier(lane)
    suffix = "" if tier == "primary" else f"|{tier.upper()}"
    return f"{market.get('ticker','')}|{str(getattr(q, 'side', '') or '').upper()}{suffix}"


def _enrich_existing_alert_event_ids(state: dict) -> None:
    fair_cache = state.get("fair_cache") or {}
    for key, alert in (state.get("alerts") or {}).items():
        if not isinstance(alert, dict) or alert.get("event_id"):
            continue
        ticker = str(key).split("|", 1)[0]
        fair = fair_cache.get(ticker) or {}
        event_id = _event_id(fair)
        if event_id:
            alert["event_id"] = event_id


def _open_intents() -> list[dict]:
    try:
        payload = execution_guard._load_queue(execution_guard.QUEUE_PATH)
    except Exception:
        return []
    rows = payload.get("intents", [])
    return rows if isinstance(rows, list) else []


def _terminal_intent(status: str) -> bool:
    status = str(status or "").lower()
    return status in {
        "canceled", "cancelled", "unfilled", "expired", "expired_stale_edge",
        "settled_win", "settled_loss", "settled_void", "settled_other",
    }


def _has_open_position_or_order(event_id: str, ticker: str, side: str) -> bool:
    """Block duplicate/new entry alerts when a submitted/fill already exists.

    Awaiting-approval intents are intentionally not counted here because the
    active alert state already handles them and stale approval intents should
    not lock an event forever.
    """
    for intent in _open_intents():
        status = str(intent.get("status") or "").lower()
        if status not in {"submitted", "partially_filled", "filled"}:
            continue
        if _terminal_intent(status):
            continue
        intent_event = str(intent.get("event_id") or "").strip()
        intent_ticker = str(intent.get("ticker") or "")
        intent_side = str(intent.get("side") or "").upper()
        if ticker and intent_ticker == ticker and intent_side == side:
            return True
        if event_id and intent_event and intent_event == event_id:
            return True
    return False


def _active_event_alert_count(state: dict, event_id: str, exclude_key: str) -> int:
    if not event_id:
        return 0
    count = 0
    for key, alert in (state.get("alerts") or {}).items():
        if key == exclude_key or not isinstance(alert, dict):
            continue
        if not alert.get("active", False):
            continue
        if str(alert.get("event_id") or "").strip() == event_id:
            count += 1
    return count


def _dispersion_ok(fair_entry: dict) -> bool:
    dispersion = _safe_float(fair_entry.get("dispersion"))
    if dispersion is None:
        return True
    return dispersion <= MAX_BOOK_DISPERSION + 1e-12


def _price_fish_old_target_valid(
    market: dict,
    fair_entry: dict,
    settings,
    *,
    side: str,
    target: float,
) -> bool:
    """Re-price the exact old resting limit using the newest fair value.

    This intentionally does NOT ask whether *some* lower Price Fish exists.
    It asks whether the already-resting target still clears the normal PM Edge
    threshold now. That closes the adverse-selection hole where a 42c order
    could remain active after fresh fair value says only 38c is acceptable.
    """
    if not _dispersion_ok(fair_entry):
        return False

    if watcher.age_hours(fair_entry.get("fetched_at")) > PRICE_FISH_MAX_FAIR_AGE_MINUTES / 60.0:
        return False

    n_books = int(fair_entry.get("n_books") or 0)
    if n_books < PRICE_FISH_MIN_BOOKS:
        return False

    side = str(side or "").upper()
    target = _safe_float(target)
    if side not in {"YES", "NO"} or target is None or not (0.0 < target < 1.0):
        return False

    ask_key = "yes_ask" if side == "YES" else "no_ask"
    bid_key = "yes_bid" if side == "YES" else "no_bid"
    current_ask = _safe_float(market.get(ask_key), target + 0.02)
    tick = v18._tick_for_side(market, side, current_ask)

    # Synthetic quote that forces the scorer to evaluate a maker fill at the
    # ORIGINAL target rather than selecting a newly cheaper target.
    fake = dict(market)
    fake[bid_key] = max(tick, target - tick)
    fake[ask_key] = min(0.999, max(target + tick, current_ask or 0.0))

    try:
        q = v22._raw_score_market(fake, fair_entry, settings, base_min_edge=None)
    except Exception:
        return False

    if not getattr(q, "qualifies", False):
        return False
    if str(getattr(q, "side", "") or "").upper() != side:
        return False
    if str(getattr(q, "execution", "") or "").upper() != "MAKE":
        return False

    rec = _safe_float(getattr(q, "recommended_limit", None))
    return rec is not None and abs(rec - target) <= max(tick / 2.0, 0.001)


def _annotate_latest_intent_event_id(market: dict, q, fair_entry: dict) -> None:
    event_id = _event_id(fair_entry)
    if not event_id:
        return
    ticker = str(market.get("ticker") or "")
    side = str(getattr(q, "side", "") or "").upper()
    try:
        payload = execution_guard._load_queue(execution_guard.QUEUE_PATH)
    except Exception:
        return

    changed = False
    for intent in reversed(payload.get("intents", [])):
        if str(intent.get("ticker") or "") != ticker:
            continue
        if str(intent.get("side") or "").upper() != side:
            continue
        if _terminal_intent(intent.get("status")):
            continue
        if not intent.get("event_id"):
            intent["event_id"] = event_id
            changed = True
            V26_STATS["intent_event_ids_recorded"] += 1
        break

    if changed:
        try:
            execution_guard._save_queue(payload, execution_guard.QUEUE_PATH)
        except Exception:
            pass


def _expire_unsubmitted_price_fish_intent(
    market: dict,
    side: str,
    target: float,
) -> None:
    """Invalidate only local, not-yet-submitted staging records.

    This does not cancel or modify a Kalshi order. Submitted/fill statuses are
    deliberately untouched and remain under the user's control.
    """
    ticker = str(market.get("ticker") or "")
    side = str(side or "").upper()
    target = _safe_float(target)
    if not ticker or side not in {"YES", "NO"} or target is None:
        return
    try:
        payload = execution_guard._load_queue(execution_guard.QUEUE_PATH)
    except Exception:
        return

    changed = False
    for intent in payload.get("intents", []):
        if str(intent.get("ticker") or "") != ticker:
            continue
        if str(intent.get("side") or "").upper() != side:
            continue
        if str(intent.get("lane") or "").lower() != "price_fish":
            continue
        if str(intent.get("status") or "").lower() not in {"awaiting_approval", "approved"}:
            continue
        price = _safe_float(intent.get("limit_price"))
        if price is None or abs(price - target) > 0.001:
            continue
        intent["status"] = "expired_stale_edge"
        intent["reconciliation_note"] = "v26_exact_target_no_longer_qualifies"
        changed = True

    if changed:
        try:
            execution_guard._save_queue(payload, execution_guard.QUEUE_PATH)
        except Exception:
            pass


def install_v26_patch() -> None:
    watcher.WATCHER_VERSION = 26

    # v26 is a safety overlay, not a matcher/data-schema rewrite. Preserve the
    # existing fair cache/alerts rather than triggering watcher's generic purge,
    # and enrich prior alerts with event IDs when the cached fair entry has one.
    original_load_state = watcher.load_state

    def load_state_v26():
        state = original_load_state()
        _enrich_existing_alert_event_ids(state)
        state["data_semantics_version"] = 26
        state["v26_quality_controls_version"] = 1
        return state

    watcher.load_state = load_state_v26

    original_should_alert = watcher.should_alert

    def should_alert_v26(state, market, fair_entry, q, tier="primary"):
        lane = _lane(tier, q)
        n_books = int(fair_entry.get("n_books") or 0)

        if not _dispersion_ok(fair_entry):
            V26_STATS["dispersion_blocks"] += 1
            return False, ""

        if lane == "price_fish":
            if n_books < PRICE_FISH_MIN_BOOKS:
                V26_STATS["price_fish_book_blocks"] += 1
                return False, ""
            if watcher.age_hours(fair_entry.get("fetched_at")) > PRICE_FISH_MAX_FAIR_AGE_MINUTES / 60.0:
                V26_STATS["price_fish_stale_fair_blocks"] += 1
                return False, ""

        event_id = _event_id(fair_entry)
        key = _alert_key(market, q, lane)
        side = str(getattr(q, "side", "") or "").upper()
        ticker = str(market.get("ticker") or "")

        if _has_open_position_or_order(event_id, ticker, side):
            V26_STATS["open_position_blocks"] += 1
            return False, ""

        active_count = _active_event_alert_count(state, event_id, key)
        reserved = _RUN_EVENT_RESERVATIONS.get(event_id, set()) if event_id else set()
        reserved_other = {x for x in reserved if x != key}
        if event_id and active_count + len(reserved_other) >= MAX_ACTIVE_ALERTS_PER_EVENT:
            V26_STATS["correlation_blocks"] += 1
            return False, ""

        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if ok and event_id:
            _RUN_EVENT_RESERVATIONS.setdefault(event_id, set()).add(key)
        return ok, reason

    watcher.should_alert = should_alert_v26

    original_record_alert = watcher.record_alert

    def record_alert_v26(state, market, fair_entry, q, tier="primary"):
        original_record_alert(state, market, fair_entry, q, tier=tier)
        lane = _lane(tier, q)
        key = _alert_key(market, q, lane)
        alert = state.setdefault("alerts", {}).get(key)
        if isinstance(alert, dict):
            event_id = _event_id(fair_entry)
            if event_id:
                alert["event_id"] = event_id
                V26_STATS["alert_event_ids_recorded"] += 1
            alert["dispersion"] = fair_entry.get("dispersion")
            alert["n_books"] = fair_entry.get("n_books")
            alert["lane"] = lane
        _annotate_latest_intent_event_id(market, q, fair_entry)

    watcher.record_alert = record_alert_v26

    original_pass_alerts = watcher.maybe_send_pass_alerts

    def maybe_send_pass_alerts_v26(state, market_by_ticker, fair_cache, settings):
        sent = 0
        alerts = state.setdefault("alerts", {})

        # Exact-target revalidation happens BEFORE the legacy "does any fish
        # still qualify?" check. That ordering is the central v26 fix.
        for key, alert in list(alerts.items()):
            if not isinstance(alert, dict) or not alert.get("active"):
                continue
            tier = str(alert.get("tier") or "primary").lower()
            lane = str(alert.get("lane") or "").lower()
            if tier != "speculative" and lane != "price_fish":
                continue

            parts = key.split("|")
            if len(parts) < 2:
                continue
            ticker, side = parts[0], parts[1].upper()
            market = market_by_ticker.get(ticker)
            fair_entry = fair_cache.get(ticker)
            target = _safe_float(alert.get("entry_price"))
            if not market or not fair_entry or target is None:
                continue

            if _price_fish_old_target_valid(
                market, fair_entry, settings, side=side, target=target
            ):
                continue

            msg = (
                f"{side} — {market.get('title','')[:150]}\n"
                f"CANCEL the old resting limit at {watcher.cents(target)}.\n"
                "Fresh sportsbook fair value no longer gives normal PM Edge "
                "edge at that exact price.\n"
                "A newly lower Price Fish target does NOT keep this old order valid."
            )
            if watcher.send_push(
                "⚠️ PM EDGE PRICE FISH — CANCEL STALE LIMIT", msg, priority=0
            ):
                alert["active"] = False
                alert["pass_sent_at"] = watcher.iso_now()
                alert["v26_cancel_reason"] = "exact_target_no_longer_qualifies"
                V26_STATS["stale_price_fish_cancels"] += 1
                sent += 1
                _expire_unsubmitted_price_fish_intent(market, side, target)

        # Let v22/v18 handle all remaining Primary/VALUE/Price Fish pass rules.
        sent += int(original_pass_alerts(state, market_by_ticker, fair_cache, settings) or 0)
        return sent

    watcher.maybe_send_pass_alerts = maybe_send_pass_alerts_v26

    original_send_push = watcher.send_push

    def send_push_v26(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v20 Auto-Sized is live",
            "✅ PM Edge Watch v21 Overnight Look-Ahead is live",
            "✅ PM Edge Watch v22 Value Volume is live",
            "✅ PM Edge Watch v23 Approval Execution is live",
            "✅ PM Edge Watch v24 Two-Tap Approval is live",
        }:
            title = "✅ PM Edge Watch v26 Quality Guard is live"
            message = (
                f"{message}\n"
                f"v26: one active entry per event; max book dispersion "
                f"{MAX_BOOK_DISPERSION:.1%}; Price Fish requires "
                f"{PRICE_FISH_MIN_BOOKS}+ books, <= {PRICE_FISH_MAX_FAIR_AGE_MINUTES:.0f}m fair age, "
                "and exact-target revalidation."
            )
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v26


def self_test() -> None:
    assert _state_tier("primary") == "primary"
    assert _state_tier("value") == "value"
    assert _state_tier("price_fish") == "speculative"
    assert _dispersion_ok({"dispersion": MAX_BOOK_DISPERSION})
    assert not _dispersion_ok({"dispersion": MAX_BOOK_DISPERSION + 0.001})

    class FakeQ:
        side = "YES"
        pmedge_lane = "value"

    assert _lane("speculative", FakeQ()) == "value"

    state = {
        "alerts": {
            "A|YES": {"active": True, "event_id": "evt-1"},
            "B|YES|VALUE": {"active": False, "event_id": "evt-1"},
        }
    }
    assert _active_event_alert_count(state, "evt-1", "C|YES") == 1
    assert _active_event_alert_count(state, "evt-1", "A|YES") == 0
    print("PM Edge v26 quality guard self-test: PASS")


def main() -> int:
    # v25 delegates installation + watcher execution to v24.main(). Wrap the
    # final v24 installer so v26 is applied after every legacy patch but before
    # watcher.main() starts, while retaining all v25 post-run tracking.
    original_install_v24 = v24.install_v24_patch

    def install_v24_then_v26():
        original_install_v24()
        install_v26_patch()

    v24.install_v24_patch = install_v24_then_v26
    try:
        rc = v25.main()
    finally:
        v24.install_v24_patch = original_install_v24

    print("PM_EDGE_V26_QUALITY_GUARD " + json.dumps(V26_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
