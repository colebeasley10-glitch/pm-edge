"""Approval-first execution staging for PM Edge.

This module deliberately does NOT submit real-money orders. It converts a
qualified PM Edge signal into a structured order intent, applies deterministic
risk controls, deduplicates repeated alerts, and persists the intent for later
human approval/reconciliation.

The design keeps the model responsible for estimating fair value while a small,
auditable rules engine owns execution policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

QUEUE_PATH = Path(os.getenv("PM_EDGE_ORDER_QUEUE_PATH", ".pm_edge_order_queue.json"))
MAX_ORDER_RISK = float(os.getenv("PM_EDGE_EXECUTION_MAX_ORDER_RISK", "10.00"))
DAILY_RISK_LIMIT_BASELINE = float(os.getenv("PM_EDGE_EXECUTION_DAILY_RISK_LIMIT", "50.00"))
MAX_QUEUE_AGE_DAYS = int(os.getenv("PM_EDGE_EXECUTION_QUEUE_RETENTION_DAYS", "14"))
RECONCILE_MATCH_WINDOW_HOURS = float(os.getenv("PM_EDGE_RECONCILE_MATCH_WINDOW_HOURS", "3"))
EXECUTION_MODE = str(os.getenv("PM_EDGE_EXECUTION_MODE", "preview") or "preview").strip().lower()

ALLOWED_MODES = {"off", "preview"}


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    created_at: str
    trade_date: str
    ticker: str
    market_title: str
    side: str
    lane: str
    limit_price: float
    shares: int
    max_loss: float
    fair_probability: float | None
    net_edge: float | None
    books: int | None
    source_version: int
    status: str = "awaiting_approval"
    requires_user_approval: bool = True
    live_submission_permitted: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _effective_daily_risk_limit(now: datetime | None = None) -> float:
    # A configured value <= 0 disables the aggregate daily risk ceiling while
    # preserving the per-order cap and all other approval/execution safeguards.
    return float("inf") if DAILY_RISK_LIMIT_BASELINE <= 0 else DAILY_RISK_LIMIT_BASELINE


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalized_mode() -> str:
    # Fail closed: an unsupported value (including "live") never enables
    # autonomous order placement.
    return EXECUTION_MODE if EXECUTION_MODE in ALLOWED_MODES else "preview"


def _load_queue(path: Path = QUEUE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "intents": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "intents": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("intents"), list):
        return {"version": 1, "intents": []}
    payload.setdefault("version", 1)
    return payload


def _save_queue(payload: dict, path: Path = QUEUE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fingerprint(*, ticker: str, side: str, lane: str, limit_price: float, trade_date: str) -> str:
    raw = f"{trade_date}|{ticker}|{side}|{lane}|{limit_price:.4f}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _prune(payload: dict, now: datetime) -> None:
    retained = []
    for item in payload.get("intents", []):
        created = str(item.get("created_at") or "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            retained.append(item)
            continue
        age_days = (now - dt.astimezone(timezone.utc)).total_seconds() / 86400.0
        if age_days <= max(1, MAX_QUEUE_AGE_DAYS):
            retained.append(item)
    payload["intents"] = retained


def _today_risk(payload: dict, trade_date: str) -> float:
    total = 0.0
    for item in payload.get("intents", []):
        if item.get("trade_date") != trade_date:
            continue
        if item.get("status") not in {"awaiting_approval", "approved", "submitted", "partially_filled", "filled"}:
            continue
        risk = _safe_float(item.get("max_loss"))
        if risk is not None and risk > 0:
            total += risk
    return total


def stage_order_intent(
    *,
    ticker: str,
    market_title: str,
    side: str,
    lane: str,
    limit_price: float,
    shares: int,
    max_loss: float,
    fair_probability: float | None = None,
    net_edge: float | None = None,
    books: int | None = None,
    source_version: int = 23,
    path: Path = QUEUE_PATH,
    now: datetime | None = None,
) -> dict:
    """Validate and persist one order intent.

    Returns a compact result dict with ``staged`` and ``reason``. No network
    calls are made and no real-money order can be submitted from this module.
    """
    mode = _normalized_mode()
    if mode == "off":
        return {"staged": False, "reason": "execution_preview_disabled"}

    ticker = str(ticker or "").strip()
    market_title = str(market_title or "").strip()
    side = str(side or "").strip().upper()
    lane = str(lane or "").strip().lower() or "primary"
    price = _safe_float(limit_price)
    risk = _safe_float(max_loss)

    try:
        shares = int(shares)
    except (TypeError, ValueError):
        shares = 0

    if not ticker:
        return {"staged": False, "reason": "missing_ticker"}
    if side not in {"YES", "NO"}:
        return {"staged": False, "reason": "invalid_side"}
    if price is None or not (0.0 < price < 1.0):
        return {"staged": False, "reason": "invalid_limit_price"}
    if shares < 1:
        return {"staged": False, "reason": "invalid_share_count"}
    if risk is None or risk <= 0:
        return {"staged": False, "reason": "invalid_max_loss"}
    if risk > MAX_ORDER_RISK + 1e-9:
        return {"staged": False, "reason": "per_order_risk_limit"}

    now = (now or _utc_now()).astimezone(timezone.utc)
    trade_date = now.date().isoformat()
    payload = _load_queue(path)
    _prune(payload, now)

    intent_id = _fingerprint(
        ticker=ticker,
        side=side,
        lane=lane,
        limit_price=price,
        trade_date=trade_date,
    )
    for item in payload.get("intents", []):
        if item.get("intent_id") == intent_id:
            return {"staged": False, "reason": "duplicate", "intent_id": intent_id}

    projected = _today_risk(payload, trade_date) + risk
    daily_limit = _effective_daily_risk_limit(now)
    if projected > daily_limit + 1e-9:
        return {
            "staged": False,
            "reason": "daily_risk_limit",
            "projected_daily_risk": round(projected, 2),
        }

    fair = _safe_float(fair_probability)
    edge = _safe_float(net_edge)
    try:
        book_count = int(books) if books is not None else None
    except (TypeError, ValueError):
        book_count = None

    intent = OrderIntent(
        intent_id=intent_id,
        created_at=now.isoformat().replace("+00:00", "Z"),
        trade_date=trade_date,
        ticker=ticker,
        market_title=market_title,
        side=side,
        lane=lane,
        limit_price=round(price, 4),
        shares=shares,
        max_loss=round(risk, 2),
        fair_probability=round(fair, 6) if fair is not None else None,
        net_edge=round(edge, 6) if edge is not None else None,
        books=book_count,
        source_version=int(source_version),
    )
    payload.setdefault("intents", []).append(asdict(intent))
    _save_queue(payload, path)
    return {
        "staged": True,
        "reason": "awaiting_user_approval",
        "intent_id": intent_id,
        "daily_risk_after": round(projected, 2),
    }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _order_side(order: dict) -> str:
    side = str(order.get("outcome_side") or order.get("side") or "").upper()
    return side if side in {"YES", "NO"} else ""


def _order_price(order: dict, side: str) -> float | None:
    value = order.get("yes_price_dollars" if side == "YES" else "no_price_dollars")
    price = _safe_float(value)
    if price is not None:
        return price
    legacy = _safe_float(order.get("yes_price" if side == "YES" else "no_price"))
    return legacy / 100.0 if legacy is not None and legacy > 1 else legacy


def _fp_count(value: Any) -> float:
    x = _safe_float(value)
    return 0.0 if x is None else max(0.0, x)


def _candidate_order(intent: dict, order: dict) -> bool:
    if str(order.get("ticker") or order.get("market_ticker") or "") != str(intent.get("ticker") or ""):
        return False
    if str(order.get("action") or "buy").lower() != "buy":
        return False
    side = str(intent.get("side") or "").upper()
    if _order_side(order) != side:
        return False
    price = _order_price(order, side)
    limit_price = _safe_float(intent.get("limit_price"))
    if price is None or limit_price is None or price > limit_price + 1e-9:
        return False
    initial = _fp_count(order.get("initial_count_fp") if order.get("initial_count_fp") is not None else order.get("initial_count"))
    if abs(initial - int(intent.get("shares") or 0)) > 1e-6:
        return False
    intent_created = _parse_dt(intent.get("created_at"))
    order_created = _parse_dt(order.get("created_time"))
    if intent_created and order_created:
        if order_created < intent_created:
            return False
        if order_created > intent_created + timedelta(hours=max(0.25, RECONCILE_MATCH_WINDOW_HOURS)):
            return False
    return True


def reconcile_order_intents(client, *, path: Path = QUEUE_PATH, now: datetime | None = None) -> dict:
    """Best-effort reconciliation using a GET-only Kalshi client.

    A manual Kalshi UI order has no PM Edge correlation ID. Therefore an intent
    is updated only when exactly one account order matches ticker, buy side,
    exact share count, acceptable price, and a short post-alert timing window.
    Ambiguous matches are never guessed. This function cannot place, modify, or
    cancel orders.
    """
    payload = _load_queue(path)
    active = [
        x for x in payload.get("intents", [])
        if x.get("status") in {"awaiting_approval", "approved", "submitted", "partially_filled"}
    ]
    if not active:
        return {"checked": 0, "matched": 0, "ambiguous": 0, "updated": 0}

    orders = client.get_orders(limit=200)
    fills = client.get_fills(limit=200)
    fills_by_order: dict[str, list[dict]] = {}
    for fill in fills:
        oid = str(fill.get("order_id") or "")
        if oid:
            fills_by_order.setdefault(oid, []).append(fill)

    matched = ambiguous = updated = 0
    checked_at = (now or _utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for intent in active:
        candidates = [o for o in orders if _candidate_order(intent, o)]
        if len(candidates) > 1:
            intent["reconciliation_note"] = "ambiguous_multiple_matching_orders"
            intent["reconciled_at"] = checked_at
            ambiguous += 1
            continue
        if not candidates:
            continue

        order = candidates[0]
        matched += 1
        oid = str(order.get("order_id") or "")
        fill_count = _fp_count(order.get("fill_count_fp") if order.get("fill_count_fp") is not None else order.get("fill_count"))
        initial_count = _fp_count(order.get("initial_count_fp") if order.get("initial_count_fp") is not None else order.get("initial_count"))
        status = str(order.get("status") or "").lower()

        new_status = "submitted"
        if fill_count > 0 and initial_count > 0 and fill_count + 1e-9 >= initial_count:
            new_status = "filled"
        elif fill_count > 0:
            new_status = "partially_filled"
        elif status in {"canceled", "cancelled"}:
            new_status = "canceled"

        order_fills = fills_by_order.get(oid, [])
        weighted_cost = 0.0
        weighted_count = 0.0
        for fill in order_fills:
            count = _fp_count(fill.get("count_fp") if fill.get("count_fp") is not None else fill.get("count"))
            price = _order_price(fill, str(intent.get("side") or "").upper())
            if count > 0 and price is not None:
                weighted_cost += count * price
                weighted_count += count

        previous_status = intent.get("status")
        previous_order_id = intent.get("kalshi_order_id")
        intent.update({
            "status": new_status,
            "kalshi_order_id": oid or None,
            "kalshi_order_status": status or None,
            "filled_shares": round(fill_count, 4),
            "remaining_shares": round(max(0.0, initial_count - fill_count), 4),
            "average_fill_price": round(weighted_cost / weighted_count, 4) if weighted_count > 0 else None,
            "reconciled_at": checked_at,
            "reconciliation_note": "unique_strict_match",
        })
        if previous_status != new_status or previous_order_id != (oid or None):
            updated += 1

    _save_queue(payload, path)
    return {
        "checked": len(active),
        "matched": matched,
        "ambiguous": ambiguous,
        "updated": updated,
    }


def queue_summary(path: Path = QUEUE_PATH) -> dict:
    payload = _load_queue(path)
    intents = payload.get("intents", [])
    counts: dict[str, int] = {}
    for intent in intents:
        status = str(intent.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "mode": _normalized_mode(),
        "total_intents": len(intents),
        "awaiting_approval": counts.get("awaiting_approval", 0),
        "submitted": counts.get("submitted", 0),
        "partially_filled": counts.get("partially_filled", 0),
        "filled": counts.get("filled", 0),
        "live_submission_permitted": False,
    }
