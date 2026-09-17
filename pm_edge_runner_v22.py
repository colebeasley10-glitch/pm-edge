"""PM Edge v22 launcher.

Adds a higher-volume VALUE lane without weakening Primary or Price Fish.

VALUE lane:
* Current-quote opportunities only (not fantasy passive fills).
* Minimum 3-book support.
* 2.5% net edge with 4+ books; 3.0% with exactly 3 books.
* Rejects ultra-long-shot/extreme prices by default (20c-80c target).
* Requires at least 25% fair probability for the side being bought.
* Uses separate alert state and a separate daily cap.
* Uses half-size auto sizing by default: about $2.50 target gross profit,
  max $5 contract cost/loss, versus Primary/Price Fish's existing $5/$10.
* Price Fish remains available when the current quote does not clear VALUE.

The existing watcher still owns event matching, stale-data checks, futures/segment
filters, suspicious-edge ceilings, alert freshness, quiet hours, and no-chase safety.
This launcher is alert-only. It never places a real-money order.
"""
from __future__ import annotations

import json
import math
import os
import re
from types import SimpleNamespace

import watcher
import pm_edge_runner_v21 as v21

VALUE_MIN_EDGE = float(os.getenv("PM_EDGE_VALUE_MIN_NET_EDGE", "0.025"))
VALUE_THREE_BOOK_EDGE = float(os.getenv("PM_EDGE_VALUE_THREE_BOOK_EDGE", "0.03"))
VALUE_MIN_BOOKS = int(os.getenv("PM_EDGE_VALUE_MIN_BOOKS", "3"))
VALUE_MIN_TARGET = float(os.getenv("PM_EDGE_VALUE_MIN_TARGET", "0.20"))
VALUE_MAX_TARGET = float(os.getenv("PM_EDGE_VALUE_MAX_TARGET", "0.80"))
VALUE_MIN_FAIR_SIDE_PROB = float(os.getenv("PM_EDGE_VALUE_MIN_FAIR_SIDE_PROB", "0.25"))
VALUE_DAILY_CAP = int(os.getenv("PM_EDGE_VALUE_DAILY_CAP", "20"))
SECONDARY_MAX_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_MAX_SECONDARY_ALERTS_PER_RUN", "6"))

VALUE_TARGET_WIN_PROFIT_DOLLARS = float(
    os.getenv("PM_EDGE_VALUE_TARGET_WIN_PROFIT_DOLLARS", "2.50")
)
VALUE_MAX_RISK_DOLLARS = float(os.getenv("PM_EDGE_VALUE_MAX_RISK_DOLLARS", "5.00"))

V22_STATS = {
    "value_candidates_built": 0,
    "value_daily_cap_blocks": 0,
    "value_alerts_recorded": 0,
    "value_sizing_applied": 0,
    "value_pass_alerts": 0,
    "price_fish_fallbacks": 0,
}

_VALUE_SIGNATURES: set[tuple[str, str, str, int]] = set()


def _safe_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


def _raw_score_market(market, fair_entry, settings, base_min_edge=None):
    tick = watcher.tick_size_for_price(
        market, market.get("yes_bid") or market.get("yes_ask") or 0.50
    )
    n_books = int(fair_entry.get("n_books") or 0)

    if base_min_edge is None:
        base = watcher.MIN_EDGE
        floor_3, floor_2 = 0.045, 0.06
    else:
        base = float(base_min_edge)
        floor_3, floor_2 = base + 0.005, base + 0.02

    if n_books >= 4:
        required_edge = base
    elif n_books == 3:
        required_edge = max(base, floor_3)
    else:
        required_edge = max(base, floor_2)

    return watcher.best_binary_trade(
        fair_yes_prob=float(fair_entry["fair_yes_prob"]),
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        no_bid=market.get("no_bid"),
        no_ask=market.get("no_ask"),
        min_net_edge=required_edge,
        nonfee_buffer=watcher.MODEL_BUFFER + watcher.SLIPPAGE_BUFFER,
        maker_fee_rate=settings.maker_fee_rate,
        taker_fee_rate=settings.taker_fee_rate,
        tick_size=tick,
    )


