"""PM Edge v24 launcher: two-tap approval + read-only reconciliation.

v24 keeps every v23 pricing, sizing, safety and staging rule. It adds:
* a compact signed Review & approve URL to actionable Pushover alerts;
* a compact quick-entry block in Pushover so the order can be referenced from
  iPhone Notification Center while entering it in Kalshi;
* an approval page that re-checks the live Kalshi quote before opening Kalshi;
* optional GET-only account reconciliation for manually submitted orders/fills.

No code in v24 can submit, amend, decrease, or cancel a Kalshi order.
"""
from __future__ import annotations

import json
import os
import re

import approval_links
import execution_guard
from kalshi_readonly import KalshiReadOnlyClient
import watcher
import pm_edge_runner_v23 as v23

V24_STATS = {
    "approval_links_prepared": 0,
    "approval_links_attached": 0,
    "approval_links_skipped": 0,
    "reconcile_configured": 0,
    "reconcile_errors": 0,
}

_APPROVAL_URLS: dict[tuple[str, str, int], str] = {}
_QUICK_ENTRIES: dict[tuple[str, str, int], dict] = {}


def _price_key(price: float) -> int:
    return int(round(float(price) * 1000))


def _alert_key(side: str, title: str, price: float) -> tuple[str, str, int]:
    return str(side).upper(), str(title)[:170], _price_key(price)


def _prepare_approval_url(market: dict, fair_entry: dict, q, tier: str) -> str | None:
    base_url = os.getenv("STREAMLIT_APP_URL", "").strip()
    if not base_url:
        return None
    lane = v23._effective_lane(tier, q)
    order = v23._size_for_intent(q, lane)
    if order is None:
        return None
    side = str(getattr(q, "side", "") or "").upper()
    if side not in {"YES", "NO"}:
        return None
    execution = str(getattr(q, "execution", "") or "").upper()
    if execution not in {"TAKE", "MAKE"}:
        return None
    fair_side = v23.v22._side_probability(fair_entry, side)
    try:
        token = approval_links.create_approval_token(
            ticker=str(market.get("ticker") or ""),
            side=side,
            lane=lane,
            execution=execution,
            limit_price=float(order["price"]),
            shares=int(order["shares"]),
            max_loss=float(order["max_loss"]),
            fair_probability=fair_side,
            net_edge=getattr(q, "net_edge", None),
            books=fair_entry.get("n_books"),
        )
        url = approval_links.build_approval_url(base_url, token)
    except Exception as exc:
        print("APPROVAL_LINK_SKIPPED", market.get("ticker"), type(exc).__name__, str(exc)[:160])
        return None
    # Pushover supplementary URLs have a finite limit; fail closed to the
    # normal PM Edge app URL rather than sending a truncated/invalid token.
    if len(url) > 512:
        print("APPROVAL_LINK_TOO_LONG", len(url), market.get("ticker"))
        return None
    return url


def _raw_message_key(message: str):
    side = None
    title = None
    cents_value = None
    for line in str(message).splitlines():
        if line.startswith("YES — ") or line.startswith("NO — "):
            side, title = line.split(" — ", 1)
            side = side.strip().upper()
            title = title.strip()
            continue
        if line.startswith("PLACE LIMIT:") or line.startswith("BUY NOW:"):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*¢", line)
            if match:
                cents_value = float(match.group(1))
    if side not in {"YES", "NO"} or title is None or cents_value is None:
        return None
    return side, title[:170], int(round(cents_value * 10))


def _quick_entry_block(details: dict) -> str:
    side = str(details.get("side") or "").upper()
    shares = int(details.get("shares") or 0)
    price = float(details.get("price") or 0.0)
    max_loss = float(details.get("max_loss") or 0.0)
    edge = details.get("net_edge")
    ticker = str(details.get("ticker") or "").strip()
    edge_text = "—" if edge is None else f"{float(edge) * 100:+.1f}%"
    cents = int(round(price * 100))
    return (
        f"QUICK ENTRY: BUY {side} | {shares} shares | {cents}¢ max\n"
        f"Cost: ${max_loss:.2f} | Edge: {edge_text}\n"
        f"Ticker: {ticker}"
    )


