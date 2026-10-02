"""Pushover alerts for AutoTrader paper fills.

All AutoTrader paper lanes call this module only after a decision is recorded as
`paper_filled`. Fill events are persisted before delivery so a temporary
notification outage or missing credentials cannot silently drop them. A shared
state file deduplicates retries across worker threads and process restarts.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import requests

PUSH_URL = "https://api.pushover.net/1/messages.json"
DEFAULT_STATE_PATH = "/data/autotrader-fill-alerts.json"

_LOCK = threading.Lock()

LANE_LABELS = {
    "value_edge": "Value Edge",
    "whale_flow": "Whale Flow",
    "quick_whale": "Quick Whale",
    "quick_whale_v2": "Quick Whale v2 Beta",
    "quick_whale_v3": "Quick Whale v3",
}


def _utcnow():
    return datetime.now(timezone.utc)


def _enabled() -> bool:
    return str(os.getenv("AUTOTRADER_FILL_ALERTS_ENABLED", "1")).strip().lower() not in {
        "0", "false", "no", "off"
    }


def _configured() -> bool:
    return bool(
        os.getenv("PUSHOVER_APP_TOKEN", "").strip()
        and os.getenv("PUSHOVER_USER_KEY", "").strip()
    )


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "pending": {}, "notified": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "pending": {}, "notified": {}}
    if not isinstance(value, dict):
        return {"version": 1, "pending": {}, "notified": {}}
    value.setdefault("version", 1)
    value.setdefault("pending", {})
    value.setdefault("notified", {})
    return value


def _save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=".fill-alerts-",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def _fmt_cents(value) -> str:
    try:
        return f"{Decimal(str(value)) * Decimal('100'):.1f}¢"
    except Exception:
        return "—"


def _fmt_money(value) -> str:
    try:
        return f"${Decimal(str(value)):.2f}"
    except Exception:
        return "—"


def _event_key(lane: str, row: dict) -> str:
    stable = {
        "lane": str(lane),
        "ticker": str(row.get("ticker") or ""),
        "side": str(row.get("side") or "").upper(),
        "source_trade_id": str(row.get("source_trade_id") or ""),
        "fair_snapshot_id": str(row.get("fair_snapshot_id") or ""),
        "price": str(row.get("simulated_fill") or row.get("price") or ""),
        "contracts": str(row.get("contracts") or row.get("contracts_considered") or ""),
    }
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _fill_event(lane: str, row: dict, now: datetime) -> dict | None:
    if str(row.get("decision") or "") != "paper_filled":
        return None
    ticker = str(row.get("ticker") or "").strip()
    side = str(row.get("side") or "").upper().strip()
    if not ticker or side not in {"YES", "NO"}:
        return None

    contracts = int(row.get("contracts") or row.get("contracts_considered") or 0)
    price_raw = row.get("simulated_fill")
    if price_raw in (None, ""):
        price_raw = row.get("price")
    try:
        price = Decimal(str(price_raw))
    except Exception:
        price = Decimal("0")
    try:
        fee_total = Decimal(str(row.get("fee_total") or "0"))
    except Exception:
        fee_total = Decimal("0")
    risk = Decimal(max(0, contracts)) * max(Decimal("0"), price) + max(Decimal("0"), fee_total)

    market = str(
        row.get("side_label")
        or row.get("market_title")
        or row.get("title")
        or ticker
    ).strip()
    label = LANE_LABELS.get(str(lane), str(lane).replace("_", " ").title())
    return {
        "id": _event_key(lane, row),
        "lane": str(lane),
        "lane_label": label,
        "ticker": ticker,
        "side": side,
        "market": market,
        "contracts": contracts,
        "price": str(price),
        "fee_total": str(fee_total),
        "risk": str(risk),
        "created_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _message(event: dict) -> tuple[str, str]:
    title = f"📈 AUTOTRADER PAPER FILL — {event.get('lane_label') or 'AutoTrader'}"
    body = "\n".join(
        [
            str(event.get("market") or event.get("ticker") or "AutoTrader bet"),
            f"BUY {event.get('side')} · {event.get('contracts')} contracts @ {_fmt_cents(event.get('price'))}",
            f"Paper risk: {_fmt_money(event.get('risk'))}",
            f"Ticker: {event.get('ticker')}",
            "Paper only — no real-money order was placed.",
        ]
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
            "priority": "0",
            "ttl": "21600",
        },
        timeout=6,
    )
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    return isinstance(payload, dict) and int(payload.get("status") or 0) == 1


def notify_paper_fills(
    lane: str,
    decisions,
    *,
    state_path: str | Path | None = None,
    sender: Callable[[str, str], bool] | None = None,
    now: datetime | None = None,
) -> dict:
    """Queue and deliver one notification per actual paper fill.

    Failed sends remain pending and are retried on later lane scans. The state
    is shared by all lanes, so duplicate calls cannot generate duplicate pushes.
    """
    now = (now or _utcnow()).astimezone(timezone.utc)
    path = Path(state_path or os.getenv("AUTOTRADER_FILL_ALERT_STATE", DEFAULT_STATE_PATH))

    with _LOCK:
        state = _load_state(path)
        pending = state.setdefault("pending", {})
        notified = state.setdefault("notified", {})

        queued = 0
        for row in decisions or []:
            if not isinstance(row, dict):
                continue
            event = _fill_event(lane, row, now)
            if event is None:
                continue
            event_id = event["id"]
            if event_id in notified or event_id in pending:
                continue
            pending[event_id] = event
            queued += 1

        if not _enabled():
            _save_state(path, state)
            return {
                "enabled": False,
                "configured": _configured() if sender is None else True,
                "queued": queued,
                "sent": 0,
                "pending": len(pending),
                "failed": 0,
            }

        configured = True if sender is not None else _configured()
        actual_sender = sender or _send_pushover
        sent = failed = 0
        if configured:
            for event_id, event in list(pending.items()):
                title, body = _message(event)
                try:
                    ok = bool(actual_sender(title, body))
                except Exception:
                    ok = False
                if not ok:
                    failed += 1
                    continue
                notified[event_id] = {
                    "notified_at": now.isoformat().replace("+00:00", "Z"),
                    "lane": event.get("lane"),
                    "ticker": event.get("ticker"),
                    "side": event.get("side"),
                }
                pending.pop(event_id, None)
                sent += 1

        if len(notified) > 2000:
            keep = list(notified.items())[-2000:]
            state["notified"] = dict(keep)

        _save_state(path, state)
        return {
            "enabled": True,
            "configured": configured,
            "queued": queued,
            "sent": sent,
            "pending": len(pending),
            "failed": failed,
        }
