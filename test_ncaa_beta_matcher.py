"""NCAA beta must only price full-game markets, with the correct spread side."""
from types import SimpleNamespace

from ncaa_beta_runner import full_game_side

H, A = "Alabama", "Mississippi St."


def spec(kind, point, selection=""):
    return SimpleNamespace(market_type=kind, point=point, selection=selection)


def sig_spread(team, home_spread):
    return {"kind": "SPREAD", "team": team, "line": home_spread}


def main():
    # Home favored by 5.5; model likes Alabama -5.5 -> Alabama contract YES.
    assert full_game_side(sig_spread(H, -5.5), spec("spread", -5.5, H),
                          "KXNCAAFSPREAD-26OCT03ALAMSST-ALA6", H, A, -5.5) == "YES"
    # Model likes Mississippi St. +5.5 -> Alabama contract NO.
    assert full_game_side(sig_spread(A, -5.5), spec("spread", -5.5, H),
                          "KXNCAAFSPREAD-26OCT03ALAMSST-ALA6", H, A, -5.5) == "NO"
    # Home is a 3.5-pt underdog; model likes home +3.5. A "home wins by over
    # 3.5" contract is a different bet and must NOT match (old bug).
    assert full_game_side(sig_spread(H, 3.5), spec("spread", -3.5, H),
                          "KXNCAAFSPREAD-X-ALA4", H, A, 3.5) is None
    # ...the right contract is "away wins by over 3.5", NO.
    assert full_game_side(sig_spread(H, 3.5), spec("spread", -3.5, A),
                          "KXNCAAFSPREAD-X-MSST4", H, A, 3.5) == "NO"
    # Half / quarter markets are never priced with full-game numbers.
    for t in ("KXNCAAF1HSPREAD-X-ALA3", "KXNCAAF2HSPREAD-X-ALA3", "KXNCAAF4QSPREAD-X-ALA3"):
        assert full_game_side(sig_spread(H, -5.5), spec("spread", -5.5, H), t, H, A, -5.5) is None
    # Totals: full game only.
    tot = {"kind": "TOTAL", "line": 46.5, "direction": "UNDER"}
    assert full_game_side(tot, spec("total", 46.5), "KXNCAAFTOTAL-X-46", H, A, -5.5) == "NO"
    assert full_game_side(tot, spec("total", 46.5), "KXNCAAF1HTOTAL-X-23", H, A, -5.5) is None
    # Missing selection never guesses.
    assert full_game_side(sig_spread(H, -5.5), spec("spread", -5.5, ""),
                          "KXNCAAFSPREAD-X-ALA6", H, A, -5.5) is None
    print("NCAA beta matcher tests: PASS")


if __name__ == "__main__":
    main()
