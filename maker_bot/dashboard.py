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
        + f"Rests Kalshi orders one cent above the best bid when that's {core.MIN_EDGE:.0%}-{core.MAX_EDGE:.0%} under "
          f"Pinnacle's fair line after fees. Every bet is one "
          f"${core.unit():.0f} unit (risk it on underdogs, win it on favorites), no daily cap. Kalshi cancels each order by itself 5 minutes before the game starts. "
          "Only fills count as bets.")

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
                          "Avg edge": _pct(s["avg_edge"]),
                          "Markout": "—" if s["avg_markout"] is None else f"{s['avg_markout']:+.1f}¢"})
        st.dataframe(table, hide_index=True, width="stretch")
    st.caption("Dry-run orders can't know whether they would have filled, so only live rows show fills and P/L. "
               "**Markout** = the market's price shortly after a fill minus what we paid. Around zero or positive is "
               "healthy; clearly negative means we only get filled when the market is moving against us.")
    with st.expander("When to scale up"):
        s = core.summarize(rows, "live")
        checks = [("100+ settled fills", s["won"] + s["lost"] >= 100),
                  ("ROI above zero", (s["roi"] or 0) > 0),
                  ("Average markout at or above 0¢", s["avg_markout"] is not None and s["avg_markout"] >= 0),
                  ("Fill rate under 60% (fills on every order = priced too generously)",
                   s["fill_rate"] is not None and s["fill_rate"] < 0.6)]
        for label, ok in checks:
            st.markdown(f"{'✅' if ok else '⬜'} {label}")
        st.caption("All four: raise the unit (MAKER_BOT_UNIT, up to CEIL_UNIT in maker_bot/core.py). "
                   "Any one failing: keep size where it is.")

    if status:
        st.subheader("Last round")
        st.write(f"{status.get('at', '')} · {status.get('candidates', 0)} candidates · "
                 f"{status.get('posted', 0)} posted · {status.get('candidates_note', '')}")
        if status.get("skips"):
            st.caption("Skipped: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in
                                               sorted(status["skips"].items(), key=lambda x: -x[1])))
        if status.get("spent_today"):
            cap = status.get("daily_budget")
            st.caption(f"Committed today: ${max(status['spent_today'].values()):.2f}"
                       + (f" of ${cap:.2f}" if cap else " (no daily cap)"))

    recent = sorted(rows, key=lambda r: r.get("placed_at") or "", reverse=True)[:60]
    if recent:
        st.subheader("Orders")
        st.dataframe([{"Placed": (r.get("placed_at") or "")[5:16].replace("T", " "), "Mode": r.get("mode"),
                       "Play": r.get("label"), "Game": r.get("game"), "Qty": r.get("contracts"),
                       "Price": f"{float(r['price']) * 100:.0f}¢", "Edge": _pct(core.f(r.get("edge"))),
                       "Fair": f"{float(r['fair']) * 100:.1f}¢", "Sharp": "✓" if r.get("sharp") else "",
                       "Expires": (r.get("expires_at") or "")[11:16] + "Z", "Status": r.get("status"),
                       "P/L": r.get("pl")} for r in recent], hide_index=True, width="stretch")
