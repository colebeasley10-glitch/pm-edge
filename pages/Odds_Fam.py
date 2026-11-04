"""Odds Fam: today's sharp plays (3-15 a day), log what you placed, shop with the Game/Prop boards."""
import os
import sys
from datetime import datetime, timezone

import streamlit as st

import data_sync  # noqa: E402  Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits
data_sync.sync()

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
sys.modules.pop("odds_calc", None)
import odds_calc  # noqa: E402
odds_calc.render()
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Odds Fam")
st.caption("Sharp plays from free data: Pinnacle line moves on props and game lines, plus spots where Kalshi "
           "or Polymarket is already cheaper than fair. Every play has a **worst price**: bet it there or better, pass on anything lower.")

BOOKS = ["Courtside", "Betr", "Rebet", "Onyx", "Sleeper", "Fliff", "DraftKings", "PrizePicks", "Underdog", "Kalshi", "Polymarket", "Novig",
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


tab_today, tab_board, tab_mine, tab_record = st.tabs(["Today", "Board", "My bets", "Record"])

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
            st.markdown(f"Fair **{p['fair_odds']}** · Worst price **{p['bet_at']}** · pass on anything lower"
                        + (f" · best we found: **{p['best_found']}**" if p.get("best_found") else ""))
            st.caption(f"{p['kind']}: {p['why']}")
            seen = st.text_input("Check a price", key=f"chk_{p['of_id']}", placeholder="Price you see, e.g. +104")
            if seen.strip():
                chk = of.price_check(seen, p["bet_at_decimal"], p["fair_prob"])
                if chk is None:
                    st.caption("Enter odds like -110 or +135.")
                elif chk["ok"]:
                    st.success(chk["msg"], icon="✅")
                else:
                    st.error(chk["msg"], icon="🚫")
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
                        chk = of.price_check(odds, p["bet_at_decimal"], p["fair_prob"])
                        if chk and not chk["ok"]:
                            st.warning(chk["msg"] + " Logging it anyway.")
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
    clvs = of.load_clv()
    st.markdown("**Beating the close?** CLV = your price vs the fair price right before the game. "
                "Hunter-style plays should beat it most of the time; over 50+ bets that's the real test.")
    for by, label in (("source", "From"), ("book", "Book")):
        rep = of.clv_report(bets, results, clvs, by=by)
        if rep:
            st.dataframe([{label: r[by], "Bets": r["bets"], "W-L": f"{r['won']}-{r['lost']}",
                           "P/L": f"${r['pl']:+,.2f}", "ROI": "—" if r["roi"] is None else f"{r['roi'] * 100:+.1f}%",
                           "Avg CLV": "—" if r["clv_avg"] is None else f"{r['clv_avg'] * 100:+.1f}%",
                           "Beat close": "—" if r["beat_close"] is None else f"{r['beat_close'] * 100:.0f}% of {r['clv_n']}"}
                          for r in rep], hide_index=True, width="stretch")
    if bets:
        st.dataframe([{"Logged": (b.get("logged_at") or "")[:10], "Bet": b["pick"],
                       "Line": b.get("line_taken") or "", "Book": b["book"], "Odds": b["odds"],
                       "Stake": f"${float(b['stake']):,.2f}",
                       "From": b.get("source") or "Odds Fam",
                       "CLV": "" if b["bet_id"] not in clvs else f"{clvs[b['bet_id']]['clv'] * 100:+.1f}%",
                       "Result": (results.get(b["bet_id"]) or {}).get("status", "open"),
                       "P/L": (results.get(b["bet_id"]) or {}).get("pl", "")}
                      for b in reversed(bets)], hide_index=True, width="stretch")
    else:
        st.write("Nothing logged yet. Tap **I placed it** on a play after you bet it.")

with tab_board:
    tb = of.load_board()
    brows = [r for r in tb.get("rows") or [] if (parse_dt(r["starts"]) or now) > now]
    st.caption(f"Shop **{of.SHOP}**. Take any price at or better than **Bet at**. "
               "**Arb at**: at that price or better, buying the other side on the exchange locks a profit. "
               + (f"Updated {when(tb['at'])}." if tb.get("at") else ""))
    if not brows:
        st.write("Fills in on the next Pinnacle pull.")
    else:
        c1, c2 = st.columns(2)
        q = c1.text_input("Search player or team", placeholder="e.g. Markkanen")
        kind = c2.selectbox("Show", ["Props", "Game lines", "All"])
        lg = st.selectbox("League", ["All"] + sorted({r["league"] for r in brows}))
        moved_only = st.toggle("Only lines that moved (stale-line hunting)", value=False)
        shown = [r for r in brows
                 if (not q or q.lower() in (r["game"] + r["pick"]).lower()) and lg in ("All", r["league"])
                 and (kind == "All" or (kind == "Props") == (r["kind"] == "prop"))
                 and (not moved_only or r.get("moved"))]
        st.dataframe([{"Bet": r["pick"], "Bet at": r["bet_at"], "Fair": r["fair_odds"], "Arb at": r["arb_at"],
                       "Moved": r["moved"], "P limit": f"${r['limit']:,}" if r.get("limit") else "",
                       "Exchanges": r["best_found"] or r["exch"], "Starts": when(r["starts"]), "Game": r["game"]}
                      for r in shown[:400]], hide_index=True, width="stretch")

        st.markdown("**I bet one of these**")
        opts = {f"{r['pick']} · {when(r['starts'])}": r for r in shown[:300]}
        if opts:
            pick_lbl = st.selectbox("Bet", list(opts), label_visibility="collapsed")
            row = opts[pick_lbl]
            seen = st.text_input("Price at your book", key="board_price", placeholder="e.g. -112")
            if seen.strip():
                chk = of.price_check(seen, row["bet_at_decimal"], row["fair_prob"])
                if chk is None:
                    st.caption("Enter odds like -110 or +135.")
                elif chk["ok"]:
                    st.success(chk["msg"], icon="✅")
                else:
                    st.error(chk["msg"], icon="🚫")
            with st.form("board_log", clear_on_submit=True):
                src = st.radio("From", of.SOURCES, horizontal=True)
                c1, c2 = st.columns(2)
                book = c1.selectbox("Book", BOOKS)
                odds = c2.text_input("Odds you got", value=seen.strip())
                c3, c4 = st.columns(2)
                stake = c3.number_input("Amount ($)", min_value=1.0, value=10.0, step=5.0)
                lt = c4.text_input("Line you got", value=f"{row['line']:g}" if row.get("line") not in ("", None) else "",
                                   help="Change it if you got a different number")
                go = st.form_submit_button("Log it", type="primary", use_container_width=True)
            if go:
                try:
                    bet = of.bet_from_board(row, source=src, book=book, odds=odds or seen, stake=stake, line_taken=lt)
                    odds_fam_log.append(bet)
                    st.success(f"Logged ${stake:,.0f} on {bet['pick']} at {bet['odds']} ({book}). "
                               "It grades itself and gets a CLV score once the game starts.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(str(exc))

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
        st.dataframe([{"Sent": when(p["sent_at"]), "Play": p["pick"], "Type": p["kind"], "Worst price": p["bet_at"],
                       "Result": p["status"], "P/L": p["pl"] or ""}
                      for p in sorted(plays, key=lambda r: r["sent_at"], reverse=True)[:150]],
                     hide_index=True, width="stretch")
