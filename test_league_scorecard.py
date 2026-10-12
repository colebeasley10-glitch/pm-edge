import pandas as pd

from league import scorecard


def _row(i, status, pl, ev="KXATPCHALLENGERMATCH-26OCT12AB", clv=None, day="2026-10-12"):
    return {"trade_id": str(i), "strategy": "sharp", "opened_at": f"{day}T16:00:00Z", "event_ticker": ev,
            "category": "Sports", "status": status, "pl": pl, "cost": 9.5, "fee": 0.3, "clv_cents": clv,
            "settled_at": f"{day}T18:00:00Z" if status in ("won", "lost") else None}


def test_scorecard_totals_groups_and_not_ready():
    df = pd.DataFrame([_row(1, "won", 10.0, clv=5), _row(2, "lost", -9.8, clv=-3),
                       _row(3, "won", 6.0, ev="KXNFLGAME-26OCT12BUFLAR"), _row(4, "open", None),
                       {**_row(5, "won", 99.0), "strategy": "weather"}])
    sc = scorecard.scorecard(df, "sharp", now=pd.Timestamp("2026-10-12T21:00:00Z"))
    assert (sc["won"], sc["lost"], sc["open"]) == (2, 1, 1)
    assert round(sc["pl"], 2) == 6.2 and round(sc["staked"], 2) == 29.4
    assert sc["clv_n"] == 2 and sc["clv"] == 1.0
    assert [g["group"] for g in sc["groups"]] == ["Tennis", "NFL"]
    assert sc["days"] == 1 and len(sc["daily"]) == 1 and not sc["ready"]


def test_ready_when_all_checks_pass():
    rows = [_row(i, "won", 1.0, clv=2, day=f"2026-09-{1 + i % 28:02d}") for i in range(160)]
    sc = scorecard.scorecard(pd.DataFrame(rows), "sharp", now=pd.Timestamp("2026-10-12T21:00:00Z"))
    assert sc["ready"]


def test_empty_and_groups():
    assert scorecard.scorecard(pd.DataFrame(), "sharp") == {}
    assert scorecard.market_group("KXSERIEA1HTOTAL-26OCT12ATAVEN") == "Soccer"
    assert scorecard.market_group("KXHIGHNY-26OCT12", "Weather") == "Weather"
