"""PM Edge v27: first-audit calibration guards and durable signal tracking.

The first 50+ tracked decisions showed losses concentrated in MLB and NFL team
totals, with KBO also weak. v27 makes targeted, reversible changes rather than
rewriting the whole model:

* MLB and NFL team totals: Primary only, 4+ books, 7.5%+ net edge, 0.5x size.
* KBO: Primary only, 4+ books, 6.5%+ net edge, 0.5x size.
* Existing restricted VALUE/Price Fish alerts are invalidated under the new
  rules instead of being allowed to linger.
* Every sent signal is appended to a durable audit CSV with its original fair
  probability, edge, book count, lane, price and market metadata.

All v26 adverse-selection/correlation safeguards remain in force.
"""
from __future__ import annotations

import json
import math
import os

import pm_edge_runner_v22 as v22
import pm_edge_runner_v23 as v23
import pm_edge_runner_v24 as v24
import pm_edge_runner_v26 as v26
import signal_tracker
import watcher

MLB_MIN_EDGE = float(os.getenv("PM_EDGE_AUDIT_MLB_MIN_NET_EDGE", "0.075"))
NFL_TEAM_TOTAL_MIN_EDGE = float(os.getenv("PM_EDGE_AUDIT_NFL_TEAM_TOTAL_MIN_NET_EDGE", "0.075"))
KBO_MIN_EDGE = float(os.getenv("PM_EDGE_AUDIT_KBO_MIN_NET_EDGE", "0.065"))
RESTRICTED_MIN_BOOKS = max(3, int(os.getenv("PM_EDGE_AUDIT_RESTRICTED_MIN_BOOKS", "4")))
REDUCED_RISK_MULTIPLIER = min(
    1.0, max(0.1, float(os.getenv("PM_EDGE_AUDIT_REDUCED_RISK_MULTIPLIER", "0.50")))
)

V27_STATS = {
    "mlb_blocks": 0,
    "nfl_team_total_blocks": 0,
    "kbo_blocks": 0,
    "secondary_lane_blocks": 0,
    "reduced_size_alerts": 0,
    "audit_pass_alerts": 0,
    "signals_recorded": 0,
    "signal_duplicates": 0,
    "signal_errors": 0,
}

_SIZE_OVERRIDES: dict[tuple[str, str, int], dict] = {}


def _safe_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _audit_bucket(market: dict, fair_entry: dict) -> str:
    ticker = str(market.get("ticker") or "").upper()
    sport_key = str(fair_entry.get("sport_key") or "").lower()
    market_type = str(fair_entry.get("market_type") or "").lower()

    if ticker.startswith("KXNFLTEAMTOTAL") or (
        "nfl" in sport_key and market_type in {"team_total", "teamtotal"}
    ):
        return "nfl_team_total"
    if ticker.startswith("KXMLB") or sport_key == "baseball_mlb":
        return "mlb"
    if ticker.startswith("KXKBO") or "kbo" in sport_key:
        return "kbo"
    return ""


def _bucket_threshold(bucket: str) -> float | None:
    return {
        "mlb": MLB_MIN_EDGE,
        "nfl_team_total": NFL_TEAM_TOTAL_MIN_EDGE,
        "kbo": KBO_MIN_EDGE,
    }.get(bucket)


def _block_stat(bucket: str) -> None:
    key = f"{bucket}_blocks"
    if key in V27_STATS:
        V27_STATS[key] += 1


def _mark_risk(q, bucket: str):
    try:
        setattr(q, "pmedge_risk_multiplier", REDUCED_RISK_MULTIPLIER)
        setattr(q, "pmedge_audit_bucket", bucket)
    except Exception:
        pass
    return q


def _force_unqualified(q, bucket: str):
    try:
        setattr(q, "qualifies", False)
        setattr(q, "pmedge_audit_bucket", bucket)
        setattr(q, "pmedge_risk_multiplier", REDUCED_RISK_MULTIPLIER)
        return q
    except Exception:
        from types import SimpleNamespace
        return SimpleNamespace(
            qualifies=False,
            side=getattr(q, "side", None),
            recommended_limit=getattr(q, "recommended_limit", None),
            hard_max=getattr(q, "hard_max", None),
            net_edge=getattr(q, "net_edge", None),
            best_ask=getattr(q, "best_ask", None),
            execution=getattr(q, "execution", None),
            pmedge_audit_bucket=bucket,
            pmedge_risk_multiplier=REDUCED_RISK_MULTIPLIER,
        )


