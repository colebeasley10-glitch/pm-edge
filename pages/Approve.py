"""Mobile approval screen for PM Edge staged bets.

This page validates the signed alert, re-checks the public Kalshi quote, and
shows the exact order details. It never authenticates to Kalshi and cannot place
an order; the user still submits the final real-money order inside Kalshi.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import requests
import streamlit as st

# Streamlit Cloud secrets -> environment used by approval_links.
for _name in ("PM_EDGE_APPROVAL_SECRET", "ODDS_API_KEY"):
    try:
        if _name in st.secrets:
            os.environ[_name] = str(st.secrets[_name])
    except Exception:
        pass

import approval_links

KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")

st.set_page_config(page_title="PM Edge — Approve", page_icon="✅", layout="centered")
st.title("PM Edge — Review Bet")
st.caption("Check the bet and live price here, then make the final Submit decision in Kalshi.")


def _as_price(market: dict, side: str) -> float | None:
    prefix = "yes" if side == "YES" else "no"
    for key in (f"{prefix}_ask_dollars", f"{prefix}_ask"):
        value = market.get(key)
        if value in (None, ""):
            continue
        try:
            x = float(value)
        except (TypeError, ValueError):
            continue
        if key.endswith("_ask") and x > 1.0:
            x /= 100.0
        if 0 <= x <= 1:
            return x
    return None


def _money(x: float) -> str:
    return f"${float(x):.2f}"


def _cents(x: float | None) -> str:
    return "—" if x is None else f"{float(x) * 100:.1f}¢"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{float(x) * 100:.1f}%"


def _load_market(ticker: str) -> dict:
    response = requests.get(
        f"{KALSHI_PUBLIC_BASE}/markets/{quote(ticker, safe='')}", timeout=12
    )
    response.raise_for_status()
    payload = response.json()
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        raise RuntimeError("Kalshi returned an unexpected market response")
    return market


def _kalshi_search_url(ticker: str) -> str:
    # Kalshi's iOS app currently may ignore exact web market/deep-link paths and
    # land on Home. The web search URL is still useful in a browser, while the
    # copyable ticker below is the reliable fallback inside the native app.
    return "https://kalshi.com/search?q=" + quote(ticker, safe="-._")


token = str(st.query_params.get("trade", "") or "").strip()
if not token:
    st.error("This approval link is missing its trade token.")
    st.stop()

try:
    trade = approval_links.verify_approval_token(token)
except Exception as exc:
    st.error(f"This approval link is not valid: {exc}")
    st.stop()

try:
    market = _load_market(trade["ticker"])
    quote_error = None
except Exception as exc:
    market = {}
    quote_error = str(exc)

market_title = str(market.get("title") or market.get("subtitle") or trade["ticker"])
current_ask = _as_price(market, trade["side"])
max_price = float(trade["limit_price"])
action_label = "BUY NOW" if trade["execution"] == "TAKE" else "PLACE LIMIT"

st.subheader(market_title)
st.markdown("**Copy this Kalshi ticker if the app opens on Home:**")
st.code(trade["ticker"], language=None)

c1, c2 = st.columns(2)
c1.metric("Side", trade["side"])
c2.metric("Shares", str(trade["shares"]))

c3, c4 = st.columns(2)
c3.metric("PM Edge max price", _cents(max_price))
c4.metric("Live Kalshi ask", _cents(current_ask))

c5, c6 = st.columns(2)
c5.metric("Max loss at limit", _money(trade["max_loss"]))
c6.metric("Fair chance", _pct(trade.get("fair_probability")))

st.caption(
    f"Action: {action_label} · Lane: {trade['lane'].replace('_', ' ').title()} · "
    f"Estimated edge: {_pct(trade.get('net_edge'))} · "
    f"Books: {trade.get('books') if trade.get('books') is not None else '—'}"
)

if quote_error:
    st.warning(
        "I couldn't refresh the live Kalshi quote. Use the ticker above to find the market, "
        "and do not submit above the PM Edge max price."
    )
elif trade["execution"] == "TAKE" and current_ask is not None and current_ask > max_price + 1e-9:
    st.error(
        f"PASS / DO NOT CHASE — this was a BUY NOW play, but the live {trade['side']} ask "
        f"is now {_cents(current_ask)}, above our {_cents(max_price)} maximum."
    )
    st.stop()
elif trade["execution"] == "MAKE" and current_ask is not None and current_ask > max_price + 1e-9:
    st.info(
        f"LIMIT PLAY — the live ask is {_cents(current_ask)}. Do not buy that ask. "
        f"Place your limit at {_cents(max_price)} or lower and wait for a fill."
    )
elif current_ask is not None:
    st.success(
        f"Price check passed: live ask {_cents(current_ask)} is at or below our "
        f"{_cents(max_price)} maximum."
    )

if trade["execution"] == "MAKE":
    st.warning(
        f"In Kalshi, search the ticker above and place a LIMIT order at {_cents(max_price)} "
        "or lower. Do not chase a higher ask."
    )
else:
    st.warning(
        "In Kalshi, search the ticker above, use the exact side shown here, and never pay "
        f"more than {_cents(max_price)}."
    )

st.link_button(
    "OPEN KALSHI / SEARCH",
    _kalshi_search_url(trade["ticker"]),
    type="primary",
    use_container_width=True,
)
st.caption(
    "iPhone note: if Kalshi opens on Home, paste the copied ticker into Kalshi Search. "
    "The native app currently does not reliably preserve exact-market web deep links."
)

with st.expander("Exact order details"):
    st.write(f"**{action_label}: BUY {trade['side']}**")
    st.write(f"**{trade['shares']} shares**")
    st.write(f"**Limit: {_cents(max_price)} or lower**")
    st.write(f"Maximum contract cost at limit: **{_money(trade['max_loss'])}**")
    st.caption("This page cannot submit, modify, or cancel a Kalshi order.")
