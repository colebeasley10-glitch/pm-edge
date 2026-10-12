import os
import tempfile
from pathlib import Path

# keep the real ledger safe no matter which test file imports league first
_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("LEAGUE_LEDGER_PATH", str(_tmp / "league.csv"))
os.environ.setdefault("LEAGUE_STATUS_PATH", str(_tmp / "status.json"))
os.environ.setdefault("LEAGUE_PUSH", "0")

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


def test_followup_records_tap_delay_price_and_marks():
    from datetime import timedelta

    from league import followup

    rows = [_row(opened_at="2026-10-12T17:25:00Z"),
            _row(trade_id="t2", ticker="T2", opened_at="2026-10-12T15:00:00Z")]  # old: no 5m check
    mkts = {"T1": {"yes_bid": 0.44, "yes_ask": 0.46, "status": "active"},
            "T2": {"result": "no", "status": "finalized"}}
    out = followup.followup(rows, lambda t: {k: mkts[k] for k in t if k in mkts}, now=NOW)
    assert out["checked_5m"] == 1 and out["settled"] == 1
    assert rows[0]["ask_5m"] == 0.46 and rows[0]["mid_5m"] == 0.45 and rows[0]["last_mid"] == 0.45
    assert rows[1].get("ask_5m") in (None, "") and rows[1]["status"] == "lost"
    # second pass doesn't overwrite the 5-minute price
    followup.followup(rows, lambda t: {"T1": {"yes_bid": 0.6, "yes_ask": 0.62, "status": "active"}},
                      now=NOW + timedelta(minutes=5))
    assert rows[0]["ask_5m"] == 0.46


def test_followup_skips_market_that_already_finished():
    from league import followup

    rows = [_row(opened_at="2026-10-12T17:25:00Z")]
    followup.followup(rows, lambda t: {"T1": {"result": "yes", "status": "finalized"}}, now=NOW)
    assert rows[0].get("ask_5m") in (None, "") and rows[0]["checked_5m_at"] and rows[0]["status"] == "won"
