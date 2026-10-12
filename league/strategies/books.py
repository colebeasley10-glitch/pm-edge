"""Sportsbooks: paper-trade the main PM Edge scanner's alerts (sportsbook
consensus vs Kalshi, v29 taker gate) so they're scored in the same league."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

from league.book import Pick, f, parse_dt

NAME = "books"
MIN_EDGE = 0.02
SIGNALS = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))


def run(kalshi, now: datetime, session=None) -> list[Pick]:
    if not SIGNALS.exists():
        return []
    with SIGNALS.open("r", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh)
                if (r.get("record_type") or "signal") == "signal"
                and (parse_dt(r.get("alerted_at")) or now - timedelta(days=9)) >= now - timedelta(hours=2)]
    if not rows:
        return []
    markets = kalshi.markets(sorted({r["ticker"] for r in rows if r.get("ticker")}))
    picks = []
    for r in rows:
        m = markets.get(r.get("ticker") or "")
        fair = f(r.get("fair_probability"))
        side = str(r.get("side") or "").upper()
        if not m or fair is None or side not in {"YES", "NO"}:
            continue
        picks.append(Pick(NAME, m, side, fair,
                          f"Sportsbooks have this at {fair:.0%} ({int(f(r.get('books'), 0) or 0)} books)",
                          category="Sports", min_edge=MIN_EDGE,
                          extra={"title": r.get("market_title") or m.get("title")}))
    return picks
