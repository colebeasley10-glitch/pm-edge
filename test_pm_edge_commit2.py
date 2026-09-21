from types import SimpleNamespace
import os

from pmedge.devig import devig_all, devig_outcomes
from pmedge.fair_value import fair_from_event
from pmedge.pricing import fee_per_contract
from pmedge.utils import normalize_text


def _book(key, outcomes):
    return {"markets": [{"key": key, "outcomes": outcomes}]}


def main():
    outcomes = [
        {"name": "A", "price": 1.80},
        {"name": "B", "price": 2.10},
    ]
    methods = devig_all(outcomes)
    assert set(methods) == {"proportional", "power", "shin"}
    for probs in methods.values():
        assert abs(sum(probs.values()) - 1.0) < 1e-10
        assert all(0.0 < p < 1.0 for p in probs.values())

    old = os.environ.pop("PM_EDGE_DEVIG_METHOD", None)
    try:
        assert devig_outcomes(outcomes) == methods["power"]
    finally:
        if old is not None:
            os.environ["PM_EDGE_DEVIG_METHOD"] = old

    assert "no saints" in normalize_text("NO Saints")
    assert "number 5" in normalize_text("No. 5 Texas")

    missing_desc = {
        "bookmakers": [
            _book("team_totals", [
                {"name": "Over", "price": 1.9, "point": 20.5},
                {"name": "Under", "price": 1.9, "point": 20.5},
            ])
            for _ in range(3)
        ]
    }
    spec = SimpleNamespace(market_type="team_total", selection="Alpha", point=20.5)
    assert fair_from_event(spec, missing_desc, {}, 3, 0.10) is None

    valid = {
        "bookmakers": [
            _book("team_totals", [
                {"name": "Over", "price": 1.9, "point": 20.5, "description": "Alpha"},
                {"name": "Under", "price": 1.9, "point": 20.5, "description": "Alpha"},
            ])
            for _ in range(3)
        ]
    }
    consensus, diag = fair_from_event(
        spec, valid, {}, 3, 0.10, return_diagnostics=True
    )
    assert consensus is not None
    assert diag["devig_method"] in {"proportional", "power", "shin"}
    assert all(diag[k] is not None for k in ("fair_proportional", "fair_power", "fair_shin"))

    assert fee_per_contract(0.50, 0.07, 10) == 0.018
    assert fee_per_contract(0.50, 0.07, 1) == 0.02

    print("commit2 engine-fix tests passed")


if __name__ == "__main__":
    main()
