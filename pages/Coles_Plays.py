"""Cole's Plays: pick'em apps (Sleeper, Underdog, PrizePicks) paying more than Pinnacle's fair price. Paper."""
import json
import sys

import streamlit as st

import data_sync  # noqa: E402  pull fresh CSVs; Streamlit Cloud misses bot commits
data_sync.sync()

for _m in [k for k in sys.modules if k == "research_lab.coles_plays"]:
    del sys.modules[_m]   # Streamlit Cloud keeps old modules around
from research_lab import coles_plays as cp  # noqa: E402
from research_lab.ledger import parse_dt  # noqa: E402

st.set_page_config(page_title="Cole's Plays", page_icon="🎯", layout="centered")
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Cole's Plays")
st.caption("Your elite list. Two parts: pick'em app lines paying 2%+ over Pinnacle's no-vig fair price (the way "
           f"Hunter finds his plays), plus the best plays (3%+ edge, at most {cp.MAX_BEST_PER_DAY} a day) from the top "
           f"{cp.TOP_LANES} lanes on the scorecard. Paper: $10 a pick. Pair pick'em picks into 2+ pick entries.")

plays = cp.load()
s = cp.summary(plays)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Record", f"{s['won']}-{s['lost']}")
c2.metric("P/L ($10 a pick)", f"{s['pl']:+.2f}")
c3.metric("ROI", "—" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%")
c4.metric("Beat the close", "—" if s["beat_close"] is None else f"{s['beat_close'] * 100:.0f}%")


def when(r):
    d = parse_dt(r.get("game_start"))
    return d.astimezone().strftime("%a %-I:%M %p") if d else ""


tab_open, tab_done, tab_feeds = st.tabs(["Open", "Results", "App feeds"])
with tab_open:
    opn = sorted((r for r in plays if r["status"] == "open"), key=lambda r: (r["game_start"], -float(r["ev"] or 0)))
    if opn:
        st.dataframe([{"Starts": when(r), "League": r["league"], "Pick": r["pick"], "From": r.get("via") or r["app"],
                       "Pays": r["target_odds"], "Fair": r["fair_odds"], "Edge": f"{float(r['ev']) * 100:.1f}%",
                       "Pinnacle line": r["pin_line"], "Also at": r.get("also_at") or "",
                       "CLV (pts)": "—" if r.get("clv") in ("", None) else f"{float(r['clv']) * 100:+.1f}",
                       "Game": r["game"]} for r in opn], hide_index=True, width="stretch")
    else:
        st.write("No open plays right now. The scan runs every Research Lab round against Pinnacle's props.")

with tab_done:
    done = sorted((r for r in plays if r["status"] != "open"), key=lambda r: r.get("settled_time") or "",
                  reverse=True)[:200]
    if done:
        st.dataframe([{"Settled": (r.get("settled_time") or "")[:10], "League": r["league"], "Pick": r["pick"],
                       "From": r.get("via") or r["app"], "Paid": r["target_odds"], "Fair": r["fair_odds"],
                       "CLV (pts)": "—" if r.get("clv") in ("", None) else f"{float(r['clv']) * 100:+.1f}",
                       "Actual": r.get("actual") or "—", "Result": r["status"], "P/L": r.get("pl") or "—"}
                      for r in done], hide_index=True, width="stretch")
    else:
        st.write("Nothing settled yet.")

with tab_feeds:
    try:
        stat = json.loads((cp._dir(None) / "status.json").read_text())
    except Exception:
        stat = {}
    if not stat:
        st.write("No scan yet.")
    else:
        bo = stat.get("best_of") or {}
        if bo.get("top_lanes") is not None:
            st.caption("Borrowing from: " + (", ".join(bo["top_lanes"]) or "no lane has earned it yet"))
        st.caption(f"Last scan {(stat.get('at') or '')[:16].replace('T', ' ')} UTC · "
                   f"{stat.get('board_props', 0)} Pinnacle props · {stat.get('matched', 0)} app lines matched · "
                   f"{stat.get('edges', 0)} edges")
        st.dataframe([{"App": k, "Lines": v.get("props", "—"), "Matched to Pinnacle": v.get("matched", "—"),
                       "Problem": v.get("error") or ""} for k, v in (stat.get("detail") or {}).items()],
                     hide_index=True, width="stretch")
