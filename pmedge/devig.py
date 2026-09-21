from __future__ import annotations

from dataclasses import dataclass
import math
import os
import statistics


@dataclass(frozen=True)
class Consensus:
    fair_prob: float
    n_books: int
    dispersion: float
    confidence: float
    book_probs: tuple[float, ...]


def decimal_to_implied(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / decimal_odds


def normalize_probs(implied: list[float]) -> list[float]:
    total = sum(implied)
    if total <= 0:
        raise ValueError("implied probabilities must sum to > 0")
    return [p / total for p in implied]


def _names_and_implied(outcomes: list[dict]) -> tuple[list[str], list[float]]:
    names = []
    implied = []
    for outcome in outcomes:
        names.append(str(outcome["name"]))
        implied.append(decimal_to_implied(float(outcome["price"])))
    if len(names) < 2:
        raise ValueError("devig requires at least two outcomes")
    return names, implied


def devig_proportional(outcomes: list[dict]) -> dict[str, float]:
    names, implied = _names_and_implied(outcomes)
    return dict(zip(names, normalize_probs(implied)))


def devig_power(outcomes: list[dict]) -> dict[str, float]:
    names, implied = _names_and_implied(outcomes)

    def total_at(exponent: float) -> float:
        return sum(p ** exponent for p in implied)

    at_one = total_at(1.0)
    if abs(at_one - 1.0) <= 1e-12:
        return dict(zip(names, normalize_probs(implied)))

    if at_one > 1.0:
        lo, hi = 1.0, 2.0
        while total_at(hi) > 1.0 and hi < 128.0:
            hi *= 2.0
    else:
        lo, hi = 1e-9, 1.0

    for _ in range(100):
        mid = (lo + hi) / 2.0
        if total_at(mid) > 1.0:
            lo = mid
        else:
            hi = mid

    exponent = (lo + hi) / 2.0
    probs = normalize_probs([p ** exponent for p in implied])
    return dict(zip(names, probs))


def devig_shin(outcomes: list[dict]) -> dict[str, float]:
    names, implied = _names_and_implied(outcomes)
    book_sum = sum(implied)
    if book_sum <= 0:
        raise ValueError("implied probabilities must sum to > 0")
    if abs(book_sum - 1.0) <= 1e-12:
        return dict(zip(names, normalize_probs(implied)))

    def probs_at(z: float) -> list[float]:
        one_minus_z = max(1e-12, 1.0 - z)
        denom = 2.0 * one_minus_z
        return [
            (math.sqrt(z * z + 4.0 * one_minus_z * (q * q) / book_sum) - z) / denom
            for q in implied
        ]

    lo, hi = 0.0, 0.999999999
    if (sum(probs_at(lo)) - 1.0) < 0 or (sum(probs_at(hi)) - 1.0) > 0:
        return devig_proportional(outcomes)

    for _ in range(100):
        mid = (lo + hi) / 2.0
        if sum(probs_at(mid)) > 1.0:
            lo = mid
        else:
            hi = mid

    probs = normalize_probs(probs_at((lo + hi) / 2.0))
    return dict(zip(names, probs))


def active_devig_method() -> str:
    method = os.getenv("PM_EDGE_DEVIG_METHOD", "power").strip().lower()
    return method if method in {"proportional", "power", "shin"} else "power"


def devig_all(outcomes: list[dict]) -> dict[str, dict[str, float]]:
    return {
        "proportional": devig_proportional(outcomes),
        "power": devig_power(outcomes),
        "shin": devig_shin(outcomes),
    }


def devig_outcomes(outcomes: list[dict]) -> dict[str, float]:
    all_methods = devig_all(outcomes)
    return all_methods[active_devig_method()]


def robust_consensus(probs: list[float], min_books: int = 3, max_dispersion: float = 0.10) -> Consensus | None:
    clean = [float(p) for p in probs if 0 < float(p) < 1]
    if len(clean) < min_books:
        return None
    med = statistics.median(clean)
    trimmed = [p for p in clean if abs(p - med) <= max_dispersion]
    if len(trimmed) < min_books:
        return None
    fair = statistics.mean(trimmed)
    disp = statistics.pstdev(trimmed) if len(trimmed) > 1 else 0.0
    breadth = min(1.0, len(trimmed) / 8.0)
    stability = max(0.0, 1.0 - disp / max(max_dispersion, 1e-9))
    confidence = max(0.05, min(1.0, 0.45 * breadth + 0.55 * stability))
    return Consensus(fair, len(trimmed), disp, confidence, tuple(trimmed))
