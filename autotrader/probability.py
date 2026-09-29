"""Fresh independent fair-value adapters for the paper simulator."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import hashlib
from pathlib import Path
from decimal import Decimal, InvalidOperation

import requests


@dataclass(frozen=True)
class ProbabilitySnapshot:
    probabilities: dict
    ages: dict
    sources: dict
    observed_at: datetime
    contexts: dict = field(default_factory=dict)


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Fail closed on ambiguous naive timestamps. Railway currently runs UTC, but
    # silently assuming server-local time can make stale values appear fresh.
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _age_seconds(now, ts, *, max_future_skew_seconds=5):
    age = (now - ts).total_seconds()
    if age < -max_future_skew_seconds:
        return None
    return max(0.0, age)


def _build_snapshot(rows, *, now, max_age_seconds, source_default,
                    require_fair_timestamp=True, pregame_only=True,
                    reject_pushable_lines=True, min_seconds_to_start=300):
    latest = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip()
        side = str(row.get("side") or "").upper()
        fair_ts_raw = row.get("fair_fetched_at")
        if require_fair_timestamp and not fair_ts_raw:
            continue
        ts = _parse_ts(
            fair_ts_raw
            or row.get("evaluated_at")
            or row.get("alerted_at")
        )
        if not ticker or side not in ("YES", "NO") or ts is None:
            continue

        market_type = str(row.get("market_type") or "").strip().lower()
        if reject_pushable_lines and market_type in {"spread", "total", "team_total"}:
            try:
                point = Decimal(str(row.get("point")))
            except (InvalidOperation, ValueError, TypeError):
                continue
            # Kalshi "over N" resolves NO at exactly N while a sportsbook whole
            # number can push. They are not semantically identical contracts.
            if point == point.to_integral_value():
                continue

        if pregame_only:
            commence = _parse_ts(row.get("commence_time"))
            if commence is None or (commence - now).total_seconds() < min_seconds_to_start:
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
        snapshot_id = str(row.get("fair_snapshot_id") or "").strip()
        if not snapshot_id:
            snapshot_raw = "|".join([
                ticker, side, str(ts.isoformat()), str(prob),
                str(row.get("event_id") or ""),
            ])
            snapshot_id = hashlib.sha256(snapshot_raw.encode("utf-8")).hexdigest()[:20]
        context = {
            "event_id": str(row.get("event_id") or "").strip(),
            "fair_snapshot_id": snapshot_id,
            "commence_time": str(row.get("commence_time") or "").strip(),
            "market_type": market_type,
            "selection": str(row.get("selection") or "").strip(),
            "point": row.get("point"),
        }
        old = latest.get(key)
        if old is None or ts > old[0]:
            latest[key] = (
                ts,
                prob,
                age,
                str(row.get("devig_method") or row.get("source_version") or source_default),
                context,
            )

    probabilities = {key: value[1] for key, value in latest.items()}
    ages = {key: value[2] for key, value in latest.items()}
    for key, value in latest.items():
        ticker, _side = key
        ages[ticker] = max(ages.get(ticker, 0.0), value[2])
    sources = {key: value[3] for key, value in latest.items()}
    contexts = {key: value[4] for key, value in latest.items()}
    return ProbabilitySnapshot(probabilities, ages, sources, now, contexts)


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
    """Load fresh scored PM Edge fair values through the authenticated read-only API.

    It reuses probabilities already produced by PM Edge before alert/execution
    gating and does not spend additional sportsbook-odds API credits.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not base_url or not token:
        return ProbabilitySnapshot({}, {}, {}, now)
    response = requests.get(
        base_url.rstrip("/") + "/v1/fair-values",
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
