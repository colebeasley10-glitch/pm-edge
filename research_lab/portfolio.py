"""Virtual bankroll and exposure limits for one paper strategy."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research_lab import config
from research_lab.ledger import Store, parse_dt

EASTERN = ZoneInfo("America/New_York")
SETTLED = {"won", "lost", "void", "closed"}


def f(value, default=0.0) -> float:
    try:
        x = float(value)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def et_day(dt: datetime) -> str:
    return dt.astimezone(EASTERN).strftime("%Y%m%d")


def realized_pl(store: Store) -> float:
    return round(sum(f(p.get("pl")) for p in store.positions if p.get("status") in SETTLED), 2)


def open_committed(store: Store) -> float:
    return round(sum(f(p.get("committed")) for p in store.positions if p.get("status") == "open"), 2)


def available_cash(store: Store) -> float:
    """Starting bankroll + realized P/L - money tied up in open positions."""
    return round(config.STARTING_BANKROLL + realized_pl(store) - open_committed(store), 2)


def committed_today(store: Store, now: datetime) -> float:
    day = et_day(now)
    return round(sum(f(p.get("committed")) for p in store.positions
                     if et_day(parse_dt(p.get("opened_at")) or now) == day), 2)


def event_exposure(store: Store, event: str) -> float:
    return round(sum(f(p.get("committed")) for p in store.positions
                     if p.get("status") == "open" and p.get("event_ticker") == event), 2)


def check_limits(store: Store, *, event: str, ticker: str, committed: float, now: datetime) -> tuple[bool, str]:
    """Every limit a new paper position must pass. No minimum trade count, no stake escalation."""
    for p in store.open_positions():
        if p.get("ticker") == ticker:
            return False, "already_holding_market"
        if event and p.get("event_ticker") == event:
            # one open position per game: never both sides, never the same result worded twice
            return False, "already_holding_event"
    if committed > available_cash(store) + 1e-9:
        return False, "bankroll_insufficient"
    if event_exposure(store, event) + committed > config.MAX_EVENT_EXPOSURE + 1e-9:
        return False, "event_exposure_cap"
    if committed_today(store, now) + committed > config.DAILY_EXPOSURE_CAP + 1e-9:
        return False, "daily_exposure_cap"
    return True, "ok"
