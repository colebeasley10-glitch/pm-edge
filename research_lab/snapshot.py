"""Shared sportsbook fair-value snapshot, reused from the main scanner.

The main PM Edge scanner already matches Kalshi sports markets to sportsbook events and
de-vigs every book three ways (power, proportional, Shin). Each scored market is written to
pm_edge_candidate_ledger.csv during its run. The Research Lab reads that file instead of
calling The Odds API again, so the lab costs zero extra odds credits (the scanner's budget
is 14 a day).
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from research_lab.ledger import parse_dt

SNAPSHOT_PATH = Path(os.getenv("RESEARCH_LAB_SNAPSHOT_PATH", "pm_edge_candidate_ledger.csv"))
MAX_SNAPSHOT_AGE = timedelta(minutes=45)


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _truthy(v) -> bool | None:
    s = str(v or "").strip().lower()
    if s in {"true", "1", "yes"}:
        return True
    if s in {"false", "0", "no"}:
        return False
    return None


@dataclass
class FairQuote:
    ticker: str
    title: str
    event_id: str
    commence_time: datetime | None
    sport_key: str
    sport_title: str
    market_type: str
    selection: str
    point: float | None
    fair_yes_power: float | None
    fair_yes_prop: float | None
    fair_yes_shin: float | None
    books: int
    dispersion: float | None
    fair_at: datetime | None          # when sportsbook prices were fetched
    evaluated_at: datetime | None     # when the scanner scored the market
    matcher_pass: bool | None
    matcher_reason: str

    def fair_yes(self, method: str = "power") -> float | None:
        return {"power": self.fair_yes_power, "proportional": self.fair_yes_prop,
                "shin": self.fair_yes_shin}.get(method)

    def fair_side(self, side: str, method: str = "power") -> float | None:
        y = self.fair_yes(method)
        if y is None:
            return None
        return y if side == "YES" else 1.0 - y


def _from_row(r: dict) -> FairQuote | None:
    ticker = (r.get("ticker") or "").strip()
    if not ticker:
        return None
    side = (r.get("side") or "").upper()
    power = _f(r.get("fair_power"))
    if power is None:  # older rows: derive the YES probability from the side-specific one
        sp = _f(r.get("fair_probability"))
        if sp is not None and side in {"YES", "NO"}:
            power = sp if side == "YES" else 1.0 - sp
    try:
        books = int(float(r.get("books") or 0))
    except ValueError:
        books = 0
    return FairQuote(
        ticker=ticker, title=r.get("market_title") or ticker, event_id=r.get("event_id") or "",
        commence_time=parse_dt(r.get("commence_time")), sport_key=r.get("sport_key") or "",
        sport_title=r.get("sport_title") or "", market_type=r.get("market_type") or "",
        selection=r.get("selection") or "", point=_f(r.get("point")),
        fair_yes_power=power, fair_yes_prop=_f(r.get("fair_proportional")), fair_yes_shin=_f(r.get("fair_shin")),
        books=books, dispersion=_f(r.get("dispersion")), fair_at=parse_dt(r.get("fair_fetched_at")),
        evaluated_at=parse_dt(r.get("evaluated_at")), matcher_pass=_truthy(r.get("matcher_shadow_pass")),
        matcher_reason=r.get("matcher_shadow_reason") or "",
    )


def load_snapshot(now: datetime, path: Path | None = None) -> tuple[list[FairQuote], dict]:
    """Latest scored row per ticker from the scanner's most recent run. Returns (quotes, info)."""
    path = path or SNAPSHOT_PATH
    info = {"path": str(path), "rows": 0, "fresh_rows": 0, "tickers": 0, "stale": False, "missing": False}
    if not path.exists() or path.stat().st_size == 0:
        info["missing"] = True
        return [], info
    latest: dict[str, FairQuote] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            info["rows"] += 1
            if (r.get("stage") or "score") != "score":
                continue
            q = _from_row(r)
            if q is None or q.evaluated_at is None:
                continue
            if now - q.evaluated_at > MAX_SNAPSHOT_AGE:
                continue
            info["fresh_rows"] += 1
            prev = latest.get(q.ticker)
            if prev is None or (prev.evaluated_at or now) <= q.evaluated_at:
                latest[q.ticker] = q
    info["tickers"] = len(latest)
    info["stale"] = info["rows"] > 0 and not latest
    return list(latest.values()), info
