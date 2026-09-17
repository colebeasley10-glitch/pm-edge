"""Mobile approval screen for PM Edge staged bets.

This page validates the signed alert, re-checks the public Kalshi quote, and
shows the exact order details. It never authenticates to Kalshi and cannot place
an order; the user still submits the final real-money order inside Kalshi.
"""
from __future__ import annotations

import html
import json
import os
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components

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


def _price_input(x: float) -> str:
    cents = float(x) * 100.0
    return f"{cents:.2f}".rstrip("0").rstrip(".")


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


def _copy_button(label: str, value: str) -> None:
    """Render a mobile-friendly clipboard button with a legacy fallback."""
    label_html = html.escape(label)
    value_js = json.dumps(str(value))
    components.html(
        f"""
        <button id="copyBtn" onclick="copyValue()" style="
            width:100%; min-height:44px; border:1px solid rgba(128,128,128,.45);
            border-radius:8px; background:#ffffff; color:#111111; font-size:16px;
            font-weight:600; padding:10px 12px; cursor:pointer;">
            {label_html}
        </button>
        <script>
        async function copyValue() {{
            const value = {value_js};
            const btn = document.getElementById('copyBtn');
            try {{
                if (navigator.clipboard && window.isSecureContext) {{
                    await navigator.clipboard.writeText(value);
                }} else {{
                    const ta = document.createElement('textarea');
                    ta.value = value;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.focus();
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                }}
                const old = btn.innerText;
                btn.innerText = 'Copied ✓';
                setTimeout(() => btn.innerText = old, 1200);
            }} catch (err) {{
                btn.innerText = 'Use copy icon below';
            }}
        }}
        </script>
        """,
        height=54,
    )


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
price_input = _price_input(max_price)
action_label = "BUY NOW" if trade["execution"] == "TAKE" else "PLACE LIMIT"

st.subheader(market_title)
st.markdown(f"## {action_label}: BUY {trade['side']}")

c1, c2, c3 = st.columns(3)
c1.metric("Shares", str(trade["shares"]))
c2.metric("Max price", _cents(max_price))
c3.metric("Live ask", _cents(current_ask))

st.markdown("### Quick copy for Kalshi")
st.caption("Tap each button, then paste into Kalshi. Price copies as cents, so 38 means 38¢.")

b1, b2, b3 = st.columns(3)
with b1:
    _copy_button("Copy Ticker", trade["ticker"])
with b2:
    _copy_button("Copy Shares", str(trade["shares"]))
with b3:
    _copy_button("Copy Price", price_input)

st.caption("Fallback if an iPhone copy button does not work: tap the copy icon in one of these boxes.")
f1, f2, f3 = st.columns(3)
with f1:
    st.code(trade["ticker"], language=None)
with f2:
    st.code(str(trade["shares"]), language=None)
with f3:
    st.code(price_input, language=None)

c4, c5 = st.columns(2)
c4.metric("Max loss at limit", _money(trade["max_loss"]))
c5.metric("Fair chance", _pct(trade.get("fair_probability")))

st.caption(
    f"Lane: {trade['lane'].replace('_', ' ').title()} · "
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
