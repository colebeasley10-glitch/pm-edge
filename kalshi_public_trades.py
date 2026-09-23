"""Read-only client for Kalshi's free public trades endpoint.

Official endpoint:
GET https://external-api.kalshi.com/trade-api/v2/markets/trades

No API key is required for this public market-data endpoint.
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class KalshiPublicTradesError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KalshiPublicTradesClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ):
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "KalshiPublicTradesClient":
        return cls(
            base_url=os.getenv("PM_EDGE_KALSHI_PUBLIC_BASE_URL", DEFAULT_BASE_URL),
            timeout=float(os.getenv("PM_EDGE_WHALE_FLOW_TIMEOUT_SECONDS", "20")),
        )

    def get_trades_page(
        self,
        *,
        limit: int = 1000,
        cursor: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        ticker: str | None = None,
        is_block_trade: bool | None = None,
    ) -> tuple[list[dict], str]:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if cursor:
            params["cursor"] = cursor
        if min_ts is not None:
            params["min_ts"] = int(min_ts)
        if max_ts is not None:
            params["max_ts"] = int(max_ts)
        if ticker:
            params["ticker"] = str(ticker)
        if is_block_trade is not None:
            params["is_block_trade"] = "true" if is_block_trade else "false"

        response = self.session.get(
            f"{self.base_url}/markets/trades",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "pm-edge-whale-flow/1.0"},
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except Exception:
            payload = None
        if response.status_code >= 400:
            raise KalshiPublicTradesError(
                f"Kalshi public trades HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if not isinstance(payload, dict):
            raise KalshiPublicTradesError("Unexpected Kalshi public trades response")
        trades = payload.get("trades")
        if not isinstance(trades, list):
            trades = []
        return trades, str(payload.get("cursor") or "")

    def get_recent_trades(
        self,
        *,
        min_ts: int,
        max_pages: int = 3,
        page_size: int = 1000,
    ) -> list[dict]:
        rows: list[dict] = []
        cursor = ""
        for _ in range(max(1, int(max_pages))):
            page, cursor = self.get_trades_page(
                limit=page_size,
                cursor=cursor or None,
                min_ts=min_ts,
            )
            rows.extend(page)
            if not cursor or len(page) < page_size:
                break
        return rows
