"""PM Edge settlement, P/L, ROI, and fill-rate tracking.

This module is read-only with respect to Kalshi. It consumes the staged-order
queue plus authenticated GET-only fills and public market settlement data, then
persists finalized PM Edge performance records locally.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

import execution_guard

HISTORY_PATH = Path(os.getenv("PM_EDGE_PERFORMANCE_HISTORY_PATH", ".pm_edge_performance_history.json"))
KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")


def _safe_float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _count(value: Any) -> float:
    x = _safe_float(value)
    return 0.0 if x is None else max(0.0, x)


def _load_history(path: Path = HISTORY_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "records": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "records": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        return {"version": 1, "records": []}
    payload.setdefault("version", 1)
    return payload


def _save_history(payload: dict, path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fill_side(fill: dict) -> str:
    side = str(fill.get("outcome_side") or fill.get("side") or "").upper()
    return side if side in {"YES", "NO"} else ""


def _fill_price(fill: dict, side: str) -> float | None:
    value = fill.get("yes_price_dollars" if side == "YES" else "no_price_dollars")
    price = _safe_float(value)
    if price is not None:
        return price
    legacy = _safe_float(fill.get("yes_price" if side == "YES" else "no_price"))
    return legacy / 100.0 if legacy is not None and legacy > 1 else legacy


def _fill_summary(client, intent: dict) -> dict:
    ticker = str(intent.get("ticker") or "")
    order_id = str(intent.get("kalshi_order_id") or "")
    side = str(intent.get("side") or "").upper()
    rows = client.get_fills(ticker=ticker, limit=200)

    count = cost = fees = 0.0
    fee_rows = 0
    matched_rows = 0
    for fill in rows:
        if order_id and str(fill.get("order_id") or "") != order_id:
            continue
        if _fill_side(fill) and _fill_side(fill) != side:
            continue
        matched_rows += 1
        qty = _count(fill.get("count_fp") if fill.get("count_fp") is not None else fill.get("count"))
        price = _fill_price(fill, side)
        if qty > 0 and price is not None:
            count += qty
            cost += qty * price
        fee = _safe_float(fill.get("fee_cost"))
        if fee is not None:
            fees += max(0.0, fee)
            fee_rows += 1

    if count <= 0:
        fallback_count = _count(intent.get("filled_shares"))
        fallback_price = _safe_float(intent.get("average_fill_price"))
        if fallback_count > 0 and fallback_price is not None:
            count = fallback_count
            cost = fallback_count * fallback_price

    return {
        "filled_shares": round(count, 6),
        "entry_cost": round(cost, 6),
        "entry_fees": round(fees, 6),
        "average_fill_price": round(cost / count, 6) if count > 0 else None,
        "fees_complete": matched_rows > 0 and fee_rows == matched_rows,
    }


def _load_market_settlement(ticker: str) -> dict | None:
    response = requests.get(
        f"{KALSHI_PUBLIC_BASE}/markets/{quote(str(ticker), safe='')}",
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        raise RuntimeError("Unexpected Kalshi market response")

    status = str(market.get("status") or "").lower()
    result = str(market.get("result") or "").lower()
    settled_time = market.get("settlement_ts") or market.get("settled_time")
    if status != "settled" and not settled_time and result not in {"yes", "no", "void"}:
        return None

    settlement_value = _safe_float(market.get("settlement_value_dollars"))
    if settlement_value is None:
        legacy = _safe_float(market.get("settlement_value"))
        if legacy is not None:
            settlement_value = legacy / 100.0 if legacy > 1 else legacy
    if settlement_value is None and result == "yes":
        settlement_value = 1.0
    elif settlement_value is None and result == "no":
        settlement_value = 0.0

    if settlement_value is not None and not (0.0 <= settlement_value <= 1.0):
        settlement_value = None

    return {
        "market_result": result or None,
        "settlement_value_yes": settlement_value,
        "settled_time": str(settled_time or "") or None,
    }


def _record_base(intent: dict) -> dict:
    return {
        "intent_id": str(intent.get("intent_id") or ""),
        "trade_date": intent.get("trade_date"),
        "ticker": intent.get("ticker"),
        "market_title": intent.get("market_title"),
        "side": intent.get("side"),
        "lane": intent.get("lane"),
        "limit_price": intent.get("limit_price"),
        "requested_shares": intent.get("shares"),
        "kalshi_order_id": intent.get("kalshi_order_id"),
        "fair_probability": intent.get("fair_probability"),
        "net_edge": intent.get("net_edge"),
        "books": intent.get("books"),
    }


def _unfilled_record(intent: dict, *, result: str | None = None, settled_time: str | None = None) -> dict:
    record = _record_base(intent)
    record.update({
        "status": "unfilled",
        "outcome": "UNFILLED",
        "market_result": result,
        "filled_shares": 0.0,
        "average_fill_price": None,
        "entry_cost": 0.0,
        "entry_fees": 0.0,
        "payout": 0.0,
        "net_pl": 0.0,
        "roi": None,
        "settled_at": settled_time,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    return record


def _settled_record(intent: dict, fill_summary: dict, settlement: dict) -> dict:
    record = _record_base(intent)
    side = str(intent.get("side") or "").upper()
    result = str(settlement.get("market_result") or "").lower()
    value_yes = _safe_float(settlement.get("settlement_value_yes"))
    filled = _count(fill_summary.get("filled_shares"))
    entry_cost = _safe_float(fill_summary.get("entry_cost")) or 0.0
    entry_fees = _safe_float(fill_summary.get("entry_fees")) or 0.0

    payout_per_share = None
    if value_yes is not None:
        payout_per_share = value_yes if side == "YES" else 1.0 - value_yes

    if payout_per_share is None:
        status = "settled_unknown"
        outcome = "UNKNOWN"
        payout = net_pl = roi = None
    else:
        payout = filled * payout_per_share
        net_pl = payout - entry_cost - entry_fees
        denominator = entry_cost + entry_fees
        roi = net_pl / denominator if denominator > 0 else None
        if result in {"yes", "no"}:
            outcome = "WIN" if side.lower() == result else "LOSS"
            status = "settled_win" if outcome == "WIN" else "settled_loss"
        elif result == "void":
            outcome = "VOID"
            status = "settled_void"
        else:
            outcome = "PROFIT" if net_pl > 0 else "LOSS" if net_pl < 0 else "PUSH"
            status = "settled_other"

    record.update({
        "status": status,
        "outcome": outcome,
        "market_result": result or None,
        "settlement_value_yes": value_yes,
        "filled_shares": round(filled, 6),
        "average_fill_price": fill_summary.get("average_fill_price"),
        "entry_cost": round(entry_cost, 6),
        "entry_fees": round(entry_fees, 6),
        "fees_complete": bool(fill_summary.get("fees_complete")),
        "payout": round(payout, 6) if payout is not None else None,
        "net_pl": round(net_pl, 6) if net_pl is not None else None,
        "roi": round(roi, 6) if roi is not None else None,
        "settled_at": settlement.get("settled_time"),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pnl_method": "pm_edge_order_fills_held_to_settlement",
    })
    return record


def update_performance(
    client,
    *,
    queue_path: Path = execution_guard.QUEUE_PATH,
    history_path: Path = HISTORY_PATH,
    market_loader: Callable[[str], dict | None] = _load_market_settlement,
) -> dict:
    """Finalize PM Edge intents when they are unfilled or their markets settle."""
    queue = execution_guard._load_queue(queue_path)
    history = _load_history(history_path)
    known = {str(r.get("intent_id") or "") for r in history.get("records", [])}
    market_cache: dict[str, dict | None] = {}

    checked = finalized = errors = 0
    queue_changed = False

    for intent in queue.get("intents", []):
        intent_id = str(intent.get("intent_id") or "")
        if not intent_id or intent_id in known:
            continue

        status = str(intent.get("status") or "")
        submitted = bool(intent.get("kalshi_order_id")) or status in {
            "submitted", "partially_filled", "filled", "canceled", "cancelled"
        }
        if not submitted:
            continue
        checked += 1

        filled = _count(intent.get("filled_shares"))
        if status in {"canceled", "cancelled"} and filled <= 0:
            record = _unfilled_record(intent)
        else:
            ticker = str(intent.get("ticker") or "")
            try:
                if ticker not in market_cache:
                    market_cache[ticker] = market_loader(ticker)
                settlement = market_cache[ticker]
            except Exception as exc:
                intent["performance_error"] = f"{type(exc).__name__}: {exc}"[:300]
                errors += 1
                queue_changed = True
                continue
            if settlement is None:
                continue

            try:
                fill_summary = _fill_summary(client, intent)
            except Exception as exc:
                intent["performance_error"] = f"{type(exc).__name__}: {exc}"[:300]
                errors += 1
                queue_changed = True
                continue

            filled = _count(fill_summary.get("filled_shares"))
            if filled <= 0:
                record = _unfilled_record(
                    intent,
                    result=settlement.get("market_result"),
                    settled_time=settlement.get("settled_time"),
                )
            else:
                record = _settled_record(intent, fill_summary, settlement)

        history.setdefault("records", []).append(record)
        known.add(intent_id)
        finalized += 1
        intent["performance_recorded"] = True
        intent["performance_status"] = record.get("status")
        intent["performance_recorded_at"] = record.get("recorded_at")
        if record.get("status") != "unfilled":
            intent["status"] = record.get("status")
        else:
            intent["status"] = "unfilled"
        intent.pop("performance_error", None)
        queue_changed = True

    if finalized:
        _save_history(history, history_path)
    if queue_changed:
        execution_guard._save_queue(queue, queue_path)

    summary = performance_summary(history_path=history_path, payload=history)
    return {
        "checked": checked,
        "finalized": finalized,
        "errors": errors,
        "summary": summary,
    }


def performance_summary(*, history_path: Path = HISTORY_PATH, payload: dict | None = None) -> dict:
    history = payload if payload is not None else _load_history(history_path)
    records = history.get("records", [])

    submitted = len(records)
    filled_records = [r for r in records if _count(r.get("filled_shares")) > 0]
    unfilled = sum(1 for r in records if r.get("status") == "unfilled")
    wins = sum(1 for r in records if r.get("outcome") == "WIN")
    losses = sum(1 for r in records if r.get("outcome") == "LOSS")
    voids = sum(1 for r in records if r.get("outcome") == "VOID")

    net_pl = sum((_safe_float(r.get("net_pl")) or 0.0) for r in records)
    entry_cost = sum((_safe_float(r.get("entry_cost")) or 0.0) for r in records)
    fees = sum((_safe_float(r.get("entry_fees")) or 0.0) for r in records)
    risked = entry_cost + fees

    lanes: dict[str, dict] = {}
    for r in records:
        lane = str(r.get("lane") or "unknown")
        bucket = lanes.setdefault(lane, {"bets": 0, "wins": 0, "losses": 0, "unfilled": 0, "net_pl": 0.0})
        bucket["bets"] += 1
        if r.get("outcome") == "WIN":
            bucket["wins"] += 1
        elif r.get("outcome") == "LOSS":
            bucket["losses"] += 1
        if r.get("status") == "unfilled":
            bucket["unfilled"] += 1
        bucket["net_pl"] += _safe_float(r.get("net_pl")) or 0.0
    for bucket in lanes.values():
        bucket["net_pl"] = round(bucket["net_pl"], 2)

    return {
        "tracked_orders": submitted,
        "filled_orders": len(filled_records),
        "unfilled_orders": unfilled,
        "fill_rate": round(len(filled_records) / submitted, 4) if submitted else None,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "record": f"{wins}-{losses}",
        "entry_cost": round(entry_cost, 2),
        "fees": round(fees, 2),
        "net_pl": round(net_pl, 2),
        "roi": round(net_pl / risked, 4) if risked > 0 else None,
        "by_lane": lanes,
    }
