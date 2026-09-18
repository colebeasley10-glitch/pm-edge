"""PM Edge v25 launcher: automatic settlement and performance tracking.

v25 runs the complete v24 scanner/approval/reconciliation flow, then uses the
GET-only Kalshi connection plus public market settlement data to finalize PM
Edge records with fill status, fees, result, net P/L, ROI, and fill rate. It
also sends one compact Pushover confirmation the first time a submitted PM Edge
order is observed on Kalshi, so the user can tell which alert has been taken.
"""
from __future__ import annotations

import json

from kalshi_readonly import KalshiReadOnlyClient
import performance_tracker
import pm_edge_runner_v24 as v24
import position_signals
import pushover_status


def self_test() -> None:
    summary = performance_tracker.performance_summary(payload={"version": 1, "records": []})
    assert summary["tracked_orders"] == 0
    assert summary["record"] == "0-0"
    assert summary["net_pl"] == 0.0
    print("PM Edge v25 performance launcher self-test: PASS")


def main() -> int:
    # Read current positions before the scan so held markets get priority for
    # fresh sportsbook fair values. This is GET-only and cannot trade.
    client = None
    held_tickers = set()
    try:
        client = KalshiReadOnlyClient.from_env()
        if client is not None:
            for row in client.get_positions(limit=200):
                try:
                    position = float(
                        row.get("position_fp")
                        if row.get("position_fp") is not None
                        else row.get("position") or 0
                    )
                except (TypeError, ValueError):
                    position = 0.0
                ticker = str(row.get("ticker") or "")
                if ticker and abs(position) >= 1:
                    held_tickers.add(ticker)
            v24.watcher.HELD_POSITION_TICKERS = held_tickers
    except Exception as exc:
        print("PM_EDGE_HELD_POSITION_PREFETCH_ERROR", type(exc).__name__, str(exc)[:300])

    rc = v24.main()

    # v24 has already reconciled the order queue against Kalshi at this point.
    # Emit one follow-up confirmation for newly observed submitted orders. This
    # does not place, modify, or cancel anything on Kalshi.
    try:
        push_status = pushover_status.notify_taken_orders()
    except Exception as exc:
        push_status = {"error": f"{type(exc).__name__}: {exc}"}
        print("PM_EDGE_PUSH_STATUS_ERROR", type(exc).__name__, str(exc)[:300])

    result = {
        "configured": False,
        "checked": 0,
        "finalized": 0,
        "errors": 0,
        "summary": performance_tracker.performance_summary(),
    }
    signal_result = {"configured": False, "candidates": 0, "sent": 0}
    try:
        if client is None:
            client = KalshiReadOnlyClient.from_env()
        if client is not None:
            result = performance_tracker.update_performance(client)
            result["configured"] = True
            try:
                signal_result = position_signals.notify_position_signals(client)
                signal_result["configured"] = True
            except Exception as exc:
                signal_result = {
                    "configured": True,
                    "error": f"{type(exc).__name__}: {exc}",
                    "candidates": 0,
                    "sent": 0,
                }
                print("PM_EDGE_POSITION_SIGNAL_ERROR", type(exc).__name__, str(exc)[:300])
    except Exception as exc:
        result = {
            "configured": True,
            "error": f"{type(exc).__name__}: {exc}",
            "summary": performance_tracker.performance_summary(),
        }
        print("PM_EDGE_PERFORMANCE_ERROR", type(exc).__name__, str(exc)[:300])

    print("PM_EDGE_PUSH_STATUS " + json.dumps(push_status, sort_keys=True))
    print("PM_EDGE_HELD_POSITIONS " + json.dumps({"count": len(held_tickers)}, sort_keys=True))
    print("PM_EDGE_POSITION_SIGNALS " + json.dumps(signal_result, sort_keys=True))
    print("PM_EDGE_V25_PERFORMANCE " + json.dumps(result, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
