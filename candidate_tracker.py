"""Durable decision ledger for PM Edge model candidates.

This complements signal_tracker.py. signal_tracker records alerts that were sent;
this ledger records model evaluations and alert-gate outcomes so missed,
suppressed, and rejected opportunities can be audited later.
"""
from __future__ import annotations

import csv
import hashlib
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_LEDGER_PATH = Path(
    os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv")
)

FIELDS = [
    "decision_id",
    "evaluated_at",
    "run_bucket_utc",
    "ticker",
    "event_id",
    "market_title",
    "sport_key",
    "sport_title",
    "market_type",
    "side",
    "lane",
    "stage",
    "decision",
    "reason",
    "qualifies",
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


def _run_bucket(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    minute = (dt.minute // 30) * 30
    return dt.replace(minute=minute, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _decision_id(
    *,
    run_bucket: str,
    ticker: str,
    side: str,
    lane: str,
    stage: str,
    decision: str,
    price: float | None,
) -> str:
    px = "" if price is None else f"{price:.4f}"
    raw = f"{run_bucket}|{ticker}|{side}|{lane}|{stage}|{decision}|{px}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return {str(row.get("decision_id") or "") for row in csv.DictReader(fh)}
    except OSError:
        return set()


def record_candidate(
    market: dict,
    fair_entry: dict,
    q,
    *,
    lane: str,
    stage: str,
    decision: str,
    reason: str = "",
    source_version: int = 28,
    path: Path = CANDIDATE_LEDGER_PATH,
    evaluated_at: str | None = None,
) -> dict:
    """Append one candidate decision, deduped within each 30-minute run bucket."""
    ticker = str(market.get("ticker") or "").strip()
    if not ticker:
        return {"recorded": False, "reason": "missing_ticker"}

    evaluated_at = evaluated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run_bucket = _run_bucket(evaluated_at)
    side = str(getattr(q, "side", "") or "").upper() or "UNKNOWN"
    lane = str(lane or "unknown").lower()
    stage = str(stage or "score").lower()
    decision = str(decision or "UNKNOWN").upper()
    price = _safe_float(getattr(q, "recommended_limit", None))

    decision_id = _decision_id(
        run_bucket=run_bucket,
        ticker=ticker,
        side=side,
        lane=lane,
        stage=stage,
        decision=decision,
        price=price,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    if decision_id in _existing_ids(path):
        return {"recorded": False, "reason": "duplicate", "decision_id": decision_id}

    books = fair_entry.get("n_books")
    try:
        books = int(books) if books is not None else None
    except (TypeError, ValueError):
        books = None

    row = {
        "decision_id": decision_id,
        "evaluated_at": evaluated_at,
        "run_bucket_utc": run_bucket,
        "ticker": ticker,
        "event_id": str(fair_entry.get("event_id") or ""),
        "market_title": str(market.get("title") or ""),
        "sport_key": str(fair_entry.get("sport_key") or ""),
        "sport_title": str(fair_entry.get("sport_title") or ""),
        "market_type": str(fair_entry.get("market_type") or ""),
        "side": side,
        "lane": lane,
        "stage": stage,
        "decision": decision,
        "reason": str(reason or ""),
        "qualifies": bool(getattr(q, "qualifies", False)),
        "execution": str(getattr(q, "execution", "") or ""),
        "recommended_limit": price,
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

    return {"recorded": True, "decision_id": decision_id}
