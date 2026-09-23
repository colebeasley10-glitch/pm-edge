"""Minimal read-only client for KalshiSpy's verified-trader API."""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE_URL = "https://kalshispy.com"


class KalshiSpyError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KalshiSpyClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ):
        api_key = str(api_key or "").strip()
        if not api_key:
            raise ValueError("KalshiSpy API key is required")
        self.api_key = api_key
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "KalshiSpyClient | None":
        key = os.getenv("KALSHISPY_API_KEY", "").strip()
        if not key:
            return None
        return cls(
            key,
            base_url=os.getenv("KALSHISPY_API_BASE_URL", DEFAULT_BASE_URL),
            timeout=float(os.getenv("KALSHISPY_TIMEOUT_SECONDS", "20")),
        )

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params or {},
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "pm-edge-whale/1.0",
            },
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except Exception:
            payload = None
        if response.status_code >= 400:
            message = ""
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("code") or "")
            raise KalshiSpyError(
                message or f"KalshiSpy HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise KalshiSpyError("Unexpected KalshiSpy response")
        return payload

    def get_whales(self, *, limit: int = 40, sort: str = "lifetime_pnl") -> list[dict]:
        payload = self._get(
            "/api/v1/whales",
            params={"limit": max(1, min(int(limit), 40)), "sort": sort},
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def search_trader_trades(
        self,
        nickname: str,
        *,
        min_amount: int = 100,
    ) -> list[dict]:
        payload = self._get(
            "/api/v1/search",
            params={
                "mode": "trader",
                "q": str(nickname),
                "min_amount": int(min_amount),
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        trades = data.get("trades")
        return trades if isinstance(trades, list) else []
