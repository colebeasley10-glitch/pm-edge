"""Odds Fam: today's sharp plays (3-15 a day), log what you placed, shop with the Game/Prop boards."""
import os
import sys
from datetime import datetime, timezone

import streamlit as st

for _m in [k for k in sys.modules if k in {"research_lab.odds_fam", "odds_fam_log", "bet_log"}]:
    del sys.modules[_m]   # Streamlit Cloud keeps old modules around

for _name in ("GITHUB_TOKEN",):
    try:
        if _name in st.secrets and not os.getenv(_name):
            os.environ[_name] = str(st.secrets[_name])
    except Exception:
        pass

import odds_fam_log  # noqa: E402
from research_lab import odds_fam as of  # noqa: E402
from research_lab.ledger import parse_dt  # noqa: E402

st.set_page_config(page_title="Odds Fam", page_icon="🎯", layout="centered")
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Odds Fam")
st.caption("Sharp plays from free data: Pinnacle line moves on props and game lines, plus spots where Kalshi "
           "or Polymarket is already cheaper than fair. Bet only at the **bet at** price or better.")

BOOKS = ["Sleeper", "Betr", "Rebet", "Fliff", "DraftKings", "PrizePicks", "Underdog", "Kalshi", "Polymarket", "Novig",
         "ProphetX", "Bovado", "Other"]
ICON = {"Pick'em gap": "🔥", "Prop steam": "🎯", "Line steam": "📈", "Price gap": "💲"}
now = datetime.now(timezone.utc)
plays = of.load()


def when(iso_s):
    d = parse_dt(iso_s)
    return d.astimezone(of.ET).strftime("%a %-I:%M %p") if d else ""


@st.cache_data(ttl=20, show_spinner=False)
def my_bets(_tok: bool):
    if _tok:
        try:
            return odds_fam_log.read()[0]
        except Exception:
            pass
    return of.load_bets()


tab_today, tab_mine, tab_games, tab_record = st.tabs(["Today", "My bets", "Game Board", "Record"])

with tab_today:
    live = [p for p in plays if p["status"] == "open" and (parse_dt(p["game_start"]) or now) > now]
    live.sort(key=lambda p: p["sent_at"], reverse=True)
    today = [p for p in plays if of.et_day(parse_dt(p["sent_at"]) or now) == of.et_day(now)]
    st.caption(f"{len(today)} plays sent today (target {of.MIN_PER_DAY}-{of.MAX_PER_DAY}) · {len(live)} still bettable")
    if not live:
        st.write("Nothing open right now. New plays push to your phone as soon as they show up.")
    for p in live:
        with st.container(border=True):
            st.markdown(f"**{ICON[p['kind']]} {p['pick']}**  \n{p['game']} · {p['league']} · {when(p['game_start'])}")
            st.markdown(f"Fair **{p['fair_odds']}** · bet at **{p['bet_at']}** or better"
                        + (f" · best we found: **{p['best_found']}**" if p.get("best_found") else ""))
            st.caption(f"{p['kind']}: {p['why']}")
            with st.expander("I placed it"):
                with st.form(f"log_{p['of_id']}"):
                    c1, c2 = st.columns(2)
                    book = c1.selectbox("Book", BOOKS)
                    odds = c2.text_input("Odds you got", value=p["best_found"].split()[-1] if p.get("best_found") else p["bet_at"])
                    c3, c4 = st.columns(2)
                    stake = c3.number_input("Amount ($)", min_value=1.0, value=10.0, step=5.0)
                    line_taken = c4.text_input("Line you got", value=str(p["line"] or ""),
                                               help="Change it if you got a better number, e.g. the old line")
                    go = st.form_submit_button("Log it", type="primary", use_container_width=True)
                if go:
                    dec = of.american_to_decimal(odds)
                    if dec is None:
                        st.error("Enter odds like -110 or +135.")
                    else:
                        if dec < float(p["bet_at_decimal"]) - 1e-9:
                            st.warning(f"That's worse than {p['bet_at']}, so it's no longer +EV. Logging it anyway.")
                        try:
                            odds_fam_log.append({"of_id": p["of_id"], "pick": p["pick"], "book": book,
                                                 "odds": odds.strip().replace("−", "-"), "decimal": round(dec, 4),
                                                 "stake": round(float(stake), 2), "line_taken": line_taken.strip()})
                            st.success(f"Logged ${stake:,.0f} on {p['pick']} at {odds} ({book}). It grades itself.")
                            st.cache_data.clear()
                        except Exception as exc:
                            st.error(f"Couldn't save it: {exc}")
    try:
        st.page_link("pages/Research_Lab_Prop_Board.py", label="Shop any prop on the Prop Board", icon="🎯")
    except Exception:
        pass

