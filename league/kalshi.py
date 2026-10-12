"""Thin public-only Kalshi access for the league (no keys, no orders)."""
from __future__ import annotations

import os
import time

from pmedge.kalshi import KalshiError, KalshiPublicClient, normalize_market

BASE = os.getenv("PM_EDGE_KALSHI_PUBLIC_BASE_URL", "https://external-api.kalshi.com/trade-api/v2")


class LeagueKalshi:
    def __init__(self, base_url: str = BASE):
        self.c = KalshiPublicClient(base_url)
        self._series_cache: dict[str, list[dict]] = {}

    def series(self, category: str) -> list[dict]:
        if category not in self._series_cache:
            self._series_cache[category] = self.c.list_series(category=category)
        return self._series_cache[category]

    def open_events(self, series_ticker: str, limit: int = 20) -> list[dict]:
        """Open events with their markets normalized; event fields copied onto each market."""
        try:
            evs = self.c.list_events(series_ticker=series_ticker, status="open",
                                     with_nested_markets=True, max_events=limit)
        except KalshiError:
            return []
        out = []
        for e in evs:
            ms = []
            for raw in e.get("markets") or []:
                m = normalize_market(raw)
                m.update({
                    "_event_title": e.get("title") or "",
                    "_event_sub": e.get("sub_title") or "",
                    "_series": series_ticker,
                    "strike_type": raw.get("strike_type"),
                    "floor_strike": raw.get("floor_strike"),
                    "cap_strike": raw.get("cap_strike"),
                    "expected_expiration_time": raw.get("expected_expiration_time"),
                })
                ms.append(m)
            out.append({"event_ticker": e.get("event_ticker"), "title": e.get("title") or "",
                        "sub_title": e.get("sub_title") or "", "series": series_ticker, "markets": ms})
        return out

    def markets(self, tickers: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(tickers), 100):
            chunk = tickers[i:i + 100]
            try:
                data = self.c._get("/markets", {"tickers": ",".join(chunk), "limit": len(chunk)})
            except KalshiError:
                continue
            for raw in data.get("markets") or []:
                m = normalize_market(raw)
                out[m["ticker"]] = m
            time.sleep(0.05)
        return out
