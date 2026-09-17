"""Compact signed approval links for PM Edge.

The watcher and Streamlit app run on different machines, so approval details are
carried in a short HMAC-signed token instead of relying on shared local files.
The token is informational only: it can never submit a Kalshi order.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

TOKEN_VERSION = 1
DEFAULT_TTL_SECONDS = int(os.getenv("PM_EDGE_APPROVAL_TTL_SECONDS", "7200"))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def signing_secret() -> bytes:
    """Return the shared signing key.

    Prefer a dedicated secret. For backward-compatible zero-setup deployment,
    derive a context-specific signing key from ODDS_API_KEY when both the
    watcher and Streamlit app already share that secret.
    """
    dedicated = os.getenv("PM_EDGE_APPROVAL_SECRET", "").strip()
    if dedicated:
        material = dedicated.encode("utf-8")
    else:
        fallback = os.getenv("ODDS_API_KEY", "").strip()
        if not fallback:
            raise RuntimeError("Missing PM_EDGE_APPROVAL_SECRET (or ODDS_API_KEY fallback)")
        material = b"pmedge-approval-v1\x00" + fallback.encode("utf-8")
    return hashlib.sha256(material).digest()


def create_approval_token(
    *,
    ticker: str,
    side: str,
    lane: str,
    limit_price: float,
    shares: int,
    max_loss: float,
    fair_probability: float | None = None,
    net_edge: float | None = None,
    books: int | None = None,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    now = int(time.time() if now is None else now)
    side = str(side or "").upper()
    if side not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    if not ticker:
        raise ValueError("ticker is required")
    if not (0 < float(limit_price) < 1):
        raise ValueError("limit_price must be between 0 and 1")
    if int(shares) < 1:
        raise ValueError("shares must be positive")

    lane_code = {"primary": "p", "value": "v", "price_fish": "f"}.get(
        str(lane or "").lower(), "p"
    )
    payload = {
        "v": TOKEN_VERSION,
        "t": str(ticker),
        "s": "Y" if side == "YES" else "N",
        "l": lane_code,
        "p": int(round(float(limit_price) * 10000)),
        "n": int(shares),
        "r": int(round(float(max_loss) * 100)),
        "x": now + max(60, int(ttl_seconds)),
    }
    if fair_probability is not None:
        payload["f"] = int(round(float(fair_probability) * 10000))
    if net_edge is not None:
        payload["e"] = int(round(float(net_edge) * 10000))
    if books is not None:
        payload["b"] = int(books)

    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    # 128-bit truncated HMAC is ample for an expiring, non-transactional link
    # while keeping the Pushover supplementary URL well below its length limit.
    signature = hmac.new(signing_secret(), body.encode("ascii"), hashlib.sha256).digest()[:16]
    return f"{body}.{_b64e(signature)}"


def verify_approval_token(token: str, *, now: int | None = None) -> dict:
    try:
        body, signature_text = str(token).split(".", 1)
        supplied = _b64d(signature_text)
    except Exception as exc:
        raise ValueError("Malformed approval token") from exc

    expected = hmac.new(signing_secret(), body.encode("ascii"), hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("Invalid approval signature")

    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid approval payload") from exc

    if int(payload.get("v", 0)) != TOKEN_VERSION:
        raise ValueError("Unsupported approval token version")
    now = int(time.time() if now is None else now)
    if int(payload.get("x", 0)) < now:
        raise ValueError("Approval link expired")

    side = "YES" if payload.get("s") == "Y" else "NO" if payload.get("s") == "N" else None
    if side is None:
        raise ValueError("Invalid side")
    lane = {"p": "primary", "v": "value", "f": "price_fish"}.get(payload.get("l"), "primary")

    return {
        "ticker": str(payload["t"]),
        "side": side,
        "lane": lane,
        "limit_price": int(payload["p"]) / 10000.0,
        "shares": int(payload["n"]),
        "max_loss": int(payload["r"]) / 100.0,
        "fair_probability": (int(payload["f"]) / 10000.0) if "f" in payload else None,
        "net_edge": (int(payload["e"]) / 10000.0) if "e" in payload else None,
        "books": int(payload["b"]) if "b" in payload else None,
        "expires_at": int(payload["x"]),
    }


def build_approval_url(base_url: str, token: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("STREAMLIT_APP_URL is required for approval links")
    return f"{base}/Approve?trade={quote(token, safe='.-_~')}"