def install_v24_patch() -> None:
    watcher.WATCHER_VERSION = 24

    original_should_alert = watcher.should_alert

    def should_alert_v24(state, market, fair_entry, q, tier="primary"):
        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if ok and getattr(q, "recommended_limit", None) is not None:
            key = _alert_key(q.side, market.get("title", ""), q.recommended_limit)
            lane = v23._effective_lane(tier, q)
            order = v23._size_for_intent(q, lane)
            if order is not None:
                _QUICK_ENTRIES[key] = {
                    "ticker": str(market.get("ticker") or ""),
                    "side": str(getattr(q, "side", "") or "").upper(),
                    "shares": int(order["shares"]),
                    "price": float(order["price"]),
                    "max_loss": float(order["max_loss"]),
                    "net_edge": getattr(q, "net_edge", None),
                }

            url = _prepare_approval_url(market, fair_entry, q, tier)
            if url:
                _APPROVAL_URLS[key] = url
                V24_STATS["approval_links_prepared"] += 1
            else:
                V24_STATS["approval_links_skipped"] += 1
        return ok, reason

    watcher.should_alert = should_alert_v24

    original_send_push = watcher.send_push

    def send_push_v24(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v20 Auto-Sized is live",
            "✅ PM Edge Watch v21 Overnight Look-Ahead is live",
            "✅ PM Edge Watch v22 Value Volume is live",
            "✅ PM Edge Watch v23 Approval Execution is live",
        }:
            title = "✅ PM Edge Watch v24 Two-Tap Approval is live"

        upper_title = str(title).upper()
        actionable = (
            "PM EDGE" in upper_title
            and "PASS" not in upper_title
            and "DIGEST" not in upper_title
            and "IS LIVE" not in upper_title
        )
        if actionable:
            key = _raw_message_key(message)
            quick = _QUICK_ENTRIES.get(key) if key else None
            if quick and not str(message).startswith("QUICK ENTRY:"):
                message = _quick_entry_block(quick) + "\n\n" + str(message)
                # Keep comfortably below Pushover's message-size ceiling while
                # preserving the quick-entry block at the top of the alert.
                if len(message) > 950:
                    message = message[:947] + "..."

            if not kwargs.get("url"):
                url = _APPROVAL_URLS.get(key) if key else None
                if url:
                    kwargs["url"] = url
                    kwargs["url_title"] = "Review & approve"
                    V24_STATS["approval_links_attached"] += 1
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v24


def self_test() -> None:
    os.environ["PM_EDGE_APPROVAL_SECRET"] = "v24-test-secret"
    os.environ["STREAMLIT_APP_URL"] = "https://example.streamlit.app"

    class FakeQ:
        side = "YES"
        recommended_limit = 0.38
        hard_max = 0.38
        net_edge = 0.149
        execution = "MAKE"

    market = {
        "ticker": "KXMLBGAME-26SEP171235MILPIT-MIL",
        "title": "Milwaukee vs Pittsburgh: Milwaukee wins",
    }
    fair = {"fair_yes_prob": 0.558, "n_books": 6}
    url = _prepare_approval_url(market, fair, FakeQ(), "primary")
    assert url is not None
    assert url.startswith("https://example.streamlit.app/Approve?trade=")
    assert len(url) < 512
    token = url.split("trade=", 1)[1]
    assert approval_links.verify_approval_token(token)["execution"] == "MAKE"

    message = (
        "MLB\n"
        "YES — Milwaukee vs Pittsburgh: Milwaukee wins\n\n"
        "PLACE LIMIT: 38.0¢\n"
        "DO NOT PAY OVER: 38.0¢\n"
    )
    assert _raw_message_key(message) == _alert_key("YES", market["title"], 0.38)

    quick = _quick_entry_block(
        {
            "ticker": market["ticker"],
            "side": "YES",
            "shares": 9,
            "price": 0.38,
            "max_loss": 3.42,
            "net_edge": 0.149,
        }
    )
    assert "BUY YES | 9 shares | 38¢ max" in quick
    assert "Cost: $3.42 | Edge: +14.9%" in quick
    assert market["ticker"] in quick
    print("PM Edge v24 two-tap approval self-test: PASS")


def main() -> int:
    v23.v22.v21.v20.v19.v18.v17.v16.install_v16_patch()
    v23.v22.v21.v20.v19.v18.v17.install_v17_patch()
    v23.v22.v21.v20.v19.v18.install_v18_patch()
    v23.v22.v21.v20.v19.install_v19_patch()
    v23.v22.v21.v20.install_v20_patch()
    v23.v22.v21.install_v21_patch()
    v23.v22.install_v22_patch()
    v23.install_v23_patch()
    install_v24_patch()

    rc = watcher.main()

    reconcile_result = {"configured": False}
    try:
        client = KalshiReadOnlyClient.from_env()
        if client is not None:
            V24_STATS["reconcile_configured"] += 1
            reconcile_result = execution_guard.reconcile_order_intents(client)
            reconcile_result["configured"] = True
    except Exception as exc:
        V24_STATS["reconcile_errors"] += 1
        reconcile_result = {"configured": True, "error": f"{type(exc).__name__}: {exc}"}
        print("PM_EDGE_READONLY_RECONCILE_ERROR", type(exc).__name__, str(exc)[:300])

    print("PM_EDGE_V24_TWO_TAP " + json.dumps(V24_STATS, sort_keys=True))
    print("PM_EDGE_V24_RECONCILE " + json.dumps(reconcile_result, sort_keys=True))
    print("PM_EDGE_EXECUTION_QUEUE " + json.dumps(execution_guard.queue_summary(), sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
