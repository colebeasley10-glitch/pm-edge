"""PM Edge v28: broader opportunity coverage with unchanged core quality.

v28 keeps every v27 audit restriction and every v26 execution-quality guard.
The changes are deliberately asymmetric:

* Primary / higher-conviction edge thresholds are unchanged.
* The existing half-size VALUE lane is formalized as STANDARD EDGE (0.5u).
  Its quality thresholds remain configurable and default to 3.5% with 4+
  books / 4.0% with 3 books.
* Price Fish is broadened back to the intended patient-limit envelope via
  deployment config: 20c+ target, 25%+ modeled side probability, and 2-8c
  below the live ask. It still never chases above the hard maximum.
* v27 restricted buckets (MLB, NFL team totals, KBO) remain Primary-only at
  their stricter audit thresholds and reduced size.
* Every scored candidate and alert-gate outcome is durably logged so sent,
  missed, suppressed, and rejected opportunities can be audited.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import candidate_tracker
import pm_edge_runner_v22 as v22
import pm_edge_runner_v23 as v23
import pm_edge_runner_v27 as v27
import watcher

STANDARD_MIN_EDGE = float(os.getenv("PM_EDGE_STANDARD_MIN_NET_EDGE", "0.035"))
STANDARD_THREE_BOOK_EDGE = float(
    os.getenv("PM_EDGE_STANDARD_THREE_BOOK_EDGE", "0.040")
)

V28_STATS = {
    "candidate_score_records": 0,
    "candidate_gate_records": 0,
    "candidate_sent_records": 0,
    "candidate_duplicates": 0,
    "candidate_errors": 0,
}


def _lane_name(tier: str, q, base_min_edge=None) -> str:
    q_lane = str(getattr(q, "pmedge_lane", "") or "").lower()
    if q_lane == "value":
        return "standard"
    if base_min_edge is not None or str(tier or "").lower() == "speculative":
        return "price_fish"
    return "primary"


def _record(market, fair_entry, q, *, lane, stage, decision, reason="") -> None:
    try:
        result = candidate_tracker.record_candidate(
            market,
            fair_entry,
            q,
            lane=lane,
            stage=stage,
            decision=decision,
            reason=reason,
            source_version=28,
        )
        if result.get("recorded"):
            key = {
                "score": "candidate_score_records",
                "gate": "candidate_gate_records",
                "sent": "candidate_sent_records",
            }.get(stage)
            if key:
                V28_STATS[key] += 1
        elif result.get("reason") == "duplicate":
            V28_STATS["candidate_duplicates"] += 1
    except Exception as exc:
        V28_STATS["candidate_errors"] += 1
        print("PM_EDGE_CANDIDATE_LEDGER_ERROR", type(exc).__name__, str(exc)[:240])


def install_v28_patch() -> None:
    watcher.WATCHER_VERSION = 28

    # Keep the stronger Primary lane unchanged. The half-size secondary lane
    # becomes an explicit STANDARD EDGE lane using the existing v22 machinery.
    v22.VALUE_MIN_EDGE = STANDARD_MIN_EDGE
    v22.VALUE_THREE_BOOK_EDGE = STANDARD_THREE_BOOK_EDGE

    # v26 intentionally stamped its semantics version to 26. Once v28 is the
    # active launcher, normalize that marker to 28 so watcher.main does not
    # purge the fair-value cache and active-alert state on every scheduled run.
    original_load_state = watcher.load_state

    def load_state_v28():
        state = original_load_state()
        state["data_semantics_version"] = 28
        state["v28_broader_coverage_version"] = 1
        return state

    watcher.load_state = load_state_v28

    original_score_market = watcher.score_market

    def score_market_v28(market, fair_entry, settings, base_min_edge=None):
        q = original_score_market(
            market, fair_entry, settings, base_min_edge=base_min_edge
        )
        lane = _lane_name("", q, base_min_edge=base_min_edge)
        qualifies = bool(
            getattr(q, "qualifies", False)
            and getattr(q, "recommended_limit", None) is not None
        )
        audit_bucket = str(getattr(q, "pmedge_audit_bucket", "") or "")
        if qualifies:
            decision = "QUALIFIED"
            reason = "clears_lane_thresholds"
        elif audit_bucket:
            decision = "REJECTED"
            reason = "audit_restriction_or_threshold"
        else:
            decision = "REJECTED"
            reason = "below_lane_threshold_or_price_guard"
        _record(
            market,
            fair_entry,
            q,
            lane=lane,
            stage="score",
            decision=decision,
            reason=reason,
        )
        return q

    watcher.score_market = score_market_v28

    original_should_alert = watcher.should_alert

    def should_alert_v28(state, market, fair_entry, q, tier="primary"):
        ok, reason = original_should_alert(
            state, market, fair_entry, q, tier=tier
        )
        lane = _lane_name(tier, q)
        _record(
            market,
            fair_entry,
            q,
            lane=lane,
            stage="gate",
            decision="ALERT_ELIGIBLE" if ok else "ALERT_SUPPRESSED",
            reason=reason or "policy_duplicate_correlation_or_position_guard",
        )
        return ok, reason

    watcher.should_alert = should_alert_v28

    original_record_alert = watcher.record_alert

    def record_alert_v28(state, market, fair_entry, q, tier="primary"):
        original_record_alert(state, market, fair_entry, q, tier=tier)
        _record(
            market,
            fair_entry,
            q,
            lane=_lane_name(tier, q),
            stage="sent",
            decision="ALERT_SENT",
            reason="pushover_sent_and_recorded",
        )

    watcher.record_alert = record_alert_v28

    original_send_push = watcher.send_push

    def send_push_v28(title, message, *args, **kwargs):
        title = str(title)
        message = str(message)

        if title == "✅ PM Edge Watch v15 is live":
            title = "✅ PM Edge Watch v28 Broader Coverage is live"
            message = (
                message
                + "\n"
                + f"v28: Primary unchanged; STANDARD EDGE 0.5u at "
                  f"{STANDARD_MIN_EDGE:.1%}+ (4+ books) / "
                  f"{STANDARD_THREE_BOOK_EDGE:.1%}+ (3 books); "
                  "broader patient Price Fish; full candidate audit logging."
            )
        elif title.startswith("💎 PM EDGE VALUE"):
            title = title.replace("💎 PM EDGE VALUE", "📈 PM EDGE STANDARD", 1)

        message = message.replace("VALUE LANE:", "STANDARD EDGE:")
        message = message.replace("VALUE SIZE: 0.5x PM Edge base size", "STANDARD SIZE: 0.5u")
        message = message.replace("max VALUE risk", "max STANDARD risk")

        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v28


def self_test() -> None:
    assert abs(STANDARD_MIN_EDGE - 0.035) < 0.02
    assert STANDARD_THREE_BOOK_EDGE >= STANDARD_MIN_EDGE

    market = {"ticker": "KXTEST-1", "title": "Test market"}
    fair = {
        "event_id": "event-1",
        "sport_key": "test_sport",
        "sport_title": "Test",
        "market_type": "h2h",
        "fair_yes_prob": 0.60,
        "n_books": 4,
        "dispersion": 0.02,
    }
    q = SimpleNamespace(
        qualifies=True,
        side="YES",
        execution="MAKE",
        recommended_limit=0.50,
        hard_max=0.52,
        net_edge=0.05,
        pmedge_risk_multiplier=0.5,
    )
    with TemporaryDirectory() as td:
        path = Path(td) / "candidates.csv"
        one = candidate_tracker.record_candidate(
            market, fair, q,
            lane="standard", stage="score", decision="QUALIFIED",
            source_version=28, path=path,
            evaluated_at="2026-09-20T20:15:00Z",
        )
        two = candidate_tracker.record_candidate(
            market, fair, q,
            lane="standard", stage="score", decision="QUALIFIED",
            source_version=28, path=path,
            evaluated_at="2026-09-20T20:16:00Z",
        )
        assert one.get("recorded") is True
        assert two.get("reason") == "duplicate"

    print("PM Edge v28 broader-coverage self-test: PASS")


def main() -> int:
    original_install_v27 = v27.install_v27_patch

    def install_v27_then_v28():
        original_install_v27()
        install_v28_patch()

    v27.install_v27_patch = install_v27_then_v28
    try:
        rc = v27.main()
    finally:
        v27.install_v27_patch = original_install_v27

    print("PM_EDGE_V28_BROADER_COVERAGE " + json.dumps(V28_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
