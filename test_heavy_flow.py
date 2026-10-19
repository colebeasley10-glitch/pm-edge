"""Heavy-flow paper test: forward-only split of Sharp picks by whale-trade count."""
from datetime import timedelta

from research_lab import heavy_flow as hf


def _row(i, trades, pl, when, status=None):
    return {"strategy": "sharp", "opened_at": when.isoformat().replace("+00:00", "Z"),
            "reason": f"$5,000 bet YES at 40¢ ({trades} trades)", "cost": "2.0", "fee": "0.04",
            "pl": str(pl), "status": status or ("won" if pl > 0 else "lost")}


def test_only_counts_bets_after_start():
    before = hf.START - timedelta(hours=1)
    after = hf.START + timedelta(hours=1)
    rows = [_row(1, 200, 3.0, before), _row(2, 200, -2.04, after), _row(3, 10, 3.0, after)]
    c = hf.compare(rows)
    assert c["heavy"]["n"] == 1 and c["heavy"]["pl"] == -2.04
    assert c["light"]["n"] == 1 and c["all"]["n"] == 2


def test_verdict_waits_then_decides():
    after = hf.START + timedelta(hours=2)
    few = [_row(i, 100, 1.0, after) for i in range(20)]
    assert hf.verdict(hf.compare(few))[1] == "wait"
    good = ([_row(i, 100, 1.5 if i % 3 else -2.04, after) for i in range(160)]
            + [_row(1000 + i, 10, -2.04 if i % 2 else 1.0, after) for i in range(160)])
    assert hf.verdict(hf.compare(good))[1] == "good"
    bad = ([_row(i, 100, -2.04 if i % 2 else 1.0, after) for i in range(160)]
           + [_row(1000 + i, 10, 1.5, after) for i in range(160)])
    assert hf.verdict(hf.compare(bad))[1] == "bad"


def test_open_bets_counted_separately():
    after = hf.START + timedelta(hours=1)
    c = hf.compare([_row(1, 90, 0, after, status="open")])
    assert c["heavy"]["open"] == 1 and c["heavy"]["n"] == 0