def _side_probability(fair_entry: dict, side: str) -> float | None:
    yes_prob = _safe_float(fair_entry.get("fair_yes_prob"))
    if yes_prob is None:
        return None
    side = str(side or "").upper()
    if side == "YES":
        return yes_prob
    if side == "NO":
        return 1.0 - yes_prob
    return None


def _value_required_edge(n_books: int) -> float | None:
    if n_books < VALUE_MIN_BOOKS:
        return None
    if n_books == 3:
        return max(VALUE_MIN_EDGE, VALUE_THREE_BOOK_EDGE)
    return VALUE_MIN_EDGE


def find_value_trade(market: dict, fair_entry: dict, settings):
    n_books = int(fair_entry.get("n_books") or 0)
    required = _value_required_edge(n_books)
    if required is None:
        return None

    tick = watcher.tick_size_for_price(
        market, market.get("yes_bid") or market.get("yes_ask") or 0.50
    )
    q = watcher.best_binary_trade(
        fair_yes_prob=float(fair_entry["fair_yes_prob"]),
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        no_bid=market.get("no_bid"),
        no_ask=market.get("no_ask"),
        min_net_edge=required,
        nonfee_buffer=watcher.MODEL_BUFFER + watcher.SLIPPAGE_BUFFER,
        maker_fee_rate=settings.maker_fee_rate,
        taker_fee_rate=settings.taker_fee_rate,
        tick_size=tick,
    )
    if not getattr(q, "qualifies", False):
        return None

    rec = _safe_float(getattr(q, "recommended_limit", None))
    if rec is None or rec < VALUE_MIN_TARGET - 1e-12 or rec > VALUE_MAX_TARGET + 1e-12:
        return None

    side_prob = _side_probability(fair_entry, getattr(q, "side", ""))
    if side_prob is None or side_prob + 1e-12 < VALUE_MIN_FAIR_SIDE_PROB:
        return None

    try:
        setattr(q, "pmedge_lane", "value")
    except Exception:
        q = SimpleNamespace(
            qualifies=getattr(q, "qualifies", False),
            side=getattr(q, "side", None),
            recommended_limit=getattr(q, "recommended_limit", None),
            hard_max=getattr(q, "hard_max", None),
            net_edge=getattr(q, "net_edge", None),
            best_ask=getattr(q, "best_ask", None),
            execution=getattr(q, "execution", None),
            pmedge_lane="value",
        )

    sport = str(fair_entry.get("sport_title") or "Sports")
    title = str(market.get("title") or "")[:170]
    cents_int = int(round(float(q.recommended_limit) * 1000))
    _VALUE_SIGNATURES.add((sport, str(q.side).upper(), title, cents_int))
    V22_STATS["value_candidates_built"] += 1
    return q


def _value_bucket(state: dict) -> dict:
    today = watcher.now_utc().date().isoformat()
    bucket = state.setdefault("value_daily", {"date": today, "sent": 0})
    if bucket.get("date") != today:
        bucket.clear()
        bucket.update({"date": today, "sent": 0})
    return bucket


def _q_lane(q) -> str:
    return str(getattr(q, "pmedge_lane", "") or "").lower()


def _parse_raw_secondary_signature(message: str):
    lines = str(message).splitlines()
    if not lines:
        return None
    sport = lines[0].strip()
    side = None
    market_title = None
    price = None

    for line in lines[1:]:
        if line.startswith("YES — ") or line.startswith("NO — "):
            side, market_title = line.split(" — ", 1)
            side = side.strip().upper()
            market_title = market_title.strip()
            break

    for line in lines:
        if line.startswith("PLACE LIMIT:") or line.startswith("BUY NOW:"):
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*¢", line)
            if m:
                price = int(round(float(m.group(1)) * 10))
                break

    if not sport or side not in {"YES", "NO"} or market_title is None or price is None:
        return None
    return sport, side, market_title, price


