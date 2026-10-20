"""SharpIQ (formerly PM Edge): phone-first dashboard.

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
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import sys as _sys

import pandas as pd
import streamlit as st

# Streamlit Cloud keeps imported modules in memory across code updates, so after a deploy the
# new app.py can run against the old league code (e.g. a KeyError on a field the old code
# doesn't return yet). Drop them so every run imports the newest league code.
for _m in [k for k in _sys.modules if k == "league" or k.startswith("league.")]:
    del _sys.modules[_m]

import dashboard_data
from kalshi_readonly import KalshiReadOnlyClient

st.set_page_config(page_title="SharpIQ", page_icon="🎯", layout="centered",
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
        "PM_EDGE_STATS_START", "GITHUB_TOKEN",
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
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
    :root {
      --bg:#0B0D10; --panel:#15181D; --panel2:#111418; --deep:#0E1013; --line:#262A31; --text:#F4F6F8; --soft:#C7CCD4;
      --muted:#8B93A0; --gold:#F5B800; --up:#22C55E; --down:#F05252; --hero:#0E2A1B;
    }
    html, body, .stApp, .stApp p, .stApp span, .stApp div, .stApp button, .stApp label, .stApp li {
      font-family:'Manrope', system-ui, sans-serif !important;
    }
    .stApp { background:var(--bg); color:var(--text); }
    /* keep Streamlit's icon font (expander arrows etc.), which the rule above would replace with text */
    .stApp [data-testid="stIconMaterial"], .stApp span[class*="material-symbols"], .stApp span[class*="material-icons"] {
      font-family:'Material Symbols Rounded', 'Material Icons' !important; }
    .num, .big, .v, .amt, .pv, .price { font-variant-numeric:tabular-nums; }
    /* hide Streamlit's own chrome: toolbar, menu, footer, deploy and status badges */
    [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
    #MainMenu, footer, [data-testid="stSidebar"], [data-testid="collapsedControl"],
    [class*="viewerBadge"], [data-testid="stAppDeployButton"], [data-testid="manage-app-button"] { display:none !important; }
    .block-container { padding-top:1.4rem; padding-bottom:4rem; max-width:640px; }
    .num { font-family:'Manrope', sans-serif !important; font-weight:700; font-variant-numeric:tabular-nums; letter-spacing:.01em; }
    .up { color:var(--up); } .down { color:var(--down); }
    /* header */
    .pe-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem; }
    .st-key-pe_head { margin-bottom:.6rem; flex-wrap:nowrap !important; }
    .st-key-pe_head .stMarkdown { margin-bottom:0 !important; }
    .st-key-pe_head .st-key-hdr_refresh .stButton button, .st-key-hdr_refresh .stButton button {
      min-height:0 !important; height:2.4rem !important; width:2.4rem !important; padding:0 !important;
      font-size:1.25rem !important; line-height:1 !important; border-radius:12px !important;
      border:1px solid var(--line) !important; background:var(--panel) !important; box-shadow:none !important;
      color:var(--text) !important; display:flex !important; align-items:center !important; justify-content:center !important; }
    .st-key-hdr_refresh .stButton button:hover { border-color:var(--gold) !important; color:var(--gold) !important; }
    .pe-mark { font-family:'Manrope', sans-serif !important; font-weight:800; font-size:1.7rem; letter-spacing:-.02em; }
    .pe-mark i { font-style:normal; color:var(--gold); }
    .pe-pill { font-size:.8rem; font-weight:700; padding:.38rem .75rem; border-radius:999px; }
    .pe-pill.on { color:var(--up); background:rgba(43,224,122,.12); }
    .pe-pill.off { color:var(--gold); background:rgba(242,193,78,.12); }
    .pe-lede { color:var(--muted); font-size:.9rem; margin:.5rem 0 1rem; line-height:1.45; }
    .k { color:var(--muted); font-size:.68rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; white-space:nowrap; }
    /* hero card */
    .pe-hero { background:linear-gradient(150deg, var(--hero) 0%, #10161A 70%); border:1px solid rgba(34,197,94,.45);
               border-radius:20px; padding:1rem; display:flex; flex-direction:column; gap:.9rem; margin:.3rem 0 .6rem; }
    .pe-hero .top { display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; }
    .pe-hero .big { font-family:'Manrope', sans-serif !important; font-weight:800; font-size:3rem; line-height:1; margin-top:.35rem; }
    .pe-hero .mid { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:1.4rem; margin-top:.25rem; text-align:right; }
    .pe-hero .stats { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:.5rem; }
    .pe-hero .stat { background:rgba(7,12,20,.55); border-radius:12px; padding:.5rem .65rem; min-width:0; }
    .pe-hero .stat .k { white-space:normal; letter-spacing:.06em; line-height:1.25; }
    .pe-hero .stat .v { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:1.25rem; margin-top:.1rem; white-space:nowrap; }
    .pe-hero .stat .sub.goal { color:var(--text); font-weight:600; margin-top:.3rem; }
    .pe-hero .stat .sub { color:var(--muted); font-size:.72rem; line-height:1.3; margin-top:.15rem; white-space:normal; overflow-wrap:anywhere; }
    .pe-hero .bar { display:flex; flex-direction:column; gap:.35rem; }
    .pe-hero .bar .lab { display:flex; justify-content:space-between; font-size:.8rem; color:var(--soft); }
    .pe-hero .units { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:1rem; margin-top:.1rem; }
    .pe-hero .bar .track { height:6px; border-radius:3px; background:var(--line); overflow:hidden; }
    .pe-hero .bar .fill { height:6px; border-radius:3px; background:var(--up); }
    /* open vs settled split */
    .pe-split { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:.6rem; margin:.2rem 0 .4rem; }
    .pe-split .box { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:.8rem .9rem;
                     display:flex; flex-direction:column; gap:.45rem; min-width:0; }
    .pe-split .hd { display:flex; justify-content:space-between; align-items:center; }
    .pe-split .cnt { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:.95rem; color:var(--soft);
                     background:var(--panel2); border:1px solid var(--line); border-radius:999px; padding:0 .5rem; }
    .pe-split .amt { font-family:'Manrope', sans-serif !important; font-weight:800; font-size:2rem; line-height:1; }
    .pe-split .cap { font-size:.7rem; color:var(--muted); margin-top:-.25rem; }
    .pe-split dl { display:grid; grid-template-columns:1fr auto; row-gap:.2rem; column-gap:.5rem; margin:.15rem 0 0;
                   padding-top:.5rem; border-top:1px solid var(--line); font-size:.78rem; }
    .pe-split dt { color:var(--muted); } .pe-split dd { margin:0; text-align:right; font-weight:700; color:var(--soft);
                   font-family:'Manrope', sans-serif !important; font-size:.95rem; }
    .pe-total { display:flex; justify-content:space-between; align-items:baseline; background:var(--panel2); border:1px solid var(--line);
                border-radius:12px; padding:.55rem .9rem; margin:0 0 .6rem; font-size:.8rem; color:var(--soft); }
    .pe-total b { font-family:'Manrope', sans-serif !important; font-size:1.15rem; }
    /* older board (scorecards) keeps working, restyled */
    .pe-board { background:var(--panel); border:1px solid var(--line); border-radius:18px;
                padding:1rem 1.1rem; display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; }
    .pe-board .k { font-size:.68rem; }
    .pe-board .big { font-family:'Manrope', sans-serif !important; font-size:2.4rem; font-weight:800; line-height:1.05; margin-top:.2rem; }
    .pe-board .side { display:flex; gap:1.25rem; text-align:right; }
    .pe-board .side .v { font-family:'Manrope', sans-serif !important; font-size:1.3rem; font-weight:700; margin-top:.1rem; white-space:nowrap; }
    @media (max-width:600px) {
      .pe-board { display:block; padding:1rem; }
      .pe-board .side { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(0,1fr); gap:.75rem;
                        text-align:left; margin-top:.9rem; padding-top:.85rem; border-top:1px solid var(--line); }
      .pe-hero .big { font-size:2.6rem; }
    }
    /* tabs -> floating bottom navigation: all tabs visible in two rows, lifted above Streamlit
       Cloud's "Manage app" button (bottom-right, outside the app, can't be hidden) */
    .stTabs [role="tablist"] { position:fixed !important; left:.6rem; right:.6rem; bottom:calc(3.6rem + env(safe-area-inset-bottom));
                               z-index:999; margin:0 auto !important; max-width:640px; padding:.35rem !important;
                               display:grid !important; grid-template-columns:repeat(4, minmax(0,1fr)); gap:.25rem !important;
                               background:#15181D; border:1px solid var(--line) !important; border-radius:18px;
                               box-shadow:0 8px 28px rgba(0,0,0,.55) !important; overflow:visible;
                               backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px); }
    .stTabs [role="tab"] { height:2.5rem !important; padding:0 .3rem !important; border-radius:12px !important; border:0 !important;
                           background:transparent !important; white-space:nowrap; min-width:0; display:flex; align-items:center;
                           justify-content:center; }
    .stTabs [role="tab"] p { font-weight:600 !important; font-size:.82rem !important; color:var(--muted) !important;
                             overflow:hidden; text-overflow:ellipsis; }
    .stTabs [role="tab"][aria-selected="true"] { background:rgba(245,184,0,.14) !important; }
    .stTabs [role="tab"][aria-selected="true"] p { color:var(--gold) !important; font-weight:800 !important; }
    .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"], .stTabs .react-aria-SelectionIndicator { display:none !important; }
    .stTabs [role="tablist"] + div, .stTabs [data-baseweb="tab-border"] { border:0 !important; }
    .block-container { padding-bottom:12rem !important; }
    [data-testid="stTabsScrollRight"], [data-testid="stTabsScrollLeft"] { display:none !important; }
    /* home screen */
    .hm-tiles { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:.6rem; margin:.2rem 0 .4rem; }
    .hm-tile { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:.8rem .9rem; min-width:0; }
    .hm-tile .v { font-weight:800; font-size:1.45rem; line-height:1.1; white-space:nowrap; }
    .hm-tile .l { color:var(--muted); font-size:.78rem; margin-top:.2rem; }
    .hm-opps { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:.1rem .95rem; margin:.5rem 0 .8rem; }
    .hm-opp { display:flex; justify-content:space-between; align-items:center; gap:.8rem; padding:.75rem 0; border-bottom:1px solid var(--line); }
    .hm-opp:last-child { border-bottom:0; }
    .hm-opp .t { font-weight:700; font-size:.95rem; } .hm-opp .s { color:var(--muted); font-size:.75rem; margin-top:.1rem; }
    .hm-opp .ev { font-weight:700; font-size:.78rem; }
    .hm-opp .px { text-align:right; font-weight:800; font-size:1.05rem; white-space:nowrap; }
    .hm-opp .px small { display:block; color:var(--muted); font-weight:600; font-size:.72rem; }
    .ev-pos { color:var(--up); } .ev-neg { color:var(--muted); }
    /* compare-all (tap an opportunity) */
    details.hm-opp-d { border-bottom:1px solid var(--line); }
    details.hm-opp-d:last-child { border-bottom:0; }
    details.hm-opp-d > summary { list-style:none; cursor:pointer; border-bottom:0; }
    details.hm-opp-d > summary::-webkit-details-marker { display:none; }
    details.hm-opp-d[open] > summary .px { color:var(--gold); }
    .cmp { padding:0 0 .9rem; display:flex; flex-direction:column; gap:.4rem; }
    .cmp-h { display:flex; justify-content:space-between; color:var(--muted); font-size:.74rem; font-weight:600; margin-bottom:.1rem; }
    a.cmp-row { display:grid; grid-template-columns:1fr auto 4.2rem; gap:.6rem; align-items:center; padding:.6rem .75rem;
                background:var(--panel2); border:1px solid var(--line); border-radius:12px; text-decoration:none !important; color:var(--text) !important; }
    a.cmp-row.best { border-color:var(--up); background:rgba(34,197,94,.08); }
    .cmp-v { font-weight:700; font-size:.9rem; } .cmp-p { font-weight:800; font-size:1rem; } .cmp-e { font-weight:700; font-size:.82rem; text-align:right; }
    a.cmp-bet { display:flex; flex-direction:column; align-items:center; gap:.1rem; margin-top:.3rem; padding:.75rem;
                border-radius:14px; background:var(--gold); color:#14110A !important; text-decoration:none !important; }
    a.cmp-bet b { font-weight:800; font-size:1rem; letter-spacing:.02em; } a.cmp-bet span { font-size:.8rem; font-weight:600; }
    .cmp-note { color:var(--muted); font-size:.72rem; line-height:1.4; }
    /* P/L calendar */
    .cal { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:.75rem .55rem .6rem; margin:.2rem 0 .6rem; }
    .cal-h { display:flex; align-items:baseline; gap:.5rem; padding:0 .3rem .55rem; }
    .cal-h b { font-size:1.15rem; font-weight:800; } .cal-h span { font-weight:700; font-size:.9rem; }
    .cal-g { display:grid; grid-template-columns:repeat(7, minmax(0,1fr)); gap:4px; }
    .cal-dow { text-align:center; color:var(--muted); font-size:.68rem; font-weight:700; padding-bottom:.2rem; }
    .cal-d { min-height:3.1rem; border-radius:10px; display:flex; flex-direction:column; align-items:center;
             justify-content:space-between; padding:.3rem 0 .35rem; min-width:0; }
    .cal-d .n { color:var(--muted); font-size:.72rem; font-weight:600; }
    .cal-d .v { font-size:.7rem; font-weight:800; white-space:nowrap; letter-spacing:-.02em; }
    .cal-d.win { background:rgba(34,197,94,.14); } .cal-d.win .v { color:var(--up); }
    .cal-d.loss { background:rgba(240,82,82,.14); } .cal-d.loss .v { color:var(--down); }
    .cal-d.flat { background:var(--panel2); } .cal-d.flat .v { color:var(--soft); }
    .cal-d.today { box-shadow:inset 0 0 0 1.5px #6EA8FE; } .cal-d.today .n { color:#6EA8FE; }
    @media (min-width:640px) { .cal-d { min-height:4rem; } .cal-d .v { font-size:.85rem; } .cal-d .n { font-size:.8rem; } }
    /* lab */
    a.hm-tile, a.lab-card { text-decoration:none !important; color:var(--text) !important; display:block; }
    a.hm-tile:hover, a.lab-card:hover { border-color:var(--gold) !important; }
    .lab-cards { display:flex; flex-direction:column; gap:.6rem; margin:.6rem 0 1rem; }
    .lab-card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:.85rem .95rem; }
    .lc-top { display:flex; justify-content:space-between; gap:.8rem; align-items:flex-start; }
    .lc-name { font-weight:800; font-size:1rem; } .lc-desc { color:var(--muted); font-size:.78rem; margin-top:.15rem; line-height:1.35; }
    .lc-bank { font-weight:800; color:var(--gold); white-space:nowrap; }
    .lc-stats { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:.4rem; margin-top:.7rem; padding-top:.65rem; border-top:1px solid var(--line); }
    .lc-stats b { display:block; font-weight:800; font-size:1.02rem; white-space:nowrap; }
    .lc-stats span { color:var(--muted); font-size:.7rem; }
    /* section headers */
    .section { font-family:'Manrope', sans-serif !important; font-weight:800; font-size:1.08rem; letter-spacing:-.01em;
               margin:1.3rem 0 .1rem; display:flex; justify-content:space-between; align-items:baseline; gap:.75rem; }
    .section .n { color:var(--muted); margin-left:.35rem; }
    /* bet cards */
    .cards { display:flex; flex-direction:column; gap:.6rem; margin:.6rem 0 1rem; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:.65rem; display:flex; gap:.75rem; align-items:stretch; }
    .bug { width:7.4rem; flex:none; background:var(--deep); border:1px solid #182334; border-radius:11px; padding:.45rem .6rem;
           display:flex; flex-direction:column; gap:.18rem; justify-content:center; }
    .bug .tr { display:flex; justify-content:space-between; align-items:center; font-family:'Manrope', sans-serif !important;
               font-weight:700; font-size:1.05rem; color:#7F8DA3; letter-spacing:.02em; line-height:1.15; }
    .bug .tr.me { color:#FFFFFF; }
    .bug .tn { display:flex; align-items:center; gap:.38rem; min-width:0; overflow:hidden; white-space:nowrap; }
    .bug .sw { width:4px; height:.9rem; border-radius:2px; flex:none; }
    .bug .cl { font-size:.62rem; font-weight:700; letter-spacing:.1em; color:var(--muted); border-top:1px solid #182334;
               padding-top:.25rem; margin-top:.1rem; display:flex; justify-content:space-between; text-transform:uppercase; }
    .bug .lv { color:var(--down); }
    .bug.tag { align-items:flex-start; }
    .bug.tag .big { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:1rem; color:#FFFFFF; }
    .card .info { flex:1; min-width:0; display:flex; flex-direction:column; justify-content:space-between; gap:.2rem; padding:.1rem .1rem .1rem 0; }
    .card .lg { font-size:.62rem; font-weight:700; letter-spacing:.12em; color:var(--muted); text-transform:uppercase; }
    .card .pn { font-weight:700; font-size:.98rem; color:var(--text); line-height:1.25; }
    .card .dot { width:.5rem; height:.5rem; border-radius:50%; display:inline-block; margin-right:.4rem; vertical-align:middle; }
    .card .pr { display:flex; justify-content:space-between; align-items:flex-end; gap:.6rem; }
    .card .ps { font-size:.78rem; color:var(--muted); line-height:1.35; }
    .card .pv { font-family:'Manrope', sans-serif !important; font-weight:800; font-size:1.35rem; line-height:1; white-space:nowrap; text-align:right; }
    .card .pv small { display:block; font-family:'Manrope', sans-serif !important; font-weight:600; font-size:.66rem; color:var(--muted); margin-top:.2rem; letter-spacing:.04em; }
    /* bet slip (alerts tab) */
    .slip { background:var(--panel); border:1px solid var(--line); border-radius:18px; margin:1rem 0 .55rem; position:relative; }
    .slip .top { padding:1rem 1.15rem .9rem; }
    .slip .meta { display:flex; justify-content:space-between; color:var(--muted); font-size:.8rem; }
    .slip .side { font-weight:700; letter-spacing:.02em; }
    .slip .side.yes { color:var(--up); } .slip .side.no { color:var(--down); }
    .slip h4 { font-size:1.08rem; font-weight:650; line-height:1.3; margin:.45rem 0 0; padding:0 !important; color:var(--text); }
    .slip .tear { border-top:1.5px dashed var(--line); margin:0 .9rem; position:relative; }
    .slip .bottom { padding:.9rem 1.15rem 1rem; display:grid; grid-template-columns:auto 1fr; gap:1.2rem; align-items:center; }
    .slip .price { font-family:'Manrope', sans-serif !important; font-size:2.6rem; font-weight:800; color:var(--up); line-height:1; }
    .slip .price small { display:block; font-family:'Manrope', sans-serif !important; font-size:.75rem; font-weight:500; color:var(--muted); margin-top:.3rem; }
    .slip dl { display:grid; grid-template-columns:1fr auto; row-gap:.28rem; column-gap:.75rem; margin:0; font-size:.88rem; }
    .slip dt { color:var(--muted); } .slip dd { margin:0; text-align:right; font-weight:650; }
    /* lists (finished bets, standings) */
    .row-list { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:.15rem 1rem; margin:.6rem 0 1rem; }
    .row-list .r { display:flex; justify-content:space-between; gap:1rem; padding:.75rem 0; border-bottom:1px solid var(--line); }
    .row-list .r:last-child { border-bottom:0; }
    .row-list .t { font-weight:600; font-size:.95rem; line-height:1.3; }
    .row-list .s { color:var(--muted); font-size:.8rem; margin-top:.15rem; overflow-wrap:anywhere; }
    .row-list .v { font-family:'Manrope', sans-serif !important; font-weight:700; font-size:1.1rem; text-align:right; white-space:nowrap; }
    .quiet { text-align:center; padding:2.6rem 1.2rem; color:var(--muted); line-height:1.5; }
    .quiet strong { display:block; color:var(--text); font-size:1.1rem; font-weight:700; margin-bottom:.35rem; }
    .verdict { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:.8rem 1rem; margin:1.2rem 0 1rem; }
    .verdict strong { font-size:1.1rem; }
    .verdict p { color:var(--muted); margin:.3rem 0 0; font-size:.9rem; line-height:1.45; }
    .stButton button, .stLinkButton a { border-radius:999px !important; font-weight:700 !important; padding:.6rem 1rem !important;
                                        background:var(--panel2) !important; border:1px solid var(--line) !important; color:var(--soft) !important; }
    .stButton button:hover { border-color:var(--up) !important; color:var(--text) !important; }
    .stLinkButton a[kind="primary"], .stLinkButton a[data-testid="stBaseLinkButton-primary"] {
      background:var(--gold) !important; color:#14110A !important; border:0 !important; }
    .stButton button:hover { border-color:var(--gold) !important; }
    [data-testid="stBaseButton-primaryFormSubmit"], [data-testid="stBaseButton-primary"] {
      background:var(--gold) !important; border:0 !important; border-radius:12px !important; }
    [data-testid="stBaseButton-primaryFormSubmit"] p, [data-testid="stBaseButton-primary"] p {
      color:#14110A !important; font-weight:800 !important; }
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


@st.cache_data(ttl=60, show_spinner=False)
def load_live_sharp():
    from league import live_sharp as _ls
    try:
        rows = _ls.load()
    except Exception:
        rows = []
    return rows, _ls.summary(rows)


def _refresh_live_sharp() -> None:
    """Button callback: current Kalshi prices for open real bets (read-only, in memory)."""
    last = st.session_state.get("live_sharp_live") or {}
    last_at = _when(last.get("checked_at"))
    if last_at and (datetime.now(timezone.utc) - last_at).total_seconds() < 5:
        return
    import importlib
    from league import book as _book, live_sharp as _ls
    from league.kalshi import LeagueKalshi
    try:
        # Streamlit Cloud reruns app.py on a code push but keeps old copies of imported
        # modules; reload so the button always runs the newest league code.
        importlib.reload(_book)
        _ls = importlib.reload(_ls)
        out = _ls.refresh(_ls.load(), LeagueKalshi().markets)
    except Exception as exc:
        st.session_state["live_sharp_err"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return
    st.session_state.pop("live_sharp_err", None)
    if out["open"] and not out["found"]:
        st.session_state["live_sharp_err"] = "Kalshi didn't answer just now. Try again in a minute."
        return
    st.session_state["live_sharp_live"] = {"rows": out["rows"], "checked_at": out["checked_at"],
                                           "settled": out["settled"], "marked": out["marked"],
                                           "waiting": out["waiting"], "base": _ls_base_key()}


def _ls_base_key() -> str:
    from pathlib import Path as _P
    from league import live_sharp as _ls
    try:
        return str(_P(_ls.LEDGER).stat().st_mtime)
    except Exception:
        return ""


@st.cache_data(ttl=30, show_spinner=False)
def live_scores(keys: tuple) -> dict:
    """ESPN score/clock for open real bets, keyed by Kalshi ticker; {} if ESPN is unreachable."""
    from league import scores as _scores
    try:
        return _scores.for_bets([{"ticker": t, "title": ti} for t, ti in keys])
    except Exception:
        return {}


def _score_line(g):
    """Kalshi-style game line: LIVE  DEL 23  26 MTU  Q4 · 12:35 (our side in bold)."""
    def side(code, score, who):
        code = f"<b>{esc(code)}</b>" if g.get("mine") == who else esc(code)
        return code, f"<b class='num'>{esc(score)}</b>"
    ac, as_ = side(g["away"], g["away_score"], "away")
    hc, hs = side(g["home"], g["home_score"], "home")
    detail = esc(str(g.get("detail") or "").replace(" - ", " · "))
    if g.get("state") == "in":
        tag = "<span class='sc-live'>● LIVE</span>"
        return f"<div class='sc'>{tag} {ac} {as_}&nbsp; {hs} {hc} <span class='sc-d'>{detail}</span></div>"
    if g.get("state") == "post":
        return (f"<div class='sc'><span class='sc-fin'>FINAL</span> {ac} {as_}&nbsp; {hs} {hc}"
                f" <span class='sc-d'>{detail if detail not in {'Final', 'FT'} else ''}</span></div>")
    from league import scores as _scores
    st_lbl = _scores.start_label(g)
    return f"<div class='sc'>{ac} vs {hc} <span class='sc-d'>{esc('Starts ' + st_lbl if st_lbl else detail)}</span></div>"


def _clock(g):
    """(left, right) for the bottom line of a score box: ('● Q4', '12:35'), ('● HALF', ''), ('FINAL', ''), ('WED', '6 PM')."""
    d = str(g.get("detail") or "")
    if g.get("state") == "in":
        if "half" in d.lower():
            return "<span class='lv'>● HALF</span>", ""
        left, _, right = d.partition(" - ")
        return f"<span class='lv'>● {esc(left.strip())}</span>", esc(right.strip())
    if g.get("state") == "post":
        return "FINAL", ""
    from league import scores as _scores
    try:
        dt = datetime.fromisoformat(str(g.get("start")).replace("Z", "+00:00")).astimezone(EASTERN)
        today = datetime.now(EASTERN).date()
        day = "TODAY" if dt.date() == today else dt.strftime("%a").upper()
        return day, _scores.start_label(g)
    except Exception:
        return esc(d), ""


def _end_short(r) -> str:
    dt = _when(r.get("ends_at") or r.get("close_time"))
    if not dt:
        return ""
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return "SETTLING"
    if secs < 3600:
        return f"ENDS {int(secs // 60)}M"
    if secs < 36 * 3600:
        return "ENDS " + local_time(dt).upper()
    return "ENDS " + dt.astimezone(EASTERN).strftime("%b %-d").upper()


def bet_card(r, game, *, n, entry, mark, extra: str = "") -> str:
    """One open bet as a sportsbook card: score box (or league box) on the left, the pick and P/L on the right."""
    from league import scores as _scores
    ticker = str(r.get("ticker") or "")
    tag = _scores.league_tag(ticker)
    raw = str(r.get("title") or ticker)
    raw = raw.rsplit(": ", 1)[-1] if " vs" in raw.split(": ", 1)[0] else raw
    mine = (game or {}).get("mine")
    if str(r.get("side") or "").upper() == "NO":
        mine = None  # betting against a team: don't mark either side as ours
    raw = raw[:-5] if raw.endswith(" wins") else raw
    wx = re.match(r"^(Highest|Lowest) temperature in (.+?) on .+?\?\s*[—-]\s*(.+)$", raw)
    if wx:
        raw = f"{wx.group(2)} {'high' if wx.group(1) == 'Highest' else 'low'} · {wx.group(3)}"
    if game and mine:
        name = esc(game[f"{mine}_name"])
        dot = f"<span class='dot' style='background:{esc(game[mine + '_color'])}'></span>"
    else:
        name, dot = esc(raw), ""
    if str(r.get("side") or "").upper() == "NO":
        name = f"No · {name}"
    if game:
        def tr(who):
            me = " me" if mine == who else ""
            return (f"<div class='tr{me}'><span class='tn'><span class='sw' style='background:{esc(game[who + '_color'])}'></span>"
                    f"{esc(game[who])}</span><span>{esc(game[who + '_score']) if game.get('state') != 'pre' else '–'}</span></div>")
        left, right = _clock(game)
        box = f"<div class='bug'>{tr('away')}{tr('home')}<div class='cl'><span>{left}</span><span>{right}</span></div></div>"
    else:
        box = (f"<div class='bug tag'><div class='big'>{esc(tag)}</div>"
               f"<div class='cl' style='width:100%'><span>{esc(_end_short(r))}</span><span></span></div></div>")
    n = n or 0
    if mark is not None and entry is not None:
        pnl = n * (mark - entry)
        move = (mark - entry) * 100
        word = "WINNING" if move > 0.4 else "LOSING" if move < -0.4 else "EVEN"
        price = f"{n:g} @ {cents(entry)} → {cents(mark)} · pays {money(n)}"
        value = f"<div class='pv {tone(move)}'>{money(pnl, signed=True)}<small>{word}</small></div>"
    else:
        price = f"{n:g} @ {cents(entry)} · pays {money(n)}"
        value = f"<div class='pv'>{cents(entry)}<small>ENTRY</small></div>"
    lg = esc(tag) + (f" · {extra}" if extra and extra.upper() != tag.upper() else "")
    return (f"<div class='card'>{box}<div class='info'><div class='lg'>{lg}</div><div class='pn'>{dot}{name}</div>"
            f"<div class='pr'><div class='ps'>{esc(price)}</div>{value}</div></div></div>")


def pl_split(*, open_n, open_pl, at_risk, payout, done_n, done_pl, record, today_pl, label="") -> str:
    """Two boxes, open vs settled, and the combined total underneath."""
    total = (open_pl or 0) + (done_pl or 0)
    return f"""
    <div class='pe-split'>
      <div class='box'>
        <div class='hd'><span class='k'>Open bets</span><span class='cnt'>{open_n}</span></div>
        <div class='amt {tone(open_pl)}'>{money(open_pl, signed=True)}</div>
        <div class='cap'>if sold at today's price</div>
        <dl><dt>At risk</dt><dd>{money(at_risk)}</dd><dt>Pays if all win</dt><dd>{money(payout)}</dd></dl>
      </div>
      <div class='box'>
        <div class='hd'><span class='k'>Settled</span><span class='cnt'>{done_n}</span></div>
        <div class='amt {tone(done_pl)}'>{money(done_pl, signed=True)}</div>
        <div class='cap'>banked profit</div>
        <dl><dt>Record</dt><dd>{esc(record)}</dd><dt>Today</dt><dd class='{tone(today_pl)}'>{money(today_pl, signed=True)}</dd></dl>
      </div>
    </div>
    <div class='pe-total'><span>{esc(label or "Total if everything closed now")}</span><b class='{tone(total)}'>{money(total, signed=True)}</b></div>
    """


def units_line(u) -> str:
    """'+10.56 units' under a P/L figure. A unit is the bet size at the time ($1 early on, $2 Oct 18, $5 from Oct 20)."""
    if u is None:
        return ""
    return f"<div class='units {tone(u)}'>{u:+.2f} units</div>"


def _cal_amt(v: float, mode: str) -> str:
    """Compact cell figure: '$137', '$22.7', '$4.13' or '2.74u'. Sign is shown by colour."""
    a = abs(v)
    if mode == "u":
        return f"{a:.2f}u" if a < 10 else f"{a:.1f}u"
    return f"${a:.2f}" if a < 10 else f"${a:.1f}" if a < 100 else f"${a:,.0f}"


def pl_calendar(days: list[dict], year: int, month: int, mode: str, today) -> str:
    """Month grid of settled P/L per Eastern day, green for up days and red for down days."""
    import calendar as _cal
    by_day = {d["day"]: d for d in days}
    key = "units" if mode == "u" else "pl"
    total = sum((d.get(key) or 0) for k, d in by_day.items() if k.startswith(f"{year:04d}{month:02d}"))
    tot_txt = (f"{total:+.2f}u" if mode == "u" else money(total, signed=True))
    cells = "".join(f"<div class='cal-dow'>{x}</div>" for x in "SMTWTFS")
    for week in _cal.Calendar(firstweekday=6).monthdayscalendar(year, month):
        for dnum in week:
            if not dnum:
                cells += "<div></div>"
                continue
            d = by_day.get(f"{year:04d}{month:02d}{dnum:02d}")
            settled = d and (d["won"] + d["lost"])
            cls, val = "", ""
            if settled:
                v = d.get(key) or 0
                cls = "win" if v > 0.004 else "loss" if v < -0.004 else "flat"
                val = _cal_amt(v, mode)
            if (year, month, dnum) == (today.year, today.month, today.day):
                cls += " today"
            cells += f"<div class='cal-d {cls}'><span class='n'>{dnum}</span><span class='v'>{val}</span></div>"
    month_name = datetime(year, month, 1).strftime("%b %Y")
    return (f"<div class='cal'><div class='cal-h'><b>{month_name}</b>"
            f"<span class='{tone(total)}'>{esc(tot_txt)}</span></div>"
            f"<div class='cal-g'>{cells}</div></div>")


def cards_html(items) -> str:
    return "<div class='cards'>" + "".join(items) + "</div>"


def _refresh_all() -> None:
    """One tap refreshes both the paper league bets and the real Sharp bets."""
    _refresh_live_bets()
    _refresh_live_sharp()
    live_scores.clear()


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
def _refresh_everything() -> None:
    """Header ↻: reload the saved files (newest round) and pull live prices for open bets."""
    st.cache_data.clear()
    _refresh_all()


with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", key="pe_head"):
    st.markdown("<div class='pe-mark'>Sharp<i>IQ</i></div>", unsafe_allow_html=True, width="content")
    with st.container(horizontal=True, vertical_alignment="center", gap="small", width="content"):
        st.markdown(f"<div class='pe-pill {'on' if running else 'off'}'>● {'Live' if running else 'Idle'}"
                    f"{(' · ' + esc(local_time(lg_last))) if lg_last else ''}</div>",
                    unsafe_allow_html=True, width="content")
        st.button("↻", key="hdr_refresh", type="tertiary", help="Refresh: newest data and live prices",
                  on_click=_refresh_everything)

tab_home, tab_live, tab_league, tab_lab, tab_picks, tab_bets, tab_results, tab_system = st.tabs(
    ["Home", "Live $5", "Paper", "Lab", f"Alerts {len(picks)}" if len(picks) else "Alerts", "My bets", "Results", "System"]
)

def _price_label(p: dict) -> str:
    """'66¢ (−200)': exchange prices also shown as American odds (after fees), comparable to sportsbooks."""
    shown, d = str(p.get("shown") or ""), _num(p.get("decimal"))
    if shown.endswith("¢") and d and d > 1.0:
        am = f"+{round((d - 1) * 100)}" if d >= 2.0 else f"−{round(100 / (d - 1))}"
        return f"{shown} ({am})"
    return shown.replace("-", "−")


def _parse_odds(text: str):
    """'-194', '+150', '150', '66c', '66¢', '0.66' -> win probability implied by that price (no fees)."""
    t = str(text or "").strip().lower().replace("−", "-").replace("¢", "c").replace(" ", "")
    if not t:
        return None
    try:
        if t.endswith("c"):
            v = float(t[:-1]) / 100.0
            return v if 0 < v < 1 else None
        v = float(t)
    except ValueError:
        return None
    if 0 < v < 1:                       # 0.66 = 66 cents
        return v
    if v <= -100:                       # American favourite
        return -v / (-v + 100)
    if v >= 100:                        # American underdog
        return 100 / (v + 100)
    return None


def _american_from_prob(p: float) -> str:
    if p <= 0 or p >= 1:
        return "—"
    return f"−{round(100 * p / (1 - p))}" if p >= 0.5 else f"+{round(100 * (1 - p) / p)}"


# ------------------------------------------------------------------- home --
with tab_home:
    _h_rows, _h_ls = load_live_sharp()
    _h_open = [r for r in _h_rows if r.get("status") == "open"]
    _h_risk = sum((_num(r.get("cost")) or 0) + (_num(r.get("fee")) or 0) for r in _h_open)
    _h_today = datetime.now(EASTERN).date()
    _h_today_pl = sum(_num(r.get("pl")) or 0 for r in _h_rows if r.get("status") in {"won", "lost"}
                      and _when(r.get("settled_at")) and _when(r.get("settled_at")).astimezone(EASTERN).date() == _h_today)
    _h_paper = league[league.get("strategy", pd.Series(dtype=str)).astype(str) == "sharp"] if not league.empty else league
    _h_paper_done = _h_paper[_h_paper["status"].isin(["won", "lost"])] if not _h_paper.empty else _h_paper
    _h_paper_pl = float(pd.to_numeric(_h_paper_done.get("pl"), errors="coerce").fillna(0).sum()) if not _h_paper_done.empty else 0.0
    _h_paper_rec = (f"{int((_h_paper_done['status'] == 'won').sum())}–{int((_h_paper_done['status'] == 'lost').sum())}"
                    if not _h_paper_done.empty else "0–0")
    _h_budget = float(_h_ls.get("budget") or 0)
    _h_fill = max(0.0, min(100.0, 100.0 * float(_h_ls.get("spent_today") or 0) / _h_budget)) if _h_budget else 0.0
    st.markdown(
        f"""
        <div class='pe-hero'>
          <div class='top'>
            <div><div class='k'>Live $5 Sharp · real money</div>
              <div class='big {tone(_h_ls["pl"])}'>{money(_h_ls["pl"], signed=True)}</div>
              {units_line(_h_ls.get("units"))}</div>
            <div class='mid {tone(_h_today_pl)}'>{money(_h_today_pl, signed=True)}<div class='k' style='text-align:right'>today{(" · " + f"{_h_ls['units_today']:+.1f} units") if _h_ls.get("units_today") is not None else ""}</div></div>
          </div>
          <div class='stats'>
            <div class='stat'><div class='k'>Record</div><div class='v'>{_h_ls["won"]}–{_h_ls["lost"]}</div></div>
            <div class='stat'><div class='k'>ROI</div><div class='v {tone(_h_ls["roi"] or 0)}'>{pct(_h_ls["roi"], signed=True)}</div></div>
            <div class='stat'><div class='k'>Open</div><div class='v'>{_h_ls["open"]}</div></div>
          </div>
          <div class='bar'>
            <div class='lab'><span>Today's budget</span><span><b style='color:var(--text)'>{money(_h_ls["spent_today"])}</b> of {money(_h_ls["budget"])}</span></div>
            <div class='track'><div class='fill' style='width:{_h_fill:.0f}%'></div></div>
          </div>
        </div>
        <div class='hm-tiles'>
          <div class='hm-tile'><div class='v'>{money(_h_risk)}</div><div class='l'>At risk now</div></div>
          <div class='hm-tile'><div class='v {tone(_h_paper_pl)}'>{money(_h_paper_pl, signed=True)}</div><div class='l'>Paper Sharp · {_h_paper_rec}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # top opportunities: best price per team on the +EV board (Pinnacle fair vs every venue)
    _h_board = {}
    try:
        import json as _hj
        from pathlib import Path as _hP
        _bp = _hP("research_lab/data/ev_board.json")
        _h_board = _hj.loads(_bp.read_text()) if _bp.exists() else {}
    except Exception:
        _h_board = {}
    _h_opps = []
    for _r in _h_board.get("rows") or []:
        if not _r.get("prices"):
            continue
        _best = max(_r["prices"], key=lambda p: p.get("ev", -9))
        _st = _when(_r.get("starts"))
        if _st is not None and _st <= datetime.now(timezone.utc):
            continue
        _h_opps.append((_best.get("ev", 0), _r, _best))
    _h_opps.sort(key=lambda x: -x[0])
    # only prices between -200 and +150 (decimal 1.5-2.5): no laying big juice, no long shots
    _h_opps = [o for o in _h_opps if 1.5 - 1e-9 <= (_num(o[2].get("decimal")) or 0) <= 2.5 + 1e-9]
    _h_plays = [o for o in _h_opps if o[0] >= 0.01]
    st.markdown(f"<div class='section'><span>Today's plays"
                f"{(' <span class=n>' + str(len(_h_plays)) + '</span>') if _h_plays else ''}</span>"
                f"<span class='n' style='font-size:.75rem;font-weight:600;white-space:nowrap'>"
                f"{esc(ago(_h_board.get('at'))) if _h_board.get('at') else ''}</span></div>",
                unsafe_allow_html=True)
    if _h_opps:
        _show = _h_plays[:12] if _h_plays else _h_opps[:3]
        if not _h_plays:
            st.markdown("<div class='pe-lede'>No plays at +1% EV right now. Closest prices:</div>", unsafe_allow_html=True)
        _items = []
        for _ev, _r, _b in _show:
            _cls = "ev-pos" if _ev > 0 else "ev-neg"
            _ps = sorted(_r["prices"], key=lambda p: -p.get("decimal", 0))
            _rows = "".join(
                f"<a class='cmp-row{' best' if i == 0 else ''}' href='{esc(p.get('url') or '#')}' target='_blank' rel='noopener'>"
                f"<span class='cmp-v'>{esc(p['venue'])}{' · best' if i == 0 else ''}</span>"
                f"<span class='cmp-p'>{esc(_price_label(p))}</span>"
                f"<span class='cmp-e {'ev-pos' if p.get('ev', 0) > 0 else 'ev-neg'}'>{p.get('ev', 0) * 100:+.1f}%</span></a>"
                for i, p in enumerate(_ps))
            _label = _r.get("label") or f"{_r['team']} ML"
            _kelly = _num(_r.get("kelly")) or 0.0
            _size = f" · bet {_kelly * 100:.1f}% of bankroll" if _ev >= 0.01 and _kelly > 0 else ""
            _items.append(
                f"<details class='hm-opp-d'><summary class='hm-opp'><div><div class='t'>{esc(_label)}</div>"
                f"<div class='s'>{esc(_r.get('game') or '')} · {esc(starts(_r.get('starts')))}</div>"
                f"<div class='ev {_cls}'>{_ev * 100:+.1f}% EV · {esc(_b['venue'])}{esc(_size)}</div></div>"
                f"<div class='px'>{esc(_price_label(_b))}<small>fair {esc(_r['fair_odds'])}</small></div></summary>"
                f"<div class='cmp'><div class='cmp-h'><span>Price at {len(_ps)} place{'s' if len(_ps) != 1 else ''}</span>"
                f"<span>Pinnacle fair {esc(_r['fair_odds'])}</span></div>{_rows}"
                f"<div class='cmp-note'>Green = best price. EV compares each price with Pinnacle's fair line. "
                f"Bet size is a quarter-Kelly suggestion (max 3%). Sportsbook prices can be a few hours old.</div></div></details>")
        st.markdown("<div class='hm-opps'>" + "".join(_items) + "</div>", unsafe_allow_html=True)
        st.caption("Plays = +1% EV or better vs Pinnacle's fair line, priced between −200 and +150, across "
                   "moneylines, spreads and totals. Tap one to compare prices. Phone alert at +3%.")
    else:
        quiet("No games priced yet", "The board fills in when the Lab pulls Pinnacle prices (every ~30 minutes).")

    # ---- log a bet you placed, and your record ----
    import sys as _bsys
    for _m in [k for k in _bsys.modules if k == "research_lab" or k.startswith("research_lab.") or k == "bet_log"]:
        del _bsys.modules[_m]
    import bet_log as _bl
    from research_lab import my_bets as _mb
    st.markdown("<div class='section'><span>Log a bet</span></div>", unsafe_allow_html=True)
    _choices = {f"{_r.get('label') or _r['team']} · {_b['venue']} {_price_label(_b)} · {_ev * 100:+.1f}%": (_ev, _r, _b)
                for _ev, _r, _b in (_h_plays or _h_opps)[:20]}
    if not _choices:
        st.caption("Plays you can log show up here.")
    else:
        _pick = st.selectbox("Play", list(_choices), label_visibility="collapsed")
        _ev0, _r0, _b0 = _choices[_pick]
        _am0 = _mb.decimal_to_american(float(_b0.get("decimal") or 2.0))
        _venues = list(dict.fromkeys([p["venue"] for p in sorted(_r0["prices"], key=lambda p: -p.get("decimal", 0))]
                                     + ["DraftKings", "Bovada", "Fliff", "Novig", "ProphetX", "Kalshi", "Polymarket",
                                        "Rebet", "Other"]))
        with st.form(f"logbet_{abs(hash(_pick)) % 10**8}", clear_on_submit=False):
            _c1, _c2 = st.columns(2)
            _amt = _c1.number_input("Amount bet ($)", min_value=0.5, value=10.0, step=5.0)
            _odds_txt = _c2.text_input("Odds you got", value=_am0, help="American odds, e.g. -110 or +135")
            _venue = st.selectbox("Where you bet it", _venues)
            _go = st.form_submit_button("Log bet", type="primary", use_container_width=True)
        if _go:
            _dec = _mb.american_to_decimal(_odds_txt)
            if _dec is None:
                st.error("Enter odds like -110 or +135.")
            elif not _bl.token():
                st.error("Logging needs a GitHub key in the app's secrets (GITHUB_TOKEN). See the setup steps in chat.")
            else:
                _fair = float(_r0.get("fair") or 0)
                try:
                    _bl.append_bet({"label": _r0.get("label") or _r0["team"], "game": _r0.get("game", ""),
                                    "league": _r0.get("sport", ""), "market": _r0.get("market", "moneyline"),
                                    "venue": _venue, "odds": _odds_txt.strip().replace("−", "-"),
                                    "decimal": round(_dec, 4), "stake": round(float(_amt), 2), "fair": _fair,
                                    "ev_at_log": round(_fair * _dec - 1, 4) if _fair else "",
                                    "settle_ticker": _r0.get("kalshi_ticker", ""), "settle_side": _r0.get("settle_side", "YES"),
                                    "starts": _r0.get("starts", "")})
                    st.success(f"Logged: ${float(_amt):,.2f} on {_r0.get('label') or _r0['team']} at {_odds_txt} "
                               f"({_venue}). It settles automatically from the official result.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Couldn't save the bet: {exc}")

    @st.cache_data(ttl=20, show_spinner=False)
    def _my_bets_rows(_tok_set: bool):
        if _tok_set:
            try:
                return _bl.read_log()[0]
            except Exception:
                pass
        return _mb.load_log()
    _mrows = _my_bets_rows(bool(_bl.token()))
    _mres = _mb.load_results()
    if _mrows:
        _ms = _mb.summary(_mrows, _mres)
        st.markdown(
            f"<div class='section'><span>Your bets</span></div>"
            f"<div class='hm-tiles'><div class='hm-tile'><div class='v {tone(_ms['pl'])}'>{money(_ms['pl'], signed=True)}</div>"
            f"<div class='l'>{_ms['won']}–{_ms['lost']} · ROI {pct(_ms['roi'], signed=True) if _ms['roi'] is not None else '—'}</div></div>"
            f"<div class='hm-tile'><div class='v'>{_ms['open']}</div><div class='l'>open · {money(_ms['open_stake'])} at risk</div></div></div>",
            unsafe_allow_html=True)
        _lst = []
        for _x in sorted(_mrows, key=lambda r: r.get("logged_at", ""), reverse=True)[:12]:
            _res = _mres.get(_x.get("bet_id") or "")
            _st = (_res or {}).get("status", "open")
            _plv = (_res or {}).get("pl")
            _right = (money(float(_plv), signed=True) if _plv is not None else "open")
            _cls = tone(float(_plv)) if _plv is not None else ""
            _lst.append(f"<div class='r'><div><div class='t'>{esc(_x.get('label'))} {esc(_x.get('odds'))}</div>"
                        f"<div class='s'>${float(_x.get('stake') or 0):,.2f} at {esc(_x.get('venue'))} · "
                        f"{esc(_x.get('game'))}</div></div><div class='v {_cls}'>{_right}</div></div>")
        st.markdown("<div class='row-list'>" + "".join(_lst) + "</div>", unsafe_allow_html=True)
    with st.expander("🧮 EV calculator", expanded=False):
        _mode = st.radio("Fair price from", ["I know the fair odds", "Both sides of a sharp line"], horizontal=True,
                         label_visibility="collapsed")
        if _mode == "I know the fair odds":
            _fair_txt = st.text_input("Fair odds", value="-208", help="American odds (-208, +150) or cents (66c)")
            _fair_p = _parse_odds(_fair_txt)
        else:
            _c1, _c2 = st.columns(2)
            _a = _parse_odds(_c1.text_input("Your side at the sharp book", value="-215"))
            _bb = _parse_odds(_c2.text_input("Other side", value="+180"))
            _fair_p = (_a / (_a + _bb)) if _a and _bb else None   # remove the vig proportionally
            if _fair_p:
                st.caption(f"Book's margin {((_a + _bb) - 1) * 100:.1f}% removed → fair {_american_from_prob(_fair_p)} "
                           f"({_fair_p * 100:.1f}%)")
        _c3, _c4 = st.columns(2)
        _price_txt = _c3.text_input("Price you can get", value="-194", help="American odds or cents (Kalshi 66c)")
        _stake = _c4.number_input("Stake ($)", min_value=1.0, value=100.0, step=10.0)
        _fee = st.checkbox("Kalshi taker fee (cents prices only)", value=str(_price_txt).strip().lower().endswith(("c", "¢")))
        _pp = _parse_odds(_price_txt)
        if not _fair_p or not _pp:
            st.write("Enter odds like **-194**, **+150**, or a price like **66c**.")
        else:
            _cost = _pp
            if _fee and str(_price_txt).strip().lower().endswith(("c", "¢")):
                import math as _m
                _cost = _pp + _m.ceil(0.07 * 100 * _pp * (1 - _pp) - 1e-9) / 100 / 100   # fee per contract (100-lot)
            _dec = 1.0 / _cost
            _ev = _fair_p * _dec - 1.0
            _ev_d = _stake * _ev
            _verdict = ("Strong value 🔥" if _ev >= 0.05 else "Good value ✅" if _ev >= 0.03 else
                        "Small edge (thin) 👍" if _ev > 0.005 else "About even ➖" if _ev > -0.005 else "Bad price ❌")
            st.markdown(
                f"<div class='hm-tiles'><div class='hm-tile'><div class='v {tone(_ev)}'>{_ev * 100:+.1f}%</div>"
                f"<div class='l'>EV · {esc(_verdict)}</div></div>"
                f"<div class='hm-tile'><div class='v {tone(_ev_d)}'>{money(_ev_d, signed=True)}</div>"
                f"<div class='l'>avg per ${_stake:,.0f} bet</div></div></div>",
                unsafe_allow_html=True)
            st.caption(f"Fair: wins {_fair_p * 100:.1f}% ({_american_from_prob(_fair_p)}). Your price needs it to win "
                       f"{(1 / _dec) * 100:.1f}% to break even ({_american_from_prob(1 / _dec)}). "
                       f"Any price better than {_american_from_prob(_fair_p)} is +EV; 3%+ is where alerts fire.")
    st.page_link("pages/Research_Lab_EV_Finder.py", label="See all prices", icon="🎯")
    st.page_link("pages/Research_Lab_Promo_Converter.py", label="Promo Converter", icon="🎁")

