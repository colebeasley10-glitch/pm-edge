"""One-shot helper to recheck and resend the three Whale Flow alerts from 2026-09-23 11:47 ET.

It never submits an order. It uses the same live price, account, duplicate-order,
position and risk guards as the PM Edge mobile review endpoint. A push is sent
only when the bet is still currently eligible for explicit user review/submit.
"""
from __future__ import annotations

import os

import mobile_backend
import watcher
from kalshi_submit import KalshiSubmitClient

TARGET_SIGNAL_IDS = (
    "8ce57b41730f449fa69b",  # Green Bay YES
    "7ed2096a4c17a79904ef",  # BTC 15m NO
    "6f77781e55f9d2157c49",  # BTC 15m YES
)


def cents(value):
    if value is None:
        return "—"
    return f"{float(value) * 100:.0f}¢"


def money(value):
    if value is None:
        return "—"
    return f"${float(value):.2f}"


def main() -> int:
    client = KalshiSubmitClient.from_env()
    if client is None:
        raise SystemExit("Missing Kalshi write credentials; refusing to judge/resend.")

    resent = 0
    for signal_id in TARGET_SIGNAL_IDS:
        try:
            row = mobile_backend._signal_row(signal_id)
            review = mobile_backend._review_payload(row, client)
        except Exception as exc:
            print(f"WHALE_RESEND_ERROR signal={signal_id} error={type(exc).__name__}: {exc}")
            continue

        if not review.get("can_submit"):
            print(
                "WHALE_RESEND_SKIP"
                f" signal={signal_id}"
                f" ticker={review.get('ticker')}"
                f" side={review.get('side')}"
                f" price_ok={review.get('price_ok')}"
                f" existing_order={bool(review.get('existing_order'))}"
                f" guard_blocked={bool((review.get('guard') or {}).get('blocked'))}"
                f" message={review.get('price_message')}"
            )
            continue

        title = "🐋 PM EDGE WHALE FLOW — STILL ACTIONABLE"
        if str(review.get("sport_title") or "").casefold() == "football":
            title = "🐋 PM EDGE WHALE FLOW — FOOTBALL"

        execution = str(review.get("execution") or "").upper()
        action_line = (
            f"BUY {review.get('side')} @ {cents(review.get('live_ask'))} OR LOWER"
            if execution == "TAKE"
            else f"REST BUY {review.get('side')} @ {cents(review.get('limit_price'))}"
        )
        message = "\n".join(
            [
                str(review.get("market_title") or review.get("ticker") or "Whale Flow"),
                action_line,
                f"Live ask: {cents(review.get('live_ask'))} · PM Edge max: {cents(review.get('limit_price'))}",
                f"{review.get('shares')} shares · max cost {money(review.get('max_loss'))}",
                "Rechecked now: price + PM Edge account/risk guards PASS.",
                "Tap Open PM Edge to review and submit.",
            ]
        )
        ok = watcher.send_push(
            title,
            message,
            priority=0,
            url=os.getenv("STREAMLIT_APP_URL") or None,
            url_title="Open PM Edge",
        )
        print(
            f"WHALE_RESEND_{'SENT' if ok else 'PUSH_FAILED'}"
            f" signal={signal_id} ticker={review.get('ticker')} side={review.get('side')}"
            f" live_ask={review.get('live_ask')} max={review.get('limit_price')}"
        )
        if ok:
            resent += 1

    print(f"WHALE_RESEND_SUMMARY resent={resent} checked={len(TARGET_SIGNAL_IDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