def _rewrite_value_push(title: str, message: str) -> tuple[str, str]:
    if not str(title).startswith("🎲 PM EDGE SPECULATIVE"):
        return title, message

    sig = _parse_raw_secondary_signature(message)
    if sig is None or sig not in _VALUE_SIGNATURES:
        return title, message

    title = str(title).replace("🎲 PM EDGE SPECULATIVE", "💎 PM EDGE VALUE", 1)
    message = str(message).replace(
        "Speculative pick: smaller edge, lower conviction. "
        "Only worth it as a passive limit order -- do not chase.",
        "VALUE LANE: smaller edge than Primary, but backed by 3+ sportsbook "
        "prices and strict price/probability filters. Smaller size; do not chase.",
    )
    return title, message


def _value_sizing(title: str, message: str):
    if not str(title).startswith("💎 PM EDGE VALUE"):
        return None

    v20 = v21.v20
    side, price, action_index = v20._extract_side_and_action_price(message)
    if side not in {"YES", "NO"} or price is None or action_index is None:
        return title, message

    order = v20.calculate_share_order(
        price,
        target_profit=VALUE_TARGET_WIN_PROFIT_DOLLARS,
        max_risk=VALUE_MAX_RISK_DOLLARS,
    )
    if order is None:
        return title, message

    lines = str(message).splitlines()
    sizing_lines = [
        "",
        "VALUE SIZE: 0.5x PM Edge base size",
        f"ORDER: BUY {side} — {order['shares']} SHARES @ {v20.cents(price)} OR LOWER",
        f"CONTRACT COST @ LIMIT: ${order['cost']:.2f}",
        f"MAX LOSS: ${order['max_loss']:.2f}",
        f"GROSS PROFIT IF CORRECT: ${order['gross_profit_if_correct']:.2f}",
    ]
    if order["risk_capped"]:
        sizing_lines.append(f"SIZE CAPPED: ${VALUE_MAX_RISK_DOLLARS:.2f} max VALUE risk")

    lines[action_index + 1:action_index + 1] = sizing_lines
    V22_STATS["value_sizing_applied"] += 1
    return title, "\n".join(lines).strip()


def _value_still_qualifies(market: dict, fair_entry: dict, settings):
    return find_value_trade(market, fair_entry, settings)


