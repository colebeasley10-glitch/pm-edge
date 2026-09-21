from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

from .devig import devig_outcomes, robust_consensus
from .matcher import team_similarity


@dataclass(frozen=True)
class FairValue:
    fair_yes_prob: float
    source: str
    confidence: float
    n_books: int
    dispersion: float
    raw_book_prob: float | None
    model_prob: float | None
    detail: str


def load_model_overlay(path: str = "data/model_overlay.csv") -> dict[str, dict]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    df = pd.read_csv(p)
    if df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        ticker = str(r.get("ticker", "")).strip()
        prob = pd.to_numeric(r.get("model_prob"), errors="coerce")
        if not ticker or pd.isna(prob) or not (0 < float(prob) < 1):
            continue
        out[ticker] = {
            "model_prob": float(prob),
            "weight": float(r.get("weight", 0.35) or 0.35),
            "source": str(r.get("source", "model")),
            "notes": str(r.get("notes", "")),
        }
    return out


def load_manual_values(path: str = "data/manual_fair_values.csv") -> dict[str, dict]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    df = pd.read_csv(p)
    out = {}
    for _, r in df.iterrows():
        ticker = str(r.get("ticker", "")).strip()
        prob = pd.to_numeric(r.get("fair_yes_prob"), errors="coerce")
        if ticker and not pd.isna(prob) and 0 < float(prob) < 1:
            out[ticker] = {
                "fair_yes_prob": float(prob),
                "source": str(r.get("source", "manual")),
                "confidence": float(r.get("confidence", 0.5) or 0.5),
                "notes": str(r.get("notes", "")),
            }
    return out


def _group_market_outcomes(bookmaker: dict, key: str):
    for m in bookmaker.get("markets", []):
        if m.get("key") == key:
            yield m.get("outcomes", [])


def _pick_name(target: str, candidates: list[str], aliases: dict[str, str]) -> str | None:
    if target.lower() in {"draw", "tie"}:
        for c in candidates:
            if c.lower() in {"draw", "tie"}:
                return c
    scored = sorted(((team_similarity(target, c, aliases), c) for c in candidates), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.70 else None


def _book_prob_h2h(bookmaker, selection, aliases):
    for outcomes in _group_market_outcomes(bookmaker, "h2h"):
        names = [str(o.get("name", "")) for o in outcomes]
        picked = _pick_name(selection, names, aliases)
        if not picked:
            continue
        fair = devig_outcomes(outcomes)
        return fair.get(picked)
    return None


def _book_prob_total(bookmaker, point, market_keys=("totals", "alternate_totals")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            pair = [o for o in outcomes if abs(float(o.get("point", -999)) - point) < 1e-6]
            if len(pair) >= 2:
                fair = devig_outcomes(pair)
                for name, p in fair.items():
                    if name.lower() == "over":
                        return p
    return None


def _book_prob_spread(bookmaker, selection, point, aliases, market_keys=("spreads", "alternate_spreads")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            candidates = [o for o in outcomes if abs(float(o.get("point", -999)) - point) < 1e-6]
            if not candidates:
                continue
            # Need both sides of same spread for a proper devig. Opponent is usually opposite point.
            target = None
            for o in candidates:
                if team_similarity(selection, str(o.get("name", "")), aliases) >= 0.70:
                    target = o
                    break
            if not target:
                continue
            pair = []
            for o in outcomes:
                opoint = float(o.get("point", -999))
                if abs(abs(opoint) - abs(point)) < 1e-6:
                    pair.append(o)
            if len(pair) >= 2:
                fair = devig_outcomes(pair[:2])
                return fair.get(str(target.get("name")))
    return None


def _book_prob_team_total(bookmaker, selection, point, aliases, market_keys=("team_totals", "alternate_team_totals")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            candidates = []
            for o in outcomes:
                if abs(float(o.get("point", -999)) - point) > 1e-6:
                    continue
                desc = str(o.get("description") or o.get("team") or "")
                if desc and team_similarity(selection, desc, aliases) < 0.70:
                    continue
                candidates.append(o)
            if len(candidates) >= 2:
                fair = devig_outcomes(candidates[:2])
                for name, p in fair.items():
                    if name.lower() == "over":
                        return p
    return None


def fair_from_event(spec, event: dict, aliases: dict[str, str], min_books: int, max_dispersion: float):
    probs = []
    for book in event.get("bookmakers", []):
        try:
            if spec.market_type == "h2h" and spec.selection:
                p = _book_prob_h2h(book, spec.selection, aliases)
            elif spec.market_type == "total" and spec.point is not None:
                p = _book_prob_total(book, spec.point)
            elif spec.market_type == "spread" and spec.selection and spec.point is not None:
                p = _book_prob_spread(book, spec.selection, spec.point, aliases)
            elif spec.market_type == "team_total" and spec.selection and spec.point is not None:
                p = _book_prob_team_total(book, spec.selection, spec.point, aliases)
            else:
                p = None
            if p is not None:
                probs.append(float(p))
        except (ValueError, TypeError, ZeroDivisionError):
            continue
    return robust_consensus(probs, min_books=min_books, max_dispersion=max_dispersion)


def blend_fair_value(ticker: str, consensus, overlay: dict[str, dict]) -> FairValue | None:
    if consensus is None:
        ov = overlay.get(ticker)
        if ov:
            return FairValue(
                ov["model_prob"], ov["source"], 0.55, 0, 0.0, None,
                ov["model_prob"], "model-only fallback",
            )
        return None

    ov = overlay.get(ticker)
    if not ov:
        return FairValue(
            consensus.fair_prob, "sportsbook_no_vig", consensus.confidence,
            consensus.n_books, consensus.dispersion, consensus.fair_prob, None,
            f"{consensus.n_books} books",
        )

    w = max(0.0, min(0.80, float(ov.get("weight", 0.35))))
    fair = (1 - w) * consensus.fair_prob + w * ov["model_prob"]
    conf = min(1.0, 0.75 * consensus.confidence + 0.25 * 0.70)
    return FairValue(
        fair, f"sportsbook_no_vig+{ov['source']}", conf,
        consensus.n_books, consensus.dispersion, consensus.fair_prob,
        ov["model_prob"], f"blend weight model={w:.2f}",
    )
