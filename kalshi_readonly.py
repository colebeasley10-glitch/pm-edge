"""Read-only authenticated Kalshi client for order/fill reconciliation.

Security boundary: this module exposes GET only. It contains no order-create,
amend, decrease, cancel, transfer, or withdrawal method.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


def _private_key_bytes() -> bytes | None:
    inline = os.getenv("KALSHI_PRIVATE_KEY", "").strip()
    if inline:
        # GitHub/Streamlit secrets often store multiline PEM with escaped newlines.
        return inline.replace("\\n", "\n").encode("utf-8")
    path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
    if path and Path(path).exists():
        return Path(path).read_bytes()
    return None


class KalshiReadOnlyClient:
    def __init__(self, api_key_id: str, private_key_pem: bytes, *, base_url: str = DEFAULT_BASE_URL, session=None):
        self.api_key_id = str(api_key_id).strip()
        self.base_url = str(base_url).rstrip("/")
        self.private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        self.session = session or requests.Session()
        if not self.api_key_id:
            raise ValueError("api_key_id is required")

    @classmethod
    def from_env(cls):
        api_key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        private_key = _private_key_bytes()
        if not api_key_id or not private_key:
            return None
        return cls(
            api_key_id,
            private_key,
            base_url=os.getenv("PM_EDGE_KALSHI_READONLY_BASE_URL", DEFAULT_BASE_URL),
        )

    def _headers(self, endpoint_path: str, *, timestamp_ms: int | None = None) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms))
        full_path = urlparse(self.base_url + endpoint_path).path.split("?", 1)[0]
        message = f"{timestamp}GET{full_path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }

    def _get(self, endpoint_path: str, *, params: dict | None = None) -> dict:
        response = self.session.get(
            self.base_url + endpoint_path,
            params=params or None,
            headers=self._headers(endpoint_path),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected Kalshi response")
        return payload

    def get_orders(self, *, ticker: str | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": max(1, min(int(limit), 200))}
        if ticker:
            params["ticker"] = str(ticker)
        payload = self._get("/portfolio/orders", params=params)
        rows = payload.get("orders", [])
        return rows if isinstance(rows, list) else []

    def get_fills(self, *, ticker: str | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": max(1, min(int(limit), 200))}
        if ticker:
            params["ticker"] = str(ticker)
        payload = self._get("/portfolio/fills", params=params)
        rows = payload.get("fills", [])
        return rows if isinstance(rows, list) else []

    def get_positions(self, *, limit: int = 100) -> list[dict]:
        payload = self._get(
            "/portfolio/positions",
            params={"limit": max(1, min(int(limit), 200))},
        )
        rows = payload.get("market_positions", [])
        return rows if isinstance(rows, list) else []


def tracking_configured() -> bool:
    return bool(os.getenv("KALSHI_API_KEY_ID", "").strip() and _private_key_bytes())
