"""Durable audit ledger for every PM Edge alert that is actually sent.

The watcher state and order queue live in GitHub Actions cache and can be
reconstructed imperfectly. This CSV is intentionally committed by the watch
workflow so the original model inputs survive for later calibration audits.
"""
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
    "risk_multiplier",
    "audit_bucket",
    "source_version",
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


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return {str(row.get("signal_id") or "") for row in csv.DictReader(fh)}
    except OSError:
        return set()


def record_signal(
    market: dict,
    fair_entry: dict,
    q,
    *,
    lane: str,
    source_version: int,
    path: Path = SIGNAL_LEDGER_PATH,
    alerted_at: str | None = None,
) -> dict:
    """Append one sent signal, deduplicated by date/ticker/side/lane/price."""
    side = str(getattr(q, "side", "") or "").upper()
    ticker = str(market.get("ticker") or "").strip()
    price = _safe_float(getattr(q, "recommended_limit", None))
    if side not in {"YES", "NO"} or not ticker or price is None:
        return {"recorded": False, "reason": "missing_signal_fields"}

    alerted_at = alerted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    trade_date = alerted_at[:10]
    lane = str(lane or "primary").lower()
    signal_id = _signal_id(
        trade_date=trade_date,
        ticker=ticker,
        side=side,
        lane=lane,
        price=price,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    if signal_id in _existing_ids(path):
        return {"recorded": False, "reason": "duplicate", "signal_id": signal_id}

    books = fair_entry.get("n_books")
    try:
        books = int(books) if books is not None else None
    except (TypeError, ValueError):
        books = None

    row = {
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
        "risk_multiplier": _safe_float(getattr(q, "pmedge_risk_multiplier", 1.0)),
        "audit_bucket": str(getattr(q, "pmedge_audit_bucket", "") or ""),
        "source_version": int(source_version),
    }

    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return {"recorded": True, "signal_id": signal_id}
