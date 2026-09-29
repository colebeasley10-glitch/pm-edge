from __future__ import annotations
from dataclasses import dataclass
import logging
from pathlib import Path
import pandas as pd

from .devig import active_devig_method, devig_all, robust_consensus
from .matcher import team_similarity

logger = logging.getLogger(__name__)


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


def _target_bundle(outcomes: list[dict], target_name: str) -> dict[str, float | None]:
    all_methods = devig_all(outcomes)
    return {method: probs.get(target_name) for method, probs in all_methods.items()}


def _book_prob_h2h(bookmaker, selection, aliases):
    for outcomes in _group_market_outcomes(bookmaker, "h2h"):
        names = [str(o.get("name", "")) for o in outcomes]
        picked = _pick_name(selection, names, aliases)
        if picked:
            return _target_bundle(outcomes, picked)
    return None


def _book_prob_total(bookmaker, point, market_keys=("totals", "alternate_totals")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            pair = [o for o in outcomes if abs(float(o.get("point", -999)) - point) < 1e-6]
            if len(pair) >= 2:
                over = next((str(o.get("name", "")) for o in pair if str(o.get("name", "")).lower() == "over"), None)
                if over:
                    return _target_bundle(pair, over)
    return None


def _book_prob_spread(bookmaker, selection, point, aliases, market_keys=("spreads", "alternate_spreads")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            candidates = [o for o in outcomes if abs(float(o.get("point", -999)) - point) < 1e-6]
            if not candidates:
                continue
            target = next((o for o in candidates if team_similarity(selection, str(o.get("name", "")), aliases) >= 0.70), None)
            if not target:
                continue
            pair = [o for o in outcomes if abs(abs(float(o.get("point", -999))) - abs(point)) < 1e-6]
            if len(pair) >= 2:
                return _target_bundle(pair[:2], str(target.get("name")))
    return None


def _book_prob_team_total(bookmaker, selection, point, aliases, market_keys=("team_totals", "alternate_team_totals")):
    for key in market_keys:
        for outcomes in _group_market_outcomes(bookmaker, key):
            point_outcomes = [o for o in outcomes if abs(float(o.get("point", -999)) - point) <= 1e-6]
            if not point_outcomes:
                continue

            candidates = []
            for o in point_outcomes:
                desc = str(o.get("description") or o.get("team") or "").strip()
                if not desc:
                    logger.info("team_total_reject reason=missing_team_description selection=%r point=%s", selection, point)
                    return None
                if team_similarity(selection, desc, aliases) < 0.70:
                    continue
                candidates.append(o)

            names = {str(o.get("name", "")).strip().lower() for o in candidates}
            if "over" not in names or "under" not in names:
                logger.info("team_total_reject reason=missing_over_under_pair selection=%r point=%s names=%r", selection, point, sorted(names))
                return None

            over = next(o for o in candidates if str(o.get("name", "")).strip().lower() == "over")
            under = next(o for o in candidates if str(o.get("name", "")).strip().lower() == "under")
            return _target_bundle([over, under], str(over.get("name")))
    return None


def _book_failure_reason(spec, bookmaker, aliases) -> str:
    """Return a stable reason code when an exact sportsbook comparison fails."""
    if spec.market_type == "h2h":
        groups = list(_group_market_outcomes(bookmaker, "h2h"))
        if not groups:
            return "market_missing"
        names = [str(o.get("name", "")) for outcomes in groups for o in outcomes]
        return "selection_mismatch" if spec.selection and not _pick_name(spec.selection, names, aliases) else "market_unmatched"

    key_map = {
        "total": ("totals", "alternate_totals"),
        "spread": ("spreads", "alternate_spreads"),
        "team_total": ("team_totals", "alternate_team_totals"),
    }
    keys = key_map.get(spec.market_type, ())
    groups = [outcomes for key in keys for outcomes in _group_market_outcomes(bookmaker, key)]
    if not groups:
        return "market_missing"
    if spec.point is None:
        return "missing_line"

    exact = []
    for outcomes in groups:
        for outcome in outcomes:
            try:
                point = float(outcome.get("point"))
            except (TypeError, ValueError):
                continue
            if abs(point - float(spec.point)) <= 1e-6:
                exact.append(outcome)
    if not exact:
        return "line_mismatch"

    if spec.market_type in {"spread", "team_total"} and spec.selection:
        if spec.market_type == "spread":
            if not any(team_similarity(spec.selection, str(o.get("name", "")), aliases) >= 0.70 for o in exact):
                return "selection_mismatch"
        else:
            if not any(
                team_similarity(
                    spec.selection,
                    str(o.get("description") or o.get("team") or ""),
                    aliases,
                ) >= 0.70
                for o in exact
            ):
                return "selection_mismatch"
    return "market_unmatched"


def fair_from_event(
    spec,
    event: dict,
    aliases: dict[str, str],
    min_books: int,
    max_dispersion: float,
    *,
    return_diagnostics: bool = False,
):
    method_probs = {"proportional": [], "power": [], "shin": []}
    method = active_devig_method()
    book_inputs = []
    bookmakers = list(event.get("bookmakers", []) or [])
    books_available = len(bookmakers)
    books_matched = 0
    book_drop_reasons: list[str] = []

    for index, book in enumerate(bookmakers):
        book_name = str(book.get("title") or book.get("key") or f"book_{index + 1}")
        try:
            if spec.market_type == "h2h" and spec.selection:
                bundle = _book_prob_h2h(book, spec.selection, aliases)
            elif spec.market_type == "total" and spec.point is not None:
                bundle = _book_prob_total(book, spec.point)
            elif spec.market_type == "spread" and spec.selection and spec.point is not None:
                bundle = _book_prob_spread(book, spec.selection, spec.point, aliases)
            elif spec.market_type == "team_total" and spec.selection and spec.point is not None:
                bundle = _book_prob_team_total(book, spec.selection, spec.point, aliases)
            else:
                bundle = None

            if bundle:
                books_matched += 1
                for devig_name in method_probs:
                    p = bundle.get(devig_name)
                    if p is not None:
                        method_probs[devig_name].append(float(p))
                active_p = bundle.get(method)
                if active_p is not None:
                    book_inputs.append({
                        "book_key": str(book.get("key") or ""),
                        "book_title": str(book.get("title") or ""),
                        "source_ts": book.get("last_update"),
                        "probability": float(active_p),
                        "probability_proportional": bundle.get("proportional"),
                        "probability_power": bundle.get("power"),
                        "probability_shin": bundle.get("shin"),
                    })
            else:
                book_drop_reasons.append(f"{book_name}:{_book_failure_reason(spec, book, aliases)}")
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            book_drop_reasons.append(f"{book_name}:{type(exc).__name__}")
            continue

    consensuses = {
        method: robust_consensus(probs, min_books=min_books, max_dispersion=max_dispersion)
        for method, probs in method_probs.items()
    }
    consensus = consensuses.get(method)
    diagnostics = {
        "devig_method": method,
        "fair_proportional": consensuses["proportional"].fair_prob if consensuses["proportional"] else None,
        "fair_power": consensuses["power"].fair_prob if consensuses["power"] else None,
        "fair_shin": consensuses["shin"].fair_prob if consensuses["shin"] else None,
        "books_available": books_available,
        "books_matched": books_matched,
        "book_drop_reasons": book_drop_reasons,
        "book_inputs": book_inputs,
    }
    if return_diagnostics:
        return consensus, diagnostics
    return consensus


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
