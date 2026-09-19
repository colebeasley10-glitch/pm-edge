"""Approval-gated Kalshi order submission client for PM Edge.

This module intentionally exposes only approval-gated order capabilities:
* GET recent orders/positions for replay and safety checks;
* POST a new buy or position-reducing sell order through Kalshi's event-order endpoint.

It contains no cancel, amend, decrease, transfer, withdrawal, or autonomous
submission path. Callers must require an explicit user approval action.
"""
from __future__ import annotations

import base64
import os
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
# Resting PM Edge maker orders are intentionally short-lived. A stale live-sports
# limit can become adverse selection as the game state changes.
LIVE_MAKE_ORDER_TTL_SECONDS = int(os.getenv("PM_EDGE_LIVE_MAKE_ORDER_TTL_SECONDS", "180"))


class KalshiSubmitError(RuntimeError):
    def __init__(self, status_code: int, message: str, payload=None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload


def _private_key_bytes() -> bytes | None:
    inline = os.getenv("KALSHI_WRITE_PRIVATE_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n").encode("utf-8")
    path = os.getenv("KALSHI_WRITE_PRIVATE_KEY_PATH", "").strip()
    if path and Path(path).exists():
        return Path(path).read_bytes()
    return None


def _fixed_price(value: float) -> str:
    """Serialize a Kalshi V2 fixed-point dollar price with explicit 4dp scale.

    Kalshi's V2 event-order endpoint accepts fixed-point dollar strings up to
    four decimal places. Keeping the scale explicit also avoids the exchange
    rejecting round values such as 0.50 as an invalid dollar precision.
    """
    price = Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return f"{price:.4f}"


class KalshiSubmitClient:
    def __init__(self, api_key_id: str, private_key_pem: bytes, *, base_url: str = DEFAULT_BASE_URL, session=None):
        self.api_key_id = str(api_key_id).strip()
        self.base_url = str(base_url).rstrip("/")
        self.private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        self.session = session or requests.Session()
        if not self.api_key_id:
            raise ValueError("api_key_id is required")

    @classmethod
    def from_env(cls):
        api_key_id = os.getenv("KALSHI_WRITE_API_KEY_ID", "").strip()
        private_key = _private_key_bytes()
        if not api_key_id or not private_key:
            return None
        return cls(
            api_key_id,
            private_key,
            base_url=os.getenv("PM_EDGE_KALSHI_WRITE_BASE_URL", DEFAULT_BASE_URL),
        )

    def _headers(self, method: str, endpoint_path: str, *, timestamp_ms: int | None = None) -> dict[str, str]:
        method = str(method).upper()
        timestamp = str(int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms))
        full_path = urlparse(self.base_url + endpoint_path).path.split("?", 1)[0]
        message = f"{timestamp}{method}{full_path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint_path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
        response = self.session.request(
            str(method).upper(),
            self.base_url + endpoint_path,
            params=params or None,
            json=json_body,
            headers=self._headers(method, endpoint_path),
            timeout=15,
        )
        try:
            payload = response.json()
        except Exception:
            payload = None
        if not (200 <= int(response.status_code) < 300):
            detail = ""
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or payload)[:500]
            if not detail:
                detail = str(getattr(response, "text", "") or "Kalshi request failed")[:500]
            raise KalshiSubmitError(response.status_code, detail, payload)
        if not isinstance(payload, dict):
            raise KalshiSubmitError(response.status_code, "Unexpected Kalshi response", payload)
        return payload

    def get_orders(self, *, limit: int = 200) -> list[dict]:
        payload = self._request("GET", "/portfolio/orders", params={"limit": max(1, min(int(limit), 200))})
        rows = payload.get("orders", [])
        return rows if isinstance(rows, list) else []

    def get_positions(self, *, limit: int = 200) -> list[dict]:
        payload = self._request(
            "GET",
            "/portfolio/positions",
            params={"limit": max(1, min(int(limit), 200))},
        )
        rows = payload.get("market_positions", [])
        return rows if isinstance(rows, list) else []

    def find_order_by_client_id(self, client_order_id: str) -> dict | None:
        target = str(client_order_id)
        for row in self.get_orders(limit=200):
            if str(row.get("client_order_id") or "") == target:
                return row
        return None

    def submitted_cost_for_prefix(self, client_id_prefix: str) -> float:
        """Conservative submitted max-cost total for PM Edge orders matching a prefix."""
        total = 0.0
        prefix = str(client_id_prefix)
        for row in self.get_orders(limit=200):
            if not str(row.get("client_order_id") or "").startswith(prefix):
                continue
            try:
                count = float(row.get("initial_count_fp") or row.get("initial_count") or row.get("count") or 0)
            except (TypeError, ValueError):
                continue
            outcome = str(row.get("outcome_side") or "").lower()
            try:
                if outcome == "yes":
                    price = float(row.get("yes_price_dollars") or 0)
                elif outcome == "no":
                    price = float(row.get("no_price_dollars") or 0)
                else:
                    legacy_side = str(row.get("side") or "").lower()
                    price = float(row.get("yes_price_dollars") or 0) if legacy_side == "yes" else float(row.get("no_price_dollars") or 0)
            except (TypeError, ValueError):
                continue
            if count > 0 and 0 < price < 1:
                total += count * price
        return total

    def create_buy_order(
        self,
        *,
        ticker: str,
        outcome_side: str,
        count: int,
        max_price: float,
        execution: str,
        client_order_id: str,
        expires_at: int | None = None,
        is_live: bool = False,
    ) -> dict:
        """Create exactly one BUY exposure order after explicit caller approval.

        Kalshi's event-order API quotes the YES book only:
        * BUY YES at p -> bid YES at p
        * BUY NO at p -> ask YES at (1 - p)
        """
        side = str(outcome_side).upper()
        execution = str(execution).upper()
        ticker = str(ticker).strip()
        count = int(count)
        max_price = float(max_price)
        if not ticker:
            raise ValueError("ticker is required")
        if side not in {"YES", "NO"}:
            raise ValueError("outcome_side must be YES or NO")
        if execution not in {"TAKE", "MAKE"}:
            raise ValueError("execution must be TAKE or MAKE")
        if count < 1:
            raise ValueError("count must be positive")
        if not (0 < max_price < 1):
            raise ValueError("max_price must be between 0 and 1")
        if not str(client_order_id).strip():
            raise ValueError("client_order_id is required")

        # Event-order API expects a fixed-point dollar string. Whole-cent
        # prices remain valid, but we serialize at 4dp so round values such as
        # 0.50 retain an unambiguous fixed-point scale.
        book_side = "bid" if side == "YES" else "ask"
        book_price = max_price if side == "YES" else (1.0 - max_price)
        payload = {
            "ticker": ticker,
            "client_order_id": str(client_order_id),
            "side": book_side,
            "count": f"{count:.2f}",
            "price": _fixed_price(book_price),
            "time_in_force": "immediate_or_cancel" if execution == "TAKE" else "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        if execution == "MAKE":
            payload["post_only"] = True
            # Pregame maker orders expire at the event-start timestamp carried
            # by the signed approval. Live maker orders get a short TTL because
            # score/time information changes rapidly.
            if is_live:
                payload["expiration_time"] = int(time.time()) + max(60, LIVE_MAKE_ORDER_TTL_SECONDS)
            elif expires_at is not None:
                payload["expiration_time"] = max(int(time.time()) + 60, int(expires_at))
            else:
                # Fail safe for legacy approvals that do not yet carry event start.
                payload["expiration_time"] = int(time.time()) + max(60, LIVE_MAKE_ORDER_TTL_SECONDS)
        return self._request("POST", "/portfolio/events/orders", json_body=payload)

    def create_sell_order(
        self,
        *,
        ticker: str,
        outcome_side: str,
        count: int,
        min_price: float,
        client_order_id: str,
    ) -> dict:
        """Create one position-reducing IOC SELL order after explicit approval.

        Kalshi's V2 event-order API quotes the YES book:
        * SELL YES at p -> ask YES at p
        * SELL NO at p -> bid YES at (1 - p)

        The approval page verifies the current position first so this method is
        only used to reduce an existing same-side position, not to open a short.
        """
        side = str(outcome_side).upper()
        ticker = str(ticker).strip()
        count = int(count)
        min_price = float(min_price)
        if not ticker:
            raise ValueError("ticker is required")
        if side not in {"YES", "NO"}:
            raise ValueError("outcome_side must be YES or NO")
        if count < 1:
            raise ValueError("count must be positive")
        if not (0 < min_price < 1):
            raise ValueError("min_price must be between 0 and 1")
        if not str(client_order_id).strip():
            raise ValueError("client_order_id is required")

        book_side = "ask" if side == "YES" else "bid"
        book_price = min_price if side == "YES" else (1.0 - min_price)
        payload = {
            "ticker": ticker,
            "client_order_id": str(client_order_id),
            "side": book_side,
            "count": f"{count:.2f}",
            "price": _fixed_price(book_price),
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
            "reduce_only": True,
        }
        return self._request("POST", "/portfolio/events/orders", json_body=payload)


def write_configured() -> bool:
    return bool(os.getenv("KALSHI_WRITE_API_KEY_ID", "").strip() and _private_key_bytes())
