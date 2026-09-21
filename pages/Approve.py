"""Mobile approval screen for PM Edge staged bets.

The signed approval token carries the exact approved order. This page refreshes
the public Kalshi quote and, when a separate write-enabled key is configured,
can submit that exact capped order only after the user presses Submit.
"""
from __future__ import annotations

import hashlib
import html
import inspect
import json
import math
import os
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

# Streamlit Cloud secrets -> environment used by approval/signing clients.
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

import approval_links
import approval_guardrails
import signal_tracker
from kalshi_submit import KalshiSubmitClient, KalshiSubmitError

KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
MAX_ORDER_RISK = 10.00
MAX_DAILY_RISK_BASELINE = 0.00
EASTERN = ZoneInfo("America/New_York")

def _effective_daily_risk_limit() -> float:
    # A non-positive baseline means there is no aggregate daily cap.
    return float("inf") if MAX_DAILY_RISK_BASELINE <= 0 else MAX_DAILY_RISK_BASELINE

st.set_page_config(page_title="PM Edge — Approve", page_icon="✅", layout="centered")
st.title("PM Edge — Review & Submit")
st.caption("PM Edge re-checks the live price. Nothing is sent to Kalshi until you press Submit.")
if math.isinf(_effective_daily_risk_limit()):
    st.caption("The PM Edge daily aggregate cap is disabled. The $10 per-order cap still applies.")


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
    return "https://kalshi.com/search?q=" + quote(ticker, safe="-._")


def _copy_button(label: str, value: str) -> None:
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


def _client_order_id(token_text: str, signal_id: str) -> str:
    # Stable across page reloads; the provenance id remains machine-joinable.
    return approval_links.client_order_id(signal_id, token_text)


def _created_today(row: dict) -> bool:
    """Whether a PM Edge order was created today in the user's Eastern day.

    Missing/unparseable timestamps count as today so the $50 cap fails closed.
    """
    raw = str(row.get("created_time") or "").strip()
    if not raw:
        return True
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if created.tzinfo is None:
            return True
        return created.astimezone(EASTERN).date() == datetime.now(EASTERN).date()
    except Exception:
        return True


def _existing_cost(row: dict) -> float:
    try:
        count = float(row.get("initial_count_fp") or row.get("initial_count") or row.get("count") or 0)
    except (TypeError, ValueError):
        return 0.0
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
        return 0.0
    return count * price if count > 0 and 0 < price < 1 else 0.0


def _today_pm_edge_cost(orders: list[dict]) -> float:
    return sum(
        _existing_cost(row)
        for row in orders
        if str(row.get("client_order_id") or "").startswith("pmedge-") and _created_today(row)
    )


def _position_guard_message(reason: str) -> str:
    messages = {
        "event_position_exists": "another position is already open for this event",
        "second_ladder_rung": "a second spread/total rung on the same game and side is blocked",
        "sport_position_cap": "this would exceed 3 open PM Edge positions in the same sport today",
        "sport_risk_cap": "this would exceed 25% of today's risk budget in one sport",
        "daily_open_risk_cap": "this would exceed $40 of open PM Edge risk today",
    }
    return messages.get(str(reason or ""), str(reason or "position guard"))


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
shares = int(trade["shares"])
computed_risk = shares * max_price
price_input = _price_input(max_price)
action_label = "BUY NOW" if trade["execution"] == "TAKE" else "PLACE LIMIT"
client_order_id = _client_order_id(token, trade["signal_id"])

# Fail closed if the signed token's stated risk and independently computed risk disagree.
if abs(float(trade["max_loss"]) - computed_risk) > 0.02:
    st.error("Safety stop: the signed max-loss amount does not match shares × price.")
    st.stop()
if computed_risk > MAX_ORDER_RISK + 1e-9:
    st.error(f"Safety stop: this order would risk {_money(computed_risk)}, above the {_money(MAX_ORDER_RISK)} per-order cap.")
    st.stop()

st.subheader(market_title)
st.markdown(f"## {action_label}: BUY {trade['side']}")

c1, c2, c3 = st.columns(3)
c1.metric("Shares", str(shares))
c2.metric("Max price", _cents(max_price))
c3.metric("Live ask", _cents(current_ask))

c4, c5 = st.columns(2)
c4.metric("Max order cost", _money(computed_risk))
c5.metric("Fair chance", _pct(trade.get("fair_probability")))
st.caption(
    f"Lane: {trade['lane'].replace('_', ' ').title()} · "
    f"Estimated edge: {_pct(trade.get('net_edge'))} · "
    f"Books: {trade.get('books') if trade.get('books') is not None else '—'}"
)