# ---------------------------------------------------------- live $1 sharp --
with tab_live:
    live_rows, ls = load_live_sharp()
    _ls_over = st.session_state.get("live_sharp_live")
    if _ls_over and _ls_over.get("base") == _ls_base_key():
        live_rows = _ls_over["rows"]
        from league import live_sharp as _ls_mod
        ls = _ls_mod.summary(live_rows)
    else:
        st.session_state.pop("live_sharp_live", None)
        _ls_over = None
    _open_now = 0.0
    for _r in live_rows:
        if _r.get("status") == "open" and _num(_r.get("last_mid")) is not None and _num(_r.get("avg_price")) is not None:
            _held = (_num(_r.get("filled")) or 0) - (_num(_r.get("sold")) or 0)
            _open_now += _held * (_num(_r.get("last_mid")) - _num(_r.get("avg_price")))
            if _num(_r.get("sold")):
                _open_now += ((_num(_r.get("sale_proceeds")) or 0) - (_num(_r.get("sale_fee")) or 0)
                              - (_num(_r.get("sold")) or 0) * _num(_r.get("avg_price")))
    _today = datetime.now(EASTERN).date()
    _risk = sum((_num(_r.get("cost")) or 0) + (_num(_r.get("fee")) or 0) for _r in live_rows if _r.get("status") == "open")
    _pays = sum((_num(_r.get("filled")) or 0) - (_num(_r.get("sold")) or 0) for _r in live_rows if _r.get("status") == "open")
    _today_pl = sum(_num(_r.get("pl")) or 0 for _r in live_rows if _r.get("status") in {"won", "lost"}
                    and _when(_r.get("settled_at")) and _when(_r.get("settled_at")).astimezone(EASTERN).date() == _today)
    from league import live_sharp as _ls_cmp
    # paper over the same window as live, so the two are comparable (paper's all-time record
    # includes the days before live started)
    _vs = _ls_cmp.vs_paper(live_rows, league.to_dict("records") if not league.empty else [])
    _sharp_roi = _vs["paper"]["roi"] if _vs else None
    _budget = float(ls["budget"] or 0)
    _fill = max(0.0, min(100.0, 100.0 * float(ls["spent_today"] or 0) / _budget)) if _budget else 0.0
    st.markdown(
        f"""
        <div class='pe-hero'>
          <div class='top'>
            <div><div class='k'>Live $5 Sharp · real money</div>
              <div class='big {tone(ls["pl"])}'>{money(ls["pl"], signed=True)}</div>
              {units_line(ls.get("units"))}</div>
          </div>
          <div class='stats'>
            <div class='stat'><div class='k'>Record</div><div class='v'>{ls["won"]}–{ls["lost"]}</div></div>
            <div class='stat'><div class='k'>ROI</div><div class='v {tone(ls["roi"] or 0)}'>{pct(ls["roi"], signed=True)}</div></div>
            <div class='stat'><div class='k'>Paper, same days</div><div class='v {tone(_sharp_roi or 0)}'>{pct(_sharp_roi, signed=True) if _sharp_roi is not None else "—"}</div></div>
          </div>
          <div class='bar'>
            <div class='lab'><span>Today's budget</span><span><b style='color:var(--text)'>{money(ls["spent_today"])}</b> of {money(ls["budget"])}</span></div>
            <div class='track'><div class='fill' style='width:{_fill:.0f}%'></div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(pl_split(open_n=ls["open"], open_pl=_open_now, at_risk=_risk, payout=_pays,
                         done_n=ls["won"] + ls["lost"], done_pl=ls["pl"], record=f'{ls["won"]}–{ls["lost"]}',
                         today_pl=_today_pl), unsafe_allow_html=True)
    _sc = _ls_cmp.scorecard(live_rows)
    _nx = _sc["next"]
    _goal = _nx["need"] if _nx else _sc["n"]
    _prog = max(0.0, min(100.0, 100.0 * _sc["n"] / _goal)) if _goal else 100.0
    _roi_v = pct(_sc["roi"], signed=True) if _sc["roi"] is not None else "—"
    _roi_sub = (f"likely {pct(_sc['roi'] - _sc['roi_half'], signed=True)} to {pct(_sc['roi'] + _sc['roi_half'], signed=True)}"
                if _sc["roi"] is not None and _sc["roi_half"] is not None else "")
    _dr_v = f"{_sc['drift']:+.1f}¢" if _sc["drift"] is not None else "—"
    _dr_sub = (f"±{_sc['drift_half']:.1f}¢ · {_sc['n_drift']} bet{'s' if _sc['n_drift'] != 1 else ''}" if _sc["drift_half"] is not None
               else f"{_sc['n_drift']} bet{'s' if _sc['n_drift'] != 1 else ''} so far" if _sc["drift"] is not None else "collecting")
    def _goal_line(target_txt, hit, have, early=""):
        if early:
            mark = f"<span style='color:var(--muted);font-weight:500'>{early}</span>"
        elif not have:
            mark = "—"
        else:
            mark = "<span class='up'>✓</span>" if hit else "<span class='down'>below</span>"
        return f"<div class='sub goal'>Goal {target_txt} {mark}</div>"
    _roi_goal = _goal_line(f"+{_sc.get('target_roi', 0.03) * 100:.0f}%", _sc.get("roi_hit"), _sc["roi"] is not None)
    _dr_min = getattr(_ls_cmp, "DRIFT_MIN_BETS", 100)
    _dr_few = _sc["drift"] is not None and _sc["n_drift"] < _dr_min
    _dr_early = " " if _dr_few else ""
    _dr_goal = _goal_line(f"+{_sc.get('target_drift', 1.0):.0f}¢", _sc.get("drift_hit"), _sc["drift"] is not None, _dr_early)
    if _dr_few:
        _dr_sub = f"{_sc['n_drift']} of {_dr_min} bets needed"
    _vt = {"good": "up", "bad": "down"}.get(_sc["tone"], "")
    st.markdown(
        f"""
        <div class='pe-hero'>
          <div class='top'><div><div class='k'>Edge scorecard · when to raise the bet</div>
            <div class='v {_vt}' style='font-weight:600;margin-top:.25rem'>{esc(_sc["verdict"])}</div></div></div>
          <div class='stats'>
            <div class='stat'><div class='k'>Settled bets</div><div class='v'>{_sc["n"]}</div><div class='sub goal'>Goal {_goal:,}</div></div>
            <div class='stat'><div class='k'>ROI</div><div class='v {tone(_sc["roi"] or 0)}'>{_roi_v}</div>{_roi_goal}<div class='sub'>{_roi_sub}</div></div>
            <div class='stat'><div class='k'>Price drift</div><div class='v {tone(_sc["drift"] or 0)}'>{_dr_v}</div>{_dr_goal}<div class='sub'>{_dr_sub}</div></div>
          </div>
          <div class='bar'>
            <div class='lab'><span>{('Next: ' + _nx['size'] + ' bets') if _nx else 'All checkpoints cleared'}</span>
              <span><b style='color:var(--text)'>{_sc["n"]}</b> of {_goal} bets</span></div>
            <div class='track'><div class='fill' style='width:{_prog:.0f}%'></div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Step up when the bet count is reached and both goals are hit: ROI of +3% or better "
               "(clears Kalshi's fees with room to spare) and price drift of +1¢ or better (about a 2% "
               "edge). At 1,000 bets the whole ROI range must also be above zero. Drift is the price "
               "~30 min after the buy minus what you paid; it counts once 100 bets have a reading. "
               "Keep each bet near 1–2% of your Kalshi cash.")
    _ls_on = (league_status.get("live_sharp") or {}).get("enabled")
    st.markdown(
        "<div class='pe-lede' style='font-size:.8rem'>" + ("" if _ls_on else "<b>Off.</b> ")
        + "Copies every Sharp pick with a real Kalshi order · $5 per bet · $300/day cap · "
          "buys only at the price the pick saw."
        + ("" if _ls_on else " Turn it on with the LIVE_SHARP_ENABLED repo variable.")
        + "</div>", unsafe_allow_html=True)
    _lsr = league_status.get("live_sharp") or {}
    _cash = _lsr.get("cash")
    if _cash is not None:
        from league import live_sharp as _lsm
        _bets_left = int(_cash // (_lsm.per_bet() * 1.03))
        _days = _cash / _lsm.daily_budget()
        _pools = " · ".join(f"{_lsm.POOL_NAMES.get(int(k), 'Exchange ' + k)} ${v:.2f}"
                            for k, v in (_lsr.get("pools") or {}).items())
        _bl = _lsm.balance_log()
        _prev = [b for b in _bl if (b.get('day') or '') < _lsm.et_day(_lsm.book.now_utc())]
        _chg = (f" · {_cash - float(_prev[-1]['cash']):+.2f} today" if _prev else "")
        (st.error if _cash < _lsm.LOW_TOTAL else st.info)(
            f"**Kalshi cash: ${_cash:.2f}**{_chg} — covers about {_bets_left} more ${_lsm.per_bet():g} bets "
            f"({_days:.1f}× the ${_lsm.daily_budget():.0f} daily cap)."
            + (f" Deposit soon: you get a push alert under ${_lsm.LOW_TOTAL:.0f}." if _cash < _lsm.LOW_TOTAL else "")
            + (f"\n\n{_pools}" if _pools else ""))
        if len(_bl) >= 2:
            import pandas as _pd
            _df = _pd.DataFrame(_bl)
            _df["at"] = _pd.to_datetime(_df["at"]); _df["cash"] = _pd.to_numeric(_df["cash"], errors="coerce")
            st.line_chart(_df.set_index("at")[["cash"]], height=160)
    for _lp in _lsr.get("low_pools") or []:
        st.warning(f"Kalshi pool low: **{_lp['name']}** (Exchange {_lp['pool']}) has ${_lp['cash']:.2f}. "
                   "Bets on those markets are skipped until you move cash there at "
                   "kalshi.com/account/exchange-indexes → Transfer.")
    if _lsr.get("kalshi_ok") is False:
        st.caption(f"⚠️ Kalshi key check failed last round: {_lsr.get('kalshi_error', '')}")
    if _lsr.get("error"):
        st.caption(f"Last round: {_lsr['error']}")

    def _live_card(r):
        return bet_card(r, _games.get(str(r.get("ticker") or "")), n=_num(r.get("filled")),
                        entry=_num(r.get("avg_price")), mark=_num(r.get("last_mid")))

    def _live_row(r):
        return _live_row_body(r, None)

    def _live_row_body(r, _pick):
        title = f"{esc(str(r.get('side') or '').title())} on {esc(r.get('title') or r.get('ticker'))}"
        stt = str(r.get("status") or "")
        filled, avg = _num(r.get("filled")) or 0, _num(r.get("avg_price"))
        cost, fee = _num(r.get("cost")) or 0.0, _num(r.get("fee")) or 0.0
        placed = _when(r.get("placed_at"))
        when = placed.astimezone(EASTERN).strftime("%a ") + local_time(placed) if placed else ""
        buy = f"{filled:g} at {cents(avg)} = {money(cost + fee)} in" if filled else ""
        paper = _num(r.get("paper_entry"))
        slip = (f", {abs(avg - paper) * 100:.0f}¢ {'worse' if avg > paper else 'better'} than paper"
                if avg is not None and paper is not None and abs(avg - paper) >= 0.005 else "")
        if stt == "open":
            ends = _when(r.get("ends_at") or r.get("close_time"))
            end_txt = ""
            if ends:
                secs = (ends - datetime.now(timezone.utc)).total_seconds()
                end_txt = ("waiting on Kalshi to settle" if secs <= 0 else f"ends in {int(secs // 60)}m" if secs < 3600
                           else f"ends {ends.astimezone(EASTERN).strftime('%a')} {local_time(ends)}" if secs < 36 * 3600
                           else f"ends {ends.astimezone(EASTERN).strftime('%b %-d')}")
            if r.get("_market_status") and r.get("_market_status") not in {"open", "active", ""}:
                end_txt = "match over, waiting on Kalshi to settle"
            mark = _num(r.get("last_mid"))
            if mark is not None and avg is not None:
                move = (mark - avg) * 100
                trend = "winning" if move > 0.4 else "losing" if move < -0.4 else "even"
                return (title, f"now {cents(mark)} ({trend}), {esc(end_txt)}<br>{esc(when)}, {buy}{slip}, "
                               f"pays {money(filled)} if it wins",
                        f"<span class='num'>{money(filled * (mark - avg), signed=True)}</span>", tone(move))
            return (title, f"{esc(end_txt or when)}<br>{esc(when)}, {buy}{slip}, pays {money(filled)} if it wins",
                    f"<span class='num'>{cents(avg)}</span>", "")
        if stt in {"won", "lost"}:
            clv = _num(r.get("clv_cents"))
            if r.get("result") == "sold":
                return (title, f"Cashed out{' (' + esc(r.get('note')) + ')' if r.get('note') else ''}. {esc(when)}, {buy}{slip}",
                        f"<span class='num'>{money(r.get('pl'), signed=True)}</span>", tone(r.get("pl")))
            return (title, f"{'Won' if stt == 'won' else 'Lost'}" + (f", {clv:+.0f}¢ vs close" if clv is not None else "")
                    + f". {esc(when)}, {buy}{slip}",
                    f"<span class='num'>{money(r.get('pl'), signed=True)}</span>", tone(r.get("pl")))
        if stt == "void":
            return (title, f"Voided, refunded. {esc(when)}", "<span class='num'>$0.00</span>", "")
        if stt == "no_fill":
            return (title, f"No fill. {esc(when)}, price moved past {cents(r.get('limit_price'))}", "—", "")
        note = str(r.get("note") or "")
        m = re.search(r"Trading_is_not_currently_allowed_in_(\w+?)_on_markets", note)
        if m:
            note = f"Kalshi blocked it: sports trading isn't allowed from the server's location ({m.group(1)})"
        elif len(note) > 90:
            note = note[:90] + "…"
        return (title, f"{esc(stt.replace('_', ' ').title())}. {esc(when)} {esc(note)}", "—", "down")

    if not live_rows:
        quiet("No real bets yet", "The first one goes out on the next Sharp pick after it's turned on.")
    else:
        ordered = sorted(live_rows, key=lambda r: str(r.get("placed_at") or ""), reverse=True)
        opn = [r for r in ordered if r.get("status") == "open"]
        rest = [r for r in ordered if r.get("status") != "open"][:40]
        _games = live_scores(tuple(sorted({(str(r.get("ticker") or ""), str(r.get("title") or "")) for r in opn})))
        unreal = sum((_num(r.get("filled")) or 0) * ((_num(r.get("last_mid")) or 0) - (_num(r.get("avg_price")) or 0))
                     for r in opn if _num(r.get("last_mid")) is not None and _num(r.get("avg_price")) is not None)
        if _ls_over:
            parts = []
            if _ls_over.get("marked"):
                parts.append(f"{_ls_over['marked']} updated")
            if _ls_over.get("waiting"):
                parts.append(f"{_ls_over['waiting']} not trading right now (match over or paused)")
            if _ls_over.get("settled"):
                parts.append(f"{_ls_over['settled']} just settled (made official on the next round)")
            as_of = f"Checked {ago(_ls_over['checked_at'])}" + (": " + ", ".join(parts) if parts else "")
        else:
            as_of = f"Prices from the last round, {ago(lg_last)}"
        st.markdown(f"<div class='section'><span>On the board<span class='n'>{len(opn)}</span></span></div>"
                    + (f"<div class='pe-lede' style='margin:.2rem 0 0;font-size:.8rem'>{esc(as_of)}.</div>" if opn else ""),
                    unsafe_allow_html=True)
        if opn or _ls_over:
            st.button("↻ Refresh live prices", key="refresh_live_sharp", width="stretch",
                      on_click=_refresh_all)
            if st.session_state.get("live_sharp_err"):
                st.caption(f"Couldn't refresh: {st.session_state['live_sharp_err']}")
        if opn:
            st.markdown(cards_html(_live_card(r) for r in opn), unsafe_allow_html=True)
        else:
            st.markdown("<div class='pe-lede'>Nothing open right now.</div>", unsafe_allow_html=True)
        if _vs and _vs["paper"]["bets"]:
            _since = _when(_vs["since"])
            _since_txt = _since.astimezone(EASTERN).strftime("%b %-d") if _since else "live started"
            st.markdown(f"<div class='section'><span>Live vs paper since {esc(_since_txt)}</span></div>",
                        unsafe_allow_html=True)
            def _vs_row(g, note):
                return (esc(g["group"]), f"{g['won']}-{g['lost']} · {esc(note)}",
                        f"<span class='num'>{pct(g['roi'], signed=True) if g['roi'] is not None else '—'}</span>",
                        tone(g["roi"] or 0))
            st.markdown(rows_html([
                _vs_row(_vs["live"], "Your real bets"),
                _vs_row(_vs["paper_copied"], "The same picks on paper. Should track the live row closely"),
                _vs_row(_vs["paper_missed"], "Picks live couldn't place: no fill, blocked or skipped"),
                _vs_row(_vs["paper"], "Every paper Sharp pick since live started"),
            ]), unsafe_allow_html=True)
            st.markdown("<div class='pe-lede' style='font-size:.8rem'>If live and paper-same-bets match, "
                        "execution is fine and any gap with paper is which picks got placed, not how.</div>",
                        unsafe_allow_html=True)
        from league import live_sharp as _ls_daily
        _sports = _ls_daily.by_sport(live_rows)
        if _sports:
            st.markdown("<div class='section'><span>By sport</span></div>", unsafe_allow_html=True)
            def _sport_row(g):
                if g["bets"]:
                    roi = f", {pct(g['roi'], signed=True)} ROI" if g["roi"] is not None else ""
                    detail = f"{g['won']}-{g['lost']}{roi} on {money(g['staked'])}"
                else:
                    detail = "nothing settled yet"
                if g["open"]:
                    detail += f", {g['open']} open"
                return (esc(g["group"]), detail,
                        f"<span class='num'>{money(g['pl'], signed=True)}</span>", tone(g["pl"]))
            st.markdown(rows_html(_sport_row(g) for g in _sports), unsafe_allow_html=True)
        _days = _ls_daily.daily(live_rows)
        if _days:
            st.markdown("<div class='section'><span>Calendar</span></div>", unsafe_allow_html=True)
            _cm = st.session_state.setdefault("cal_month", (_today.year, _today.month))
            _first = datetime.strptime(_days[0]["day"], "%Y%m%d")

            def _cal_step(delta: int) -> None:
                y, m = st.session_state["cal_month"]
                m += delta
                y, m = (y - 1, 12) if m < 1 else (y + 1, 1) if m > 12 else (y, m)
                st.session_state["cal_month"] = (y, m)

            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.button("‹", key="cal_prev", on_click=_cal_step, args=(-1,),
                          disabled=_cm <= (_first.year, _first.month))
                st.button("›", key="cal_next", on_click=_cal_step, args=(1,),
                          disabled=_cm >= (_today.year, _today.month))
                _cal_mode = st.segmented_control("Show in", ["$", "u"], default="$", key="cal_mode",
                                                 label_visibility="collapsed")
            st.markdown(pl_calendar(_days, _cm[0], _cm[1], _cal_mode or "$", _today), unsafe_allow_html=True)
            st.caption("Settled profit by the day each bet settled (Eastern). u = units, each bet's "
                       "profit divided by its bet size (\\$1 before Oct 18, \\$2 Oct 18–19, \\$5 from Oct 20).")
            _day_list = st.expander("Day-by-day details")
            def _day_row(d):
                dt = datetime.strptime(d["day"], "%Y%m%d")
                rec = f"{d['won']}-{d['lost']}" if d["won"] + d["lost"] else "nothing settled"
                roi = f", {pct(d['roi'], signed=True)} ROI" if d["roi"] is not None else ""
                orders = (f"<br>{d['orders']} orders: {d['filled']} filled, {d['missed']} missed"
                          + (f", {d['blocked']} blocked" if d["blocked"] else "")) if d["orders"] else ""
                return (esc(dt.strftime("%a %b %-d")),
                        f"{rec}{roi}, running total {money(d['total'], signed=True)}{orders}",
                        f"<span class='num'>{money(d['pl'], signed=True)}</span>", tone(d["pl"]))
            _day_list.markdown(rows_html(_day_row(d) for d in reversed(_days)), unsafe_allow_html=True)
        st.markdown(f"<div class='section'><span>Finished<span class='n'>{len(rest)}</span></span></div>", unsafe_allow_html=True)
        if rest:
            st.markdown(rows_html(_live_row(r) for r in rest), unsafe_allow_html=True)
        if ls["no_fill"]:
            st.markdown(f"<div class='pe-lede'>{ls['no_fill']} pick(s) didn't fill because the price moved "
                        "before the order arrived. That's the gap between paper and real.</div>",
                        unsafe_allow_html=True)

# ----------------------------------------------------------------- league --
with tab_league:
    _clv_mean = lg_clv.mean() if len(lg_clv) else None
    st.markdown(
        f"""
        <div class='pe-hero'>
          <div class='top'>
            <div><div class='k'>Paper league · since {esc(since)}</div>
              <div class='big {tone(lg_pl)}'>{money(lg_pl, signed=True)}</div></div>
          </div>
          <div class='stats'>
            <div class='stat'><div class='k'>Record</div><div class='v'>{esc(lg_record).replace("-", "–")}</div></div>
            <div class='stat'><div class='k'>Beat close</div><div class='v {tone(_clv_mean or 0)}'>{(f"{_clv_mean:+.1f}¢") if _clv_mean is not None else "—"}</div></div>
            <div class='stat'><div class='k'>Last round</div><div class='v'>{esc(local_time(lg_last)) if lg_last else "—"}</div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not league.empty:
        _st = league.get("status", pd.Series(dtype=str)).fillna("")
        _o = league[_st == "open"]
        _d = league[_st.isin(["won", "lost"])]
        _num_s = lambda df, c: pd.to_numeric(df.get(c), errors="coerce").fillna(0) if not df.empty and c in df else pd.Series(dtype=float)
        _mark = pd.to_numeric(_o.get("last_mid"), errors="coerce") if not _o.empty else pd.Series(dtype=float)
        _ent = pd.to_numeric(_o.get("entry_price"), errors="coerce") if not _o.empty else pd.Series(dtype=float)
        _ctr = _num_s(_o, "contracts")
        _opl = float(((_mark - _ent) * _ctr).dropna().sum()) if not _o.empty else 0.0
        _sat = pd.to_datetime(_d.get("settled_at"), errors="coerce", utc=True) if not _d.empty else pd.Series(dtype="datetime64[ns, UTC]")
        _tod = float(_num_s(_d, "pl")[_sat.dt.tz_convert(EASTERN).dt.date == datetime.now(EASTERN).date()].sum()) if not _d.empty else 0.0
        st.markdown(pl_split(open_n=len(_o), open_pl=_opl,
                             at_risk=float(_num_s(_o, "cost").sum() + _num_s(_o, "fee").sum()), payout=float(_ctr.sum()),
                             done_n=len(_d), done_pl=float(_num_s(_d, "pl").sum()),
                             record=f"{int((_st == 'won').sum())}–{int((_st == 'lost').sum())}", today_pl=_tod,
                             label="Paper total if everything closed now"), unsafe_allow_html=True)
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

        def _buy(r):
            n = int(_num(r.get("contracts")) or 0)
            cost, fee = _num(r.get("cost")) or 0.0, _num(r.get("fee")) or 0.0
            unit = "contract" if n == 1 else "contracts"
            return (f"<br>{n} {unit} at {cents(r.get('entry_price'))} = {money(cost)} "
                    f"+ {money(fee)} fee ({money(cost + fee)} in), " + {
                        "won": f"paid back {money(n)}", "lost": "paid back $0.00",
                        "void": f"{money(cost)} refunded"}.get(str(r.get("status")), f"pays {money(n)} if it wins"))

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
                    sub = (f"{strat}, now {cents(mark)} ({trend}), {esc(_ends(r))}"
                           f"{_buy(r)}{esc(_tap_note(r))}")
                    return (title, sub, f"<span class='num'>{value}</span>", tone(move))
                return (title, f"{strat}, {esc(_ends(r))}{_buy(r)}{esc(_tap_note(r))}",
                        f"<span class='num'>{cents(entry)}</span>", "")
            if stt == "void":
                return (title, f"{strat}, market voided, stake returned{_buy(r)}", "<span class='num'>$0.00</span>", "")
            word = "Won" if stt == "won" else "Lost"
            clv = _num(r.get("clv_cents"))
            sub = (f"{word}. {strat}" + (f", {clv:+.0f}¢ vs close" if clv is not None else "")
                   + f"{_buy(r)}{esc(_tap_note(r))}")
            return (title, sub, f"<span class='num'>{money(r.get('pl'), signed=True)}</span>", tone(r.get("pl")))

        live = league[league["status"].fillna("") == "open"].copy()
        _paper_games = live_scores(tuple(sorted({(str(r.get("ticker") or ""), str(r.get("title") or ""))
                                                 for r in live.to_dict("records")}))) if not live.empty else {}
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
        st.markdown(f"<div class='section'><span>Paper bets running<span class='n'>{len(live)}</span></span></div>"
                    + (f"<div class='pe-lede' style='margin:.2rem 0 0;font-size:.8rem'>{esc(as_of)}.</div>"
                       if len(live) else ""),
                    unsafe_allow_html=True)
        if len(live) or _live_override:
            st.button("↻ Refresh live prices", key="refresh_live", width="stretch",
                      on_click=_refresh_all)
            if st.session_state.get("league_live_error"):
                st.caption(f"Couldn't refresh: {st.session_state['league_live_error']}")
        if live.empty:
            st.markdown("<div class='pe-lede'>No paper bets running right now.</div>", unsafe_allow_html=True)
        else:
            st.markdown(cards_html(
                bet_card(r, _paper_games.get(str(r.get("ticker") or "")), n=_num(r.get("contracts")),
                         entry=_num(r.get("entry_price")), mark=_num(r.get("last_mid")),
                         extra=esc(LEAGUE_LABELS.get(r.get("strategy"), r.get("strategy"))))
                for r in live.to_dict("records")), unsafe_allow_html=True)

        st.markdown(f"<div class='section'><span>Finished<span class='n'>{len(done)}</span></span></div>", unsafe_allow_html=True)
        if done.empty:
            st.markdown("<div class='pe-lede'>Nothing settled yet. You'll get a push when bets finish.</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown(rows_html(_pick_row(r) for r in done.to_dict("records")), unsafe_allow_html=True)
        # ---- Sharp scorecard: is it ready for a real-money test? ----
        from league import scorecard as league_scorecard
        sc = league_scorecard.scorecard(league, "sharp")
        if sc:
            st.markdown("<div class='section'>Sharp scorecard</div>", unsafe_allow_html=True)
            since_sc = sc["since"].tz_convert(EASTERN).strftime("%b %-d") if pd.notna(sc["since"]) else "the start"
            st.markdown(
                f"""
                <div class='pe-board'>
                  <div><div class='k'>Sharp profit since {esc(since_sc)}</div>
                    <div class='big num {tone(sc["pl"])}'>{money(sc["pl"], signed=True)}</div>
                    <div class='k'>{money(sc["staked"])} staked, {sc["open"]} open</div></div>
                  <div class='side'>
                    <div><div class='k'>Record</div><div class='v num'>{sc["won"]}-{sc["lost"]}</div></div>
                    <div><div class='k'>ROI</div><div class='v num {tone(sc["roi"] or 0)}'>{pct(sc["roi"], signed=True)}</div></div>
                    <div><div class='k'>Beat close</div><div class='v num {tone(sc["clv"] or 0)}'>{(f"{sc['clv']:+.1f}¢") if sc["clv"] is not None else "—"}</div></div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            done_checks = sum(1 for c in sc["checks"] if c[3])
            verdict = ("Ready for a small real-money test." if sc["ready"]
                       else f"Not ready for real money yet: {done_checks} of {len(sc['checks'])} checks passed.")
            st.markdown(f"<div class='pe-lede' style='margin:.4rem 0 0'><b>{esc(verdict)}</b></div>",
                        unsafe_allow_html=True)
            st.markdown(rows_html(
                (("✅ " if ok else "⬜ ") + esc(label), esc(detail), "", "up" if ok else "")
                for label, _v, _need, ok, detail in sc["checks"]
            ), unsafe_allow_html=True)
            if len(sc["curve"]) >= 2:
                st.line_chart(sc["curve"].set_index("when")["Profit"], height=180, color="#F2C14E")
            if sc["groups"]:
                note = ""
                if sc["top_share"] and sc["top_share"] >= 0.5 and sc["bets"] >= 5:
                    note = (f"<div class='pe-lede' style='margin:.2rem 0 0'>{sc['top_share']:.0%} of settled bets are "
                            f"{esc(sc['groups'][0]['group'])}, so the record mostly reflects that one market.</div>")
                st.markdown("<div class='section'>Sharp by market</div>" + note, unsafe_allow_html=True)
                st.markdown(rows_html(
                    (esc(g["group"]), f"{g['won']}-{g['lost']}, {pct(g['roi'], signed=True)} ROI on {money(g['staked'])}",
                     f"<span class='num'>{money(g['pl'], signed=True)}</span>", tone(g["pl"]))
                    for g in sc["groups"]
                ), unsafe_allow_html=True)
            if not sc["daily"].empty:
                st.markdown("<div class='section'>Sharp by day</div>", unsafe_allow_html=True)
                st.markdown(rows_html(
                    (esc(pd.Timestamp(d["day"]).strftime("%a %b %-d")),
                     f"{int(d['won'])}-{int(d['lost'])}, {pct(d['roi'], signed=True)} ROI, "
                     f"running total {money(d['total'], signed=True)}",
                     f"<span class='num'>{money(d['pl'], signed=True)}</span>", tone(d["pl"]))
                    for d in sc["daily"].sort_values("day", ascending=False).to_dict("records")
                ), unsafe_allow_html=True)

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

        try:
            from league import book as _lbook
            _shadow = _lbook.standings(_lbook.load(_lbook.SHADOW_LEDGER))
        except Exception:
            _shadow = []
        st.markdown("<div class='section'>Benched, still graded</div>", unsafe_allow_html=True)
        if _shadow:
            st.markdown(rows_html(
                (esc(LEAGUE_LABELS.get(s["strategy"], s["strategy"].title())),
                 f"{s['won']}-{s['lost']} record, {s['open']} open"
                 + (f", {pct(s['roi'], signed=True)} ROI" if s["roi"] is not None else "")
                 + "<br>Off the board: picks are tracked here only, never in the standings or Live Sharp.",
                 f"<span class='num'>{money(s['pl'], signed=True)}</span>", tone(s["pl"]))
                for s in sorted(_shadow, key=lambda s: -s["pl"])
            ), unsafe_allow_html=True)
        else:
            st.markdown("<div class='pe-lede'>Weather, crypto and Polymarket are benched. Their would-be "
                        "picks are graded here from Oct 19 on; the first ones show up after the next round.</div>",
                        unsafe_allow_html=True)

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

# ------------------------------------------------------------ research lab --
with tab_lab:
    import sys as _lsys
    for _m in [k for k in _lsys.modules if k == "research_lab" or k.startswith("research_lab.")]:
        del _lsys.modules[_m]  # always the newest lab code (Streamlit Cloud keeps old modules around)
    from research_lab import config as _lcfg, metrics as _lmet
    from research_lab.ledger import Store as _LStore
    st.markdown("<div class='pe-lede'>Strategies tested side by side on <b>paper</b>, each with its own "
                "$500 virtual bankroll. Tap one for its bets, misses and model checks.</div>", unsafe_allow_html=True)
    _tools = [("Research_Lab_EV_Finder", "+EV Finder", "Best price across all your books vs Pinnacle"),
              ("Research_Lab_Best_Price", "Best Price", "Were your Sharp picks cheaper on Polymarket?"),
              ("Research_Lab_Heavy_Flow", "Heavy Flow test", "Do Sharp picks with 60+ whale trades do better?"),
              ("Research_Lab_Promo_Converter", "Promo Converter", "Turn bonus bets into cash")]
    st.markdown("<div class='hm-tiles'>" + "".join(
        f"<a class='hm-tile lab-tool' href='/{pg}' target='_self'><div class='v' style='font-size:1.05rem'>{esc(t)}</div>"
        f"<div class='l'>{esc(d)}</div></a>" for pg, t, d in _tools) + "</div>", unsafe_allow_html=True)
    _pages = {"VALUE_ONLY_v1": "Research_Lab_Value_Only", "HYBRID_SHARP_VALUE_v1": "Research_Lab_Hybrid",
              "STRUCTURAL_ARB_v1": "Research_Lab_Structural_Arb", "VALUE_ONLY_v2": "Research_Lab_Value_Pinnacle",
              "HYBRID_SHARP_VALUE_v2": "Research_Lab_Hybrid_Pinnacle", "EV_FINDER_v1": "Research_Lab_EV_Finder",
              "EV_FINDER_v2": "Research_Lab_Todays_Plays", "EV_FINDER_v3": "Research_Lab_Todays_Plays_Band"}
    _cards = []
    try:
        _sh = _lmet.sharp_summary()
        _cards.append(("Research_Lab_Compare", "Sharp (benchmark)", "Follows big public Kalshi trades", _sh, None))
    except Exception:
        pass
    try:  # Trader Follow: alerts on proven Polymarket bettors' pre-game bets ($10 paper each)
        for _m in [k for k in _lsys.modules if k == "trader_follow" or k.startswith("trader_follow.")]:
            del _lsys.modules[_m]
        from trader_follow import core as _tf
        _tfs = _tf._stats(_tf._alerted(_tf.load_ledger(), True))
        _cards.append(("Research_Lab_Trader_Follow", "Trader Follow", "Copies big pre-game bets from top Polymarket bettors",
                       {"won": _tfs["wins"], "lost": _tfs["losses"], "roi": _tfs["roi_copy"], "open": _tfs["open"]}, None))
    except Exception:
        pass
    for _spec in _lcfg.RESEARCH_STRATEGIES:
        try:
            _sm = _lmet.summarize(_LStore(_spec.key).positions)
        except Exception:
            continue
        _short = {"VALUE_ONLY_v1": "Sportsbook consensus vs Kalshi", "HYBRID_SHARP_VALUE_v1": "Value + matching Kalshi trade flow",
                  "STRUCTURAL_ARB_v1": "Locked-profit price gaps on Kalshi", "VALUE_ONLY_v2": "Pinnacle's fair line vs Kalshi",
                  "HYBRID_SHARP_VALUE_v2": "Pinnacle value + trade flow", "EV_FINDER_v1": "Best price on any of your books",
                  "EV_FINDER_v2": "Every +1% play, any odds",
                  "EV_FINDER_v3": "+1% plays priced -200 to +150 (what you bet)"}
        _cards.append((_pages.get(_spec.key, "Research_Lab_Compare"), _spec.label, _short.get(_spec.key, ""), _sm, _sm["bankroll"]))
    _html = []
    for _pg, _name, _desc, _sm, _bank in _cards:
        _n = _sm["won"] + _sm["lost"]
        _wr = f"{100 * _sm['won'] / _n:.0f}%" if _n else "—"
        _roi = pct(_sm["roi"], signed=True) if _sm["roi"] is not None else "—"
        _html.append(
            f"<a class='lab-card' href='/{_pg}' target='_self'>"
            f"<div class='lc-top'><div><div class='lc-name'>{esc(_name)}</div><div class='lc-desc'>{esc(_desc)}</div></div>"
            f"<div class='lc-bank'>{('$' + format(_bank, ',.0f')) if _bank is not None else ''}</div></div>"
            f"<div class='lc-stats'><div><b>{_sm['won']}–{_sm['lost']}</b><span>Record</span></div>"
            f"<div><b>{_wr}</b><span>Win rate</span></div>"
            f"<div><b class='{tone(_sm['roi'] or 0)}'>{_roi}</b><span>ROI</span></div>"
            f"<div><b>{_sm['open']}</b><span>Open</span></div></div></a>")
    st.markdown("<div class='section'><span>Strategies</span></div><div class='lab-cards'>" + "".join(_html) + "</div>",
                unsafe_allow_html=True)
    st.page_link("pages/Research_Lab_Compare.py", label="Compare all side by side", icon="📊")
