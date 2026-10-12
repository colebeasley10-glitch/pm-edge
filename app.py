"""PM Edge — phone-first dashboard.

Four tabs: Picks (what to bet now), My Bets (open + settled), Results (is it
working?), System (is it running?). Stats count from the fresh start set in
dashboard_data.DEFAULT_STATS_START; older history stays in the ledgers.
"""
from __future__ import annotations

import html
import math
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import dashboard_data
from kalshi_readonly import KalshiReadOnlyClient

st.set_page_config(page_title="PM Edge", page_icon="🎯", layout="centered",
                   initial_sidebar_state="collapsed")

EASTERN = ZoneInfo("America/New_York")
FOCUS_LABELS = {
    "americanfootball_ncaaf": "NCAAF",
    "baseball_kbo": "KBO",
    "baseball_npb": "NPB",
    "cricket_": "Cricket",
}
MIN_EDGE = float(os.getenv("PM_EDGE_TAKER_GATE_MIN_EDGE", "0.03"))
CLV_VERDICT_MIN = 20  # alerts needed before CLV says anything


def _seed_env_from_secrets() -> None:
    names = (
        "PM_EDGE_APPROVAL_SECRET", "ODDS_API_KEY", "STREAMLIT_APP_URL",
        "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY",
        "KALSHI_WRITE_API_KEY_ID", "KALSHI_WRITE_PRIVATE_KEY",
        "PM_EDGE_KALSHI_READONLY_BASE_URL", "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
        "PM_EDGE_STATS_START",
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

# ---------------------------------------------------------------- styling --
st.markdown(
    """
    <style>
    :root { --pe-green:#16a34a; --pe-red:#dc2626; --pe-amber:#d97706; --pe-muted:rgba(128,128,128,.85); }
    .block-container { padding-top: 1rem; padding-bottom: 3rem; max-width: 720px; }
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
    h1, h2, h3 { letter-spacing: -0.01em; }
    .pe-top { display:flex; align-items:center; justify-content:space-between; margin-bottom:.25rem; }
    .pe-brand { font-size:1.6rem; font-weight:800; }
    .pe-status { font-size:.85rem; color:var(--pe-muted); }
    .pe-dot { display:inline-block; width:.55rem; height:.55rem; border-radius:50%; margin-right:.35rem; vertical-align:middle; }
    .pe-kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:.5rem; margin:.75rem 0 .25rem; }
    .pe-kpi { border:1px solid rgba(128,128,128,.25); border-radius:14px; padding:.65rem .75rem; }
    .pe-kpi .l { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--pe-muted); }
    .pe-kpi .v { font-size:1.35rem; font-weight:750; margin-top:.1rem; }
    .pe-card { border:1px solid rgba(128,128,128,.25); border-radius:16px; padding:.9rem 1rem; margin-bottom:.4rem; }
    .pe-card .row { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
    .pe-title { font-weight:700; font-size:1.02rem; line-height:1.3; }
    .pe-sub { font-size:.82rem; color:var(--pe-muted); margin-top:.15rem; }
    .pe-big { font-size:1.5rem; font-weight:800; text-align:right; white-space:nowrap; }
    .pe-tag { display:inline-block; padding:.08rem .5rem; border-radius:999px; font-size:.72rem; font-weight:650;
              border:1px solid rgba(128,128,128,.35); margin-right:.3rem; }
    .pe-yes { background:rgba(22,163,74,.12); border-color:rgba(22,163,74,.45); }
    .pe-no  { background:rgba(220,38,38,.10); border-color:rgba(220,38,38,.40); }
    .pe-stats { display:flex; gap:1.1rem; margin-top:.6rem; font-size:.85rem; }
    .pe-stats b { display:block; font-size:1rem; }
    .pe-win { color:var(--pe-green); } .pe-loss { color:var(--pe-red); }
    .pe-empty { text-align:center; padding:2rem 1rem; border:1px dashed rgba(128,128,128,.35); border-radius:16px; }
    .pe-empty .e { font-size:2rem; }
    .pe-verdict { border-radius:14px; padding:.8rem 1rem; margin:.5rem 0 1rem; border:1px solid rgba(128,128,128,.3); }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- helpers --
def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def money(value, signed=False) -> str:
    x = _num(value)
    if x is None:
        return "—"
    s = f"${abs(x):,.2f}"
    if signed:
        return ("+" if x >= 0 else "−") + s
    return ("−" if x < 0 else "") + s


def pct(value, signed=False) -> str:
    x = _num(value)
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def cents(value) -> str:
    x = _num(value)
    return "—" if x is None else f"{x * 100:.0f}¢"


def esc(value) -> str:
    return html.escape(str(value if value is not None and str(value) != "nan" else ""))


def _when(value) -> datetime | None:
    try:
        ts = pd.to_datetime(value, utc=True)
        return None if pd.isna(ts) else ts.to_pydatetime()
    except Exception:
        return None


def ago(value) -> str:
    dt = _when(value)
    if not dt:
        return "never"
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def starts(value) -> str:
    dt = _when(value)
    if not dt:
        return "Start time unknown"
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    local = dt.astimezone(EASTERN).strftime("%a %-I:%M %p")
    if secs <= 0:
        return f"Started · {local}"
    h, m = int(secs // 3600), int(secs % 3600 // 60)
    return f"Starts in {h}h {m}m · {local}" if h else f"Starts in {m}m · {local}"


def app_root() -> str | None:
    try:
        current = str(st.context.url or "").strip()
        parsed = urlsplit(current)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return os.getenv("STREAMLIT_APP_URL", "").strip() or None


def focus_label() -> str:
    raw = os.getenv("PM_EDGE_FOCUS_SPORTS", ",".join(FOCUS_LABELS))
    labels = [FOCUS_LABELS.get(s.strip(), s.strip()) for s in raw.split(",") if s.strip()]
    return " · ".join(labels) if labels else "All sports"


def side_tag(side: str) -> str:
    side = str(side or "").upper()
    cls = "pe-yes" if side == "YES" else "pe-no"
    return f"<span class='pe-tag {cls}'>BUY {esc(side)}</span>"


def empty(emoji: str, title: str, body: str) -> None:
    st.markdown(
        f"<div class='pe-empty'><div class='e'>{emoji}</div><b>{esc(title)}</b>"
        f"<div class='pe-sub'>{esc(body)}</div></div>",
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------- data --
@st.cache_data(ttl=60, show_spinner=False)
def load():
    data = dashboard_data.load_data()
    data["orders"] = dashboard_data.enrich_orders(data["orders"], data["signals"])
    return data, dashboard_data.read_state()


@st.cache_data(ttl=30, show_spinner=False)
def kalshi_status():
    client = KalshiReadOnlyClient.from_env()
    if client is None:
        return {"connected": False, "error": "Kalshi keys not set in Streamlit secrets."}
    try:
        client.get_positions(limit=1)
        return {"connected": True, "error": ""}
    except Exception as exc:
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}


data, state = load()
signals, orders, clv = data["signals"], data["orders"], data["clv"]
summary = dashboard_data.actual_summary(orders)
picks = dashboard_data.new_signals(signals, orders)
open_bets = dashboard_data.open_order_rows(orders)
open_bets = open_bets[~open_bets.get("status", pd.Series(dtype=str)).fillna("").astype(str)
                      .isin(["awaiting_approval", "expired_stale_edge"])] if not open_bets.empty else open_bets
start = dashboard_data.stats_start()
last_run = (state.get("daily_usage") or {}).get("date")
last_activity = dashboard_data.last_model_activity(data) or _when(state.get("events_cache", {}).get("fetched_at"))
clv_vals = pd.to_numeric(clv.get("clv_cents", pd.Series(dtype=float)), errors="coerce").dropna()

# ----------------------------------------------------------------- header --
running = last_activity is not None and (datetime.now(timezone.utc) - last_activity).total_seconds() < 3 * 3600
dot = "#16a34a" if running else "#d97706"
st.markdown(
    f"""
    <div class='pe-top'><div class='pe-brand'>🎯 PM Edge</div></div>
    <div class='pe-status'><span class='pe-dot' style='background:{dot}'></span>
    {'Scanning' if running else 'Waiting for data'} {esc(focus_label())} · last activity {esc(ago(last_activity))}</div>
    <div class='pe-kpis'>
      <div class='pe-kpi'><div class='l'>Profit</div>
        <div class='v {"pe-win" if summary["net_pl"] > 0 else "pe-loss" if summary["net_pl"] < 0 else ""}'>{money(summary["net_pl"], signed=True)}</div></div>
      <div class='pe-kpi'><div class='l'>Record</div><div class='v'>{esc(summary["record"])}</div></div>
      <div class='pe-kpi'><div class='l'>Beat close</div>
        <div class='v'>{(f"{clv_vals.mean():+.1f}¢") if len(clv_vals) else "—"}</div></div>
    </div>
    <div class='pe-status'>Tracking since {esc(start.astimezone(EASTERN).strftime("%b %-d") if start else "the beginning")}</div>
    """,
    unsafe_allow_html=True,
)

tab_picks, tab_bets, tab_results, tab_system = st.tabs(
    [f"Picks ({len(picks)})", f"My bets ({len(open_bets)})", "Results", "System"]
)

# ------------------------------------------------------------------ picks --
with tab_picks:
    if picks.empty:
        empty("🧘", "No picks right now",
              f"PM Edge only alerts when there's {MIN_EDGE:.0%}+ edge at the price you can actually buy. "
              "Expect a few per week, mostly NCAAF Saturdays. You'll get a push notification.")
    for row in picks.to_dict("records"):
        order = dashboard_data.size_signal(row) or {}
        price = row.get("recommended_limit")
        st.markdown(
            f"""
            <div class='pe-card'>
              <div class='row'>
                <div>
                  {side_tag(row.get('side'))}<span class='pe-tag'>{esc(row.get('sport_title') or 'Sports')}</span>
                  <div class='pe-title' style='margin-top:.35rem'>{esc(row.get('market_title') or row.get('ticker'))}</div>
                  <div class='pe-sub'>{esc(starts(row.get('commence_time')))}</div>
                </div>
                <div class='pe-big'>{cents(price)}</div>
              </div>
              <div class='pe-stats'>
                <div>Edge<b class='pe-win'>{pct(row.get('net_edge'), signed=True)}</b></div>
                <div>True odds<b>{pct(row.get('fair_probability'))}</b></div>
                <div>Max price<b>{cents(row.get('hard_max'))}</b></div>
                <div>Stake<b>{f"{order.get('shares')} @ {money(order.get('max_loss'))}" if order else "—"}</b></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        url = dashboard_data.approval_url_for_signal(row, base_url=app_root())
        if url:
            st.link_button("Review & place bet", url, type="primary", width="stretch")
        st.write("")

# --------------------------------------------------------------- my bets --
with tab_bets:
    st.markdown("##### Open")
    if open_bets.empty:
        empty("📭", "No open bets", "Bets you place from a pick show up here until they settle.")
    for row in open_bets.to_dict("records"):
        st.markdown(
            f"""
            <div class='pe-card'><div class='row'>
              <div>{side_tag(row.get('side'))}
                <div class='pe-title' style='margin-top:.35rem'>{esc(row.get('market_title') or row.get('ticker'))}</div>
                <div class='pe-sub'>{esc(starts(row.get('commence_time')))} · {esc(str(row.get('status') or '').replace('_', ' '))}</div>
              </div>
              <div style='text-align:right'><div class='pe-big'>{money(row.get('entry_cost'))}</div>
                <div class='pe-sub'>{esc(row.get('filled_shares') or row.get('requested_shares') or '')} @ {cents(row.get('average_fill_price') or row.get('limit_price'))}</div></div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )

    settled = orders[orders.get("status", pd.Series(dtype=str)).fillna("").astype(str)
                     .str.startswith(("settled_", "closed_early_"))] if not orders.empty else orders
    st.markdown("##### Settled")
    if settled.empty:
        st.caption("Nothing settled yet.")
    else:
        view = settled.sort_values("trade_date", ascending=False).head(50)
        st.dataframe(
            pd.DataFrame({
                "Date": view["trade_date"],
                "Bet": view.get("market_title", view["ticker"]).fillna(view["ticker"]),
                "Side": view["side"],
                "Result": view["outcome"].fillna("").str.title(),
                "P/L": pd.to_numeric(view["net_pl"], errors="coerce").round(2),
            }),
            hide_index=True, width="stretch",
            column_config={"P/L": st.column_config.NumberColumn(format="$%.2f")},
        )

# ---------------------------------------------------------------- results --
with tab_results:
    n_clv = len(clv_vals)
    if n_clv < CLV_VERDICT_MIN:
        verdict = ("⏳", "Too early to tell",
                   f"{n_clv} of {CLV_VERDICT_MIN} alerts tracked. The real test is whether our price beats the "
                   "closing price, not short-term wins and losses.")
    elif clv_vals.mean() > 0.5:
        verdict = ("✅", "The edge looks real",
                   f"Our alerts beat the closing price by {clv_vals.mean():+.1f}¢ on average over {n_clv} alerts.")
    else:
        verdict = ("⚠️", "No edge yet",
                   f"Our alerts average {clv_vals.mean():+.1f}¢ vs the close over {n_clv} alerts. Keep stakes small.")
    st.markdown(
        f"<div class='pe-verdict'><b>{verdict[0]} {esc(verdict[1])}</b><div class='pe-sub'>{esc(verdict[2])}</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""<div class='pe-kpis'>
          <div class='pe-kpi'><div class='l'>Risked</div><div class='v'>{money(summary["risked"])}</div></div>
          <div class='pe-kpi'><div class='l'>ROI</div><div class='v'>{pct(summary["roi"], signed=True)}</div></div>
          <div class='pe-kpi'><div class='l'>Settled</div><div class='v'>{summary["settled"]}</div></div>
        </div>""",
        unsafe_allow_html=True,
    )

    if not settled.empty:
        curve = settled.copy()
        curve["when"] = pd.to_datetime(curve["settled_at"].fillna(curve["trade_date"]), errors="coerce", utc=True)
        curve = curve.dropna(subset=["when"]).sort_values("when")
        curve["Profit"] = pd.to_numeric(curve["net_pl"], errors="coerce").fillna(0).cumsum()
        st.markdown("##### Profit over time")
        st.line_chart(curve.set_index("when")["Profit"], height=220, color="#16a34a")

    if not settled.empty and "sport_title" in settled.columns:
        by = dashboard_data.aggregate_performance(settled, "sport_title")
        if not by.empty:
            st.markdown("##### By sport")
            st.dataframe(
                by.rename(columns={"sport_title": "Sport", "bets": "Bets", "record": "Record",
                                   "net_pl": "P/L", "roi": "ROI"})[["Sport", "Bets", "Record", "P/L", "ROI"]],
                hide_index=True, width="stretch",
                column_config={"P/L": st.column_config.NumberColumn(format="$%.2f"),
                               "ROI": st.column_config.NumberColumn(format="percent")},
            )
    if settled.empty and n_clv == 0:
        empty("📈", "Results start with your first pick", "Profit, record and closing-line value fill in here.")

# ----------------------------------------------------------------- system --
with tab_system:
    k = kalshi_status()
    credits = (state.get("daily_usage") or {}).get("credits")
    breaker = bool((state.get("drawdown_circuit") or {}).get("active"))
    rows = [
        ("Kalshi account", "🟢 Connected" if k["connected"] else "🟠 Not connected"),
        ("Scanner", ("🟢 Running" if running else "🟠 No activity in 3h") + f" · {ago(last_activity)}"),
        ("Sports in focus", focus_label()),
        ("Edge required", f"{MIN_EDGE:.0%}+ at the live price, after fees"),
        ("Odds credits used today", f"{credits if credits is not None else 0} of {os.getenv('PM_EDGE_ODDS_DAILY_BUDGET', '14')}"),
        ("Loss breaker", "🔴 Active — alerts paused" if breaker else "🟢 Clear"),
    ]
    st.markdown(
        "<div class='pe-card'>" + "".join(
            f"<div class='row' style='padding:.45rem 0;border-bottom:1px solid rgba(128,128,128,.15)'>"
            f"<span class='pe-sub' style='margin:0'>{esc(label)}</span>"
            f"<b style='text-align:right'>{esc(value)}</b></div>"
            for label, value in rows
        ) + "</div>",
        unsafe_allow_html=True,
    )
    if k.get("error") and not k["connected"]:
        st.caption(k["error"])
    st.divider()
    if st.button("Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.page_link("pages/Kalshi_Connection_Check.py", label="Kalshi connection check", icon="🔌")
    st.caption("Runs every 30 minutes on GitHub Actions. Alerts go to Pushover. "
               "Old pre-reset history is kept in the ledgers and archive/ folder.")
