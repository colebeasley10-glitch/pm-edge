from unit_sizing import contracts_for_unit, risk_for_unit


def test_dogs_risk_a_unit_favorites_win_a_unit():
    assert contracts_for_unit(0.25, 5) == 20          # $5 risked
    assert contracts_for_unit(0.40, 2) == 5           # $2 risked
    assert contracts_for_unit(0.50, 5) == 10          # even money: risk $5 to win $5
    assert contracts_for_unit(0.60, 5) == 12          # risk $7.20 to win $4.80
    assert contracts_for_unit(0.75, 2) == 8           # risk $6 to win $2
    assert contracts_for_unit(0.0, 5) == 0 and contracts_for_unit(0.5, 0) == 0
    assert risk_for_unit(0.30, 5) == 5.0 and risk_for_unit(0.60, 5) == 7.5


def test_heavy_favorites_skipped():
    assert contracts_for_unit(0.75, 2) == 8                    # -300 still bets, wins a full unit
    assert contracts_for_unit(0.76, 2) == 0                    # over 75c: no bet
    assert contracts_for_unit(0.90, 1) == 0                    # the Emerson Jones case
    assert contracts_for_unit(0.97, 5) == 0
    assert risk_for_unit(0.90, 5) == 0.0
