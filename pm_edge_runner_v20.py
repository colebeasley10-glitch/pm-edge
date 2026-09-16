"""PM Edge v20 launcher.

Builds on v19 and adds automatic share-count math to every actionable PM Edge
alert so the owner can act without doing contract arithmetic manually.

Sizing rule (configurable through environment variables):
* Target roughly $5 of gross profit if the contract resolves correctly.
* Never risk more than $10 of contract cost on a single alert.
* Size from PM Edge's recommended price, not from a worse/chased price.
* If the $5 profit target would require more than $10 of risk, clamp shares to
  the $10 risk cap rather than increasing exposure.

The push therefore includes the exact side, shares, price, contract cost,
maximum loss, and gross profit if correct. Platform fees are not included in
these displayed contract-cost figures; PM Edge's edge qualification already
uses its configured maker/taker fee assumptions.

This launcher is alert-only. It never places a real-money order.
"""
from __future__ import annotations

import json
import math
import os
import re

import watcher
import pm_edge_runner_v19 as v19

TARGET_WIN_PROFIT_DOLLARS = float(
    os.getenv("PM_EDGE_TARGET_WIN_PROFIT_DOLLARS", "5.00")
)
MAX_RISK_DOLLARS = float(os.getenv("PM_EDGE_MAX_RISK_DOLLARS", "10.00"))

V20_STATS = {
    "alerts_sized": 0,
    "alerts_risk_capped": 0,
    "alerts_sizing_skipped": 0,
}


def cents(price: float) -> str:
    return f"{price * 100:.1f}¢"


def _safe_price(value):
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or not (0.0 < p < 1.0):
        return None
    return p


def calculate_share_order(
    price: float,
    target_profit: float = TARGET_WIN_PROFIT_DOLLARS,
    max_risk: float = MAX_RISK_DOLLARS,
) -> dict | None:
    """Calculate whole-contract sizing at a given entry price.

    Each winning binary contract returns $1, so gross profit per winning share
    is (1 - price) and maximum loss per share is price.
    """
    p = _safe_price(price)
    if p is None:
        return None

    target_profit = max(0.01, float(target_profit))
    max_risk = max(0.01, float(max_risk))

    desired_shares = max(1, int(math.ceil((target_profit / (1.0 - p)) - 1e-12)))
    max_shares_by_risk = int(math.floor((max_risk / p) + 1e-12))
    if max_shares_by_risk < 1:
        return None

    shares = min(desired_shares, max_shares_by_risk)
    risk_capped = shares < desired_shares
    cost = shares * p
    gross_profit_if_correct = shares * (1.0 - p)
    gross_payout_if_correct = float(shares)

    return {
        "shares": shares,
        "price": p,
        "cost": cost,
        "max_loss": cost,
        "gross_profit_if_correct": gross_profit_if_correct,
        "gross_payout_if_correct": gross_payout_if_correct,
        "risk_capped": risk_capped,
        "desired_shares": desired_shares,
        "max_shares_by_risk": max_shares_by_risk,
    }


def _extract_side_and_action_price(message: str) -> tuple[str | None, float | None, int | None]:
    """Read the raw watcher alert before downstream v18/v17 copy rewrites."""
    side = None
    price = None
    action_index = None
    lines = str(message).splitlines()

    for line in lines:
        if line.startswith("YES — "):
            side = "YES"
            break
        if line.startswith("NO — "):
            side = "NO"
            break
        if line.startswith("SIDE TO BUY:"):
            candidate = line.split(":", 1)[1].strip().upper()
            if candidate in {"YES", "NO"}:
                side = candidate
                break

    for i, line in enumerate(lines):
        if line.startswith("PLACE LIMIT:") or line.startswith("PLACE LIMIT ORDER:") or line.startswith("BUY NOW:") or line.startswith("BUY NOW AT/BELOW:"):
            raw = line.split(":", 1)[1].strip()
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*¢", raw)
            if match:
                price = _safe_price(float(match.group(1)) / 100.0)
                action_index = i
                break

    return side, price, action_index


