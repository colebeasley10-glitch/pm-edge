"""PM Edge v17 launcher.

Builds on v16's rate-limit and Odds API budget protections, then adds an
execution-safety layer for actionable Pushover alerts:

* At most one h2h recommendation per matched sportsbook event per run. The
  v15 watcher sorts actionable candidates by estimated net edge first, so the
  first qualifying h2h candidate is the strongest one and duplicate/opposing
  contracts for the same game are suppressed.
* Actionable push copy explicitly states the side to buy, exact contract,
  order type, recommended price, and absolute maximum.
* The probability label is changed to "Fair chance of this side" so a NO
  contract cannot be mistaken for the underlying contract's YES probability.

This launcher never places real-money orders; watcher.py remains alert-only.
"""
from __future__ import annotations

import json

import watcher
import pm_edge_runner_v16 as v16

V17_STATS = {
    "h2h_duplicate_alerts_suppressed": 0,
}

_H2H_ALERT_EVENTS_THIS_RUN: set[tuple[str, str]] = set()


def h2h_alert_event_key(fair_entry: dict) -> tuple[str, str] | None:
    """Return a stable key for one matched sportsbook h2h event.

    The Odds API event_id is preferable to team-name text because it keeps
    doubleheaders/back-to-back games distinct while grouping multiple Kalshi
    contracts that refer to the same underlying game.
    """
    if str(fair_entry.get("market_type") or "").lower() != "h2h":
        return None

    event_id = str(fair_entry.get("event_id") or "").strip()
    if not event_id:
        return None

    sport_key = str(fair_entry.get("sport_key") or "").strip().lower()
    return sport_key, event_id


def allow_h2h_alert(fair_entry: dict) -> bool:
    """Allow only the first qualifying h2h candidate for an event this run."""
    event_key = h2h_alert_event_key(fair_entry)
    if event_key is None:
        return True

    if event_key in _H2H_ALERT_EVENTS_THIS_RUN:
        V17_STATS["h2h_duplicate_alerts_suppressed"] += 1
        return False

    _H2H_ALERT_EVENTS_THIS_RUN.add(event_key)
    return True


def execution_safe_push(title: str, message: str) -> tuple[str, str]:
    """Rewrite actionable PM Edge push copy without changing the trade math."""
    if "PM EDGE" not in str(title).upper():
        return title, message

    lines = str(message).splitlines()
    contract_index = None
    side = None
    contract = None

    for i, line in enumerate(lines):
        if line.startswith("YES — ") or line.startswith("NO — "):
            side, contract = line.split(" — ", 1)
            contract_index = i
            break

    if contract_index is None or side not in {"YES", "NO"}:
        return title, message

    rewritten = lines[:contract_index]
    rewritten.extend([
        f"SIDE TO BUY: {side}",
        f"EXACT CONTRACT: {contract}",
        f"Meaning: buy {side} on that exact contract.",
    ])
    rewritten.extend(lines[contract_index + 1:])

    for i, line in enumerate(rewritten):
        if line.startswith("PLACE LIMIT:"):
            value = line.split(":", 1)[1].strip()
            rewritten[i] = f"PLACE LIMIT ORDER: {value}"
        elif line.startswith("BUY NOW:"):
            value = line.split(":", 1)[1].strip()
            rewritten[i] = f"BUY NOW AT/BELOW: {value}"
        elif line.startswith("DO NOT PAY OVER:"):
            value = line.split(":", 1)[1].strip()
            rewritten[i] = f"ABSOLUTE MAX: {value}"
            rewritten.insert(i + 1, "If the price is above MAX, SKIP the bet.")
            break

    rewritten = [
        "Fair chance of this side:" + line.split(":", 1)[1]
        if line.startswith("Fair chance:") else line
        for line in rewritten
    ]

    rewritten = [
        line for line in rewritten
        if line.strip() != "Do not chase above MAX."
    ]

    if "— YES" in title:
        title = title.replace("— YES", "— BUY YES")
    elif "— NO" in title:
        title = title.replace("— NO", "— BUY NO")

    return title, "\n".join(rewritten).strip()


def install_v17_patch() -> None:
    watcher.WATCHER_VERSION = 17

    original_should_alert = watcher.should_alert

    def should_alert_v17(state, market, fair_entry, q, tier="primary"):
        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if not ok:
            return ok, reason

        if not allow_h2h_alert(fair_entry):
            print(
                "H2H_DUPLICATE_ALERT_SUPPRESSED",
                fair_entry.get("event_id"),
                market.get("ticker"),
                market.get("title"),
                q.side,
                q.net_edge,
            )
            return False, ""

        return ok, reason

    watcher.should_alert = should_alert_v17

    # v16 already wrapped watcher.send_push. This wrapper runs first, makes the
    # alert explicit, and changes the heartbeat to v17 before handing off.
    original_send_push = watcher.send_push

    def send_push_v17(title, message, *args, **kwargs):
        if title == "✅ PM Edge Watch v15 is live":
            title = "✅ PM Edge Watch v17 is live"
        else:
            title, message = execution_safe_push(title, message)
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v17


def self_test() -> None:
    _H2H_ALERT_EVENTS_THIS_RUN.clear()
    V17_STATS["h2h_duplicate_alerts_suppressed"] = 0

    samsung = {
        "market_type": "h2h",
        "event_id": "kbo-samsung-doosan-2026-09-16",
        "sport_key": "baseball_kbo",
    }
    doosan_same_game = dict(samsung)
    ssg = {
        "market_type": "h2h",
        "event_id": "kbo-ssg-lotte-2026-09-16",
        "sport_key": "baseball_kbo",
    }

    assert h2h_alert_event_key(samsung) == h2h_alert_event_key(doosan_same_game)
    assert h2h_alert_event_key(samsung) != h2h_alert_event_key(ssg)
    assert allow_h2h_alert(samsung) is True
    assert allow_h2h_alert(doosan_same_game) is False
    assert allow_h2h_alert(ssg) is True
    assert V17_STATS["h2h_duplicate_alerts_suppressed"] == 1
    assert allow_h2h_alert({"market_type": "totals", "event_id": "same"}) is True

    sample_title = "🔥 PM EDGE +5.9% — NO"
    sample_message = (
        "KBO\n"
        "NO — Samsung Lions vs Doosan Bears: Doosan Bears wins\n"
        "\n"
        "PLACE LIMIT: 48.0¢\n"
        "DO NOT PAY OVER: 49.9¢\n"
        "Current ask: 49.0¢\n"
        "Fair chance: 55.6%\n"
        "Est. net edge: +5.9%\n"
        "Books: 8\n"
        "\n"
        "Do not chase above MAX."
    )
    safe_title, safe_message = execution_safe_push(sample_title, sample_message)
    assert "BUY NO" in safe_title
    assert "SIDE TO BUY: NO" in safe_message
    assert "EXACT CONTRACT: Samsung Lions vs Doosan Bears: Doosan Bears wins" in safe_message
    assert "PLACE LIMIT ORDER: 48.0¢" in safe_message
    assert "ABSOLUTE MAX: 49.9¢" in safe_message
    assert "If the price is above MAX, SKIP the bet." in safe_message
    assert "Fair chance of this side: 55.6%" in safe_message

    print("PM Edge v17 execution-safety self-test: PASS")


def main() -> int:
    v16.install_v16_patch()
    install_v17_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(V17_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v16.self_test()
        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
