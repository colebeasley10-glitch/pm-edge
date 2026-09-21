"""Durable audit ledger for every PM Edge alert and approved submission."""
from __future__ import annotations

import csv
import hashlib
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIGNAL_LEDGER_PATH = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))

FIELDS = [
    "record_type",
    "signal_id",
    "alerted_at",
    "trade_date",
    "ticker",
    "event_id",
    "market_title",
    "sport_key",
    "sport_title",
    "market_type",
    "side",
    "lane",
    "execution",
    "recommended_limit",
    "hard_max",
    "fair_probability",
    "net_edge",
    "books",
    "dispersion",
    "fair_fetched_at",
    "commence_time",
    "risk_multiplier",
    "audit_bucket",
    "source_version",
    "client_order_id",
    "submitted_at",
    "kalshi_order_id",
]


def _safe_float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _side_probability(fair_entry: dict, side: str) -> float | None:
    yes = _safe_float(fair_entry.get("fair_yes_prob"))
    if yes is None:
        return None
    side = str(side or "").upper()
    if side == "YES":
        return yes
    if side == "NO":
        return 1.0 - yes
    return None


def _signal_id(*, trade_date: str, ticker: str, side: str, lane: str, price: float) -> str:
    raw = f"{trade_date}|{ticker}|{side}|{lane}|{price:.4f}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _ensure_schema(path: Path) -> None:
    """Upgrade older signal-ledger headers in place without altering values."""
    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            old_fields = list(reader.fieldnames or [])
            if old_fields == FIELDS:
                return
            rows = list(reader)
    except OSError:
        return

    tmp = path.with_suffix(path.suffix + ".schema.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            upgraded = {field: row.get(field, "") for field in FIELDS}
            upgraded["record_type"] = upgraded.get("record_type") or "signal"
            writer.writerow(upgraded)
    tmp.replace(path)


def _existing_signal_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    _ensure_schema(path)
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return {
                str(row.get("signal_id") or "")
                for row in csv.DictReader(fh)
                if str(row.get("record_type") or "signal") == "signal"
            }
    except OSError:
        return set()


def _existing_client_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    _ensure_schema(path)
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return {
                str(row.get("client_order_id") or "")
                for row in csv.DictReader(fh)
                if row.get("client_order_id")
            }
    except OSError:
        return set()


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(path)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in FIELDS})


def record_signal(
    market: dict,
    fair_entry: dict,
    q,
    *,
    lane: str,
    source_version: int,
    path: Path = SIGNAL_LEDGER_PATH,
    alerted_at: str | None = None,
    signal_id: str | None = None,
) -> dict:
    """Append one sent signal, with a provenance id carried into approval."""
    side = str(getattr(q, "side", "") or "").upper()
    ticker = str(market.get("ticker") or "").strip()
    price = _safe_float(getattr(q, "recommended_limit", None))
    if side not in {"YES", "NO"} or not ticker or price is None:
        return {"recorded": False, "reason": "missing_signal_fields"}

    alerted_at = alerted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    trade_date = alerted_at[:10]
    lane = str(lane or "primary").lower()
    signal_id = str(signal_id or "").strip().lower() or _signal_id(
        trade_date=trade_date,
        ticker=ticker,
        side=side,
        lane=lane,
        price=price,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    if signal_id in _existing_signal_ids(path):
        return {"recorded": False, "reason": "duplicate", "signal_id": signal_id}

    books = fair_entry.get("n_books")
    try:
        books = int(books) if books is not None else None
    except (TypeError, ValueError):
        books = None

    row = {
        "record_type": "signal",
        "signal_id": signal_id,
        "alerted_at": alerted_at,
        "trade_date": trade_date,
        "ticker": ticker,
        "event_id": str(fair_entry.get("event_id") or ""),
        "market_title": str(market.get("title") or ""),
        "sport_key": str(fair_entry.get("sport_key") or ""),
        "sport_title": str(fair_entry.get("sport_title") or ""),
        "market_type": str(fair_entry.get("market_type") or ""),
        "side": side,
        "lane": lane,
        "execution": str(getattr(q, "execution", "") or ""),
        "recommended_limit": round(price, 6),
        "hard_max": _safe_float(getattr(q, "hard_max", None)),
        "fair_probability": _side_probability(fair_entry, side),
        "net_edge": _safe_float(getattr(q, "net_edge", None)),
        "books": books,
        "dispersion": _safe_float(fair_entry.get("dispersion")),
        "fair_fetched_at": str(fair_entry.get("fetched_at") or ""),
        "commence_time": str(fair_entry.get("commence_time") or ""),
        "risk_multiplier": _safe_float(getattr(q, "pmedge_risk_multiplier", 1.0)),
        "audit_bucket": str(getattr(q, "pmedge_audit_bucket", "") or ""),
        "source_version": int(source_version),
        "client_order_id": "",
        "submitted_at": "",
        "kalshi_order_id": "",
    }
    _append(path, row)
    return {"recorded": True, "signal_id": signal_id}


def record_submission(
    trade: dict,
    *,
    client_order_id: str,
    order_result: dict,
    path: Path = SIGNAL_LEDGER_PATH,
    submitted_at: str | None = None,
) -> dict:
    """Append the approved order using client_order_id as its durable key."""
    client_order_id = str(client_order_id or "").strip()
    signal_id = str(trade.get("signal_id") or "").strip().lower()
    if not client_order_id or not signal_id:
        return {"recorded": False, "reason": "missing_submission_fields"}
    if client_order_id in _existing_client_ids(path):
        return {"recorded": False, "reason": "duplicate", "client_order_id": client_order_id}

    submitted_at = submitted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = {
        "record_type": "submission",
        "signal_id": signal_id,
        "alerted_at": "",
        "trade_date": submitted_at[:10],
        "ticker": str(trade.get("ticker") or ""),
        "event_id": "",
        "market_title": "",
        "sport_key": "",
        "sport_title": "",
        "market_type": "",
        "side": str(trade.get("side") or "").upper(),
        "lane": str(trade.get("lane") or ""),
        "execution": str(trade.get("execution") or ""),
        "recommended_limit": _safe_float(trade.get("limit_price")),
        "hard_max": "",
        "fair_probability": _safe_float(trade.get("fair_probability")),
        "net_edge": _safe_float(trade.get("net_edge")),
        "books": trade.get("books"),
        "dispersion": _safe_float(trade.get("dispersion")),
        "fair_fetched_at": str(trade.get("fair_fetched_at") or ""),
        "commence_time": "",
        "risk_multiplier": "",
        "audit_bucket": "",
        "source_version": trade.get("source_version"),
        "client_order_id": client_order_id,
        "submitted_at": submitted_at,
        "kalshi_order_id": str(order_result.get("order_id") or ""),
    }
    _append(path, row)
    return {"recorded": True, "signal_id": signal_id, "client_order_id": client_order_id}