def restricted_score_market(market, fair_entry, settings, base_min_edge=None):
    """Apply v27's sport/market-specific qualification gates."""
    bucket = _audit_bucket(market, fair_entry)
    if not bucket:
        return None

    threshold = _bucket_threshold(bucket)
    n_books = int(fair_entry.get("n_books") or 0)

    # The weak audit buckets no longer get lower-conviction VALUE/Price Fish.
    if base_min_edge is not None:
        q = v22._raw_score_market(market, fair_entry, settings, base_min_edge=base_min_edge)
        V27_STATS["secondary_lane_blocks"] += 1
        _block_stat(bucket)
        return _force_unqualified(q, bucket)

    if n_books < RESTRICTED_MIN_BOOKS:
        q = v22._raw_score_market(market, fair_entry, settings, base_min_edge=threshold)
        _block_stat(bucket)
        return _force_unqualified(q, bucket)

    q = v22._raw_score_market(market, fair_entry, settings, base_min_edge=threshold)
    if not getattr(q, "qualifies", False):
        _block_stat(bucket)
        return _force_unqualified(q, bucket)
    return _mark_risk(q, bucket)


def _reduced_order(original_size_for_intent, q, lane: str):
    order = original_size_for_intent(q, lane)
    if order is None:
        return None
    multiplier = _safe_float(getattr(q, "pmedge_risk_multiplier", 1.0), 1.0)
    if multiplier is None or multiplier >= 0.999:
        return order

    original_shares = int(order.get("shares") or 0)
    if original_shares < 1:
        return order
    shares = max(1, int(math.floor(original_shares * multiplier + 1e-12)))
    price = float(order["price"])
    revised = dict(order)
    revised.update({
        "shares": shares,
        "cost": shares * price,
        "max_loss": shares * price,
        "gross_profit_if_correct": shares * (1.0 - price),
        "gross_payout_if_correct": float(shares),
        "risk_capped": True,
        "audit_reduced": True,
        "audit_original_shares": original_shares,
    })
    return revised


def _size_key(q, market: dict) -> tuple[str, str, int] | None:
    price = _safe_float(getattr(q, "recommended_limit", None))
    side = str(getattr(q, "side", "") or "").upper()
    if price is None or side not in {"YES", "NO"}:
        return None
    return side, str(market.get("title") or "")[:170], int(round(price * 1000))


def _audit_sizing_block(details: dict) -> str:
    side = details["side"]
    shares = int(details["shares"])
    price = float(details["price"])
    max_loss = float(details["max_loss"])
    bucket = str(details["bucket"]).replace("_", " ").upper()
    return (
        f"AUDIT SIZE ({bucket}): 0.5x\n"
        f"BUY {side} — {shares} SHARES @ {price * 100:.1f}¢ OR LOWER\n"
        f"MAX LOSS @ LIMIT: USD {max_loss:.2f}"
    )


