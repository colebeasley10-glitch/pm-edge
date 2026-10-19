"""Streamlit views for the Strategy Research Lab (read-only, PAPER results)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from research_lab import config, metrics
from research_lab.ledger import LedgerError, Store, loads, parse_dt
from research_lab.portfolio import EASTERN, available_cash

PAPER_BANNER = "🧪 **PAPER TRADING ONLY.** Simulated bets with a virtual bankroll. No real money, no real orders."


def _money(x) -> str:
    if x is None:
        return "—"
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def _when(v) -> str:
    dt = parse_dt(v)
    return dt.astimezone(EASTERN).strftime("%b %d %I:%M %p") if dt else "—"


def load_store(spec) -> Store | None:
    try:
        return Store(spec.key)
    except LedgerError as exc:
        st.error(f"{spec.label} ledger couldn't be read: {exc}")
        return None


def load_status() -> dict:
    p = config.DATA_DIR / "status.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def _top_bar() -> None:
    """Back link plus a tiny ↻ that reloads the newest saved data."""
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center",
                      key="lab_top"):
        try:
            st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
        except Exception:  # page opened outside the multipage app (e.g. tests)
            pass
        if st.button("↻", key="lab_refresh", type="tertiary", help="Refresh: newest data"):
            st.cache_data.clear()
            st.rerun()
    st.markdown("<style>.st-key-lab_refresh .stButton button{min-height:0!important;height:2.4rem!important;"
                "width:2.4rem!important;padding:0!important;font-size:1.25rem!important;line-height:1!important;"
                "border-radius:12px!important;border:1px solid #262A31!important;background:#15181D!important;"
                "box-shadow:none!important;color:#F4F6F8!important}"
                ".st-key-lab_refresh .stButton button:hover{border-color:#F5B800!important;color:#F5B800!important}"
                "</style>", unsafe_allow_html=True)


def header(title: str) -> None:
    st.set_page_config(page_title=f"Research Lab: {title}", page_icon="🧪", layout="centered")
    _top_bar()
    st.title(title)
    st.info(PAPER_BANNER)


# ------------------------------------------------------------------ comparison

def render_compare() -> None:
    header("Strategy Research Lab")
    status = load_status()
    window = st.radio("Window", ["Since lab start", "All time"], horizontal=True,
                      help="Since lab start compares every strategy over the same dates.")
    since = min(s.started for s in config.RESEARCH_STRATEGIES) if window == "Since lab start" else None
    rows, hist = [], []
    sharp = metrics.sharp_summary(since=since)
    rows.append(_overview_row(config.SHARP_EXISTING_LABEL, sharp, bankroll=None))
    for h in sharp["history"]:
        hist.append({"strategy": "Existing Sharp", "at": h["at"], "cum_pl": h["cum_pl"]})
    for spec in config.RESEARCH_STRATEGIES:
        stv = load_store(spec)
        if stv is None:
            continue
        s = metrics.summarize(stv.positions)
        rows.append(_overview_row(f"{spec.label} v{spec.version}", s, bankroll=s["bankroll"]))
        for h in s["history"]:
            hist.append({"strategy": spec.label, "at": h["at"], "cum_pl": h["cum_pl"]})
    st.subheader("Overview")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Net P/L and ROI use one definition for every strategy: settled bets only, after fees. "
               "Existing Sharp is read from the league ledger and has no virtual bankroll.")
    if hist:
        df = pd.DataFrame(hist)
        df["at"] = pd.to_datetime(df["at"], errors="coerce", utc=True)
        df = df.dropna(subset=["at"]).sort_values("at")
        wide = df.pivot_table(index="at", columns="strategy", values="cum_pl", aggfunc="last").ffill()
        st.subheader("Cumulative net P/L")
        st.line_chart(wide)
    else:
        st.caption("No settled paper bets yet, so there's nothing to chart.")
    _health(status)
    st.caption("Short samples prove nothing. Don't pick a winner on a few weeks of ROI; "
               "compare closing-line value and calibration too, on each strategy's page.")


def _overview_row(label: str, s: dict, bankroll) -> dict:
    return {"Strategy": label, "Record": f"{s['won']}-{s['lost']}" + (f" ({s['voids']} void)" if s["voids"] else ""),
            "Net P/L": _money(s["net_pl"]), "ROI": _pct(s["roi"]), "Open": s["open"],
            "Bankroll": "—" if bankroll is None else f"${bankroll:,.2f}"}


def _health(status: dict) -> None:
    with st.expander("System health"):
        last = status.get("last_run") or {}
        st.write(f"Last run: {_when(last.get('at'))} · last fully successful: {_when(status.get('last_success'))}")
        if status.get("last_failure"):
            st.write(f"Last failure: {_when(status.get('last_failure'))}")
        snap = last.get("snapshot") or {}
        st.write(f"Sportsbook snapshot: {snap.get('tickers', 0)} priced markets"
                 + (" (missing)" if snap.get("missing") else " (stale)" if snap.get("stale") else ""))
        if snap.get("empty"):
            st.warning("The main scanner priced no markets on its last run, so Value and Hybrid had nothing "
                       "to evaluate. Usually this means the Odds API credits are used up.")
        pin = last.get("pinnacle") or {}
        if pin:
            st.write(f"Pinnacle feed: {pin.get('requests_today', 0)}/{pin.get('budget', 90)} requests today · "
                     f"{pin.get('matched', 0)} Kalshi markets priced"
                     + (" · " + "; ".join(pin["errors"][:2]) if pin.get("errors") else ""))
        st.write("Ledgers reconciled: " + ("yes ✅" if last.get("reconciled") else "no ⚠️"))
        if last.get("data_failures"):
            st.write("Failed data fetches: " + "; ".join(last["data_failures"][:5]))
        for n in last.get("notes") or []:
            st.write("• " + n)


# ------------------------------------------------------------------ one strategy

def render_strategy(spec) -> None:
    header(f"{spec.label} (v{spec.version})")
    st.caption(spec.description)
    store = load_store(spec)
    if store is None:
        return
    s = metrics.store_summary(store)
    c1, c2, c3 = st.columns(3)
    c1.metric("Record", f"{s['won']}-{s['lost']}", help=f"{s['voids']} void · win rate {_pct(s['win_pct'])}")
    c2.metric("Net P/L", _money(s["net_pl"]), _pct(s["roi"]) + " ROI")
    c3.metric("Bankroll", f"${s['bankroll']:,.2f}", f"${available_cash(store):,.2f} free")
    c4, c5, c6 = st.columns(3)
    c4.metric("Open", s["open"], f"${s['open_exposure']:,.2f} at risk", delta_color="off")
    c5.metric("Max drawdown", f"${s['max_drawdown']:,.2f}")
    c6.metric("Avg CLV", "—" if s["clv_avg"] is None else f"{s['clv_avg']:+.1f}¢", f"{s['clv_n']} bets",
              delta_color="off")

    tabs = st.tabs(["Bets", "Candidates", "Model check", "Settings & health"])
    with tabs[0]:
        _bets_tab(store, s)
    with tabs[1]:
        _candidates_tab(store, s)
    with tabs[2]:
        _model_tab(s)
    with tabs[3]:
        _settings_tab(spec, store)


def _bets_tab(store: Store, s: dict) -> None:
    if s["history"]:
        h = pd.DataFrame(s["history"])
        h["at"] = pd.to_datetime(h["at"], errors="coerce", utc=True)
        st.caption("Virtual bankroll after each settled bet")
        st.line_chart(h.set_index("at")["bankroll"])
    if not store.positions:
        st.write("No paper bets yet. That's fine: the strategy only bets when every check passes.")
        return
    df = pd.DataFrame(store.positions)
    df["opened"] = pd.to_datetime(df["opened_at"], errors="coerce", utc=True)
    cats = sorted(x for x in df["category"].dropna().unique() if x)
    types = sorted(x for x in df["market_type"].dropna().unique() if x)
    f1, f2 = st.columns(2)
    state = f1.selectbox("Show", ["All", "Open", "Settled", "Won", "Lost"])
    cat = f2.selectbox("Category", ["All"] + cats)
    typ = st.selectbox("Market type", ["All"] + types) if len(types) > 1 else "All"
    dmin = df["opened"].min()
    dates = st.date_input("Opened between", value=(dmin.date(), datetime.now(timezone.utc).date())) \
        if pd.notna(dmin) else None
    view = df
    if state == "Open":
        view = view[view["status"] == "open"]
    elif state == "Settled":
        view = view[view["status"].isin(["won", "lost", "void", "closed"])]
    elif state in {"Won", "Lost"}:
        view = view[view["status"] == state.lower()]
    if cat != "All":
        view = view[view["category"] == cat]
    if typ != "All":
        view = view[view["market_type"] == typ]
    if dates and isinstance(dates, tuple) and len(dates) == 2:
        d = view["opened"].dt.tz_convert(EASTERN).dt.date
        view = view[(d >= dates[0]) & (d <= dates[1])]
    view = view.sort_values("opened", ascending=False)
    settled = df[df["status"].isin(["won", "lost"])].copy()
    if not settled.empty:
        settled["pl_n"] = pd.to_numeric(settled["pl"], errors="coerce")
        b1, b2 = st.columns(2)
        top = settled.nlargest(3, "pl_n")
        bot = settled.nsmallest(3, "pl_n")
        b1.caption("Largest wins")
        b1.dataframe(top[["title", "pl"]], hide_index=True)
        b2.caption("Largest losses")
        b2.dataframe(bot[["title", "pl"]], hide_index=True)
    st.caption(f"{len(view)} bets · tap one for details")
    for p in view.head(60).to_dict("records"):
        icon = {"open": "⏳", "won": "✅", "lost": "❌", "void": "↩️", "closed": "➖"}.get(p.get("status"), "•")
        pl = metrics.f(p.get("pl"))
        label = (f"{icon} PAPER · {p.get('side')} {str(p.get('title'))[:55]} @ "
                 f"{float(p.get('entry_price') or 0) * 100:.0f}¢" + ("" if pl is None else f" · {_money(pl)}"))
        with st.expander(label):
            _trade_detail(p)


def _trade_detail(p: dict) -> None:
    rows = [("Ticker", p.get("ticker")), ("Market", p.get("title")), ("Strategy", f"{p.get('strategy')} v{p.get('version')}"),
            ("Side", p.get("side")), ("Entered", _when(p.get("opened_at"))),
            ("Entry price", f"{float(p.get('entry_price') or 0) * 100:.1f}¢"),
            ("Contracts", f"{p.get('contracts')} of {p.get('wanted')} wanted ({p.get('fill_status')})"),
            ("Cost + fees", f"${float(p.get('cost') or 0):.2f} + ${float(p.get('fee') or 0):.2f}"),
            ("Fair probability", "—" if not p.get("fair") else f"{float(p['fair']):.1%} (power)"
             + (f", {float(p['fair_proportional']):.1%} proportional" if p.get("fair_proportional") else "")
             + (f", {float(p['fair_shin']):.1%} Shin" if p.get("fair_shin") else "")),
            ("Net edge", "—" if not p.get("net_edge") else f"{float(p['net_edge']) * 100:.1f} pts"),
            ("Why", p.get("reason")), ("Kalshi quote at", _when(p.get("quote_at"))),
            ("Sportsbook prices at", _when(p.get("fair_at"))), ("Kickoff", _when(p.get("commence_time"))),
            ("Status", p.get("status")), ("Result", p.get("result") or "pending"),
            ("Realized P/L", "—" if metrics.f(p.get("pl")) is None else _money(metrics.f(p.get("pl")))),
            ("CLV", "—" if not p.get("clv_cents") else f"{float(p['clv_cents']):+.1f}¢")]
    for k, v in rows:
        st.write(f"**{k}:** {v}")
    legs = loads(p.get("legs"), None)
    if legs:
        st.write("**Legs** (all filled from the same snapshot):")
        st.dataframe(pd.DataFrame(legs)[["ticker", "side", "contracts", "avg_price", "cost", "fee", "outcome"]],
                     hide_index=True)
    det = loads(p.get("details"), {}) or {}
    if det:
        with st.popover("Raw evidence"):
            st.json(det)


def _candidates_tab(store: Store, s: dict) -> None:
    sig = s["signal"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Markets scanned", f"{sig.get('scanned_total', 0):,}")
    c2.metric("Paper fills", sig.get("filled_total", 0))
    c3.metric("Fill rate", _pct(sig.get("fill_rate")).replace("+", ""), f"{sig.get('no_fill_total', 0)} missed",
              delta_color="off")
    if sig.get("avg_net_edge") is not None:
        st.caption(f"Avg net edge on attempts {sig['avg_net_edge'] * 100:.1f} pts · avg spread "
                   f"{(sig.get('avg_spread') or 0) * 100:.1f}¢ · avg quote age {sig.get('avg_quote_age_s') or 0:.0f}s")
    if sig.get("rejections"):
        st.caption("Why candidates were rejected (all time)")
        st.bar_chart(pd.Series(sig["rejections"]).head(12))
    if not store.candidates:
        st.write("No candidates logged yet.")
        return
    df = pd.DataFrame(store.candidates).sort_values("run_at", ascending=False)
    pick = st.selectbox("Show", ["Recent qualified & missed", "All recent", "Rejected only"])
    if pick == "Recent qualified & missed":
        df = df[df["decision"].isin(["FILLED", "PARTIAL", "NO_FILL"])]
    elif pick == "Rejected only":
        df = df[df["decision"] == "REJECTED"]
    st.dataframe(df[["run_at", "title", "side", "decision", "reason", "fair", "ask", "net_edge"]].head(200),
                 hide_index=True, width="stretch")
    st.caption("Candidate rows are kept 14 days; rejection counts above are kept forever.")


def _model_tab(s: dict) -> None:
    avg_fair = "—" if s["avg_fair"] is None else f"{s['avg_fair']:.1%}"
    brier = "—" if s["brier"] is None else f"{s['brier']:.3f}"
    st.write(f"Average fair probability: {avg_fair} · Brier score: {brier} (lower is better; 0.25 = coin flip)")
    if s["calibration"]:
        st.caption("Calibration: predicted vs actual win rate by probability bucket")
        st.dataframe(pd.DataFrame(s["calibration"]), hide_index=True)
    for title, key in (("By category", "by_category"), ("By entry price", "by_price"), ("By edge size", "by_edge"),
                       ("By trade-flow size", "by_flow"), ("By week", "by_week")):
        g = s.get(key) or {}
        if not g or list(g) == ["n/a"]:
            continue
        st.caption(title)
        st.dataframe(pd.DataFrame([{"Bucket": k, "Record": f"{v['won']}-{v['lost']}", "Net P/L": _money(v["pl"]),
                                    "ROI": _pct(v["roi"])} for k, v in g.items()]), hide_index=True)
    if not s["calibration"] and not any(s.get(k) for k in ("by_category",)):
        st.write("Nothing settled yet.")


def _settings_tab(spec, store: Store) -> None:
    st.write(f"**Frozen since:** {spec.started} · **version:** {spec.version}")
    st.write(f"**Bankroll:** ${config.STARTING_BANKROLL:,.0f} start · ~${config.TARGET_STAKE:.0f} per bet · "
             f"${config.MAX_EVENT_EXPOSURE:.0f} max per game · ${config.DAILY_EXPOSURE_CAP:.0f}/day cap")
    st.json(spec.params)
    runs = store.stats.get("runs") or []
    st.write(f"Last successful run: {_when(store.stats.get('last_success'))}")
    if store.stats.get("last_failure"):
        st.write(f"Last failed run: {_when(store.stats.get('last_failure'))}")
    if runs:
        st.dataframe(pd.DataFrame(runs[-20:][::-1]), hide_index=True, width="stretch")


# ------------------------------------------------------------------ +EV board and promo converter

BONUS_BOOKS = {"DraftKings", "Bovada", "Fliff"}


def load_board() -> dict:
    p = config.DATA_DIR / "ev_board.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def _odds(d: float) -> str:
    if d >= 2.0:
        return f"+{round((d - 1) * 100)}"
    return f"{round(-100 / (d - 1))}"


def render_ev_finder() -> None:
    render_strategy(config.EV_FINDER)
    board = load_board()
    st.subheader("Live price board")
    if not board.get("rows"):
        st.write("No games priced yet. The board fills in each round Pinnacle prices are pulled.")
        return
    st.caption(f"Pinnacle fair line vs every venue · updated {_when(board.get('at'))} · sportsbook prices from "
               f"{_when(board.get('books_taken_at')) if board.get('books_taken_at') else 'no snapshot yet'}. "
               "Always confirm a price before betting.")
    min_ev = st.slider("Show prices with EV of at least", -10, 10, 0, format="%d%%")
    rows = []
    for r in board["rows"]:
        for p in r["prices"]:
            if p["ev"] * 100 >= min_ev:
                _price = p["shown"]
                if str(_price).endswith("¢") and p.get("decimal", 0) > 1:
                    _price = f"{_price} ({_odds(p['decimal'])})"
                rows.append({"Game": r["game"], "Team": r["team"], "Fair": r["fair_odds"], "Venue": p["venue"],
                             "Price": _price, "EV": f"{p['ev'] * 100:+.1f}%", "Kickoff": _when(r["starts"]),
                             "Price at": _when(p["at"]), "_ev": p["ev"]})
    if not rows:
        st.write("Nothing at that EV right now.")
        return
    df = pd.DataFrame(rows).sort_values("_ev", ascending=False).drop(columns="_ev")
    st.dataframe(df, hide_index=True, width="stretch")


def promo_conversion(book_decimal: float, hedge_decimal: float) -> float:
    """Share of a bonus bet (stake not returned) locked in as cash by hedging the other side."""
    return (book_decimal - 1.0) * (1.0 - 1.0 / hedge_decimal)


def render_promo() -> None:
    header("Promo Converter")
    st.caption("Turn a bonus bet into guaranteed cash: bet the bonus on one side at your sportsbook, "
               "hedge the other side somewhere else. Bonus-bet stakes aren't returned, so longer odds convert better.")
    st.subheader("Calculator")
    c1, c2 = st.columns(2)
    bonus = c1.number_input("Bonus bet ($)", min_value=1.0, value=25.0, step=5.0)
    book_odds = c2.number_input("Bonus bet odds (American)", value=250, step=5)
    c3, c4 = st.columns(2)
    venue = c3.selectbox("Hedge at", ["Kalshi (¢ price)", "Sportsbook (American odds)"])
    if venue.startswith("Kalshi"):
        cents = c4.number_input("Kalshi price for the other side (¢)", min_value=1, max_value=99, value=72)
        from research_lab.fills import fee_per_contract
        hedge_dec = 1.0 / (cents / 100 + fee_per_contract(cents / 100, 10))
    else:
        h = c4.number_input("Other side odds (American)", value=-300, step=5)
        hedge_dec = 1 + (h / 100 if h > 0 else 100 / abs(h))
    d = 1 + (book_odds / 100 if book_odds > 0 else 100 / abs(book_odds))
    hedge_stake = bonus * (d - 1) / hedge_dec
    locked = hedge_stake * (hedge_dec - 1)
    st.metric("Guaranteed cash", f"${locked:,.2f}", f"{locked / bonus:.0%} of the bonus", delta_color="off")
    if venue.startswith("Kalshi"):
        st.write(f"Hedge: buy about **{hedge_stake * hedge_dec:,.0f} contracts** at {cents}¢ "
                 f"(≈ ${hedge_stake:,.2f} with fees).")
    else:
        st.write(f"Hedge: bet **${hedge_stake:,.2f}** on the other side.")
    st.caption("Either outcome leaves you the same amount. Most people aim for 65–80% conversion.")

    st.subheader("Best conversions right now")
    board = load_board()
    games: dict[str, dict] = {}
    for r in board.get("rows") or []:
        games.setdefault(r["game"], {})[r["team"]] = r
    out = []
    for game, sides in games.items():
        if len(sides) != 2:
            continue
        (ta, ra), (tb, rb) = sides.items()
        for team, mine, other in ((ta, ra, rb), (tb, rb, ra)):
            hedges = [p for p in other["prices"] if p["venue"] not in BONUS_BOOKS]
            if not hedges:
                continue
            hb = max(hedges, key=lambda p: p["decimal"])
            for p in mine["prices"]:
                if p["venue"] in BONUS_BOOKS:
                    out.append({"Bonus at": p["venue"], "Bet": f"{team} {p['shown']}", "Hedge": f"{hb['venue']} "
                                f"{other['team']} {hb['shown']}", "Converts": promo_conversion(p["decimal"], hb["decimal"]),
                                "Game": game, "Kickoff": _when(mine["starts"])})
    if not out:
        st.write("Needs a sportsbook snapshot plus a Kalshi, Polymarket, Novig or ProphetX price on the same game. "
                 "Use the calculator above in the meantime.")
        return
    df = pd.DataFrame(out).sort_values("Converts", ascending=False).head(15)
    df["Converts"] = df["Converts"].map(lambda x: f"{x:.0%}")
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("Sportsbook prices may be hours old. Check both prices before placing either bet.")


# ------------------------------------------------------------------ best price (live Sharp vs Polymarket)

def render_heavy_flow() -> None:
    from research_lab import heavy_flow as hf
    st.set_page_config(page_title="Research Lab: Heavy Flow", page_icon="🐋", layout="centered")
    _top_bar()
    st.title("Heavy Flow test")
    st.info(f"📝 **PAPER ONLY.** Same Sharp picks, split by how many whale trades backed them "
            f"({hf.MIN_TRADES}+ = heavy). Only picks from {hf.START.strftime('%b %-d')} on count, so the "
            "test isn't graded on the bets that gave us the idea. Live $2 Sharp is unchanged.")
    rows = hf.load()
    cmp = hf.compare(rows)
    text, tone = hf.verdict(cmp)
    (st.success if tone == "good" else st.warning if tone == "bad" else st.info)(text)

    def _roi(s):
        if s["roi"] is None:
            return "—"
        if s["roi_half"] is None:
            return _pct(s["roi"])
        return f"{_pct(s['roi'])} ({_pct(s['roi'] - s['roi_half'])} to {_pct(s['roi'] + s['roi_half'])})"

    def _table(c):
        return [{"Group": name, "Settled": c[k]["n"], "Open": c[k]["open"], "ROI (95% range)": _roi(c[k]),
                 "P/L (paper)": _money(c[k]["pl"])}
                for k, name in (("heavy", f"Heavy ({hf.MIN_TRADES}+ trades)"),
                                ("light", f"Light (<{hf.MIN_TRADES})"), ("all", "All Sharp"))]

    st.subheader("Since the test started")
    st.dataframe(_table(cmp), hide_index=True, width="stretch")
    st.caption(f"Decision at {hf.DECIDE_AT} settled heavy-flow bets (about 1–2 weeks at ~19 heavy picks a day).")
    st.subheader("Before the test (where the idea came from)")
    st.dataframe(_table(hf.compare(rows, since=None, before=hf.START)), hide_index=True, width="stretch")
    st.caption("Shown for context only — these bets suggested the filter, so they can't confirm it.")


def render_best_price() -> None:
    from research_lab import best_price
    st.set_page_config(page_title="Research Lab: Best Price", page_icon="🏷️", layout="centered")
    _top_bar()
    st.title("Best Price")
    st.info("👀 **READ-ONLY.** Your live Sharp bets still go to Kalshi. This only records what the same bet "
            "cost on Polymarket a few minutes after each pick, fees included, so you can see if buying "
            "wherever it's cheapest would pay off.")
    rows = best_price.load()
    if not rows:
        st.write("No picks compared yet. Each new live Sharp pick is checked in the next Research Lab round.")
        return
    s = best_price.summary(rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("Polymarket cheaper", f"{s['poly_cheaper']} of {s['compared']}" if s["compared"] else "—")
    c2.metric("Avg edge when cheaper", f"{s['avg_edge_cents']:.1f}¢" if s["avg_edge_cents"] is not None else "—")
    c3.metric("Would have saved", _money(s["saved"]) if s["compared"] else "—",
              help="Same number of contracts bought at the cheaper venue instead of Kalshi.")
    if s["spent"]:
        st.caption(f"That's {s['saved'] / s['spent'] * 100:.1f}% of the ${s['spent']:.2f} spent on compared picks. "
                   f"{s['picks'] - s['compared']} of {s['picks']} picks couldn't be compared (no matching "
                   "Polymarket market, too thin, or not a game-winner bet).")
    results = {t.get("trade_id"): t.get("status") for t in best_price.sharp_trades()}
    table = []
    for r in reversed(rows):
        d = best_price._f(r.get("diff_cents"))
        table.append({"Placed": _when(r.get("placed_at")), "Pick": r.get("pick"),
                      "Kalshi": f"{best_price._f(r.get('kalshi_cost'), 0) * 100:.1f}¢" if r.get("kalshi_cost") else "—",
                      "Polymarket": f"{best_price._f(r.get('poly_cost')) * 100:.1f}¢" if r.get("poly_cost") else "—",
                      "Best": r.get("best_venue") if d is not None else "—",
                      "Diff": f"{d:+.1f}¢" if d is not None else (r.get("note") or "—"),
                      "Result": results.get(r.get("trade_id"), "—")})
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption("Diff is Kalshi's all-in price minus Polymarket's (a 1% Polymarket fee allowance is included). "
               "Positive means Polymarket was cheaper.")
