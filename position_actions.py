"""Signed one-tap action links for strong PM Edge position signals.

The watcher can recommend an ADD or SELL action, but the token itself cannot
trade. Real-money submission still requires an explicit tap on the Streamlit
action page.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

import approval_links

TOKEN_VERSION = 1
DEFAULT_TTL_SECONDS = int(os.getenv("PM_EDGE_POSITION_ACTION_TTL_SECONDS", "1800"))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode("ascii"))


def create_position_action_token(
    *,
    action: str,
    ticker: str,
    side: str,
    shares: int,
    limit_price: float,
    fair_probability: float,
    signal_gap: float,
    books: int,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    action = str(action).upper()
    side = str(side).upper()
    if action not in {"SELL", "ADD"}:
        raise ValueError("action must be SELL or ADD")
    if side not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    if not ticker:
        raise ValueError("ticker is required")
    if int(shares) < 1:
        raise ValueError("shares must be positive")
    if not (0 < float(limit_price) < 1):
        raise ValueError("limit_price must be between 0 and 1")
    if not (0 <= float(fair_probability) <= 1):
        raise ValueError("fair_probability must be between 0 and 1")

    now = int(time.time() if now is None else now)
    payload = {
        "v": TOKEN_VERSION,
        "a": "S" if action == "SELL" else "A",
        "t": str(ticker),
        "s": "Y" if side == "YES" else "N",
        "n": int(shares),
        "p": int(round(float(limit_price) * 10000)),
        "f": int(round(float(fair_probability) * 10000)),
        "g": int(round(float(signal_gap) * 10000)),
        "b": int(books),
        "x": now + max(60, int(ttl_seconds)),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(approval_links.signing_secret(), body.encode("ascii"), hashlib.sha256).digest()[:16]
    return f"{body}.{_b64e(sig)}"


def verify_position_action_token(token: str, *, now: int | None = None) -> dict:
    try:
        body, sig_text = str(token).split(".", 1)
        supplied = _b64d(sig_text)
    except Exception as exc:
        raise ValueError("Malformed position-action token") from exc

    expected = hmac.new(
        approval_links.signing_secret(), body.encode("ascii"), hashlib.sha256
    ).digest()[:16]
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("Invalid position-action signature")

    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid position-action payload") from exc

    if int(payload.get("v", 0)) != TOKEN_VERSION:
        raise ValueError("Unsupported position-action token version")
    now = int(time.time() if now is None else now)
    if int(payload.get("x", 0)) < now:
        raise ValueError("Position-action link expired")

    action = {"S": "SELL", "A": "ADD"}.get(payload.get("a"))
    side = {"Y": "YES", "N": "NO"}.get(payload.get("s"))
    if action is None or side is None:
        raise ValueError("Invalid action or side")

    return {
        "action": action,
        "ticker": str(payload["t"]),
        "side": side,
        "shares": int(payload["n"]),
        "limit_price": int(payload["p"]) / 10000.0,
        "fair_probability": int(payload["f"]) / 10000.0,
        "signal_gap": int(payload["g"]) / 10000.0,
        "books": int(payload["b"]),
        "expires_at": int(payload["x"]),
    }


def build_position_action_url(base_url: str, token: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("STREAMLIT_APP_URL is required")
    return f"{base}/Position_Action?signal={quote(token, safe='.-_~')}"
