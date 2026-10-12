"""PM Edge: phone-first dashboard.

Four tabs: Picks (what to bet now), My bets (open + settled), Results (is it
working?), System (is it running?). Stats count from the fresh start set in
dashboard_data.DEFAULT_STATS_START; older history stays in the ledgers.

Look: night-game navy, scoreboard numerals (Archivo Expanded), and picks drawn
as bet slips with a tear line. Gold is reserved for prices and the main action.
"""
from __future__ import annotations

import html
import io
import math
import os
from datetime import datetime, timedelta, timezone
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
    "cricket_": "cricket",
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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..800&display=swap" rel="stylesheet">
    <style>
    :root {
      --bg:#0D1B2A; --panel:#14263A; --line:#24394F; --text:#EAF0F6; --muted:#8DA0B5;
      --gold:#F2C14E; --up:#4CC38A; --down:#F26D5B;
    }
    html, body, .stApp, .stApp p, .stApp span, .stApp div, .stApp button, .stApp label {
      font-family:'Archivo', system-ui, sans-serif !important;
    }
    .stApp { background:var(--bg); color:var(--text); }
    [data-testid="stHeader"] { background:transparent; }
    [data-testid="stSidebar"], [data-testid="collapsedControl"], [data-testid="stDecoration"] { display:none; }
    .block-container { padding-top:4.25rem; padding-bottom:4rem; max-width:640px; }
    .num { font-stretch:125%; font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
    /* header */
    .pe-head { display:flex; align-items:center; justify-content:space-between; }
    .pe-mark { font-stretch:125%; font-weight:800; font-size:1.45rem; letter-spacing:-.02em; }
    .pe-mark i { font-style:normal; color:var(--gold); }
    .pe-pill { font-size:.8rem; font-weight:600; padding:.3rem .7rem; border-radius:999px;
               background:var(--panel); border:1px solid var(--line); }
    .pe-pill b { display:inline-block; width:.5rem; height:.5rem; border-radius:50%; margin-right:.4rem; }
    .pe-lede { color:var(--muted); font-size:.92rem; margin:.5rem 0 1.1rem; line-height:1.45; }
    /* scoreboard */
    .pe-board { background:var(--panel); border:1px solid var(--line); border-radius:18px;
                padding:1rem 1.15rem; display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; }
    .pe-board .k { color:var(--muted); font-size:.82rem; }
    .pe-board .big { font-size:2.4rem; font-weight:800; line-height:1.05; margin-top:.15rem; }
    .pe-board .side { display:flex; gap:1.25rem; text-align:right; }
    .pe-board .side .v { font-size:1.2rem; font-weight:700; margin-top:.1rem; }
    .up { color:var(--up); } .down { color:var(--down); }
    /* tabs */
    .stTabs [data-baseweb="tab-list"] { gap:1.4rem; border-bottom:1px solid var(--line); margin-top:1.1rem; }
    .stTabs [data-baseweb="tab"] { padding:.6rem 0; color:var(--muted); }
    .stTabs [aria-selected="true"] { color:var(--text) !important; }
    .stTabs [data-baseweb="tab-highlight"] { background:var(--gold) !important; }
    .stTabs [data-baseweb="tab-border"] { display:none; }
    /* bet slip */
    .slip { background:var(--panel); border:1px solid var(--line); border-radius:18px; margin:1rem 0 .55rem; position:relative; }
    .slip .top { padding:1rem 1.15rem .9rem; }
    .slip .meta { display:flex; justify-content:space-between; color:var(--muted); font-size:.8rem; }
    .slip .side { font-weight:700; letter-spacing:.02em; }
    .slip .side.yes { color:var(--up); } .slip .side.no { color:var(--down); }
    .slip h4 { font-size:1.08rem; font-weight:650; line-height:1.3; margin:.45rem 0 0; padding:0 !important; color:var(--text); }
    .slip .tear { border-top:1.5px dashed var(--line); margin:0 .9rem; position:relative; }
    .slip .tear:before, .slip .tear:after { content:""; position:absolute; top:-10px; width:20px; height:20px;
        border-radius:50%; background:var(--bg); border:1px solid var(--line); }
    .slip .tear:before { left:-1.55rem; clip-path:inset(0 0 0 50%); }
    .slip .tear:after { right:-1.55rem; clip-path:inset(0 50% 0 0); }
    .slip .bottom { padding:.9rem 1.15rem 1rem; display:grid; grid-template-columns:auto 1fr; gap:1.2rem; align-items:center; }
    .slip .price { font-size:2.6rem; font-weight:800; color:var(--gold); line-height:1; }
    .slip .price small { display:block; font-size:.75rem; font-weight:500; color:var(--muted); font-stretch:100%; margin-top:.3rem; }
    .slip dl { display:grid; grid-template-columns:1fr auto; row-gap:.28rem; column-gap:.75rem; margin:0; font-size:.88rem; }
    .slip dt { color:var(--muted); } .slip dd { margin:0; text-align:right; font-weight:650; }
    /* lists */
    .row-list { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:.2rem 1.1rem; margin:.6rem 0 1rem; }
    .row-list .r { display:flex; justify-content:space-between; gap:1rem; padding:.75rem 0; border-bottom:1px solid var(--line); }
    .row-list .r:last-child { border-bottom:0; }
    .row-list .t { font-weight:600; font-size:.95rem; line-height:1.3; }
    .row-list .s { color:var(--muted); font-size:.8rem; margin-top:.15rem; }
    .row-list .v { font-weight:700; text-align:right; white-space:nowrap; }
    .section { font-size:.95rem; font-weight:700; margin:1.3rem 0 .1rem; }
    .quiet { text-align:center; padding:2.6rem 1.2rem; color:var(--muted); line-height:1.5; }
    .quiet strong { display:block; color:var(--text); font-size:1.1rem; font-weight:700; margin-bottom:.35rem; }
    .verdict { border-left:3px solid var(--gold); padding:.2rem 0 .2rem 1rem; margin:1.2rem 0 1rem; }
    .verdict strong { font-size:1.15rem; }
    .verdict p { color:var(--muted); margin:.3rem 0 0; font-size:.9rem; line-height:1.45; }
    .stButton button, .stLinkButton a { border-radius:12px !important; font-weight:700 !important; padding:.65rem 1rem !important; }
    .stLinkButton a[kind="primary"], .stLinkButton a[data-testid="stBaseLinkButton-primary"] {
      background:var(--gold) !important; color:#1A1405 !important; border:0 !important; }
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


def tone(x) -> str:
    x = _num(x) or 0
    return "up" if x > 0 else "down" if x < 0 else ""


def _when(value) -> datetime | None:
    try:
        ts = pd.to_datetime(value, utc=True)
        return None if pd.isna(ts) else ts.to_pydatetime()
    except Exception:
        return None


def ago(value) -> str:
    dt = _when(value)
    if not dt:
        return "not yet"
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440} days ago"


def local_time(dt: datetime) -> str:
    return dt.astimezone(EASTERN).strftime("%-I:%M %p").lower().replace(" ", "")


def starts(value) -> str:
    dt = _when(value)
    if not dt:
        return "Start time unknown"
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    day = dt.astimezone(EASTERN).strftime("%a")
    if secs <= 0:
        return f"Started {day} {local_time(dt)}"
    h, m = int(secs // 3600), int(secs % 3600 // 60)
    return f"{day} {local_time(dt)}, in {h}h {m}m" if h else f"{day} {local_time(dt)}, in {m}m"


def short_date(value) -> str:
    dt = _when(value)
    return dt.strftime("%b %-d") if dt else ""


def next_scan() -> str:
    now = datetime.now(timezone.utc)
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=30 - now.minute % 30)
    return local_time(nxt)


def app_root() -> str | None:
    try:
        parsed = urlsplit(str(st.context.url or "").strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return os.getenv("STREAMLIT_APP_URL", "").strip() or None


def focus_label() -> str:
    raw = os.getenv("PM_EDGE_FOCUS_SPORTS", ",".join(FOCUS_LABELS))
    labels = [FOCUS_LABELS.get(s.strip(), s.strip()) for s in raw.split(",") if s.strip()]
    if not labels:
        return "every sport"
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def quiet(title: str, body: str) -> None:
    st.markdown(f"<div class='quiet'><strong>{esc(title)}</strong>{esc(body)}</div>", unsafe_allow_html=True)


def rows_html(items) -> str:
    return "<div class='row-list'>" + "".join(
        f"<div class='r'><div><div class='t'>{t}</div><div class='s'>{s}</div></div><div class='v {c}'>{v}</div></div>"
        for t, s, v, c in items
    ) + "</div>"


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
        return {"connected": False, "error": "Add your Kalshi keys in the app's Streamlit secrets."}
    try:
        client.get_positions(limit=1)
        return {"connected": True, "error": ""}
    except Exception as exc:
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}


@st.cache_data(ttl=60, show_spinner=False)
def load_league():
    import json as _json
    from pathlib import Path as _P
    trades = pd.DataFrame()
    status = {}
    try:
        if _P("league_trades.csv").exists():
            trades = pd.read_csv("league_trades.csv")
        if _P("league_status.json").exists():
            status = _json.loads(_P("league_status.json").read_text())
    except Exception:
        pass
    return trades, status


LEAGUE_LABELS = {"weather": "Weather", "polymarket": "Polymarket", "books": "Sportsbooks",
                 "sharp": "Sharp money", "crypto": "Crypto"}
LEAGUE_HOW = {
    "weather": "NWS-station forecasts vs temperature brackets",
    "polymarket": "Same game cheaper on Kalshi than Polymarket",
    "books": "Sportsbook consensus vs Kalshi (the original PM Edge)",
    "sharp": "Follows big trades on Kalshi's tape",
    "crypto": "BTC and ETH volatility model vs hourly markets",
}

REFRESH_COOLDOWN_SECS = 15


def _refresh_live_bets() -> None:
    """Button callback: pull current Kalshi prices for open league bets (read-only, in memory)."""
    last = st.session_state.get("league_live") or {}
    last_at = _when(last.get("checked_at"))
    if last_at and (datetime.now(timezone.utc) - last_at).total_seconds() < REFRESH_COOLDOWN_SECS:
        return
    from league import book as league_book, live as league_live
    from league.kalshi import LeagueKalshi
    _, status_now = load_league()
    try:
        out = league_live.refresh(league_book.load(), LeagueKalshi().markets)
    except Exception as exc:
        st.session_state["league_live_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return
    st.session_state.pop("league_live_error", None)
    if out["open"] and not (out["marked"] or out["settled"]):
        st.session_state["league_live_error"] = "Kalshi didn't return prices just now. Try again in a minute."
        return
    df = pd.read_csv(io.StringIO(pd.DataFrame(out["rows"]).to_csv(index=False)))
    st.session_state["league_live"] = {
        "df": df, "checked_at": out["checked_at"], "settled": out["settled"],
        "base_ran_at": status_now.get("ran_at"),
    }


data, state = load()
league, league_status = load_league()
_live_override = st.session_state.get("league_live")
if _live_override and _live_override.get("base_ran_at") == league_status.get("ran_at"):
    league = _live_override["df"]
else:
    # a newer round landed since the last manual check; its marks are fresher and official
    st.session_state.pop("league_live", None)
    _live_override = None
signals, orders, clv = data["signals"], data["orders"], data["clv"]
summary = dashboard_data.actual_summary(orders)
picks = dashboard_data.new_signals(signals, orders)
open_bets = dashboard_data.open_order_rows(orders)
if not open_bets.empty:
    open_bets = open_bets[~open_bets.get("status", pd.Series(dtype=str)).fillna("").astype(str)
                          .isin(["awaiting_approval", "expired_stale_edge"])]
settled = orders[orders.get("status", pd.Series(dtype=str)).fillna("").astype(str)
                 .str.startswith(("settled_", "closed_early_"))] if not orders.empty else orders
start = dashboard_data.stats_start()
last_activity = dashboard_data.last_model_activity(data) or _when((state.get("events_cache") or {}).get("fetched_at"))
clv_vals = pd.to_numeric(clv.get("clv_cents", pd.Series(dtype=float)), errors="coerce").dropna()
running = last_activity is not None and (datetime.now(timezone.utc) - last_activity).total_seconds() < 3 * 3600

# ----------------------------------------------------------------- header --
since = start.astimezone(EASTERN).strftime("%b %-d") if start else "the start"
lg_status = league.get("status", pd.Series(dtype=str)).fillna("") if not league.empty else pd.Series(dtype=str)
lg_pl = float(pd.to_numeric(league.get("pl"), errors="coerce").fillna(0).sum()) if not league.empty else 0.0
lg_record = f"{int((lg_status == 'won').sum())}-{int((lg_status == 'lost').sum())}"
lg_clv = pd.to_numeric(league.get("clv_cents"), errors="coerce").dropna() if not league.empty else pd.Series(dtype=float)
lg_last = _when(league_status.get("ran_at"))
if lg_last is not None and (datetime.now(timezone.utc) - lg_last).total_seconds() < 75 * 60:
    running = True
st.markdown(
    f"""
    <div class='pe-head'>
      <div class='pe-mark'>PM Edge<i>.</i></div>
      <div class='pe-pill'><b style='background:{"var(--up)" if running else "var(--gold)"}'></b>{"Live" if running else "Idle"}</div>
    </div>
    <div class='pe-lede'>Paper trading all of Kalshi with five strategies. Last round {esc(ago(lg_last))}.</div>
    <div class='pe-board'>
      <div><div class='k'>Paper profit since {esc(since)}</div>
        <div class='big num {tone(lg_pl)}'>{money(lg_pl, signed=True)}</div></div>
      <div class='side'>
        <div><div class='k'>Record</div><div class='v num'>{esc(lg_record)}</div></div>
        <div><div class='k'>Beat close</div><div class='v num {tone(lg_clv.mean() if len(lg_clv) else 0)}'>{(f"{lg_clv.mean():+.1f}¢") if len(lg_clv) else "—"}</div></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_league, tab_picks, tab_bets, tab_results, tab_system = st.tabs(
    ["League", f"Alerts {len(picks)}" if len(picks) else "Alerts", "My bets", "Results", "System"]
)

# ----------------------------------------------------------------- league --
with tab_league:
    if league.empty:
        quiet("The league starts on the next round",
              "Every 30 minutes each strategy looks across Kalshi for mispriced markets and takes $10 paper bets.")
    else:
        rows = []
        for name in LEAGUE_LABELS:
            g = league[league["strategy"] == name]
            stt = g.get("status", pd.Series(dtype=str)).fillna("")
            pl = float(pd.to_numeric(g.get("pl"), errors="coerce").fillna(0).sum()) if not g.empty else 0.0
            clv_g = pd.to_numeric(g.get("clv_cents"), errors="coerce").dropna() if not g.empty else pd.Series(dtype=float)
            slip = pd.Series(dtype=float)
            if not g.empty and "ask_5m" in g:
                slip = ((pd.to_numeric(g["ask_5m"], errors="coerce")
                         - pd.to_numeric(g["entry_price"], errors="coerce")) * 100).dropna()
            rows.append((name, len(g), int((stt == "open").sum()), int((stt == "won").sum()), int((stt == "lost").sum()),
                         pl, clv_g.mean() if len(clv_g) else None, len(clv_g),
                         slip.mean() if len(slip) else None, len(slip)))
        def _ends(r):
            dt = _when(r.get("ends_at") or r.get("close_time"))
            if not dt:
                return "end time unknown"
            secs = (dt - datetime.now(timezone.utc)).total_seconds()
            day = dt.astimezone(EASTERN).strftime("%a")
            if secs <= 0:
                return "waiting on Kalshi to settle"
            if secs < 3600:
                return f"ends in {int(secs // 60)}m"
            if secs < 36 * 3600:
                return f"ends {day} {local_time(dt)}"
            return f"ends {dt.astimezone(EASTERN).strftime('%b %-d')}"

        def _tap_note(r):
            a5, en = _num(r.get("ask_5m")), _num(r.get("entry_price"))
            if a5 is None or en is None:
                return ""
            d = (a5 - en) * 100
            word = "same price" if abs(d) < 0.5 else f"{abs(d):.0f}¢ {'worse' if d > 0 else 'better'}"
            return f", 5 min later {cents(a5)} ({word})"

        def _pick_row(r):
            strat = esc(LEAGUE_LABELS.get(r.get("strategy"), r.get("strategy")))
            title = f"{esc(str(r.get('side') or '').title())} on {esc(r.get('title') or r.get('ticker'))}"
            entry = _num(r.get("entry_price"))
            stt = str(r.get("status") or "")
            if stt == "open":
                mark = _num(r.get("last_mid"))
                contracts = _num(r.get("contracts")) or 0
                if mark is not None and entry is not None:
                    move = (mark - entry) * 100
                    trend = "winning" if move > 0.4 else "losing" if move < -0.4 else "even"
                    value = money(contracts * (mark - entry), signed=True)
                    sub = (f"{strat}, bought {cents(entry)}{esc(_tap_note(r))}, now {cents(mark)} ({trend}), {esc(_ends(r))}")
                    return (title, sub, f"<span class='num'>{value}</span>", tone(move))
                return (title, f"{strat}, bought {cents(entry)}{esc(_tap_note(r))}, {esc(_ends(r))}",
                        f"<span class='num'>{cents(entry)}</span>", "")
            if stt == "void":
                return (title, f"{strat}, market voided, stake returned", "<span class='num'>$0.00</span>", "")
            word = "Won" if stt == "won" else "Lost"
            clv = _num(r.get("clv_cents"))
            sub = (f"{word}. {strat}, bought {cents(entry)}{esc(_tap_note(r))}"
                   + (f", {clv:+.0f}¢ vs close" if clv is not None else ""))
            return (title, sub, f"<span class='num'>{money(r.get('pl'), signed=True)}</span>", tone(r.get("pl")))

        live = league[league["status"].fillna("") == "open"].copy()
        if not live.empty:
            live["_end"] = pd.to_datetime(live.get("ends_at", live["close_time"]).fillna(live["close_time"]),
                                          errors="coerce", utc=True)
            live = live.sort_values("_end")
        done = league[league["status"].fillna("").isin(["won", "lost", "void"])].copy()
        if not done.empty:
            done = done.sort_values("settled_at", ascending=False).head(30)

        unreal = 0.0
        for r in live.to_dict("records"):
            mk, en = _num(r.get("last_mid")), _num(r.get("entry_price"))
            if mk is not None and en is not None:
                unreal += (_num(r.get("contracts")) or 0) * (mk - en)
        if _live_override:
            as_of = f"Prices checked {ago(_live_override['checked_at'])}"
            if _live_override.get("settled"):
                n = _live_override["settled"]
                as_of += f", {n} just settled (made official on the next round)"
        else:
            as_of = f"Prices from the last round, {ago(lg_last)}"
        st.markdown(f"<div class='section'>Live bets ({len(live)})</div>"
                    + (f"<div class='pe-lede' style='margin:.2rem 0 0'>Worth {money(unreal, signed=True)} "
                       f"right now if they all sold at today's price. {esc(as_of)}.</div>"
                       if len(live) else ""),
                    unsafe_allow_html=True)
        if len(live) or _live_override:
            st.button("↻ Refresh live prices", key="refresh_live", width="stretch",
                      on_click=_refresh_live_bets)
            if st.session_state.get("league_live_error"):
                st.caption(f"Couldn't refresh: {st.session_state['league_live_error']}")
        if live.empty:
            st.markdown("<div class='pe-lede'>No paper bets running right now.</div>", unsafe_allow_html=True)
        else:
            st.markdown(rows_html(_pick_row(r) for r in live.to_dict("records")), unsafe_allow_html=True)

        st.markdown(f"<div class='section'>Finished ({len(done)})</div>", unsafe_allow_html=True)
        if done.empty:
            st.markdown("<div class='pe-lede'>Nothing settled yet. You'll get a push when bets finish.</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown(rows_html(_pick_row(r) for r in done.to_dict("records")), unsafe_allow_html=True)
        rows.sort(key=lambda r: (-(r[6] if r[6] is not None else -99), -r[5]))
        st.markdown("<div class='section'>Strategy standings</div>", unsafe_allow_html=True)
        st.markdown(rows_html(
            (esc(LEAGUE_LABELS[n]),
             f"{w}-{l} record, {o} open" + (f", {c:+.1f}¢ vs close" if c is not None else "") +
             (f", tapping 5 min late costs {sl:+.1f}¢ on average" if sl is not None and ns >= 3 else "") +
             f"<br>{esc(LEAGUE_HOW[n])}",
             f"<span class='num'>{money(pl, signed=True)}</span>", tone(pl))
            for n, total, o, w, l, pl, c, nc, sl, ns in rows
        ), unsafe_allow_html=True)

        st.markdown("<div class='pe-lede'>Each pick is a $10 paper bet at the price you could buy right then, "
                    "after Kalshi's fee. Beating the close means the price moved our way before the market shut, "
                    "the best early sign a strategy is real.</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ picks --
with tab_picks:
    if picks.empty:
        quiet("Nothing clears the bar right now",
              f"A pick needs {MIN_EDGE:.0%} edge at the price you can buy today. "
              f"Next scan around {next_scan()}. You'll get a push the moment one qualifies.")
    for row in picks.to_dict("records"):
        order = dashboard_data.size_signal(row) or {}
        side = str(row.get("side") or "").upper()
        st.markdown(
            f"""
            <div class='slip'>
              <div class='top'>
                <div class='meta'><span class='side {side.lower()}'>Buy {esc(side)}</span><span>{esc(row.get('sport_title') or 'Sports')}</span></div>
                <h4>{esc(row.get('market_title') or row.get('ticker'))}</h4>
                <div class='meta' style='margin-top:.35rem'><span>{esc(starts(row.get('commence_time')))}</span></div>
              </div>
              <div class='tear'></div>
              <div class='bottom'>
                <div class='price num'>{cents(row.get('recommended_limit'))}<small>per contract</small></div>
                <dl>
                  <dt>Edge</dt><dd class='up'>{pct(row.get('net_edge'), signed=True)}</dd>
                  <dt>True odds</dt><dd>{pct(row.get('fair_probability'))}</dd>
                  <dt>Pay up to</dt><dd>{cents(row.get('hard_max'))}</dd>
                  <dt>Stake</dt><dd>{f"{order.get('shares')} for {money(order.get('max_loss'))}" if order else "—"}</dd>
                </dl>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        url = dashboard_data.approval_url_for_signal(row, base_url=app_root())
        if url:
            st.link_button("Review and place bet", url, type="primary", width="stretch")

# --------------------------------------------------------------- my bets --
with tab_bets:
    st.markdown("<div class='section'>Open</div>", unsafe_allow_html=True)
    if open_bets.empty:
        quiet("No open bets", "Bets you place from a pick show up here until they settle.")
    else:
        st.markdown(rows_html(
            (esc(r.get("market_title") or r.get("ticker")),
             f"{esc(str(r.get('side') or '').upper())}, {esc(starts(r.get('commence_time')))}",
             f"<span class='num'>{money(r.get('entry_cost'))}</span>", "")
            for r in open_bets.to_dict("records")
        ), unsafe_allow_html=True)

    st.markdown("<div class='section'>Settled</div>", unsafe_allow_html=True)
    if settled.empty:
        st.markdown("<div class='pe-lede'>Nothing settled yet.</div>", unsafe_allow_html=True)
    else:
        view = settled.sort_values("trade_date", ascending=False).head(50)
        st.markdown(rows_html(
            (esc(r.get("market_title") or r.get("ticker")),
             f"{esc(str(r.get('side') or '').upper())}, {esc(short_date(r.get('trade_date')))}, {esc(str(r.get('outcome') or '').title())}",
             f"<span class='num'>{money(r.get('net_pl'), signed=True)}</span>", tone(r.get("net_pl")))
            for r in view.to_dict("records")
        ), unsafe_allow_html=True)

# ---------------------------------------------------------------- results --
with tab_results:
    n_clv = len(clv_vals)
    if n_clv < CLV_VERDICT_MIN:
        verdict = ("Too early to tell",
                   f"{n_clv} of {CLV_VERDICT_MIN} picks tracked. The test that matters is whether our price "
                   "beats the last price before the game, not a few wins or losses.")
    elif clv_vals.mean() > 0.5:
        verdict = ("The edge looks real",
                   f"Our picks beat the closing price by {clv_vals.mean():+.1f}¢ on average across {n_clv} picks.")
    else:
        verdict = ("No edge yet",
                   f"Our picks average {clv_vals.mean():+.1f}¢ against the close across {n_clv} picks. Keep stakes small.")
    st.markdown(f"<div class='verdict'><strong>{esc(verdict[0])}</strong><p>{esc(verdict[1])}</p></div>",
                unsafe_allow_html=True)

    st.markdown(rows_html([
        ("Risked", "Total staked including fees", f"<span class='num'>{money(summary['risked'])}</span>", ""),
        ("Return", "Profit divided by risked", f"<span class='num'>{pct(summary['roi'], signed=True)}</span>", tone(summary["roi"])),
        ("Settled bets", "Graded wins and losses", f"<span class='num'>{summary['settled']}</span>", ""),
    ]), unsafe_allow_html=True)

    if not settled.empty:
        curve = settled.copy()
        curve["when"] = pd.to_datetime(curve["settled_at"].fillna(curve["trade_date"]), errors="coerce", utc=True)
        curve = curve.dropna(subset=["when"]).sort_values("when")
        curve["Profit"] = pd.to_numeric(curve["net_pl"], errors="coerce").fillna(0).cumsum()
        st.markdown("<div class='section'>Profit over time</div>", unsafe_allow_html=True)
        st.line_chart(curve.set_index("when")["Profit"], height=200, color="#F2C14E")

        if "sport_title" in settled.columns:
            by = dashboard_data.aggregate_performance(settled, "sport_title")
            if not by.empty:
                st.markdown("<div class='section'>By sport</div>", unsafe_allow_html=True)
                st.markdown(rows_html(
                    (esc(r["sport_title"]), f"{esc(r['record'])} record",
                     f"<span class='num'>{money(r['net_pl'], signed=True)}</span>", tone(r["net_pl"]))
                    for r in by.to_dict("records")
                ), unsafe_allow_html=True)

# ----------------------------------------------------------------- system --
with tab_system:
    k = kalshi_status()
    credits = (state.get("daily_usage") or {}).get("credits")
    breaker = bool((state.get("drawdown_circuit") or {}).get("active"))
    st.markdown(rows_html([
        ("Kalshi account", esc(k["error"]) if not k["connected"] else "Read access working",
         "Connected" if k["connected"] else "Not connected", "up" if k["connected"] else "down"),
        ("Scanner", f"Every 30 minutes, last {esc(ago(last_activity))}",
         "Running" if running else "Idle", "up" if running else ""),
        ("Sports", "Where the odds credits go", esc(focus_label()).replace(" and ", ", "), ""),
        ("Edge required", "At the live price, after Kalshi's fee", f"{MIN_EDGE:.0%}+", ""),
        ("Odds credits today", "Free plan, about 16 a day",
         f"<span class='num'>{credits if credits is not None else 0} / {os.getenv('PM_EDGE_ODDS_DAILY_BUDGET', '14')}</span>", ""),
        ("Loss breaker", "Pauses alerts after a bad run", "Tripped" if breaker else "Clear", "down" if breaker else "up"),
    ]), unsafe_allow_html=True)
    if st.button("Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.page_link("pages/Kalshi_Connection_Check.py", label="Run a Kalshi connection check")
