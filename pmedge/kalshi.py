from __future__ import annotations
import base64
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse
import uuid
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .utils import to_float


class KalshiError(RuntimeError):
    pass


class KalshiPublicClient:
    def __init__(self, base_url: str, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PM-Edge/2.0"})

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self.session.get(self.base_url + path, params=params or {}, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            raise KalshiError(f"Kalshi request failed: {exc}") from exc

    def list_open_markets(self, max_markets: int = 3000) -> list[dict]:
        out, cursor = [], None
        while len(out) < max_markets:
            params = {"status": "open", "limit": min(1000, max_markets - len(out))}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params)
            batch = data.get("markets", [])
            if not isinstance(batch, list):
                raise KalshiError("Unexpected /markets response")
            out.extend(batch)
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
        return out[:max_markets]

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}").get("market", {})

    def get_orderbook(self, ticker: str, depth: int = 20) -> dict:
        return self._get(f"/markets/{ticker}/orderbook", {"depth": max(0, min(depth, 100))})


class KalshiAuthClient(KalshiPublicClient):
    def __init__(self, base_url: str, api_key_id: str, private_key_path: str, timeout: int = 20):
        super().__init__(base_url, timeout)
        if not api_key_id or not private_key_path:
            raise KalshiError("Kalshi API key ID/private-key path are required for authenticated actions")
        self.api_key_id = api_key_id
        key_bytes = Path(private_key_path).read_bytes()
        self.private_key = serialization.load_pem_private_key(key_bytes, password=None)

    def _signed_headers(self, method: str, path: str) -> dict:
        ts = str(int(dt.datetime.now().timestamp() * 1000))
        sign_path = urlparse(self.base_url + path).path.split("?")[0]
        message = f"{ts}{method.upper()}{sign_path}".encode("utf-8")
        sig = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
            "Content-Type": "application/json",
        }

    def _request_auth(self, method: str, path: str, *, params=None, payload=None) -> dict:
        headers = self._signed_headers(method, path)
        try:
            r = self.session.request(
                method, self.base_url + path, params=params or {}, json=payload,
                headers=headers, timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json() if r.content else {}
        except (requests.RequestException, ValueError) as exc:
            body = getattr(getattr(exc, "response", None), "text", "")
            raise KalshiError(f"Authenticated Kalshi request failed: {exc}; {body[:500]}") from exc

    def get_balance(self) -> dict:
        return self._request_auth("GET", "/portfolio/balance")

    def get_fills(self, **params) -> dict:
        return self._request_auth("GET", "/portfolio/fills", params={k: v for k, v in params.items() if v is not None})

    def place_event_order(
        self, *, ticker: str, side: str, count: float, price: float,
        post_only: bool, time_in_force: str, client_order_id: str | None = None,
    ) -> dict:
        payload = {
            "ticker": ticker,
            "client_order_id": client_order_id or str(uuid.uuid4()),
            "side": side,
            "count": f"{count:.2f}",
            "price": f"{price:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": bool(post_only),
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0,
        }
        return self._request_auth("POST", "/portfolio/events/orders", payload=payload)

    def cancel_event_order(self, order_id: str) -> dict:
        return self._request_auth("DELETE", f"/portfolio/events/orders/{order_id}")


def normalize_market(m: dict) -> dict:
    yes_bid = to_float(m.get("yes_bid_dollars"))
    yes_ask = to_float(m.get("yes_ask_dollars"))
    no_bid = to_float(m.get("no_bid_dollars"))
    no_ask = to_float(m.get("no_ask_dollars"))
    if yes_ask is None and no_bid is not None:
        yes_ask = 1 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 1 - yes_bid
    if yes_bid is None and no_ask is not None:
        yes_bid = 1 - no_ask
    if no_bid is None and yes_ask is not None:
        no_bid = 1 - yes_ask

    return {
        "ticker": str(m.get("ticker", "")),
        "event_ticker": str(m.get("event_ticker", "")),
        "title": str(m.get("title") or ""),
        "subtitle": str(m.get("subtitle") or ""),
        "yes_sub_title": str(m.get("yes_sub_title") or ""),
        "no_sub_title": str(m.get("no_sub_title") or ""),
        "rules_primary": str(m.get("rules_primary") or ""),
        "rules_secondary": str(m.get("rules_secondary") or ""),
        "close_time": m.get("close_time"),
        "occurrence_datetime": m.get("occurrence_datetime"),
        "status": m.get("status"),
        "result": m.get("result"),
        "settlement_value": to_float(m.get("settlement_value_dollars")),
        "settlement_ts": m.get("settlement_ts"),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_bid_size": to_float(m.get("yes_bid_size_fp"), 0.0) or 0.0,
        "yes_ask_size": to_float(m.get("yes_ask_size_fp"), 0.0) or 0.0,
        "volume_24h": to_float(m.get("volume_24h_fp"), 0.0) or 0.0,
        "volume": to_float(m.get("volume_fp"), 0.0) or 0.0,
        "liquidity_dollars": to_float(m.get("liquidity_dollars"), 0.0) or 0.0,
        "open_interest": to_float(m.get("open_interest_fp"), 0.0) or 0.0,
        "price_level_structure": m.get("price_level_structure"),
        "price_ranges": m.get("price_ranges") or [],
    }


def tick_size_for_price(market: dict, price: float) -> float:
    ranges = market.get("price_ranges") or []
    for r in ranges:
        start = to_float(r.get("start"), 0.0)
        end = to_float(r.get("end"), 1.0)
        step = to_float(r.get("step"))
        if step and start <= price <= end:
            return step
    return 0.01