def install_v27_patch() -> None:
    watcher.WATCHER_VERSION = 27

    original_score_market = watcher.score_market

    def score_market_v27(market, fair_entry, settings, base_min_edge=None):
        restricted = restricted_score_market(
            market, fair_entry, settings, base_min_edge=base_min_edge
        )
        if restricted is not None:
            return restricted
        return original_score_market(
            market, fair_entry, settings, base_min_edge=base_min_edge
        )

    watcher.score_market = score_market_v27

    original_size_for_intent = v23._size_for_intent

    def size_for_intent_v27(q, lane: str):
        return _reduced_order(original_size_for_intent, q, lane)

    v23._size_for_intent = size_for_intent_v27

    original_should_alert = watcher.should_alert

    def should_alert_v27(state, market, fair_entry, q, tier="primary"):
        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if ok and getattr(q, "pmedge_risk_multiplier", 1.0) < 0.999:
            key = _size_key(q, market)
            order = v23._size_for_intent(q, v23._effective_lane(tier, q))
            if key and order:
                _SIZE_OVERRIDES[key] = {
                    "side": str(getattr(q, "side", "")).upper(),
                    "shares": int(order["shares"]),
                    "price": float(order["price"]),
                    "max_loss": float(order["max_loss"]),
                    "bucket": str(getattr(q, "pmedge_audit_bucket", "") or "restricted"),
                }
        return ok, reason

    watcher.should_alert = should_alert_v27

    original_record_alert = watcher.record_alert

    def record_alert_v27(state, market, fair_entry, q, tier="primary"):
        original_record_alert(state, market, fair_entry, q, tier=tier)
        lane = v23._effective_lane(tier, q)
        try:
            result = signal_tracker.record_signal(
                market, fair_entry, q, lane=lane, source_version=27
            )
            if result.get("recorded"):
                V27_STATS["signals_recorded"] += 1
            elif result.get("reason") == "duplicate":
                V27_STATS["signal_duplicates"] += 1
        except Exception as exc:
            V27_STATS["signal_errors"] += 1
            print("PM_EDGE_SIGNAL_LEDGER_ERROR", type(exc).__name__, str(exc)[:240])

    watcher.record_alert = record_alert_v27

    original_send_push = watcher.send_push

    def send_push_v27(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v20 Auto-Sized is live",
            "✅ PM Edge Watch v21 Overnight Look-Ahead is live",
            "✅ PM Edge Watch v22 Value Volume is live",
            "✅ PM Edge Watch v23 Approval Execution is live",
            "✅ PM Edge Watch v24 Two-Tap Approval is live",
            "✅ PM Edge Watch v26 Quality Guard is live",
        }:
            title = "✅ PM Edge Watch v27 Audit Guard is live"
            message = (
                f"{message}\n"
                "v27 audit calibration: MLB/NFL team totals Primary-only at "
                f"{MLB_MIN_EDGE:.1%}+ / {NFL_TEAM_TOTAL_MIN_EDGE:.1%}+; "
                f"KBO {KBO_MIN_EDGE:.1%}+; {RESTRICTED_MIN_BOOKS}+ books; "
                f"{REDUCED_RISK_MULTIPLIER:.1f}x size."
            )

        key = v24._raw_message_key(message)
        details = _SIZE_OVERRIDES.get(key) if key else None
        upper = str(title).upper()
        if (
            details
            and "PM EDGE" in upper
            and "PASS" not in upper
            and "IS LIVE" not in upper
            and not str(message).startswith("AUDIT SIZE")
        ):
            message = _audit_sizing_block(details) + "\n\n" + str(message)
            V27_STATS["reduced_size_alerts"] += 1
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v27

    original_pass_alerts = watcher.maybe_send_pass_alerts

    def maybe_send_pass_alerts_v27(state, market_by_ticker, fair_cache, settings):
        sent = 0
        for key, alert in list(state.setdefault("alerts", {}).items()):
            if not isinstance(alert, dict) or not alert.get("active"):
                continue
            parts = str(key).split("|")
            if len(parts) < 2:
                continue
            ticker, side = parts[0], parts[1]
            market = market_by_ticker.get(ticker)
            fair_entry = fair_cache.get(ticker)
            if not market or not fair_entry:
                continue
            bucket = _audit_bucket(market, fair_entry)
            if not bucket:
                continue

            tier = str(alert.get("tier") or "primary").lower()
            if tier == "primary":
                q = restricted_score_market(market, fair_entry, settings, base_min_edge=None)
            else:
                q = restricted_score_market(
                    market,
                    fair_entry,
                    settings,
                    base_min_edge=watcher.SPECULATIVE_MIN_EDGE,
                )
            if q is not None and getattr(q, "qualifies", False):
                continue

            threshold = _bucket_threshold(bucket) or 0.0
            msg = (
                f"{side} — {market.get('title','')[:150]}\n"
                "PASS / CANCEL OLD LIMIT.\n"
                f"v27 audit guard now requires Primary-only, {RESTRICTED_MIN_BOOKS}+ books "
                f"and {threshold:.1%}+ net edge for this bucket."
            )
            if watcher.send_push("⚠️ PM EDGE AUDIT GUARD — PASS NOW", msg, priority=0):
                alert["active"] = False
                alert["pass_sent_at"] = watcher.iso_now()
                alert["v27_pass_reason"] = "audit_restriction"
                sent += 1
                V27_STATS["audit_pass_alerts"] += 1

        sent += int(original_pass_alerts(state, market_by_ticker, fair_cache, settings) or 0)
        return sent

    watcher.maybe_send_pass_alerts = maybe_send_pass_alerts_v27


def self_test() -> None:
    class FakeSettings:
        maker_fee_rate = 0.0175
        taker_fee_rate = 0.07

    fair = {
        "fair_yes_prob": 0.62,
        "n_books": 4,
        "sport_key": "baseball_mlb",
        "sport_title": "MLB",
        "market_type": "h2h",
    }
    weak_market = {
        "ticker": "KXMLBGAME-TEST",
        "title": "MLB test",
        "yes_bid": 0.55,
        "yes_ask": 0.56,
        "no_bid": 0.43,
        "no_ask": 0.44,
    }
    q_weak = restricted_score_market(weak_market, fair, FakeSettings(), None)
    assert q_weak is not None and not q_weak.qualifies

    strong_market = dict(
        weak_market,
        yes_bid=0.49,
        yes_ask=0.50,
        no_bid=0.49,
        no_ask=0.50,
    )
    q_strong = restricted_score_market(strong_market, fair, FakeSettings(), None)
    assert q_strong is not None and q_strong.qualifies
    assert getattr(q_strong, "pmedge_risk_multiplier") == REDUCED_RISK_MULTIPLIER

    q_secondary = restricted_score_market(
        strong_market, fair, FakeSettings(), base_min_edge=0.03
    )
    assert q_secondary is not None and not q_secondary.qualifies

    q_books = restricted_score_market(
        strong_market, {**fair, "n_books": 3}, FakeSettings(), None
    )
    assert q_books is not None and not q_books.qualifies

    print("PM Edge v27 audit guard self-test: PASS")


def main() -> int:
    original_install_v26 = v26.install_v26_patch

    def install_v26_then_v27():
        original_install_v26()
        install_v27_patch()

    v26.install_v26_patch = install_v26_then_v27
    try:
        rc = v26.main()
    finally:
        v26.install_v26_patch = original_install_v26

    print("PM_EDGE_V27_AUDIT_GUARD " + json.dumps(V27_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
