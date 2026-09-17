"""Pushover status confirmations for PM Edge order intents.

Pushover normal notifications cannot be edited after delivery, so PM Edge sends
one compact confirmation notification the first time an order is observed on
Kalshi. A small persisted state file prevents duplicate confirmations across
scheduled watcher runs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

QUEUE_PATH = Path(os.getenv("PM_EDGE_ORDER_QUEUE_PATH", ".pm_edge_order_queue.json"))
STATE_PATH = Path(os.getenv("PM_EDGE_PUSH_STATUS_PATH", ".pm_edge_pushover_status.json"))
PUSH_URL = "https://api.pushover.net/1/messages.json"

TAKEN_STATUSES = {"submitted", "partially_filled", "filled"}


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value if isinstance(value, dict) else default


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fmt_cents(value) -> str:
    try:
        return f"{float(value) * 100:.1f}¢"
    except (TypeError, ValueError):
        return "—"


def _fmt_count(value) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "0"
    return str(int(x)) if x.is_integer() else f"{x:.2f}".rstrip("0").rstrip(".")


def _status_line(intent: dict) -> str:
    status = str(intent.get("status") or "").lower()
    filled = intent.get("filled_shares")
    total = intent.get("shares")
    if status == "filled":
        return f"Filled: {_fmt_count(filled)} / {_fmt_count(total)} shares"
    if status == "partially_filled":
        return f"Partially filled: {_fmt_count(filled)} / {_fmt_count(total)} shares"
    return "Order is on Kalshi (may be resting for your limit price)"


def _message(intent: dict) -> tuple[str, str]:
    title = "✅ TAKEN IN KALSHI"
    side = str(intent.get("side") or "").upper()
    market = str(intent.get("market_title") or intent.get("ticker") or "PM Edge order")
    shares = _fmt_count(intent.get("shares"))
    price = _fmt_cents(intent.get("limit_price"))
    body = (
        f"{side} — {market}\n"
        f"{shares} shares @ {price} max\n"
        f"{_status_line(intent)}"
    )
    return title, body


def _send_pushover(title: str, message: str) -> bool:
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        return False
    response = requests.post(
        PUSH_URL,
        data={
            "token": token,
            "user": user,
            "title": title,
            "message": message,
            "priority": "-1",
            "ttl": "21600",
        },
        timeout=12,
    )
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    return isinstance(payload, dict) and int(payload.get("status") or 0) == 1


def notify_taken_orders(
    *,
    queue_path: Path = QUEUE_PATH,
    state_path: Path = STATE_PATH,
    sender: Callable[[str, str], bool] | None = None,
    now: datetime | None = None,
) -> dict:
    """Send one confirmation push per PM Edge order that reaches Kalshi.

    The queue is populated by the normal read-only reconciliation step. Only
    intents with a Kalshi order id and a submitted/filled status qualify. Failed
    Pushover sends are not marked complete, so a later watcher run can retry.
    """
    queue = _load_json(queue_path, {"version": 1, "intents": []})
    state = _load_json(state_path, {"version": 1, "notified": {}})
    notified = state.setdefault("notified", {})
    actual_sender = sender or _send_pushover
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    checked = sent = already = failed = 0
    for intent in queue.get("intents", []):
        if not isinstance(intent, dict):
            continue
        status = str(intent.get("status") or "").lower()
        if status not in TAKEN_STATUSES:
            continue
        if not str(intent.get("kalshi_order_id") or "").strip():
            continue
        intent_id = str(intent.get("intent_id") or "").strip()
        if not intent_id:
            continue
        checked += 1
        if intent_id in notified:
            already += 1
            continue
        title, body = _message(intent)
        try:
            ok = bool(actual_sender(title, body))
        except Exception:
            ok = False
        if not ok:
            failed += 1
            continue
        notified[intent_id] = {
            "notified_at": now.isoformat().replace("+00:00", "Z"),
            "kalshi_order_id": str(intent.get("kalshi_order_id") or ""),
        }
        sent += 1

    _save_json(state_path, state)
    configured = bool(os.getenv("PUSHOVER_APP_TOKEN", "").strip() and os.getenv("PUSHOVER_USER_KEY", "").strip())
    return {
        "configured": True if sender is not None else configured,
        "checked": checked,
        "sent": sent,
        "already_notified": already,
        "failed": failed,
    }