def install_v22_patch() -> None:
    watcher.WATCHER_VERSION = 22
    watcher.MAX_SPECULATIVE_ALERTS_PER_RUN = max(
        int(watcher.MAX_SPECULATIVE_ALERTS_PER_RUN),
        max(1, SECONDARY_MAX_ALERTS_PER_RUN),
    )

    original_load_state = watcher.load_state

    def load_state_v22():
        state = original_load_state()
        if int(state.get("data_semantics_version", 0) or 0) == 21:
            state["data_semantics_version"] = 22
        state.setdefault("value_daily", {
            "date": watcher.now_utc().date().isoformat(),
            "sent": 0,
        })
        return state

    watcher.load_state = load_state_v22

    price_fish_score = watcher.score_market

    def score_market_v22(market, fair_entry, settings, base_min_edge=None):
        if base_min_edge is None:
            return _raw_score_market(market, fair_entry, settings, base_min_edge=None)

        q_value = find_value_trade(market, fair_entry, settings)
        if q_value is not None:
            return q_value

        V22_STATS["price_fish_fallbacks"] += 1
        return price_fish_score(
            market, fair_entry, settings, base_min_edge=base_min_edge
        )

    watcher.score_market = score_market_v22

    original_should_alert = watcher.should_alert

    def should_alert_v22(state, market, fair_entry, q, tier="primary"):
        effective_tier = "value" if tier == "speculative" and _q_lane(q) == "value" else tier
        if effective_tier == "value":
            if int(_value_bucket(state).get("sent", 0) or 0) >= VALUE_DAILY_CAP:
                V22_STATS["value_daily_cap_blocks"] += 1
                return False, ""
        return original_should_alert(
            state, market, fair_entry, q, tier=effective_tier
        )

    watcher.should_alert = should_alert_v22

    original_record_alert = watcher.record_alert

    def record_alert_v22(state, market, fair_entry, q, tier="primary"):
        effective_tier = "value" if tier == "speculative" and _q_lane(q) == "value" else tier
        original_record_alert(
            state, market, fair_entry, q, tier=effective_tier
        )
        if effective_tier == "value":
            bucket = _value_bucket(state)
            bucket["sent"] = int(bucket.get("sent", 0) or 0) + 1
            V22_STATS["value_alerts_recorded"] += 1

    watcher.record_alert = record_alert_v22

    def maybe_send_pass_alerts_v22(state, market_by_ticker, fair_cache, settings):
        sent = 0
        for key, alert in list(state.setdefault("alerts", {}).items()):
            if not alert.get("active"):
                continue

            parts = key.split("|")
            if len(parts) < 2:
                continue
            ticker, side = parts[0], parts[1]
            tier = str(alert.get("tier", "primary") or "primary").lower()
            market = market_by_ticker.get(ticker)
            fair_entry = fair_cache.get(ticker)
            if not market or not fair_entry:
                continue

            event = {"commence_time": fair_entry.get("commence_time")}
            if watcher.age_hours(fair_entry.get("fetched_at")) > max(
                8.0, watcher.fair_ttl_hours(event) * 2
            ):
                continue

            if tier == "value":
                q = _value_still_qualifies(market, fair_entry, settings)
                if q is not None:
                    continue
                threshold_text = f"{VALUE_MIN_EDGE * 100:.1f}%+ VALUE"
                title = "⚠️ PM EDGE VALUE — PASS NOW"
                lane_note = "Current quote no longer clears the VALUE rules."
            elif tier == "speculative":
                q = price_fish_score(
                    market,
                    fair_entry,
                    settings,
                    base_min_edge=watcher.SPECULATIVE_MIN_EDGE,
                )
                if getattr(q, "qualifies", False):
                    continue
                threshold_text = f"{watcher.MIN_EDGE * 100:.1f}% Price Fish"
                title = "⚠️ PM EDGE PRICE FISH — CANCEL / PASS"
                lane_note = "This resting Price Fish no longer clears PM Edge."
            else:
                q = _raw_score_market(market, fair_entry, settings, base_min_edge=None)
                if getattr(q, "qualifies", False):
                    continue
                threshold_text = f"{watcher.MIN_EDGE * 100:.1f}% Primary"
                title = "⚠️ PM EDGE — PASS NOW"
                lane_note = "Current quote no longer clears the Primary rules."

            msg = (
                f"{side} — {market.get('title','')[:150]}\n"
                f"{lane_note}\n"
                f"Threshold: {threshold_text}.\n"
                "Status: PASS / DO NOT CHASE."
            )
            if watcher.send_push(title, msg, priority=0):
                alert["active"] = False
                alert["pass_sent_at"] = watcher.iso_now()
                sent += 1
                if tier == "value":
                    V22_STATS["value_pass_alerts"] += 1
        return sent

    watcher.maybe_send_pass_alerts = maybe_send_pass_alerts_v22

    original_send_push = watcher.send_push

    def send_push_v22(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v20 Auto-Sized is live",
            "✅ PM Edge Watch v21 Overnight Look-Ahead is live",
        }:
            title = "✅ PM Edge Watch v22 Value Volume is live"
        title, message = _rewrite_value_push(title, message)
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v22

    v20 = v21.v20
    original_add_order_sizing = v20.add_order_sizing

    def add_order_sizing_v22(title, message):
        sized = _value_sizing(title, message)
        if sized is not None:
            return sized
        return original_add_order_sizing(title, message)

    v20.add_order_sizing = add_order_sizing_v22


