from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import dashboard_data
from kalshi_readonly import KalshiReadOnlyClient

st.set_page_config(page_title="PM Edge Hub", page_icon="📊", layout="wide")


def _seed_env_from_secrets() -> None:
    names = (
        "PM_EDGE_APPROVAL_SECRET",
        "STREAMLIT_APP_URL",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY",
        "KALSHI_WRITE_API_KEY_ID",
        "KALSHI_WRITE_PRIVATE_KEY",
        "PM_EDGE_KALSHI_READONLY_BASE_URL",
        "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    )
    for name in names:
        try:
            if name in st.secrets and not os.getenv(name):
                os.environ[name] = str(st.secrets[name])
        except Exception:
            pass

    if not os.getenv("KALSHI_API_KEY_ID") and os.getenv("KALSHI_WRITE_API_KEY_ID"):
        os.environ["KALSHI_API_KEY_ID"] = os.getenv("KALSHI_WRITE_API_KEY_ID", "")
    if not os.getenv("KALSHI_PRIVATE_KEY") and os.getenv("KALSHI_WRITE_PRIVATE_KEY"):
        os.environ["KALSHI_PRIVATE_KEY"] = os.getenv("KALSHI_WRITE_PRIVATE_KEY", "")


_seed_env_from_secrets()

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.1rem; padding-bottom: 2rem;}
    [data-testid="stMetricValue"] {font-size: 1.65rem;}
    .pmedge-pill {
        display:inline-block; padding:.16rem .48rem; border-radius:999px;
        border:1px solid rgba(128,128,128,.35); font-size:.78rem; margin-right:.28rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def money(value) -> str:
    try:
        return "$" + f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def cents(value) -> str:
    try:
        return f"{float(value) * 100:.1f}¢"
    except (TypeError, ValueError):
        return "—"


def whole_number(value, default: int = 0) -> int:
    try:
        x = float(value)
        if pd.isna(x):
            return default
        return int(x)
    except (TypeError, ValueError, OverflowError):
        return default


def age_text(value) -> str:
    try:
        dt = pd.to_datetime(value, utc=True).to_pydatetime()
        mins = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 60))
        if mins < 60:
            return f"{mins}m"
        return f"{mins / 60:.1f}h"
    except Exception:
        return "—"


