"""Research Lab: Sharp Plays (Pinnacle steam -> shop-it-anywhere alerts, PAPER-tracked)."""
import sys

import streamlit as st

for _m in [k for k in sys.modules if k == "research_lab.sharp_plays"]:
    del sys.modules[_m]   # Streamlit Cloud keeps old modules around
from research_lab import sharp_plays as sp  # noqa: E402

st.set_page_config(page_title="Research Lab: Sharp Plays", page_icon="🎯", layout="centered")
sys.modules.pop("odds_calc", None)
import odds_calc  # noqa: E402
odds_calc.render()
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Sharp Plays")
st.info("📝 **ALERTS + PAPER.** When Pinnacle's line moves hard on an NFL, college football, NBA or NHL game "
        "(sharp money), you get the side that got bet, its fair price, and the worst odds still worth taking. "
        "Shop it on Rebet, Betr or any book; soft books often lag the move. Paper: 1u ($10) at the target price. "
        f"At most {sp.MAX_PER_DAY} a day, one per game.")

rows = sp.load()
s = sp.summary(rows)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Record", f"{s['won']}-{s['lost']}")
c2.metric("P/L (1u = $10)", f"{s['pl']:+.2f}")
c3.metric("ROI", "—" if s["roi"] is None else f"{s['roi'] * 100:+.1f}%")
c4.metric("Beat the close", "—" if s["beat_close"] is None else f"{s['beat_close'] * 100:.0f}%")
st.caption("Beat the close = Pinnacle's last price before kickoff was better for our side than when we alerted. "
           "Above ~55% over 100+ plays means the moves are real; it shows up long before W-L does.")

if not rows:
    st.write("No plays yet. Pinnacle needs about 2 hours of history on a game before a move counts.")
else:
    recent = sorted(rows, key=lambda r: r.get("alert_time") or "", reverse=True)[:80]
    st.dataframe([{"When": (r["alert_time"] or "")[:16].replace("T", " "), "Play": r["pick"], "Game": r["game"],
                   "Why": r["move"], "Fair": r["fair_odds"], "Worst price": r["target_odds"],
                   "CLV (pts)": "—" if r.get("clv") in ("", None) else f"{float(r['clv']) * 100:+.1f}",
                   "Result": r["status"], "P/L": r.get("pl") or "—"} for r in recent],
                 hide_index=True, width="stretch")
