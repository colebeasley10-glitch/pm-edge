from __future__ import annotations

from pathlib import Path

import pm_edge_runner_v26 as v26
import watcher


def main() -> None:
    # Quota-constrained deployment: 500 credits/month => stay under ~16.7/day.
    assert watcher.DAILY_ODDS_BUDGET == 15
    assert watcher.MAX_BULK_SPORTS_PER_RUN == 2

    # Bulk paid refresh is deliberately moneyline-only (one market credit).
    assert watcher.BULK_FEATURED_KEY_FOR_TYPE == {"h2h": "h2h"}

    # Keep the existing fair TTL schedule. The 95-minute Price Fish guard must
    # sit just above the imminent 1.5-hour fair TTL to avoid a dead band.
    class EventSoon(dict):
        pass

    original_event_hours = watcher.event_hours
    try:
        watcher.event_hours = lambda _event: 2.0
        assert abs(watcher.fair_ttl_hours(EventSoon()) - 1.5) < 1e-9
        assert watcher.fair_ttl_hours(EventSoon()) * 60 < v26.PRICE_FISH_MAX_FAIR_AGE_MINUTES

        watcher.event_hours = lambda _event: 8.0
        assert abs(watcher.fair_ttl_hours({}) - 3.0) < 1e-9
        watcher.event_hours = lambda _event: 24.0
        assert abs(watcher.fair_ttl_hours({}) - 6.0) < 1e-9
        watcher.event_hours = lambda _event: 48.0
        assert abs(watcher.fair_ttl_hours({}) - 12.0) < 1e-9
    finally:
        watcher.event_hours = original_event_hours

    assert abs(v26.PRICE_FISH_MAX_FAIR_AGE_MINUTES - 95.0) < 1e-9

    workflow = Path(".github/workflows/pm-edge-watch.yml").read_text(encoding="utf-8")
    assert 'PM_EDGE_ODDS_DAILY_BUDGET: "15"' in workflow
    assert 'PM_EDGE_MAX_BULK_SPORTS_PER_RUN: "2"' in workflow
    assert 'PM_EDGE_PRICE_FISH_MAX_FAIR_AGE_MINUTES: "95"' in workflow

    cache_block = workflow.split("- name: Restore watcher state", 1)[1].split("- name: Run PM Edge watcher", 1)[0]
    assert ".pm_edge_watch_state.json" not in cache_block
    assert "[ -f .pm_edge_watch_state.json ] && git add .pm_edge_watch_state.json" in workflow

    # PR A must not smuggle PR B calibration/tiering changes.
    assert 'PM_EDGE_MIN_NET_EDGE: "0.04"' in workflow
    assert 'PM_EDGE_STANDARD_MIN_NET_EDGE: "0.030"' in workflow
    assert 'PM_EDGE_STANDARD_THREE_BOOK_EDGE: "0.035"' in workflow
    assert 'PM_EDGE_MAX_BOOK_DISPERSION: "0.055"' in workflow
    assert 'PM_EDGE_MIN_MATCH_CONFIDENCE: "0.82"' in workflow
    assert 'PM_EDGE_AUDIT_MLB_MIN_NET_EDGE: "0.075"' in workflow
    assert 'PM_EDGE_AUDIT_NFL_TEAM_TOTAL_MIN_NET_EDGE: "0.075"' in workflow
    assert 'PM_EDGE_AUDIT_KBO_MIN_NET_EDGE: "0.065"' in workflow

    print("PM Edge Commit 6 data-pipeline tests: PASS")


if __name__ == "__main__":
    main()