def self_test() -> None:
    class FakeSettings:
        maker_fee_rate = 0.0175
        taker_fee_rate = 0.07

    fair = {"fair_yes_prob": 0.50, "n_books": 4, "sport_title": "Test"}
    market = {
        "ticker": "VALUE-1",
        "title": "Team A vs Team B: Team A wins",
        "yes_bid": 0.44,
        "yes_ask": 0.45,
        "no_bid": 0.54,
        "no_ask": 0.55,
    }
    q_primary = _raw_score_market(market, fair, FakeSettings(), None)
    q_value = find_value_trade(market, fair, FakeSettings())
    assert q_primary.qualifies is False
    assert q_value is not None
    assert _q_lane(q_value) == "value"

    assert find_value_trade(
        market, {**fair, "n_books": 2}, FakeSettings()
    ) is None

    moonshot = {
        "ticker": "VALUE-2",
        "title": "Longshot",
        "yes_bid": 0.08,
        "yes_ask": 0.09,
        "no_bid": 0.90,
        "no_ask": 0.91,
    }
    assert find_value_trade(
        moonshot,
        {"fair_yes_prob": 0.20, "n_books": 4, "sport_title": "Test"},
        FakeSettings(),
    ) is None

    state = {"alerts": {}}
    class FakeQ:
        side = "YES"
        recommended_limit = 0.45
        hard_max = 0.45
        net_edge = 0.03
        pmedge_lane = "value"
    watcher.record_alert(state, {"ticker": "X", "title": "T"}, fair, FakeQ(), tier="value")
    assert "X|YES|VALUE" in state["alerts"]

    sport = "Test"
    market_title = market["title"][:170]
    price_int = int(round(float(q_value.recommended_limit) * 1000))
    _VALUE_SIGNATURES.add((sport, str(q_value.side).upper(), market_title, price_int))
    raw_title = f"🎲 PM EDGE SPECULATIVE +{q_value.net_edge * 100:.1f}% — {q_value.side}"
    raw_msg = (
        f"{sport}\n"
        f"{q_value.side} — {market_title}\n\n"
        "Speculative pick: smaller edge, lower conviction. "
        "Only worth it as a passive limit order -- do not chase.\n\n"
        f"PLACE LIMIT: {q_value.recommended_limit * 100:.1f}¢\n"
        f"DO NOT PAY OVER: {q_value.hard_max * 100:.1f}¢\n"
        f"Current ask: {q_value.best_ask * 100:.1f}¢\n"
    )
    rewritten_title, rewritten_msg = _rewrite_value_push(raw_title, raw_msg)
    assert rewritten_title.startswith("💎 PM EDGE VALUE")
    assert "VALUE LANE:" in rewritten_msg

    value_message = (
        "SIDE TO BUY: YES\n"
        "EXACT CONTRACT: Test\n"
        "BUY NOW AT/BELOW: 45.0¢\n"
    )
    sized = _value_sizing("💎 PM EDGE VALUE +3.0% — YES", value_message)
    assert sized is not None
    assert "VALUE SIZE: 0.5x PM Edge base size" in sized[1]
    assert "MAX LOSS:" in sized[1]

    print("PM Edge v22 Value Volume self-test: PASS")


def main() -> int:
    v21.v20.v19.v18.v17.v16.install_v16_patch()
    v21.v20.v19.v18.v17.install_v17_patch()
    v21.v20.v19.v18.install_v18_patch()
    v21.v20.v19.install_v19_patch()
    v21.v20.install_v20_patch()
    v21.install_v21_patch()
    install_v22_patch()

    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v21.v20.v19.v18.v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v21.v20.v19.v18.v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(v21.v20.v19.v18.V18_STATS, sort_keys=True))
    print("PM_EDGE_V19_REALISTIC_FISH " + json.dumps(v21.v20.v19.V19_STATS, sort_keys=True))
    print("PM_EDGE_V20_AUTO_SIZE " + json.dumps(v21.v20.V20_STATS, sort_keys=True))
    print("PM_EDGE_V21_OVERNIGHT " + json.dumps(v21.V21_STATS, sort_keys=True))
    print("PM_EDGE_V22_VALUE " + json.dumps(V22_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v21.v20.v19.v18.v17.v16.self_test()
        v21.v20.v19.v18.v17.self_test()
        v21.v20.v19.v18.self_test()
        v21.v20.v19.self_test()
        v21.v20.self_test()
        v21.self_test()

        v21.v20.v19.v18.v17.v16.install_v16_patch()
        v21.v20.v19.v18.v17.install_v17_patch()
        v21.v20.v19.v18.install_v18_patch()
        v21.v20.v19.install_v19_patch()
        v21.v20.install_v20_patch()
        v21.install_v21_patch()
        install_v22_patch()

        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
