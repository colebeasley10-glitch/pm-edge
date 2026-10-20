"""Nightly push: when it's due and what it says."""
from datetime import datetime, timezone

from league import nightly


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_target_day():
    # 11:14pm ET Oct 13 = 03:14Z Oct 14
    assert nightly.target_day(utc(2026, 10, 14, 3, 14), None) == "20261013"
    assert nightly.target_day(utc(2026, 10, 14, 3, 44), "20261013") is None   # already sent
    # GitHub skipped the 11pm runs: 1am ET still sends for the day before
    assert nightly.target_day(utc(2026, 10, 14, 5, 0), None) == "20261013"
    assert nightly.target_day(utc(2026, 10, 14, 16, 0), None) is None         # noon: not due


def test_build_summary():
    live = [
        {"day": "20261013", "status": "won", "cost": "0.50", "fee": "0.02", "pl": "0.48", "settled_at": "2026-10-13T20:00:00Z"},
        {"day": "20261013", "status": "lost", "cost": "0.60", "fee": "0.02", "pl": "-0.62", "settled_at": "2026-10-13T21:00:00Z"},
        {"day": "20261013", "status": "no_fill", "cost": "0", "fee": "0"},
        {"day": "20261013", "status": "error", "note": "{'code': 'Trading_is_not_currently_allowed_in_Washington_on_markets_in_Sports'}"},
        {"day": "20261013", "status": "open", "cost": "0.81", "fee": "0.01"},
    ]
    paper = [
        {"strategy": "sharp", "status": "won", "pl": "5.00", "opened_at": "2026-10-13T15:00:00Z", "settled_at": "2026-10-13T22:00:00Z"},
        {"strategy": "sharp", "status": "lost", "pl": "-9.90", "opened_at": "2026-10-13T15:00:00Z", "settled_at": "2026-10-13T23:00:00Z"},
        {"strategy": "weather", "status": "open", "pl": "", "opened_at": "2026-10-13T16:00:00Z"},
    ]
    title, lines = nightly.build("20261013", paper, live, {"fair_cache": {}, "daily_usage": {"credits": 0}})
    text = "\n".join(lines)
    assert "Live $5 −$0.14 (1-1)" in title and "Paper Sharp −$4.90 (1-1)" in title
    assert "Orders 5: 3 filled, 1 missed (price moved), 1 blocked by Kalshi" in text
    assert "Spent $1.96 of $300" in text
    assert "Opened 3: Sharp 2, Weather 1" in text and "Sharp 1-1, −$4.90" in text
    assert "Odds API is out of credits" in text
    _, lines2 = nightly.build("20261013", paper, live, {"fair_cache": {"X": {}}, "daily_usage": {"credits": 3}})
    assert not any("Odds API" in ln for ln in lines2)


def test_maybe_send_marks_day():
    sent = []
    day = nightly.maybe_send(utc(2026, 10, 14, 3, 14), None, [], [], lambda t, ls: sent.append(t))
    assert day == "20261013" and len(sent) == 1
    assert nightly.maybe_send(utc(2026, 10, 14, 3, 44), day, [], [], lambda t, ls: sent.append(t)) is None
