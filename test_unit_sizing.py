from unit_sizing import contracts_for_unit, risk_for_unit


def test_dogs_risk_a_unit_favorites_win_a_unit():
    assert contracts_for_unit(0.25, 5) == 20          # $5 risked
    assert contracts_for_unit(0.40, 2) == 5           # $2 risked
    assert contracts_for_unit(0.50, 5) == 10          # even money: risk $5 to win $5
    assert contracts_for_unit(0.60, 5) == 12          # risk $7.20 to win $4.80
    assert contracts_for_unit(0.75, 2) == 8           # risk $6 to win $2
    assert contracts_for_unit(0.0, 5) == 0 and contracts_for_unit(0.5, 0) == 0
    assert risk_for_unit(0.30, 5) == 5.0 and risk_for_unit(0.60, 5) == 7.5


def test_heavy_favorites_capped_at_three_units():
    assert contracts_for_unit(0.75, 2) == 8                    # exactly 3 units, not trimmed
    assert contracts_for_unit(0.97, 5) == 15                   # $14.55, not $161
    assert risk_for_unit(0.97, 5) == 15.0
