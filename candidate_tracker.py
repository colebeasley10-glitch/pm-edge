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
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_LEDGER_PATH = Path(
    os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv")
)

_SEEN_BY_PATH: dict[str, set[str]] = {}

FIELDS = [
    "decision_id",
    "evaluated_at",
    "fair_fetched_at",
    "fair_snapshot_id",
    "run_bucket_utc",
    "ticker",
    "event_id",
    "commence_time",
    "market_title",
    "sport_key",
    "sport_title",
    "market_type",
    "selection",
    "point",
    "side",
    "lane",
    "stage",
    "decision",
    "reason",
    "qualifies",
    "execution",
    "recommended_limit",
    "hard_max",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "fair_probability",
    "fair_proportional",
    "fair_power",
    "fair_shin",
    "devig_method",
    "net_edge",
    "books",
    "books_available",
    "books_matched",
    "book_drop_reasons",
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


def _ensure_schema(path: Path) -> None:
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
            writer.writerow({field: row.get(field, "") for field in FIELDS})
    tmp.replace(path)


def _existing_ids(path: Path) -> set[str]:
    _ensure_schema(path)
    key = str(path.resolve())
    cached = _SEEN_BY_PATH.get(key)
    if cached is not None:
        return cached
    if not path.exists():
        cached = set()
    else:
        try:
            with path.open("r", encoding="utf-8", newline="") as fh:
                cached = {str(row.get("decision_id") or "") for row in csv.DictReader(fh)}
        except OSError:
            cached = set()
    _SEEN_BY_PATH[key] = cached
    return cached


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

    fair_fetched_at = str(fair_entry.get("fetched_at") or "")
    snapshot_raw = "|".join([
        ticker,
        fair_fetched_at,
        str(fair_entry.get("fair_yes_prob") or ""),
        str(fair_entry.get("n_books") or ""),
        str(fair_entry.get("devig_method") or ""),
    ])
    fair_snapshot_id = hashlib.sha256(snapshot_raw.encode("utf-8")).hexdigest()[:20]

    row = {
        "decision_id": decision_id,
        "evaluated_at": evaluated_at,
        "fair_fetched_at": fair_fetched_at,
        "fair_snapshot_id": fair_snapshot_id,
        "run_bucket_utc": run_bucket,
        "ticker": ticker,
        "event_id": str(fair_entry.get("event_id") or ""),
        "commence_time": str(fair_entry.get("commence_time") or ""),
        "market_title": str(market.get("title") or ""),
        "sport_key": str(fair_entry.get("sport_key") or ""),
        "sport_title": str(fair_entry.get("sport_title") or ""),
        "market_type": str(fair_entry.get("market_type") or ""),
        "selection": str(fair_entry.get("selection") or ""),
        "point": _safe_float(fair_entry.get("point")),
        "side": side,
        "lane": lane,
        "stage": stage,
        "decision": decision,
        "reason": str(reason or ""),
        "qualifies": bool(getattr(q, "qualifies", False)),
        "execution": str(getattr(q, "execution", "") or ""),
        "recommended_limit": price,
        "hard_max": _safe_float(getattr(q, "hard_max", None)),
        "yes_bid": _safe_float(market.get("yes_bid")),
        "yes_ask": _safe_float(market.get("yes_ask")),
        "no_bid": _safe_float(market.get("no_bid")),
        "no_ask": _safe_float(market.get("no_ask")),
        "fair_probability": _side_probability(fair_entry, side),
        "fair_proportional": _safe_float(fair_entry.get("fair_proportional")),
        "fair_power": _safe_float(fair_entry.get("fair_power")),
        "fair_shin": _safe_float(fair_entry.get("fair_shin")),
        "devig_method": str(fair_entry.get("devig_method") or ""),
        "net_edge": _safe_float(getattr(q, "net_edge", None)),
        "books": books,
        "books_available": fair_entry.get("books_available"),
        "books_matched": fair_entry.get("books_matched"),
        "book_drop_reasons": json.dumps(fair_entry.get("book_drop_reasons") or [], separators=(",", ":")),
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

    _existing_ids(path).add(decision_id)
    return {"recorded": True, "decision_id": decision_id}
