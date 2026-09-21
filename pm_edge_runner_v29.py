"""PM Edge v29: audit-correctness release.

Implements only changes justified by verified code defects/modeling mechanics from
the independent audit. It deliberately does NOT tune thresholds from the tiny
realized-ROI sample.

Changes:
* Power-method devig replaces proportional normalization for sportsbook pairs.
* Team-total matching requires an identified team and a same-team O/U pair.
* Text normalization no longer rewrites standalone "NO" to "north".
* Approval-path pricing is conservatively evaluated with taker fees for both
  MAKE and TAKE until execution telemetry proves maker treatment.
* Primary/Standard thresholds are not loosened by this release.
"""
from __future__ import annotations

import math
import re

# Import watcher first: it materializes the embedded pmedge runtime and adds
# it to sys.path. Only then can the quantitative engine modules be imported.
import watcher
import pmedge.devig as devig
import pmedge.fair_value as fair_value
import pmedge.matcher as matcher
import pmedge.utils as utils
import pm_edge_runner_v28 as v28


def _power_devig_outcomes(outcomes: list[dict]) -> dict[str, float]:
    """Power devig: solve sum(q_i ** k) = 1, then p_i = q_i ** k.

    This removes the known longshot bias of proportional normalization while
    remaining deterministic and dependency-free.
    """
    names, implied = [], []
    for o in outcomes:
        price = float(o["price"])
        q = devig.decimal_to_implied(price)
        if not (0.0 < q < 1.0):
            raise ValueError("invalid implied probability")
        names.append(str(o["name"]))
        implied.append(q)
    if len(implied) < 2:
        raise ValueError("devig requires at least two outcomes")

    # Typical overround => k > 1. Use a wide bracket and bisection.
    lo, hi = 0.05, 20.0
    def total(k: float) -> float:
        return sum(q ** k for q in implied)

    # If malformed/underround input cannot be bracketed, fail closed to the
    # old normalized result rather than inventing a probability.
    if not (total(lo) > 1.0 and total(hi) < 1.0):
        fair = devig.normalize_probs(implied)
        return dict(zip(names, fair))

    for _ in range(80):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    probs = [q ** k for q in implied]
    # Numerical cleanup only.
    s = sum(probs)
    probs = [p / s for p in probs]
    return dict(zip(names, probs))


def _normalize_text_v29(s: str) -> str:
    """Same normalization as the embedded engine without the unsafe NO=>north rule."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\bst\.?\b", "state", s)
    s = re.sub(r"\buniversity\b", "", s)
    s = re.sub(r"\bcollege\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _book_prob_team_total_v29(
    bookmaker, selection, point, aliases,
    market_keys=("team_totals", "alternate_team_totals"),
):
    """Require the O/U pair to be explicitly tied to the requested team."""
    for key in market_keys:
        for outcomes in fair_value._group_market_outcomes(bookmaker, key):
            candidates = []
            for o in outcomes:
                try:
                    if abs(float(o.get("point", -999)) - point) > 1e-6:
                        continue
                except (TypeError, ValueError):
                    continue
                desc = str(o.get("description") or o.get("team") or "").strip()
                # Empty description used to bypass the team filter. Fail closed.
                if not desc or matcher.team_similarity(selection, desc, aliases) < 0.70:
                    continue
                name = str(o.get("name") or "").lower()
                if name not in {"over", "under"}:
                    continue
                candidates.append(o)

            overs = [o for o in candidates if str(o.get("name") or "").lower() == "over"]
            unders = [o for o in candidates if str(o.get("name") or "").lower() == "under"]
            if not overs or not unders:
                continue
            pair = [overs[0], unders[0]]
            fair = _power_devig_outcomes(pair)
            return fair.get(str(overs[0].get("name")))
    return None


def install_v29_patch() -> None:
    watcher.WATCHER_VERSION = 29

    # Correct the quantitative engine references already imported by watcher.
    devig.devig_outcomes = _power_devig_outcomes
    fair_value.devig_outcomes = _power_devig_outcomes
    fair_value._book_prob_team_total = _book_prob_team_total_v29

    utils.normalize_text = _normalize_text_v29
    matcher.normalize_text = _normalize_text_v29

    # Approval links ultimately submit on a separate host. Until we persist
    # maker/taker execution telemetry end-to-end, score conservatively at the
    # taker schedule even when the recommendation is a resting MAKE.
    original_best = watcher.best_binary_trade
    def conservative_best_binary_trade(*args, **kwargs):
        taker = float(kwargs.get("taker_fee_rate", 0.07) or 0.07)
        kwargs["maker_fee_rate"] = max(taker, float(kwargs.get("maker_fee_rate", 0.0) or 0.0))
        return original_best(*args, **kwargs)
    watcher.best_binary_trade = conservative_best_binary_trade

    # v28 stamps 28 into state. Normalize once to 29 so state is not purged
    # every scheduled run after this launcher becomes production.
    original_load_state = watcher.load_state
    def load_state_v29():
        state = original_load_state()
        state["data_semantics_version"] = 29
        state["v29_audit_correctness_version"] = 1
        return state
    watcher.load_state = load_state_v29


def self_test() -> None:
    # Favorite/longshot pair: power devig must assign less probability to the
    # longshot than proportional normalization.
    outcomes = [{"name": "Fav", "price": 1.50}, {"name": "Dog", "price": 3.00}]
    prop = devig.normalize_probs([1/1.50, 1/3.00])
    power = _power_devig_outcomes(outcomes)
    assert power["Dog"] < prop[1]

    assert _normalize_text_v29("NO Saints") == "no saints"
    assert _normalize_text_v29("St. John's University") == "state john s"

    fake = {
        "markets": [{
            "key": "team_totals",
            "outcomes": [
                {"name": "Over", "point": 24.5, "price": 1.91, "description": ""},
                {"name": "Under", "point": 24.5, "price": 1.91, "description": ""},
            ],
        }]
    }
    assert _book_prob_team_total_v29(fake, "Buffalo", 24.5, {}) is None
    print("PM Edge v29 audit-correctness self-test: PASS")


def main() -> int:
    original_install_v28 = v28.install_v28_patch

    def install_v28_then_v29():
        original_install_v28()
        install_v29_patch()

    v28.install_v28_patch = install_v28_then_v29
    try:
        return int(v28.main() or 0)
    finally:
        v28.install_v28_patch = original_install_v28


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
