"""Five-minute follow-up after a league round.

Paper bets fill at the ask the instant a round runs, but a real bet happens
after the push arrives and you tap. This re-checks every bet opened in the last
round a few minutes later and records:

  ask_5m  the price you'd have paid if you tapped then (tap-delay slippage)
  mid_5m  where the market sat (did the price drift toward or away from us)

It also re-marks and settles open bets, so short in-play markets that resolve
between rounds still get a closing mark for CLV. Paper only, read-only Kalshi.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from league import book, run
from league.kalshi import LeagueKalshi

DELAY_SECS = int(os.getenv("LEAGUE_FOLLOWUP_DELAY_SECS", "300"))
WINDOW = timedelta(minutes=20)


def followup(rows: list[dict], fetch_markets, *, now: datetime | None = None) -> dict:
    now = now or book.now_utc()
    fresh = [r for r in rows
             if not r.get("checked_5m_at")
             and (opened := book.parse_dt(r.get("opened_at"))) is not None
             and timedelta(0) <= now - opened <= WINDOW]
    # recorded before update() can settle anything, so a bet that already
    # resolved still gets its 5-minute price if the market is quoting
    markets = fetch_markets(sorted({r["ticker"] for r in fresh})) if fresh else {}
    checked = 0
    for r in fresh:
        m = markets.get(r["ticker"])
        if not m:
            continue
        status = str(m.get("status") or "").lower()
        if str(m.get("result") or "").lower() or status not in {"open", "active", ""}:
            r["checked_5m_at"] = book.iso(now)  # finished before a tap could land
            continue
        _, ask = book.side_quotes(m, r["side"])
        mid = book.side_mid(m, r["side"])
        r["ask_5m"] = "" if ask is None else round(ask, 4)
        r["mid_5m"] = "" if mid is None else round(mid, 4)
        r["checked_5m_at"] = book.iso(now)
        checked += 1
    upd = book.update(rows, fetch_markets, now=now)
    return {"checked_5m": checked, **upd}


def main() -> None:
    if DELAY_SECS > 0:
        time.sleep(DELAY_SECS)
    rows = book.load()
    was_open = {r.get("trade_id") for r in rows if r.get("status") == "open"}
    out = followup(rows, LeagueKalshi().markets)
    book.save(rows)
    run.push_settled(rows, was_open)
    print("LEAGUE_FOLLOWUP", json.dumps(out))


if __name__ == "__main__":
    main()
    sys.exit(0)
