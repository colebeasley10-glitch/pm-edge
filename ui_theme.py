"""Shared SharpIQ look (fonts, colors, cards) for the home page and the full dashboard."""
import streamlit as st


def apply() -> None:
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
        .lc-stats { display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:.3rem; margin-top:.7rem; padding-top:.65rem; border-top:1px solid var(--line); }
        .lc-stats b { display:block; font-weight:800; font-size:.95rem; white-space:nowrap; }
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
