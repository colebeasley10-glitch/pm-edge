"""Conservative ADD/SELL alerts for existing Kalshi positions.

Signals are read-only. They require a fresh sportsbook-derived PM Edge fair
value already produced by the watcher and a large executable-price/fair-value
gap. No order is submitted here.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import position_actions

WATCH_STATE_PATH = Path(os.getenv("PM_EDGE_STATE_PATH", ".pm_edge_watch_state.json"))
SIGNAL_STATE_PATH = Path(os.getenv("PM_EDGE_POSITION_SIGNAL_STATE_PATH", ".pm_edge_position_signal_state.json"))
PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
PUSH_URL = "https://api.pushover.net/1/messages.json"

SELL_OVER_FAIR = float(os.getenv("PM_EDGE_SELL_OVER_FAIR", "0.08"))
ADD_UNDER_FAIR = float(os.getenv("PM_EDGE_ADD_UNDER_FAIR", "0.08"))
MIN_BOOKS_SELL = int(os.getenv("PM_EDGE_POSITION_SELL_MIN_BOOKS", "4"))
MIN_BOOKS_ADD = int(os.getenv("PM_EDGE_POSITION_ADD_MIN_BOOKS", "4"))
MAX_LIVE_FAIR_AGE_MINUTES = float(os.getenv("PM_EDGE_POSITION_LIVE_FAIR_MAX_AGE_MINUTES", "20"))
MAX_PREGAME_FAIR_AGE_MINUTES = float(os.getenv("PM_EDGE_POSITION_PREGAME_FAIR_MAX_AGE_MINUTES", "90"))
MAX_ADD_RISK = float(os.getenv("PM_EDGE_POSITION_ADD_MAX_RISK", "5.00"))
MAX_ADD_SHARES = int(os.getenv("PM_EDGE_POSITION_ADD_MAX_SHARES", "10"))
ALERT_COOLDOWN_HOURS = float(os.getenv("PM_EDGE_POSITION_SIGNAL_COOLDOWN_HOURS", "4"))
MAX_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_POSITION_SIGNAL_MAX_ALERTS_PER_RUN", "2"))


def _safe_float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(value: Any) -> float:
    d = _parse_dt(value)
    if d is None:
        return 1e9
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        x = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return x if isinstance(x, dict) else default


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _market(ticker: str) -> dict:
    r = requests.get(f"{PUBLIC_BASE}/markets/{ticker}", timeout=12)
    r.raise_for_status()
    p = r.json()
    m = p.get("market") if isinstance(p, dict) else None
    if not isinstance(m, dict):
        raise RuntimeError("Unexpected Kalshi market response")
    return m


def _price(market: dict, side: str, kind: str) -> float | None:
    prefix = "yes" if side == "YES" else "no"
    for key in (f"{prefix}_{kind}_dollars", f"{prefix}_{kind}"):
        v = market.get(key)
        if v in (None, ""):
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if not key.endswith("_dollars") and x > 1:
            x /= 100.0
        if 0 < x < 1:
            return x

    # Some Kalshi payloads only expose the YES book. Binary complements are:
    # NO bid = 1 - YES ask; NO ask = 1 - YES bid.
    if side == "NO" and kind in {"bid", "ask"}:
        yes_kind = "ask" if kind == "bid" else "bid"
        for key in (f"yes_{yes_kind}_dollars", f"yes_{yes_kind}"):
            v = market.get(key)
            if v in (None, ""):
                continue
            try:
                y = float(v)
            except (TypeError, ValueError):
                continue
            if not key.endswith("_dollars") and y > 1:
                y /= 100.0
            x = 1.0 - y
            if 0 < x < 1:
                return x
    return None


def _send(title: str, body: str, *, url: str | None = None, url_title: str | None = None) -> bool:
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        return False
    data = {"token": token, "user": user, "title": title, "message": body, "priority": "0"}
    if url:
        data["url"] = url
        data["url_title"] = url_title or "Review"
    r = requests.post(PUSH_URL, data=data, timeout=12)
    if r.status_code != 200:
        return False
    try:
        return int(r.json().get("status") or 0) == 1
    except Exception:
        return False


def _fresh_enough(fair: dict) -> bool:
    commence = _parse_dt(fair.get("commence_time"))
    live = commence is not None and datetime.now(timezone.utc) >= commence
    max_age = MAX_LIVE_FAIR_AGE_MINUTES if live else MAX_PREGAME_FAIR_AGE_MINUTES
    return _age_minutes(fair.get("fetched_at")) <= max_age


def _position_side(position: float) -> tuple[str, int] | None:
    if position > 0:
        return "YES", int(math.floor(position + 1e-9))
    if position < 0:
        return "NO", int(math.floor(abs(position) + 1e-9))
    return None


def _can_repeat(state: dict, key: str, current_gap: float) -> bool:
    prev = state.get("signals", {}).get(key)
    if not isinstance(prev, dict):
        return True
    sent = _parse_dt(prev.get("sent_at"))
    age_h = 1e9 if sent is None else max(
        0.0, (datetime.now(timezone.utc) - sent).total_seconds() / 3600.0
    )
    old_gap = _safe_float(prev.get("gap")) or 0.0
    return age_h >= ALERT_COOLDOWN_HOURS and current_gap >= old_gap + 0.03


def evaluate_positions(client) -> list[dict]:
    watcher_state = _load(WATCH_STATE_PATH, {"fair_cache": {}})
    fair_cache = watcher_state.get("fair_cache", {})
    if not isinstance(fair_cache, dict):
        fair_cache = {}

    rows = client.get_positions(limit=200)
    signals = []
    for row in rows:
        pos = _safe_float(row.get("position_fp") if row.get("position_fp") is not None else row.get("position"))
        if pos is None or abs(pos) < 1:
            continue
        side_qty = _position_side(pos)
        if side_qty is None:
            continue
        side, qty = side_qty
        ticker = str(row.get("ticker") or "")
        fair = fair_cache.get(ticker)
        if not ticker or not isinstance(fair, dict) or not _fresh_enough(fair):
            continue

        books = int(fair.get("n_books") or 0)
        yes_fair = _safe_float(fair.get("fair_yes_prob"))
        if yes_fair is None or not (0 < yes_fair < 1):
            continue
        side_fair = yes_fair if side == "YES" else 1.0 - yes_fair

        try:
            market = _market(ticker)
        except Exception:
            continue
        if str(market.get("status") or "").lower() not in {"active", "open"}:
            continue

        sell_bid = _price(market, side, "bid")
        buy_ask = _price(market, side, "ask")
        title = str(market.get("title") or row.get("market_title") or ticker)

        if sell_bid is not None and books >= MIN_BOOKS_SELL:
            gap = sell_bid - side_fair
            if gap >= SELL_OVER_FAIR:
                min_price = max(0.01, round((sell_bid - 0.01) * 100) / 100)
                signals.append({
                    "action": "SELL", "ticker": ticker, "side": side, "shares": qty,
                    "limit_price": min_price, "market_price": sell_bid,
                    "fair_probability": side_fair, "gap": gap, "books": books,
                    "title": title,
                })
                continue

        if buy_ask is not None and books >= MIN_BOOKS_ADD and 0.20 <= buy_ask <= 0.80 and side_fair >= 0.30:
            gap = side_fair - buy_ask
            if gap >= ADD_UNDER_FAIR:
                shares = min(qty, MAX_ADD_SHARES, int(MAX_ADD_RISK // buy_ask))
                if shares >= 1:
                    signals.append({
                        "action": "ADD", "ticker": ticker, "side": side, "shares": shares,
                        "limit_price": buy_ask, "market_price": buy_ask,
                        "fair_probability": side_fair, "gap": gap, "books": books,
                        "title": title,
                    })

    signals.sort(key=lambda s: (0 if s["action"] == "SELL" else 1, -float(s["gap"])))
    return signals


def notify_position_signals(client) -> dict:
    state = _load(SIGNAL_STATE_PATH, {"version": 1, "signals": {}})
    state.setdefault("signals", {})
    base_url = os.getenv("STREAMLIT_APP_URL", "").strip()
    candidates = evaluate_positions(client)
    sent = 0
    details = []

    for s in candidates:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        key = f"{s['ticker']}|{s['side']}|{s['action']}"
        if not _can_repeat(state, key, float(s["gap"])):
            continue

        try:
            token = position_actions.create_position_action_token(
                action=s["action"], ticker=s["ticker"], side=s["side"],
                shares=int(s["shares"]), limit_price=float(s["limit_price"]),
                fair_probability=float(s["fair_probability"]),
                signal_gap=float(s["gap"]), books=int(s["books"]),
            )
            url = position_actions.build_position_action_url(base_url, token) if base_url else None
        except Exception:
            url = None

        if s["action"] == "SELL":
            title = "🚨 PM EDGE — SELL NOW"
            body = (
                f"{s['side']} — {s['title'][:170]}\n"
                f"Position: {s['shares']} shares\n"
                f"Current sell: {s['market_price']*100:.1f}¢\n"
                f"Fresh fair: {s['fair_probability']*100:.1f}%\n"
                f"Market is {s['gap']*100:+.1f} pts above fair ({s['books']} books).\n"
                f"SELL at {s['limit_price']*100:.1f}¢ or better."
            )
            url_title = "Review & sell"
        else:
            title = "🟢 PM EDGE — BUY MORE NOW"
            cost = int(s["shares"]) * float(s["limit_price"])
            body = (
                f"{s['side']} — {s['title'][:170]}\n"
                f"Current buy: {s['market_price']*100:.1f}¢\n"
                f"Fresh fair: {s['fair_probability']*100:.1f}%\n"
                f"Value gap: +{s['gap']*100:.1f} pts ({s['books']} books).\n"
                f"BUY MORE: {s['shares']} shares at {s['limit_price']*100:.1f}¢ max · ${cost:.2f} max."
            )
            url_title = "Review & add"

        if _send(title, body, url=url, url_title=url_title):
            state["signals"][key] = {
                "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "gap": round(float(s["gap"]), 6),
                "price": round(float(s["market_price"]), 6),
                "fair": round(float(s["fair_probability"]), 6),
            }
            sent += 1
            details.append({"action": s["action"], "ticker": s["ticker"], "side": s["side"]})

    _save(SIGNAL_STATE_PATH, state)
    return {"candidates": len(candidates), "sent": sent, "details": details}