def add_order_sizing(title: str, message: str) -> tuple[str, str]:
    """Insert an exact shares/price block into actionable PM Edge pushes."""
    upper_title = str(title).upper()
    if "PM EDGE" not in upper_title or "PASS" in upper_title or "DIGEST" in upper_title:
        return title, message

    if "BUY " in str(message) and " SHARES @ " in str(message):
        return title, message

    side, price, action_index = _extract_side_and_action_price(message)
    if side not in {"YES", "NO"} or price is None or action_index is None:
        V20_STATS["alerts_sizing_skipped"] += 1
        return title, message

    order = calculate_share_order(price)
    if order is None:
        V20_STATS["alerts_sizing_skipped"] += 1
        return title, message

    if order["risk_capped"]:
        V20_STATS["alerts_risk_capped"] += 1
    V20_STATS["alerts_sized"] += 1

    lines = str(message).splitlines()
    sizing_lines = [
        "",
        f"ORDER: BUY {side} — {order['shares']} SHARES @ {cents(price)} OR LOWER",
        f"CONTRACT COST @ LIMIT: ${order['cost']:.2f}",
        f"MAX LOSS: ${order['max_loss']:.2f}",
        f"GROSS PROFIT IF CORRECT: ${order['gross_profit_if_correct']:.2f}",
    ]
    if order["risk_capped"]:
        sizing_lines.append(f"SIZE CAPPED: ${MAX_RISK_DOLLARS:.2f} max single-bet risk")

    insert_at = action_index + 1
    lines[insert_at:insert_at] = sizing_lines
    return title, "\n".join(lines).strip()


def install_v20_patch() -> None:
    watcher.WATCHER_VERSION = 20

    original_send_push = watcher.send_push

    def send_push_v20(title, message, *args, **kwargs):
        if title in {
            "✅ PM Edge Watch v15 is live",
            "✅ PM Edge Watch v18 Price Fish is live",
            "✅ PM Edge Watch v19 Realistic Price Fish is live",
        }:
            title = "✅ PM Edge Watch v20 Auto-Sized is live"
        else:
            title, message = add_order_sizing(title, message)
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v20


def self_test() -> None:
    # 42c: 9 shares targets just over $5 gross profit and risks $3.78.
    order = calculate_share_order(0.42)
    assert order is not None
    assert order["shares"] == 9
    assert abs(order["cost"] - 3.78) < 1e-9
    assert abs(order["gross_profit_if_correct"] - 5.22) < 1e-9
    assert order["risk_capped"] is False

    # 60c: 13 shares = $7.80 risk and $5.20 gross profit.
    order = calculate_share_order(0.60)
    assert order is not None
    assert order["shares"] == 13
    assert abs(order["cost"] - 7.80) < 1e-9
    assert abs(order["gross_profit_if_correct"] - 5.20) < 1e-9

    # 70c would need 17 shares to target $5 profit, but that exceeds $10 risk.
    # Clamp at 14 shares / $9.80 rather than increasing exposure.
    order = calculate_share_order(0.70)
    assert order is not None
    assert order["shares"] == 14
    assert abs(order["cost"] - 9.80) < 1e-9
    assert order["risk_capped"] is True

    sample_title = "🎲 PM EDGE SPECULATIVE +6.1% — YES"
    sample_message = (
        "MLB\n"
        "YES — White Sox win\n\n"
        "PLACE LIMIT: 42.0¢\n"
        "DO NOT PAY OVER: 42.0¢\n"
        "Current ask: 47.0¢\n"
        "Fair chance: 50.0%\n"
        "Est. net edge: +6.1%\n"
        "Books: 8\n\n"
        "Do not chase above MAX."
    )
    _, sized = add_order_sizing(sample_title, sample_message)
    assert "ORDER: BUY YES — 9 SHARES @ 42.0¢ OR LOWER" in sized
    assert "CONTRACT COST @ LIMIT: $3.78" in sized
    assert "MAX LOSS: $3.78" in sized
    assert "GROSS PROFIT IF CORRECT: $5.22" in sized

    pass_title = "⚠️ PM EDGE — PASS NOW"
    _, untouched = add_order_sizing(pass_title, "No longer qualifies")
    assert untouched == "No longer qualifies"

    print("PM Edge v20 auto-sizing self-test: PASS")


def main() -> int:
    v19.v18.v17.v16.install_v16_patch()
    v19.v18.v17.install_v17_patch()
    v19.v18.install_v18_patch()
    v19.install_v19_patch()
    install_v20_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v19.v18.v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v19.v18.v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(v19.v18.V18_STATS, sort_keys=True))
    print("PM_EDGE_V19_REALISTIC_FISH " + json.dumps(v19.V19_STATS, sort_keys=True))
    print("PM_EDGE_V20_AUTO_SIZE " + json.dumps(V20_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v19.v18.v17.v16.self_test()
        v19.v18.v17.self_test()
        v19.v18.self_test()
        v19.self_test()
        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
