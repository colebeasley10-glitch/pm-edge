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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_PATH = Path(os.getenv("PM_EDGE_ORDER_QUEUE_PATH", ".pm_edge_order_queue.json"))
MAX_ORDER_RISK = float(os.getenv("PM_EDGE_EXECUTION_MAX_ORDER_RISK", "10.00"))
DAILY_RISK_LIMIT = float(os.getenv("PM_EDGE_EXECUTION_DAILY_RISK_LIMIT", "50.00"))
MAX_QUEUE_AGE_DAYS = int(os.getenv("PM_EDGE_EXECUTION_QUEUE_RETENTION_DAYS", "14"))
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
    if projected > DAILY_RISK_LIMIT + 1e-9:
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


def queue_summary(path: Path = QUEUE_PATH) -> dict:
    payload = _load_queue(path)
    intents = payload.get("intents", [])
    awaiting = [x for x in intents if x.get("status") == "awaiting_approval"]
    return {
        "mode": _normalized_mode(),
        "total_intents": len(intents),
        "awaiting_approval": len(awaiting),
        "live_submission_permitted": False,
    }
