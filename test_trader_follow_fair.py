"""Trader Follow fair price: Polymarket moneylines/spreads matched to the Odds Fam game board."""
from trader_follow.fair import ev, fair_for

START = "2026-11-01T18:00:00Z"
G = "Cleveland Browns at Pittsburgh Steelers"
ROWS = [
    {"kind": "game", "game": G, "starts": START, "market": "moneyline", "pick": "Cleveland Browns ML", "fair_prob": 0.345},
    {"kind": "game", "game": G, "starts": START, "market": "moneyline", "pick": "Pittsburgh Steelers ML", "fair_prob": 0.655},
    {"kind": "game", "game": G, "starts": START, "market": "spread", "pick": "Pittsburgh S −3.5", "fair_prob": 0.505},
    {"kind": "game", "game": G, "starts": START, "market": "spread", "pick": "Cleveland Browns +3.5", "fair_prob": 0.495},
    {"kind": "game", "game": G, "starts": START, "market": "spread", "pick": "Cleveland Browns +6.5", "fair_prob": 0.64},
]


def sig(question, outcome):
    return {"question": question, "outcome": outcome, "game_start": START}


def test_moneyline_both_sides():
    assert fair_for(sig("Browns vs. Steelers", "Browns"), ROWS) == 0.345
    assert fair_for(sig("Browns vs. Steelers", "Steelers"), ROWS) == 0.655


def test_spread_favorite_and_dog():
    assert fair_for(sig("Spread: Steelers (-3.5)", "Browns"), ROWS) == 0.495
    assert fair_for(sig("Spread: Steelers (-3.5)", "Steelers"), ROWS) == 0.505   # truncated Kalshi label


def test_no_match_when_line_missing_or_total():
    assert fair_for(sig("Spread: Steelers (-2.5)", "Browns"), ROWS) is None
    assert fair_for(sig("Browns vs. Steelers: O/U 41.5", "Over"), ROWS) is None
    assert fair_for({**sig("Browns vs. Steelers", "Browns"), "game_start": "2026-11-02T18:00:00Z"}, ROWS) is None


def test_ev():
    assert round(ev(0.495, 0.50), 3) == -0.01 and ev(None, 0.5) is None
