"""Explicit one-tap execution page for PM Edge position signals.

A strong signal may prepare an ADD or SELL order, but this page never submits
until the user presses the real-money confirmation button.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st

for _name in (
    "PM_EDGE_APPROVAL_SECRET",
    "ODDS_API_KEY",
    "KALSHI_WRITE_API_KEY_ID",
    "KALSHI_WRITE_PRIVATE_KEY",
    "PM_EDGE_KALSHI_WRITE_BASE_URL",
):
    try:
        if _name in st.secrets:
            os.environ[_name] = str(st.secrets[_name])
    except Exception:
        pass

import position_actions
from kalshi_submit import KalshiSubmitClient, KalshiSubmitError

PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
MAX_ADD_RISK = 5.00
MAX_DAILY_RISK = 50.00
EASTERN = ZoneInfo("America/New_York")

st.set_page_config(page_title="PM Edge — Position Action", page_icon="🎯", layout="centered")
st.title("PM Edge — Position Action")
st.caption("Nothing is sent to Kalshi until you press the button below.")


def _market(ticker: str) -> dict:
    r = requests.get(f"{PUBLIC_BASE}/markets/{quote(ticker, safe='')}", timeout=12)
    r.raise_for_status()
    p = r.json()
    m = p.get("market") if isinstance(p, dict) else None
    if not isinstance(m, dict):
        raise RuntimeError("Unexpected Kalshi market response")
    return m


def _px(m: dict, side: str, kind: str) -> float | None:
    prefix = "yes" if side == "YES" else "no"
    for key in (f"{prefix}_{kind}_dollars", f"{prefix}_{kind}"):
        v = m.get(key)
        if v in (None, ""):
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if not key.endswith("_dollars") and x > 1:
            x /= 100
        if 0 < x < 1:
            return x
    return None


def _cents(x) -> str:
    return "—" if x is None else f"{float(x)*100:.1f}¢"


def _client_order_id(token: str) -> str:
    return "pmedge-pos-" + hashlib.sha256(str(token).encode()).hexdigest()[:24]


def _created_today(row: dict) -> bool:
    raw = str(row.get("created_time") or "").strip()
    if not raw:
        return True
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d.astimezone(EASTERN).date() == datetime.now(EASTERN).date()
    except Exception:
        return True


def _order_cost(row: dict) -> float:
    try:
        count = float(row.get("initial_count_fp") or row.get("initial_count") or row.get("count") or 0)
    except Exception:
        return 0.0
    outcome = str(row.get("outcome_side") or "").lower()
    try:
        if outcome == "yes":
            p = float(row.get("yes_price_dollars") or 0)
        elif outcome == "no":
            p = float(row.get("no_price_dollars") or 0)
        else:
            side = str(row.get("side") or "").lower()
            p = float(row.get("yes_price_dollars") or 0) if side == "yes" else float(row.get("no_price_dollars") or 0)
    except Exception:
        return 0.0
    return count * p if count > 0 and 0 < p < 1 else 0.0


def _find_position(client, ticker: str) -> tuple[str | None, int]:
    for row in client.get_positions(limit=200):
        if str(row.get("ticker") or "") != ticker:
            continue
        try:
            q = float(row.get("position_fp") if row.get("position_fp") is not None else row.get("position") or 0)
        except Exception:
            q = 0
        if q > 0:
            return "YES", int(q)
        if q < 0:
            return "NO", int(abs(q))
    return None, 0


token = str(st.query_params.get("signal", "") or "").strip()
if not token:
    st.error("Missing position signal.")
    st.stop()

try:
    action = position_actions.verify_position_action_token(token)
except Exception as exc:
    st.error(f"Invalid or expired signal: {exc}")
    st.stop()

try:
    client = KalshiSubmitClient.from_env()
except Exception as exc:
    client = None
    st.error(f"Kalshi connection error: {exc}")

if client is None:
    st.error("Write-enabled Kalshi connection is not configured.")
    st.stop()

ticker = action["ticker"]
side = action["side"]
shares = int(action["shares"])
limit_price = float(action["limit_price"])
client_order_id = _client_order_id(token)

try:
    market = _market(ticker)
    current_bid = _px(market, side, "bid")
    current_ask = _px(market, side, "ask")
    position_side, position_qty = _find_position(client, ticker)
    orders = client.get_orders(limit=200)
except Exception as exc:
    st.error(f"Live safety check failed: {exc}")
    st.stop()

st.subheader(str(market.get("title") or ticker))
st.write(f"**Signal:** {action['action']} {side}")
c1, c2, c3 = st.columns(3)
c1.metric("Shares", str(shares))
c2.metric("Fresh fair", f"{action['fair_probability']*100:.1f}%")
c3.metric("Signal gap", f"{action['signal_gap']*100:+.1f} pts")
st.caption(f"Sportsbooks used: {action['books']}")

existing = next((o for o in orders if str(o.get("client_order_id") or "") == client_order_id), None)
if existing is not None:
    st.success("This signal has already been submitted to Kalshi. No duplicate order will be sent.")
    st.stop()

if position_side != side or position_qty < 1:
    st.error("Safety stop: that position is no longer open on the same side.")
    st.stop()

if action["action"] == "SELL":
    st.metric("Current executable sell", _cents(current_bid))
    st.metric("Minimum sell price", _cents(limit_price))
    if shares > position_qty:
        st.error("Safety stop: requested sell is larger than the current position.")
        st.stop()
    if current_bid is None or current_bid + 1e-9 < limit_price:
        st.warning("SELL SIGNAL EXPIRED — current sell price fell below the approved minimum.")
        st.stop()

    st.warning(
        f"Pressing this sends a REAL SELL order for {shares} {side} shares at "
        f"{_cents(limit_price)} or better."
    )
    if st.button(f"SUBMIT SELL — {shares} {side} @ {_cents(limit_price)}+", type="primary", use_container_width=True):
        try:
            fresh = _market(ticker)
            fresh_bid = _px(fresh, side, "bid")
            fresh_side, fresh_qty = _find_position(client, ticker)
            if fresh_side != side or fresh_qty < shares:
                raise RuntimeError("Position changed; sell was not submitted")
            if fresh_bid is None or fresh_bid + 1e-9 < limit_price:
                raise RuntimeError("Sell price moved below the approved minimum")
            result = client.create_sell_order(
                ticker=ticker, outcome_side=side, count=shares,
                min_price=limit_price, client_order_id=client_order_id,
            )
        except KalshiSubmitError as exc:
            st.error(f"Kalshi rejected the sell ({exc.status_code}): {exc}")
        except Exception as exc:
            st.error(f"NOT SUBMITTED — {exc}")
        else:
            st.success("SELL ORDER SENT TO KALSHI ✓")
            st.write(f"**Filled:** {result.get('fill_count','0.00')} · **Remaining:** {result.get('remaining_count','0.00')}")

else:
    max_cost = shares * limit_price
    st.metric("Current buy", _cents(current_ask))
    st.metric("Maximum buy price", _cents(limit_price))
    if max_cost > MAX_ADD_RISK + 1e-9:
        st.error("Safety stop: add-on exceeds the $5 add-risk cap.")
        st.stop()
    if current_ask is None or current_ask > limit_price + 1e-9:
        st.warning("ADD SIGNAL EXPIRED — current ask moved above the approved maximum.")
        st.stop()

    daily = sum(
        _order_cost(o) for o in orders
        if str(o.get("client_order_id") or "").startswith("pmedge-") and _created_today(o)
    )
    if daily + max_cost > MAX_DAILY_RISK + 1e-9:
        st.error("Daily PM Edge risk cap would be exceeded.")
        st.stop()

    st.warning(
        f"Pressing this sends a REAL ADD order for {shares} {side} shares at "
        f"{_cents(limit_price)} max (${max_cost:.2f} max)."
    )
    if st.button(f"SUBMIT ADD — {shares} {side} @ ≤{_cents(limit_price)}", type="primary", use_container_width=True):
        try:
            fresh = _market(ticker)
            fresh_ask = _px(fresh, side, "ask")
            fresh_side, fresh_qty = _find_position(client, ticker)
            if fresh_side != side or fresh_qty < 1:
                raise RuntimeError("Position changed; add was not submitted")
            if fresh_ask is None or fresh_ask > limit_price + 1e-9:
                raise RuntimeError("Buy price moved above the approved maximum")
            fresh_orders = client.get_orders(limit=200)
            if any(str(o.get("client_order_id") or "") == client_order_id for o in fresh_orders):
                st.success("Already submitted. No duplicate order was sent.")
                st.stop()
            fresh_daily = sum(
                _order_cost(o) for o in fresh_orders
                if str(o.get("client_order_id") or "").startswith("pmedge-") and _created_today(o)
            )
            if fresh_daily + max_cost > MAX_DAILY_RISK + 1e-9:
                raise RuntimeError("Daily risk cap would be exceeded")
            result = client.create_buy_order(
                ticker=ticker, outcome_side=side, count=shares,
                max_price=limit_price, execution="TAKE",
                client_order_id=client_order_id,
            )
        except KalshiSubmitError as exc:
            st.error(f"Kalshi rejected the add ({exc.status_code}): {exc}")
        except Exception as exc:
            st.error(f"NOT SUBMITTED — {exc}")
        else:
            st.success("ADD ORDER SENT TO KALSHI ✓")
            st.write(f"**Filled:** {result.get('fill_count','0.00')} · **Remaining:** {result.get('remaining_count','0.00')}")

st.caption("Signal execution is always approval-first. PM Edge does not submit ADD or SELL orders by itself.")
