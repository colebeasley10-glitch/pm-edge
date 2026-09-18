"""PM Edge predictive-selection layer.

This module deliberately separates *bet selection* from *execution price*.
It never submits orders.  It converts an independent model probability plus
sportsbook consensus into a conservative blended probability and requires the
model and market to agree before PM Edge can treat the idea as model-backed.

The first production target is NCAA football.  Rich feature ingestion (EPA,
pace, explosiveness, injuries, weather, etc.) can populate model_prob later;
until those inputs exist the module refuses to invent a model probability.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ModelSignal:
    side: str
    model_prob: float
    market_prob: float
    blended_prob: float
    model_edge_vs_market: float
    agreement: bool
    confidence: str
    reasons: tuple[str, ...]


def _p(x):
    try:
        x=float(x)
    except (TypeError,ValueError):
        return None
    return x if math.isfinite(x) and 0 < x < 1 else None


def build_signal(*, model_yes_prob, sportsbook_yes_prob, min_model_edge=0.025,
                 model_weight=0.55, market_weight=0.45) -> ModelSignal | None:
    """Return a model-backed side only when independent model and market agree.

    sportsbook_yes_prob must already be a no-vig consensus.  We intentionally
    blend rather than letting either source dominate.  A signal requires the
    independent model to differ from sportsbook consensus by >= min_model_edge
    in the same direction as the selected side.
    """
    mp=_p(model_yes_prob); sp=_p(sportsbook_yes_prob)
    if mp is None or sp is None:
        return None
    w=max(0.0,min(1.0,float(model_weight)))
    blend=w*mp+(1-w)*sp
    delta=mp-sp
    if abs(delta) < float(min_model_edge):
        return None
    side="YES" if delta > 0 else "NO"
    side_model=mp if side=="YES" else 1-mp
    side_market=sp if side=="YES" else 1-sp
    side_blend=blend if side=="YES" else 1-blend
    confidence="strong" if abs(delta)>=0.06 else ("medium" if abs(delta)>=0.04 else "qualified")
    return ModelSignal(
        side=side, model_prob=side_model, market_prob=side_market,
        blended_prob=side_blend, model_edge_vs_market=abs(delta),
        agreement=True, confidence=confidence,
        reasons=(f"Independent model: {side_model:.1%}",
                 f"Sportsbook no-vig consensus: {side_market:.1%}",
                 f"Model-vs-market gap: {abs(delta):.1%}"),
    )


def enrich_fair_entry(fair_entry: dict, *, model_yes_prob) -> dict:
    """Attach a model signal without silently replacing sportsbook fair value."""
    out=dict(fair_entry)
    sig=build_signal(model_yes_prob=model_yes_prob,
                     sportsbook_yes_prob=fair_entry.get("fair_yes_prob"))
    if sig is None:
        out["model_signal"]=None
        return out
    out["model_signal"]={
        "side":sig.side, "model_prob":sig.model_prob,
        "market_prob":sig.market_prob, "blended_prob":sig.blended_prob,
        "model_edge_vs_market":sig.model_edge_vs_market,
        "confidence":sig.confidence, "reasons":list(sig.reasons),
    }
    return out


def self_test():
    s=build_signal(model_yes_prob=.60,sportsbook_yes_prob=.54)
    assert s and s.side=="YES" and s.confidence=="strong"
    n=build_signal(model_yes_prob=.43,sportsbook_yes_prob=.49)
    assert n and n.side=="NO"
    assert build_signal(model_yes_prob=.51,sportsbook_yes_prob=.50) is None
    assert build_signal(model_yes_prob=None,sportsbook_yes_prob=.50) is None
    print("PM Edge predictive model layer self-test: PASS")


if __name__=="__main__":
    self_test()
