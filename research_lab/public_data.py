"""Read-only Kalshi public market data for the Research Lab.

GET requests only, no API keys, no signed headers. The class has no method that can
send anything but a GET, and test_research_lab_safety.py checks that stays true.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests

from pmedge.kalshi import normalize_market

BASE = os.getenv("PM_EDGE_KALSHI_PUBLIC_BASE_URL", "https://external-api.kalshi.com/trade-api/v2").rstrip("/")


class PublicDataError(RuntimeError):
    pass


class PublicKalshi:
    """GET-only client. Counts calls and failures so a run can report data problems."""

    def __init__(self, base_url: str = BASE, timeout: int = 20, session=None, pause: float = 0.06):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.pause = pause
        self.calls = 0
        self.failures: list[str] = []

    def _get(self, path: str, params: dict | None = None) -> dict:
        last = None
        for attempt in range(3):
            self.calls += 1
            try:
                r = self.session.get(self.base_url + path, params=params or {}, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(1.0 + attempt)
                    continue
                r.raise_for_status()
                if self.pause:
                    time.sleep(self.pause)
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        msg = f"GET {path} failed: {type(last).__name__ if last else 'rate_limited'}"
        self.failures.append(msg)
        raise PublicDataError(msg)

    # ---- markets ----
    def markets(self, tickers: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        tickers = sorted({t for t in tickers if t})
        for i in range(0, len(tickers), 100):
            chunk = tickers[i:i + 100]
            try:
                data = self._get("/markets", {"tickers": ",".join(chunk), "limit": len(chunk)})
            except PublicDataError:
                continue
            for raw in data.get("markets") or []:
                m = normalize_market(raw)
                m["expected_expiration_time"] = raw.get("expected_expiration_time")
                m["strike_type"] = raw.get("strike_type")
                m["floor_strike"] = raw.get("floor_strike")
                m["cap_strike"] = raw.get("cap_strike")
                out[m["ticker"]] = m
        return out

    def open_events(self, max_events: int = 400) -> list[dict]:
        """Open events with nested markets. Event-level fields (mutually_exclusive) are kept."""
        out, cursor = [], None
        while len(out) < max_events:
            params = {"status": "open", "with_nested_markets": "true", "limit": min(200, max_events - len(out))}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/events", params)
            batch = data.get("events") or []
            for e in batch:
                ms = []
                for raw in e.get("markets") or []:
                    m = normalize_market(raw)
                    m.update({"strike_type": raw.get("strike_type"), "floor_strike": raw.get("floor_strike"),
                              "cap_strike": raw.get("cap_strike"),
                              "expected_expiration_time": raw.get("expected_expiration_time")})
                    ms.append(m)
                out.append({"event_ticker": e.get("event_ticker") or "", "title": e.get("title") or "",
                            "series_ticker": e.get("series_ticker") or "",
                            "category": e.get("category") or "",
                            "mutually_exclusive": bool(e.get("mutually_exclusive")), "markets": ms})
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
        return out[:max_events]

    def series_events(self, series_ticker: str, max_events: int = 200) -> list[dict]:
        """Open events (with fresh nested market quotes) for one Kalshi series."""
        data = self._get("/events", {"series_ticker": series_ticker, "status": "open",
                                     "with_nested_markets": "true", "limit": min(200, max_events)})
        out = []
        for e in data.get("events") or []:
            ms = []
            for raw in e.get("markets") or []:
                m = normalize_market(raw)
                m["expected_expiration_time"] = raw.get("expected_expiration_time")
                ms.append(m)
            out.append({"event_ticker": e.get("event_ticker") or "", "title": e.get("title") or "",
                        "series_ticker": series_ticker, "markets": ms})
        return out

    # ---- order book ----
    def orderbook(self, ticker: str, depth: int = 20) -> "Book":
        data = self._get(f"/markets/{ticker}/orderbook", {"depth": depth})
        return Book.parse(data, fetched_at=datetime.now(timezone.utc))

    # ---- public trade tape ----
    def trades(self, *, min_ts: int, page_cap: int = 10) -> tuple[list[dict], bool]:
        """Trades since min_ts, newest first. Returns (trades, complete)."""
        out, cursor = [], None
        for _ in range(page_cap):
            params = {"limit": 1000, "min_ts": int(min_ts)}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets/trades", params)
            batch = data.get("trades") or []
            out.extend(batch)
            cursor = data.get("cursor")
            if not cursor or not batch:
                return out, True
        return out, False


def _levels(raw) -> list[tuple[float, float]]:
    """[(price_dollars, size)] from either the dollar (fp) or the legacy cents format."""
    out = []
    for lv in raw or []:
        try:
            p, s = Decimal(str(lv[0])), Decimal(str(lv[1]))
        except Exception:
            continue
        if p >= 1:  # legacy cents
            p = p / 100
        if Decimal("0") < p < Decimal("1") and s > 0:
            out.append((float(p), float(s)))
    return out


class Book:
    """Kalshi order book. Kalshi lists bids only: a YES ask is 1 - a NO bid, and vice versa."""

    def __init__(self, yes_bids, no_bids, fetched_at: datetime):
        self.yes_bids = sorted(yes_bids, key=lambda x: -x[0])
        self.no_bids = sorted(no_bids, key=lambda x: -x[0])
        self.fetched_at = fetched_at

    @classmethod
    def parse(cls, data: dict, fetched_at: datetime) -> "Book":
        fp = (data or {}).get("orderbook_fp")
        if fp:
            return cls(_levels(fp.get("yes_dollars") or fp.get("yes")),
                       _levels(fp.get("no_dollars") or fp.get("no")), fetched_at)
        ob = (data or {}).get("orderbook") or {}
        return cls(_levels(ob.get("yes_dollars") or ob.get("yes")),
                   _levels(ob.get("no_dollars") or ob.get("no")), fetched_at)

    def asks(self, side: str) -> list[tuple[float, float]]:
        """Ask levels for buying `side`, cheapest first: (price, contracts available)."""
        opp = self.no_bids if side == "YES" else self.yes_bids
        return sorted(((round(1.0 - p, 4), s) for p, s in opp), key=lambda x: x[0])

    def best_bid(self, side: str) -> float | None:
        own = self.yes_bids if side == "YES" else self.no_bids
        return own[0][0] if own else None

    def best_ask(self, side: str) -> float | None:
        a = self.asks(side)
        return a[0][0] if a else None
