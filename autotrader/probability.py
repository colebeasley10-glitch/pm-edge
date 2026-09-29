"""Fresh independent fair-value adapters for the paper simulator."""
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
from pathlib import Path
from decimal import Decimal, InvalidOperation


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
    except ValueError:
        return None


def load_pm_edge_ledger(path="pm_edge_candidate_ledger.csv", *, now=None, max_age_seconds=300):
    """Load the newest fresh sportsbook-derived probability per ticker/side.

    This is intentionally limited to PM Edge rows that already contain an
    independent fair probability. Kalshi prices are never used as fair value.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return ProbabilitySnapshot({}, {}, {}, now)

    latest = {}
    with p.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("stage") or "").lower() != "score":
                continue
            ticker = str(row.get("ticker") or "").strip()
            side = str(row.get("side") or "").upper()
            ts = _parse_ts(row.get("evaluated_at"))
            if not ticker or side not in ("YES", "NO") or ts is None:
                continue
            try:
                prob = Decimal(str(row.get("fair_probability")))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if not (Decimal("0") < prob < Decimal("1")):
                continue
            age = max(0.0, (now - ts).total_seconds())
            if age > max_age_seconds:
                continue
            key = (ticker, side)
            old = latest.get(key)
            if old is None or ts > old[0]:
                latest[key] = (ts, prob, age, str(row.get("devig_method") or "sportsbook_no_vig"))

    probabilities = {key: value[1] for key, value in latest.items()}
    ages = {}
    sources = {}
    for key, value in latest.items():
        ticker, side = key
        ages[ticker] = min(ages.get(ticker, value[2]), value[2])
        sources[key] = value[3]
    return ProbabilitySnapshot(probabilities, ages, sources, now)