@st.cache_data(ttl=20, show_spinner=False)
def _account_snapshot():
    client = KalshiReadOnlyClient.from_env()
    if client is None:
        return {
            "connected": False,
            "orders": [],
            "positions": [],
            "error": "Kalshi read credentials are not configured in Streamlit.",
        }
    try:
        return {
            "connected": True,
            "orders": client.get_orders(limit=200),
            "positions": client.get_positions(limit=200),
            "error": "",
        }
    except Exception as exc:
        return {
            "connected": False,
            "orders": [],
            "positions": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


@st.cache_data(ttl=30, show_spinner=False)
def _market_snapshot(ticker: str):
    try:
        return dashboard_data.market_snapshot(ticker)
    except Exception as exc:
        return {"ticker": ticker, "status": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def _data():
    data = dashboard_data.load_data()
    data["orders"] = dashboard_data.enrich_orders(data["orders"], data["signals"])
    return data


def _signals_only(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals
    return signals[
        signals["record_type"].fillna("signal").astype(str).eq("signal")
    ].copy()


def _sport_filter(df: pd.DataFrame, key: str, selected: str) -> pd.DataFrame:
    if df.empty or selected == "All" or key not in df.columns:
        return df
    return df[df[key].fillna("Unknown").astype(str).eq(selected)]


def _lane_label(value: str) -> str:
    value = str(value or "").lower()
    return {
        "price_fish": "Price Fish",
        "value": "Value",
        "primary": "Primary",
        "approval_link": "Legacy / Approval",
    }.get(value, value.replace("_", " ").title() or "Unknown")


data = _data()
signals = data["signals"]
orders = data["orders"]
candidates = data["candidates"]
outcomes = data["signal_outcomes"]
state = dashboard_data.read_state()
account = _account_snapshot()

actual = dashboard_data.actual_summary(orders)
model = dashboard_data.opportunity_summary(outcomes)
new = dashboard_data.new_signals(signals, orders)
open_orders = dashboard_data.open_order_rows(orders)

st.title("PM Edge Hub")
st.caption(
    "Kalshi execution, live tracking, model performance, Price Fish, and system health in one place."
)

with st.sidebar:
    page = st.radio(
        "Navigate",
        ["Dashboard", "Bet Center", "Live / Open", "History", "Analytics", "System Status"],
        index=0,
    )
    st.divider()
    if st.button("Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data refreshes automatically when the PM Edge ledgers update.")

if page == "Dashboard":
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("P/L", money(actual["net_pl"]))
    c2.metric("ROI", pct(actual["roi"]))
    c3.metric("Record", actual["record"])
    c4.metric("Risked", money(actual["risked"]))
    c5.metric("Open", actual["open"])

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("New bets", len(new))
    c7.metric("Unfilled", actual["unfilled"])
    c8.metric("Model signals settled", model["settled"])
    c9.metric("All-signal ROI*", pct(model["roi"]))
    st.caption(
        "*All-signal ROI is hypothetical full-fill performance at PM Edge's recommended price, not your actual account return."
    )

    left, right = st.columns([1.35, 1])
    with left:
        st.subheader("🔥 New PM Edge bets")
        if new.empty:
            st.success("No unplaced PM Edge alerts are waiting right now.")
        else:
            for row in new.head(5).to_dict("records"):
                with st.container(border=True):
                    a, b = st.columns([3, 1])
                    with a:
                        st.markdown(
                            f"**{row.get('side', '')} — {row.get('market_title') or row.get('ticker')}**"
                        )
                        st.markdown(
                            f"<span class='pmedge-pill'>{row.get('sport_title') or 'Sports'}</span>"
                            f"<span class='pmedge-pill'>{_lane_label(row.get('lane'))}</span>"
                            f"<span class='pmedge-pill'>{str(row.get('market_type') or '').replace('_', ' ').title()}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            f"{dashboard_data.signal_state(row)} · Fair {pct(row.get('fair_probability'))} · "
                            f"Edge +{pct(row.get('net_edge'))} · {whole_number(row.get('books'))} books · "
                            f"fair age {age_text(row.get('fair_fetched_at'))}"
                        )
                    with b:
                        st.metric("Entry", cents(row.get("recommended_limit")))
                        order = dashboard_data.size_signal(row)
                        if order:
                            st.caption(
                                f"{order['shares']} shares · {money(order['max_loss'])} max"
                            )
                    url = dashboard_data.approval_url_for_signal(row)
                    if url:
                        st.link_button(
                            "REVIEW & PLACE BET",
                            url,
                            type="primary",
                            use_container_width=True,
                        )
                    else:
                        st.caption(
                            "Approval link unavailable; open the original PM Edge alert."
                        )

    with right:
        st.subheader("📈 Performance snapshot")
        if orders.empty:
            st.info("No tracked PM Edge orders yet.")
        else:
            lane = dashboard_data.aggregate_performance(orders, "lane")
            if not lane.empty:
                lane = lane.copy()
                lane["lane"] = lane["lane"].map(_lane_label)
                lane["ROI"] = lane["roi"].map(pct)
                lane["P/L"] = lane["net_pl"].map(money)
                st.dataframe(
                    lane[["lane", "bets", "record", "P/L", "ROI"]].rename(
                        columns={"lane": "Category", "bets": "Bets", "record": "Record"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        st.subheader("System")
        last = dashboard_data.last_model_activity(data)
        credits = (state.get("daily_usage") or {}).get("credits")
        breaker = bool((state.get("drawdown_circuit") or {}).get("active"))
        st.write(
            "Kalshi:", "🟢 Connected" if account["connected"] else "🟠 Not connected"
        )
        st.write("Drawdown breaker:", "🔴 ACTIVE" if breaker else "🟢 Clear")
        st.write("Odds credits today:", credits if credits is not None else "—")
        st.write(
            "Last model activity:",
            last.isoformat().replace("+00:00", "Z") if last else "—",
        )

elif page == "Bet Center":
    st.header("Bet Center")
    st.caption(
        "Every fresh PM Edge alert that has not yet been tied to a submitted PM Edge order."
    )

    sigs = _signals_only(signals)
    sports = ["All"] + sorted(
        x
        for x in sigs.get("sport_title", pd.Series(dtype=str))
        .dropna()
        .astype(str)
        .unique()
        if x
    )
    lanes = ["All", "Primary", "Value", "Price Fish"]
    f1, f2 = st.columns(2)
    sport_choice = f1.selectbox("Sport", sports)
    lane_choice = f2.selectbox("Category", lanes)

    view = _sport_filter(new, "sport_title", sport_choice)
    if lane_choice != "All" and not view.empty:
        raw = {
            "Primary": "primary",
            "Value": "value",
            "Price Fish": "price_fish",
        }[lane_choice]
        view = view[view["lane"].fillna("").astype(str).eq(raw)]

    if view.empty:
        st.info("No matching unplaced alerts.")
    else:
        for row in view.to_dict("records"):
            with st.container(border=True):
                top1, top2, top3 = st.columns([3.2, 1, 1])
                top1.markdown(
                    f"### {row.get('side', '')} — {row.get('market_title') or row.get('ticker')}"
                )
                top1.caption(
                    f"{row.get('sport_title') or 'Sports'} · {_lane_label(row.get('lane'))} · "
                    f"{dashboard_data.signal_state(row)}"
                )
                top2.metric("Entry", cents(row.get("recommended_limit")))
                top3.metric("Edge", f"+{pct(row.get('net_edge'))}")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Fair", pct(row.get("fair_probability")))
                m2.metric("Books", int(row.get("books") or 0))
                m3.metric("Fair age", age_text(row.get("fair_fetched_at")))
                m4.metric(
                    "Type",
                    str(row.get("market_type") or "—").replace("_", " ").title(),
                )
                order = dashboard_data.size_signal(row)
                if order:
                    st.caption(
                        f"Suggested: **{order['shares']} shares @ {cents(order['price'])}** · "
                        f"max cost {money(order['max_loss'])} · "
                        f"gross win profit {money(order['gross_profit_if_correct'])}"
                    )
                url = dashboard_data.approval_url_for_signal(row)
                if url:
                    st.link_button(
                        "REVIEW & PLACE BET",
                        url,
                        type="primary",
                        use_container_width=True,
                    )
                else:
                    st.warning(
                        "Could not build the approval link for this signal. Use its original PM Edge notification."
                    )

elif page == "Live / Open":
    st.header("Live / Open")
    st.caption(
        "Placed PM Edge orders that are not yet recorded as settled or unfilled."
    )

    if not account["connected"]:
        st.warning(
            "Kalshi account data is unavailable. Ledger-only tracking is still shown below."
        )
        if account["error"]:
            st.caption(account["error"])

    if open_orders.empty:
        st.info("No open PM Edge orders are currently in the ledger.")
    else:
        sort_col = "trade_date" if "trade_date" in open_orders.columns else open_orders.columns[0]
        for row in open_orders.sort_values(sort_col, ascending=False).to_dict("records"):
            ticker = str(row.get("ticker") or "")
            snap = _market_snapshot(ticker) if ticker else {}
            side = str(row.get("side") or "").upper()
            qty = (
                float(row.get("filled_shares") or 0)
                if pd.notna(row.get("filled_shares"))
                else 0.0
            )
            entry_cost = (
                float(row.get("entry_cost") or 0)
                if pd.notna(row.get("entry_cost"))
                else 0.0
            )
            bid = snap.get("yes_bid") if side == "YES" else snap.get("no_bid")
            current_value = (
                qty * float(bid) if qty > 0 and bid is not None else None
            )
            unrealized = (
                current_value - entry_cost
                if current_value is not None and entry_cost > 0
                else None
            )
            with st.container(border=True):
                x1, x2, x3, x4 = st.columns([3, 1, 1, 1])
                x1.markdown(
                    f"**{side} — {row.get('market_title') or ticker}**"
                )
                x1.caption(
                    f"{row.get('sport_title') or 'Sports'} · {_lane_label(row.get('lane'))} · "
                    f"Kalshi status: {snap.get('status') or row.get('status') or '—'}"
                )
                x2.metric("Risked", money(entry_cost))
                x3.metric("Current value", money(current_value))
                x4.metric("Unrealized", money(unrealized))
                st.caption(
                    f"Filled {qty:g} shares · avg fill {cents(row.get('average_fill_price'))} · "
                    f"current {side} bid {cents(bid)}"
                )

    if account["positions"]:
        st.subheader("Kalshi account positions")
        positions_df = pd.DataFrame(account["positions"])
        cols = [
            c
            for c in [
                "ticker",
                "position_fp",
                "market_exposure_dollars",
                "realized_pnl_dollars",
            ]
            if c in positions_df.columns
        ]
        if cols:
            st.dataframe(
                positions_df[cols], use_container_width=True, hide_index=True
            )

elif page == "History":
    st.header("History")
    if orders.empty:
        st.info("No PM Edge order history is available.")
    else:
        sports = ["All"] + sorted(
            x
            for x in orders.get("sport_title", pd.Series(dtype=str))
            .dropna()
            .astype(str)
            .unique()
            if x and x != "nan"
        )
        lanes = ["All"] + sorted(
            x
            for x in orders.get("lane", pd.Series(dtype=str))
            .dropna()
            .astype(str)
            .unique()
            if x
        )
        h1, h2, h3 = st.columns(3)
        sport_choice = h1.selectbox("Sport", sports)
        lane_choice = h2.selectbox(
            "Category",
            lanes,
            format_func=lambda x: _lane_label(x) if x != "All" else x,
        )
        status_choice = h3.selectbox(
            "Status", ["All", "Wins", "Losses", "Unfilled", "Open"]
        )

        view = _sport_filter(orders, "sport_title", sport_choice)
        if lane_choice != "All":
            view = view[view["lane"].fillna("").astype(str).eq(lane_choice)]
        status = view.get("status", pd.Series("", index=view.index)).fillna("").astype(str)
        if status_choice == "Wins":
            view = view[status.eq("settled_win")]
        elif status_choice == "Losses":
            view = view[status.eq("settled_loss")]
        elif status_choice == "Unfilled":
            view = view[status.eq("unfilled")]
        elif status_choice == "Open":
            view = view[
                ~status.str.startswith("settled_")
                & ~status.str.startswith("closed_early_")
                & ~status.eq("unfilled")
            ]

        display = view.copy()
        if not display.empty:
            if "net_pl" in display.columns:
                display["P/L"] = pd.to_numeric(
                    display["net_pl"], errors="coerce"
                ).map(money)
            if "roi" in display.columns:
                display["ROI"] = pd.to_numeric(
                    display["roi"], errors="coerce"
                ).map(pct)
            display["Category"] = display.get(
                "lane", pd.Series("", index=display.index)
            ).map(_lane_label)
            wanted = [
                "trade_date",
                "sport_title",
                "market_title",
                "side",
                "Category",
                "status",
                "filled_shares",
                "average_fill_price",
                "P/L",
                "ROI",
            ]
            wanted = [c for c in wanted if c in display.columns]
            if "trade_date" in display.columns:
                display = display.sort_values("trade_date", ascending=False)
            st.dataframe(
                display[wanted], use_container_width=True, hide_index=True
            )

elif page == "Analytics":
    st.header("Analytics")
    st.caption(
        "Actual account results and the model's full signal set are kept separate."
    )

    a1, a2 = st.columns(2)
    with a1:
        st.subheader("Your placed bets")
        st.metric("Record", actual["record"])
        st.metric("Net P/L", money(actual["net_pl"]))
        st.metric("ROI", pct(actual["roi"]))
    with a2:
        st.subheader("All PM Edge signals")
        st.metric("Hypothetical record", model["record"])
        st.metric("Hypothetical P/L", money(model["net_pl"]))
        st.metric("Hypothetical ROI", pct(model["roi"]))
        if outcomes.empty:
            st.caption(
                "Signal settlement tracking has just been added; this fills automatically as markets settle."
            )

    st.divider()
    group = st.selectbox(
        "Break actual performance down by",
        ["sport_title", "lane", "market_type"],
    )
    perf = dashboard_data.aggregate_performance(orders, group)
    if perf.empty:
        st.info("Not enough placed-bet data for this breakdown yet.")
    else:
        perf = perf.copy()
        if group == "lane":
            perf[group] = perf[group].map(_lane_label)
        perf["ROI"] = perf["roi"].map(pct)
        perf["P/L"] = perf["net_pl"].map(money)
        st.dataframe(
            perf[[group, "bets", "record", "P/L", "ROI", "risked"]].rename(
                columns={
                    group: "Group",
                    "bets": "Bets",
                    "record": "Record",
                    "risked": "Risked",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Signal funnel")
    funnel = dashboard_data.signal_funnel(candidates, signals, orders)
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Qualified evaluations", funnel["qualified"])
    f2.metric("Alerts sent", funnel["alerts"])
    f3.metric("Orders tracked", funnel["placed"])
    f4.metric("Filled", funnel["filled"])

    if not candidates.empty:
        st.subheader("Opportunity mix")
        mix_col = st.selectbox(
            "Group qualified opportunities by",
            ["lane", "sport_title", "market_type"],
            key="mix",
        )
        if "decision" in candidates.columns:
            qualified = candidates[
                candidates["decision"].fillna("").astype(str).eq("QUALIFIED")
            ]
        else:
            qualified = candidates
        if mix_col in qualified.columns:
            mix = (
                qualified.groupby(mix_col, dropna=False)
                .size()
                .reset_index(name="Qualified")
            )
            if mix_col == "lane":
                mix[mix_col] = mix[mix_col].astype(str).map(_lane_label)
            st.dataframe(
                mix.sort_values("Qualified", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

    if not data["clv"].empty:
        clv = data["clv"].copy()
        clv["clv_cents"] = pd.to_numeric(clv["clv_cents"], errors="coerce")
        st.subheader("Closing-line value")
        st.metric("Average CLV", f"{clv['clv_cents'].mean():+.2f}¢")

elif page == "System Status":
    st.header("System Status")
    last = dashboard_data.last_model_activity(data)
    now = datetime.now(timezone.utc)
    age_minutes = (now - last).total_seconds() / 60 if last else None
    healthy = age_minutes is not None and age_minutes <= 90
    breaker = state.get("drawdown_circuit") or {}
    usage = state.get("daily_usage") or {}

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Watcher", "HEALTHY" if healthy else "CHECK")
    s2.metric("Kalshi", "CONNECTED" if account["connected"] else "CHECK")
    s3.metric("Odds credits", usage.get("credits", "—"))
    s4.metric("Drawdown breaker", "ACTIVE" if breaker.get("active") else "CLEAR")

    st.write(
        "**Last model activity:**",
        last.isoformat().replace("+00:00", "Z")
        if last
        else "No activity timestamp available",
    )
    st.write("**Odds budget date:**", usage.get("date") or "—")
    if breaker.get("active"):
        st.error(
            f"New alerts are paused. Trailing-20 P/L: {money(breaker.get('trailing_20_pl'))} · "
            f"threshold {money(breaker.get('threshold'))}. Manual re-arm is required."
        )

    st.subheader("Tracking coverage")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Signals", len(_signals_only(signals)))
    t2.metric("Candidate decisions", len(candidates))
    t3.metric("CLV rows", len(data["clv"]))
    t4.metric("Fill-audit rows", len(data["fills"]))

    if account["error"]:
        st.caption(account["error"])

    st.subheader("PR A soak measurements")
    if not orders.empty:
        status = orders.get(
            "status", pd.Series("", index=orders.index)
        ).fillna("").astype(str)
        settled = orders[status.str.startswith("settled_")]
    else:
        settled = pd.DataFrame()
    metadata_pct = None
    if not settled.empty and "fair_probability" in settled.columns:
        metadata_pct = float(settled["fair_probability"].notna().mean())
    clv_coverage = None
    sig_count = len(_signals_only(signals))
    if sig_count:
        clv_coverage = len(data["clv"]) / sig_count
    p1, p2 = st.columns(2)
    p1.metric("Settled fair metadata", pct(metadata_pct))
    p2.metric("CLV coverage", pct(clv_coverage))
    st.caption(
        "The full one-week pass/fail gate remains separate; this page exposes the measurements as they accumulate."
    )