with tab_mine:
    # ---- Quick log: any bet (Hunter's picks, your own), graded with one tap (Oct 27, 2026) ----
    import quick_bets as qb
    st.markdown("**Quick log any bet**")
    with st.form("quick_log", clear_on_submit=True):
        q_src = st.radio("From", qb.SOURCES, horizontal=True, label_visibility="collapsed")
        q_pick = st.text_input("Bet", placeholder="e.g. Luka over 8.5 assists")
        c1, c2, c3 = st.columns(3)
        q_book = c1.selectbox("Book", qb.BOOKS)
        q_odds = c2.text_input("Odds", placeholder="-110")
        q_stake = c3.number_input("Amount ($)", min_value=1.0, value=10.0, step=5.0)
        q_go = st.form_submit_button("Log it", type="primary", use_container_width=True)
    if q_go:
        try:
            b = qb.append(qb.new_bet(q_src, q_pick, q_book, q_odds, q_stake))
            st.success(f"Logged ${b['stake']:,.0f} on {b['pick']} at {b['odds']} ({b['book']}).")
        except Exception as exc:
            st.error(str(exc))
    try:
        q_rows = qb.read()[0]
    except Exception as exc:
        q_rows = []
        st.caption(f"Couldn't load quick bets: {exc}")
    q_open = [r for r in q_rows if r.get("status") == "open"]
    for r in sorted(q_open, key=lambda r: r.get("logged_at", ""), reverse=True)[:15]:
        with st.container(border=True):
            st.markdown(f"**{r['pick']}** {r['odds']} · ${float(r['stake']):,.0f} at {r['book']} · {r['source']}")
            g1, g2, g3 = st.columns(3)
            for col, label, status in ((g1, "Won", "won"), (g2, "Lost", "lost"), (g3, "Push", "push")):
                if col.button(label, key=f"g_{r['bet_id']}_{status}", use_container_width=True):
                    try:
                        qb.grade(r["bet_id"], status)
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    if q_rows:
        cols = st.columns(len(qb.SOURCES))
        for col, src in zip(cols, qb.SOURCES):
            s = qb.summary(q_rows, src)
            col.metric(src, f"{s['won']}-{s['lost']}", f"${s['pl']:+,.2f}" if s["won"] + s["lost"] else None)
    st.divider()

    bets = my_bets(bool(odds_fam_log.bet_log.token()))
    results = of.load_results()
    s = of.my_summary(bets, results)
    c1, c2, c3 = st.columns(3)
    c1.metric("Record", f"{s['won']}-{s['lost']}")
    c2.metric("Profit", f"${s['pl']:+,.2f}")
    c3.metric("ROI", "—" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%")
    st.caption(f"Open: ${s['open_stake']:,.2f} riding. Results come in after each game.")
    if bets:
        st.dataframe([{"Logged": (b.get("logged_at") or "")[:10], "Bet": b["pick"],
                       "Line": b.get("line_taken") or "", "Book": b["book"], "Odds": b["odds"],
                       "Stake": f"${float(b['stake']):,.2f}",
                       "Result": (results.get(b["bet_id"]) or {}).get("status", "open"),
                       "P/L": (results.get(b["bet_id"]) or {}).get("pl", "")}
                      for b in reversed(bets)], hide_index=True, width="stretch")
    else:
        st.write("Nothing logged yet. Tap **I placed it** on a play after you bet it.")

with tab_games:
    gb = of.load_game_board()
    rows = [r for r in gb.get("rows") or [] if (parse_dt(r["starts"]) or now) > now]
    if not rows:
        st.write("Fills in on the next Pinnacle pull.")
    else:
        q = st.text_input("Search team", placeholder="e.g. Pacers")
        lg = st.selectbox("League", ["All"] + sorted({r["league"] for r in rows}))
        shown = [r for r in rows if (not q or q.lower() in (r["game"] + r["pick"]).lower())
                 and lg in ("All", r["league"])]
        shown.sort(key=lambda r: (r["starts"], r["game"], r["market"]))
        st.caption("If your book's price is at or better than **Bet at**, it's +EV (2%+).")
        st.dataframe([{"Bet": r["pick"], "Game": r["game"], "Fair": r["fair_odds"], "Bet at": r["bet_at"],
                       "Kalshi/Poly best": r["best_found"],
                       "EV there": "" if r["ev"] is None else f"{r['ev'] * 100:+.1f}%", "Starts": when(r["starts"])}
                      for r in shown[:400]], hide_index=True, width="stretch")

with tab_record:
    st.caption("Paper record: 1 unit ($10) on every play at the bet-at price (or the price we found). "
               "Beat the close = Pinnacle's last price before the game moved further our way; "
               "above ~55% over 100+ plays means the edge is real.")
    table = []
    for kind in (None, "Pick'em gap", "Prop steam", "Line steam", "Price gap"):
        s = of.summary(plays, kind)
        table.append({"Type": kind or "All plays", "Plays": s["plays"], "W-L": f"{s['won']}-{s['lost']}",
                      "P/L": f"${s['pl']:+,.2f}", "ROI": "—" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%",
                      "Beat close": "—" if s["beat_close"] is None else f"{s['beat_close'] * 100:.0f}%",
                      "Open": s["open"]})
    st.dataframe(table, hide_index=True, width="stretch")
    if plays:
        st.dataframe([{"Sent": when(p["sent_at"]), "Play": p["pick"], "Type": p["kind"], "Bet at": p["bet_at"],
                       "Result": p["status"], "P/L": p["pl"] or ""}
                      for p in sorted(plays, key=lambda r: r["sent_at"], reverse=True)[:150]],
                     hide_index=True, width="stretch")