submit_price_ok = quote_error is None and current_ask is not None
if quote_error:
    st.error("LIVE CHECK FAILED — one-tap Submit is disabled until the Kalshi quote refresh succeeds.")
elif trade["execution"] == "TAKE" and current_ask is not None and current_ask > max_price + 1e-9:
    st.error(
        f"PASS / DO NOT CHASE — live {trade['side']} ask is {_cents(current_ask)}, "
        f"above our {_cents(max_price)} maximum."
    )
    submit_price_ok = False
elif trade["execution"] == "MAKE" and current_ask is not None and current_ask > max_price + 1e-9:
    st.info(
        f"LIMIT PLAY — live ask is {_cents(current_ask)}. Submit will rest a post-only limit at "
        f"{_cents(max_price)}; it will not chase the ask."
    )
else:
    st.success(
        f"Price check passed: live ask {_cents(current_ask)} is at or below our "
        f"{_cents(max_price)} maximum."
    )

try:
    submit_client = KalshiSubmitClient.from_env()
    submit_config_error = None
except Exception as exc:
    submit_client = None
    submit_config_error = str(exc)

if submit_client is None:
    st.warning(
        "One-tap Submit is not connected yet. Add the separate write-enabled Kalshi key to "
        "Streamlit Secrets, then this page will show the Submit button."
    )
    if submit_config_error:
        st.caption(f"Key configuration error: {submit_config_error}")
