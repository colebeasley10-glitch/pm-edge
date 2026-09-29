"""Fair Value v2 research metrics.

This module is deliberately diagnostic-only in v1: it does not replace the
existing Value Edge fair probability or lower any entry threshold.  It computes
sharp-weighted fair estimates and uncertainty components so they can be logged,
calibrated against CLV, and promoted later under a new lane version.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone


TIER1 = {"pinnacle"}
TIER2 = {
    "circa", "circasports", "betonlineag", "betonline", "lowvig",
    "bookmaker", "bookmaker.eu",
}


@dataclass(frozen=True)
class FairV2Metrics:
    weighted_fair: float | None
    sigma: float | None
    sigma_base: float | None
    sigma_disagree: float | None
    sigma_age: float | None
    n_books: int
    n_sharp: int
    n_eff: float
    disagreement: float | None
    info_ts: str | None
    source_age_s: float | None
    system_age_s: float
    delivery_lag_s: float | None
    method_version: str = "fair_v2_shadow_1"


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def book_weight(key: str | None, title: str | None = None) -> float:
    blob = f"{key or ''} {title or ''}".lower()
    if any(x in blob for x in TIER1):
        return 2.0
    if any(x in blob for x in TIER2):
        return 1.0
    return 0.5


def sigma_base_for(market_type: str | None) -> float:
    defaults = {
        "h2h": 0.015,
        "spread": 0.018,
        "total": 0.018,
        "team_total": 0.022,
    }
    env = f"PM_EDGE_SIGMA_BASE_{str(market_type or 'OTHER').upper()}"
    return max(0.0, float(os.getenv(env, defaults.get(str(market_type or ""), 0.025))))


def proximity_factor(event_start, now) -> float:
    start = _parse_ts(event_start)
    if start is None:
        return 1.0
    hours = max(0.0, (start - now).total_seconds() / 3600.0)
    if hours < 0.25:
        return 4.0
    if hours < 2:
        return 3.0
    if hours < 6:
        return 2.0
    if hours < 24:
        return 1.5
    return 1.0


def compute_fair_v2_metrics(
    book_inputs: list[dict] | None,
    *,
    market_type: str | None,
    event_start=None,
    processed_at=None,
) -> FairV2Metrics:
    now = _parse_ts(processed_at) or datetime.now(timezone.utc)
    rows = []
    for row in book_inputs or []:
        try:
            p = float(row.get("probability"))
        except (TypeError, ValueError):
            continue
        if not (0 < p < 1):
            continue
        w = max(0.01, float(row.get("weight") or book_weight(row.get("book_key"), row.get("book_title"))))
        ts = _parse_ts(row.get("source_ts"))
        rows.append((p, w, ts, row))

    if not rows:
        return FairV2Metrics(
            None, None, None, None, None, 0, 0, 0.0, None, None, None, 0.0, None
        )

    sum_w = sum(w for _, w, _, _ in rows)
    fair = sum(p * w for p, w, _, _ in rows) / sum_w
    sum_w2 = sum(w * w for _, w, _, _ in rows)
    n_eff = (sum_w * sum_w / sum_w2) if sum_w2 else 0.0
    variance = sum(w * (p - fair) ** 2 for p, w, _, _ in rows) / sum_w
    disagreement = math.sqrt(max(0.0, variance))

    sharp = sum(
        1 for _, _, _, r in rows
        if book_weight(r.get("book_key"), r.get("book_title")) >= 1.0
    )

    # Weighted source timestamp is provenance/staleness metadata only. It never
    # changes point-in-time availability, which remains processed_at/received_at.
    timestamped = [(ts.timestamp(), w) for _, w, ts, _ in rows if ts is not None]
    info_dt = None
    if timestamped:
        denom = sum(w for _, w in timestamped)
        info_dt = datetime.fromtimestamp(
            sum(t * w for t, w in timestamped) / denom,
            tz=timezone.utc,
        )

    source_age_s = max(0.0, (now - info_dt).total_seconds()) if info_dt else None
    delivery_lag_s = source_age_s
    sigma_base = sigma_base_for(market_type)
    sigma_disagree = disagreement / math.sqrt(max(n_eff, 1e-9))

    # lambda has units variance per second. Shadow-only until calibrated to CLV.
    lam = max(0.0, float(os.getenv("PM_EDGE_SIGMA_AGE_LAMBDA", "0.00000002")))
    age_for_penalty = source_age_s if source_age_s is not None else 0.0
    prox = proximity_factor(event_start, now)
    sigma_age = math.sqrt(max(0.0, lam * age_for_penalty * prox))
    sigma = math.sqrt(
        sigma_base ** 2 + sigma_disagree ** 2 + sigma_age ** 2
    )

    return FairV2Metrics(
        weighted_fair=fair,
        sigma=sigma,
        sigma_base=sigma_base,
        sigma_disagree=sigma_disagree,
        sigma_age=sigma_age,
        n_books=len(rows),
        n_sharp=sharp,
        n_eff=n_eff,
        disagreement=disagreement,
        info_ts=(info_dt.isoformat().replace("+00:00", "Z") if info_dt else None),
        source_age_s=source_age_s,
        system_age_s=0.0,
        delivery_lag_s=delivery_lag_s,
    )
