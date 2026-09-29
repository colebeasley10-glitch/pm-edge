"""Fresh independent fair-value adapters for the paper simulator."""
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
from pathlib import Path
from decimal import Decimal, InvalidOperation

import requests


@dataclass(frozen=True)
class ProbabilitySnapshot:
    probabilities: dict
    ages: dict
    sources: dict
    observed_at: datetime


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _age_seconds(now, ts, *, max_future_skew_seconds=5):
    age = (now - ts).total_seconds()
    if age < -max_future_skew_seconds:
        return None
    return max(0.0, age)


def _build_snapshot(rows, *, now, max_age_seconds, source_default):
    latest = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip()
        side = str(row.get("side") or "").upper()
        ts = _parse_ts(
            row.get("fair_fetched_at")
            or row.get("evaluated_at")
            or row.get("alerted_at")
        )
        if not ticker or side not in ("YES", "NO") or ts is None:
            continue
        try:
            prob = Decimal(str(row.get("fair_probability")))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if not (Decimal("0") < prob < Decimal("1")):
            continue
        age = _age_seconds(now, ts)
        if age is None or age > max_age_seconds:
            continue
        key = (ticker, side)
        old = latest.get(key)
        if old is None or ts > old[0]:
            latest[key] = (
                ts,
                prob,
                age,
                str(row.get("devig_method") or row.get("source_version") or source_default),
            )

    probabilities = {key: value[1] for key, value in latest.items()}
    ages = {key: value[2] for key, value in latest.items()}
    for key, value in latest.items():
        ticker, _side = key
        ages[ticker] = max(ages.get(ticker, 0.0), value[2])
    sources = {key: value[3] for key, value in latest.items()}
    return ProbabilitySnapshot(probabilities, ages, sources, now)


def load_pm_edge_ledger(path="pm_edge_candidate_ledger.csv", *, now=None, max_age_seconds=300):
    """Load newest fresh sportsbook-derived probability per ticker/side from CSV."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return ProbabilitySnapshot({}, {}, {}, now)

    rows = []
    with p.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("stage") or "").lower() != "score":
                continue
            rows.append(row)
    return _build_snapshot(
        rows, now=now, max_age_seconds=max_age_seconds,
        source_default="pm_edge_sportsbook_ledger",
    )


def load_pm_edge_api(base_url, token, *, now=None, max_age_seconds=5400, timeout=10):
    """Load fresh PM Edge alert fair values through the existing authenticated GET API.

    This endpoint is read-only. It reuses probabilities already produced by PM Edge;
    it does not spend additional sportsbook-odds API credits.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not base_url or not token:
        return ProbabilitySnapshot({}, {}, {}, now)
    response = requests.get(
        base_url.rstrip("/") + "/v1/bets",
        params={"status": "new"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unexpected PM Edge fair-value API response")
    return _build_snapshot(
        rows, now=now, max_age_seconds=max_age_seconds,
        source_default="pm_edge_mobile_api",
    )
