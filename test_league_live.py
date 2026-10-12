from datetime import datetime, timezone

from league import live


def _row(**kw):
    base = {"trade_id": "t1", "strategy": "weather", "ticker": "T1", "side": "YES", "entry_price": "0.40",
            "contracts": "25", "cost": "10.0", "fee": "0.42", "close_time": "2099-01-01T00:00:00Z",
            "status": "open", "last_mid": "0.40", "last_mid_at": ""}
    base.update(kw)
    return base


NOW = datetime(2026, 10, 12, 17, 30, tzinfo=timezone.utc)


def test_refresh_marks_open_bet_without_touching_input():
    rows = [_row()]
    out = live.refresh(rows, lambda t: {"T1": {"yes_bid": 0.5, "yes_ask": 0.54, "status": "active"}}, now=NOW)
    assert out["marked"] == 1 and out["open"] == 1
    assert abs(float(out["rows"][0]["last_mid"]) - 0.52) < 1e-9
    assert rows[0]["last_mid"] == "0.40"  # caller's rows untouched


def test_refresh_settles_resolved_market():
    out = live.refresh([_row()], lambda t: {"T1": {"result": "yes", "status": "finalized"}}, now=NOW)
    r = out["rows"][0]
    assert out["settled"] == 1 and r["status"] == "won" and r["pl"] == round(25 - 10.0 - 0.42, 2)


def test_refresh_handles_empty_fetch():
    out = live.refresh([_row()], lambda t: {}, now=NOW)
    assert out["marked"] == 0 and out["settled"] == 0 and out["open"] == 1
