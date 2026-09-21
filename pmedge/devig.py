from __future__ import annotations
from dataclasses import dataclass
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


def devig_outcomes(outcomes: list[dict]) -> dict[str, float]:
    vals = []
    names = []
    for o in outcomes:
        price = float(o["price"])
        vals.append(decimal_to_implied(price))
        names.append(str(o["name"]))
    fair = normalize_probs(vals)
    return dict(zip(names, fair))


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
