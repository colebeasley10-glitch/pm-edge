"""Streamlit view for the Maker Bot (read-only)."""
from __future__ import annotations

import json

import streamlit as st

from maker_bot import core


def _money(x) -> str:
    return "—" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x, signed=True) -> str:
    return "—" if x is None else (f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%")


def render() -> None:
    st.set_page_config(page_title="Research Lab: Maker Bot", page_icon="🎯", layout="centered")
    try:
        st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
    except Exception:
        pass
    st.title("Maker Bot")
    rows = core.load()
    try:
        status = json.loads(core.STATUS.read_text())
    except Exception:
        status = {}
    live = status.get("mode") == "live"
    (st.warning if live else st.info)(
        ("💵 **LIVE, real money.** " if live else "📝 **DRY RUN.** Logs the orders it would post; "
         "set the repo variable MAKER_BOT_ENABLED to 1 to go live. ")
        + f"Posts ${core.CEIL_PER_ORDER:.0f} resting Kalshi orders priced at least {core.MIN_EDGE:.0%} better than "
          f"Pinnacle's fair line ({core.MIN_EDGE_SHARP:.0%} when big Sharp flow agrees), +150 to -200 only. "
          "Kalshi cancels each order by itself 5 minutes before the game starts. Only fills count as bets.")

    for mode, title in (("live", "Live"), ("dry", "Dry run (would-have-posted)")):
        if not any(r.get("mode") == mode for r in rows):
            continue
        st.subheader(title)
        table = []
        for name, sharp in (("All", None), ("EV only", False), ("EV + Sharp", True)):
            s = core.summarize(rows, mode, sharp)
            table.append({"": name, "Posted": s["posted"], "Resting": s["resting"], "Fill rate": _pct(s["fill_rate"], False),
                          "Filled": s["fills"], "W-L": f"{s['won']}-{s['lost']}", "Open": s["open"],
                          "P/L": _money(s["pl"]) if mode == "live" else "—", "ROI": _pct(s["roi"]) if mode == "live" else "—",
                          "Avg edge": _pct(s["avg_edge"])})
        st.dataframe(table, hide_index=True, width="stretch")
    st.caption("Dry-run orders can't know whether they would have filled, so only live rows show fills and P/L. "
               "Watch fill rate next to ROI: if fills mostly lose, the orders are only getting hit when the market "
               "moves against them.")

    if status:
        st.subheader("Last round")
        st.write(f"{status.get('at', '')} · {status.get('candidates', 0)} candidates · "
                 f"{status.get('posted', 0)} posted · {status.get('candidates_note', '')}")
        if status.get("skips"):
            st.caption("Skipped: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in
                                               sorted(status["skips"].items(), key=lambda x: -x[1])))
        if status.get("spent_today"):
            st.caption(f"Committed today: ${max(status['spent_today'].values()):.2f} of ${status.get('daily_budget', 0):.2f}")

    recent = sorted(rows, key=lambda r: r.get("placed_at") or "", reverse=True)[:60]
    if recent:
        st.subheader("Orders")
        st.dataframe([{"Placed": (r.get("placed_at") or "")[5:16].replace("T", " "), "Mode": r.get("mode"),
                       "Play": r.get("label"), "Game": r.get("game"), "Price": f"{float(r['price']) * 100:.0f}¢",
                       "Fair": f"{float(r['fair']) * 100:.1f}¢", "Sharp": "✓" if r.get("sharp") else "",
                       "Expires": (r.get("expires_at") or "")[11:16] + "Z", "Status": r.get("status"),
                       "P/L": r.get("pl")} for r in recent], hide_index=True, width="stretch")
