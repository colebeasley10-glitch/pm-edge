"""Scorecard: every lane against one go-live bar (Oct 27, 2026)."""
import sys

import streamlit as st

sys.modules.pop("scorecard", None)   # Streamlit Cloud keeps old modules around
import scorecard as sc  # noqa: E402

st.set_page_config(page_title="Scorecard", page_icon="✅", layout="centered")
try:
    st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
except Exception:
    pass
st.title("Scorecard")
st.caption(f"A lane earns real money only after **{sc.MIN_BETS}+ settled bets**, **ROI above zero**, and "
           "**CLV at or above zero** (beating the closing price on average). A hot week doesn't count.")

lanes = sc.lanes()


def fmt_roi(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def mark(ok):
    return "—" if ok is None else ("✅" if ok else "❌")


for title, group in (("Real money", [l for l in lanes if l["money"] == "real"]),
                     ("Paper", [l for l in lanes if l["money"] == "paper"])):
    st.subheader(title)
    if not group:
        st.write("Nothing yet.")
        continue
    st.dataframe([{"Lane": l["name"], "Bets": l["n"], "Record": f"{l['won']}-{l['lost']}",
                   "P/L": f"{l['pl']:+,.2f}", "ROI": fmt_roi(l["roi"]),
                   f"{sc.MIN_BETS}+": mark(l["checks"]["bets"]), "ROI>0": mark(l["checks"]["roi"]),
                   "CLV≥0": mark(l["checks"]["clv"]), "Verdict": l["verdict"], "Note": l["note"]}
                  for l in sorted(group, key=lambda l: -l["n"])],
                 hide_index=True, width="stretch")

st.caption("P/L is in dollars except the NCAA Grader (units). Paper lanes risk $10 per bet unless noted. "
           "CLV is in each lane's own unit; only whether it's above or below zero matters here.")