else:
    orders = None
    positions = None
    risk_check_error = None
    try:
        orders = submit_client.get_orders(limit=200)
        positions = submit_client.get_positions(limit=200)
    except Exception as exc:
        risk_check_error = str(exc)

    existing = None
    daily_submitted = 0.0
    position_guard = None
    if orders is not None and positions is not None:
        for row in orders:
            if str(row.get("client_order_id") or "") == client_order_id:
                existing = row
        daily_submitted = _today_pm_edge_cost(orders)
        position_guard = approval_guardrails.evaluate_position_guard(
            ticker=trade["ticker"],
            side=trade["side"],
            candidate_risk=computed_risk,
            positions=positions,
            orders=orders,
            now=datetime.now(timezone.utc),
        )

    if existing is not None:
        st.success("This approval has already been submitted to Kalshi. No duplicate order will be sent.")
        status = str(existing.get("status") or "submitted")
        st.caption(f"Kalshi status: {status} · Client order ID: {client_order_id}")
    elif risk_check_error:
        st.error("Safety stop: PM Edge could not verify orders and open positions, so Submit is disabled.")
        st.caption(risk_check_error)
    elif position_guard and position_guard.get("blocked"):
        st.error(f"Position guard stop: {_position_guard_message(position_guard.get('reason'))}.")
    elif (not math.isinf(_effective_daily_risk_limit())) and daily_submitted + computed_risk > _effective_daily_risk_limit() + 1e-9:
        st.error(
            f"Daily cap stop: {_money(daily_submitted)} has already been submitted through PM Edge today. "
            f"This {_money(computed_risk)} order would exceed the {_money(_effective_daily_risk_limit())} daily cap."
        )
    elif submit_price_ok:
        st.markdown("### Ready to submit")
        st.warning(
            f"Pressing the button below sends a REAL Kalshi order: BUY {trade['side']} · "
            f"{shares} shares · max {_cents(max_price)} · max cost {_money(computed_risk)}."
        )
        button_text = f"SUBMIT TO KALSHI — BUY {trade['side']} {shares} @ {_cents(max_price)}"
        if st.button(button_text, type="primary", use_container_width=True):
            try:
                # Refresh the public quote again at the exact moment of approval.
                fresh_market = _load_market(trade["ticker"])
                fresh_ask = _as_price(fresh_market, trade["side"])
                if fresh_ask is None:
                    raise RuntimeError("Could not read a fresh Kalshi ask")
                if trade["execution"] == "TAKE" and fresh_ask > max_price + 1e-9:
                    st.error(
                        f"NOT SUBMITTED — price moved to {_cents(fresh_ask)}, above the "
                        f"{_cents(max_price)} max."
                    )
                    st.stop()

                # Re-read PM Edge orders immediately before POST to enforce replay + daily caps.
                fresh_orders = submit_client.get_orders(limit=200)
                fresh_positions = submit_client.get_positions(limit=200)
                for row in fresh_orders:
                    if str(row.get("client_order_id") or "") == client_order_id:
                        st.success("Already submitted. No duplicate order was sent.")
                        st.stop()
                fresh_guard = approval_guardrails.evaluate_position_guard(
                    ticker=trade["ticker"],
                    side=trade["side"],
                    candidate_risk=computed_risk,
                    positions=fresh_positions,
                    orders=fresh_orders,
                    now=datetime.now(timezone.utc),
                )
                if fresh_guard.get("blocked"):
                    st.error(f"NOT SUBMITTED — {_position_guard_message(fresh_guard.get('reason'))}.")
                    st.stop()
                fresh_daily = _today_pm_edge_cost(fresh_orders)
                fresh_daily_limit = _effective_daily_risk_limit()
                if (not math.isinf(fresh_daily_limit)) and fresh_daily + computed_risk > fresh_daily_limit + 1e-9:
                    st.error(f"NOT SUBMITTED — the {_money(fresh_daily_limit)} PM Edge daily risk cap would be exceeded.")
                    st.stop()

                order_kwargs = {
                    "ticker": trade["ticker"],
                    "outcome_side": trade["side"],
                    "count": shares,
                    "max_price": max_price,
                    "execution": trade["execution"],
                    "client_order_id": client_order_id,
                }

                # TAKE orders do not need an expiry. Keeping those kwargs off the
                # call also makes approval links tolerant of a briefly stale
                # Streamlit worker that still has the older submit-client
                # signature cached during a deploy.
                if trade["execution"] == "MAKE":
                    supported = inspect.signature(submit_client.create_buy_order).parameters
                    if "expires_at" not in supported or "is_live" not in supported:
                        raise RuntimeError(
                            "Submit worker is still on an older order client. "
                            "Reload/restart the app before placing a resting limit."
                        )
                    order_kwargs["expires_at"] = trade.get("event_start_at")
                    order_kwargs["is_live"] = bool(trade.get("is_live", False))

                result = submit_client.create_buy_order(**order_kwargs)
                try:
                    signal_tracker.record_submission(
                        trade,
                        client_order_id=client_order_id,
                        order_result=result,
                    )
                except Exception as ledger_exc:
                    # The order already exists at Kalshi; provenance logging
                    # failure must be visible but must never retry the order.
                    st.caption(
                        f"Provenance ledger warning: {type(ledger_exc).__name__}: {ledger_exc}"
                    )
            except KalshiSubmitError as exc:
                if exc.status_code == 409:
                    st.warning("Kalshi reports this order already exists or conflicts. Check your Kalshi Orders before trying anything else.")
                else:
                    st.error(f"Kalshi rejected the order ({exc.status_code}): {exc}")
            except Exception as exc:
                st.error(f"NOT SUBMITTED — {type(exc).__name__}: {exc}")
            else:
                fill_count = str(result.get("fill_count") or "0.00")
                remaining = str(result.get("remaining_count") or "0.00")
                avg_fill = result.get("average_fill_price")
                st.success("ORDER SENT TO KALSHI ✓")
                st.write(f"**Filled:** {fill_count} · **Remaining:** {remaining}")
                if avg_fill not in (None, ""):
                    try:
                        displayed_fill = float(avg_fill)
                        if trade["side"] == "NO":
                            displayed_fill = 1.0 - displayed_fill
                        st.write(f"**Average fill:** {_cents(displayed_fill)}")
                    except (TypeError, ValueError):
                        pass
                st.caption(f"Kalshi order ID: {result.get('order_id', '—')}")

st.divider()
with st.expander("Manual fallback / exact order details"):
    st.write(f"**{action_label}: BUY {trade['side']}**")
    st.write(f"**{shares} shares**")
    st.write(f"**Limit: {_cents(max_price)} or lower**")
    st.write(f"Maximum contract cost at limit: **{_money(computed_risk)}**")
    st.markdown("**Quick copy for Kalshi:**")
    b1, b2, b3 = st.columns(3)
    with b1:
        _copy_button("Copy Ticker", trade["ticker"])
    with b2:
        _copy_button("Copy Shares", str(shares))
    with b3:
        _copy_button("Copy Price", price_input)
    st.link_button(
        "OPEN KALSHI / SEARCH",
        _kalshi_search_url(trade["ticker"]),
        use_container_width=True,
    )
    st.caption("Manual fallback only. The one-tap button above is the intended flow once the write key is connected.")
