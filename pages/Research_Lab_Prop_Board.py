"""Research Lab: Prop Board (Pinnacle player props, fair prices to shop against) + prop steam plays."""
import sys

import streamlit as st

for _m in [k for k in sys.modules if k == "research_lab.prop_board"]:
    del sys.modules[_m]   # Streamlit Cloud keeps old modules around
from research_lab import prop_board as pb  # noqa: E402
from research_lab.ledger import parse_dt  # noqa: E402

st.set_page_config(page_title="Prop Board", page_icon="🎯", layout="centered")
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Prop Board")
st.caption("Pinnacle's player props with the vig removed. Find the prop on Betr, Rebet or any book: "
           "if their price is at or better than **Bet at**, it's +EV (2%+). Bigger P limit = a line Pinnacle trusts more.")

board = pb.load_board()
rows = board.get("rows") or []
tab_board, tab_plays = st.tabs(["Board", "Steam plays"])

with tab_board:
    if not rows:
        st.write("No props yet. They fill in on the next Pinnacle pull (football every 45 min, NBA hourly).")
    else:
        q = st.text_input("Search player", placeholder="e.g. Luka, Jacobs")
        c1, c2 = st.columns(2)
        leagues = sorted({r["league"] for r in rows})
        league = c1.selectbox("League", ["All"] + leagues)
        stats = sorted({r["stat"] for r in rows if league in ("All", r["league"])})
        stat = c2.selectbox("Stat", ["All"] + stats)
        moved_only = st.toggle("Only lines that moved", value=False)
        shown = [r for r in rows
                 if (not q or q.lower() in r["player"].lower())
                 and league in ("All", r["league"]) and stat in ("All", r["stat"])
                 and (not moved_only or r["line"] != r["open_line"] or abs(r["p_over"] - r["open_p_over"]) >= 0.03)]
        shown.sort(key=lambda r: (r["starts"], r["game"], r["player"]))

        def moved(r):
            if r["line"] != r["open_line"]:
                return f"{r['open_line']:g} → {r['line']:g}"
            d = (r["p_over"] - r["open_p_over"]) * 100
            return f"Over {d:+.0f} pts" if abs(d) >= 2 else ""

        st.caption(f"{len(shown)} props · updated {(board.get('at') or '')[11:16]} UTC")
        st.dataframe([{"Player": r["player"], "Stat": r["stat"], "Line": f"{r['line']:g}",
                       "Over fair": r["fair_over"], "Bet Over at": r["bet_over_at"],
                       "Under fair": r["fair_under"], "Bet Under at": r["bet_under_at"],
                       "Moved": moved(r), "P limit": f"${r['limit']:,}", "Game": r["game"],
                       "Starts": (parse_dt(r["starts"]).strftime("%a %H:%M UTC") if parse_dt(r["starts"]) else "")}
                      for r in shown[:400]], hide_index=True, width="stretch")

with tab_plays:
    plays = pb.load()
    s = pb.summary(plays)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Record", f"{s['won']}-{s['lost']}")
    c2.metric("P/L (1u = $10)", f"{s['pl']:+.2f}")
    c3.metric("ROI", "—" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%")
    c4.metric("Beat the close", "—" if s["beat_close"] is None else f"{s['beat_close'] * 100:.0f}%")
    st.caption(f"Pushed when Pinnacle moves a prop hard (sharp money). At most {pb.MAX_PER_DAY} a day. "
               "Paper at the target price; graded from ESPN box scores.")
    if plays:
        recent = sorted(plays, key=lambda r: r.get("alert_time") or "", reverse=True)[:100]
        st.dataframe([{"When": (r["alert_time"] or "")[:16].replace("T", " "), "Play": r["pick"], "Why": r["move"],
                       "Fair": r["fair_odds"], "Bet at": r["target_odds"], "P limit": r["limit"],
                       "CLV (pts)": "—" if r.get("clv") in ("", None) else f"{float(r['clv']) * 100:+.1f}",
                       "Actual": r.get("actual") or "—", "Result": r["status"], "P/L": r.get("pl") or "—"}
                      for r in recent], hide_index=True, width="stretch")
    else:
        st.write("No steam plays yet. Props need about 2 hours of history before a move counts.")
