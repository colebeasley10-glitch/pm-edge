"""On-demand price check for the app's Refresh button.

Re-marks open league bets (and settles any Kalshi has already resolved) using the
same rules as a league round, but in memory only. Nothing is written to the
ledger; the next scheduled round still records the official marks and results.
"""
from __future__ import annotations

import copy
from datetime import datetime

from league import book


def refresh(rows: list[dict], fetch_markets, *, now: datetime | None = None) -> dict:
    """Return {"rows": updated copy, "marked": n, "settled": n, "open": n, "checked_at": iso}.

    fetch_markets(tickers) -> {ticker: normalized_market}, e.g. LeagueKalshi().markets.
    """
    now = now or book.now_utc()
    work = copy.deepcopy(rows)
    n_open = sum(1 for r in work if r.get("status") == "open")
    result = book.update(work, fetch_markets, now=now)
    return {
        "rows": work,
        "marked": int(result.get("marked", 0)),
        "settled": int(result.get("settled", 0)),
        "open": n_open,
        "checked_at": book.iso(now),
    }
