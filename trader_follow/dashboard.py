"""Streamlit view for Trader Follow (read-only, PAPER results)."""
from __future__ import annotations

import streamlit as st

from trader_follow import core


def _money(x) -> str:
    if x is None:
        return "—"
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def render() -> None:
    st.set_page_config(page_title="Research Lab: Trader Follow", page_icon="👤", layout="centered")
    try:
        st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
    except Exception:
        pass
    st.title("Trader Follow")
    st.info("📝 **PAPER + ALERTS ONLY.** Pushes you when a followed Polymarket bettor puts "
            f"${core.MIN_USDC:,.0f}+ on one side **before** a game starts, priced +150 to -200. "
            f"Paper P/L assumes ${core.PAPER_STAKE:.0f} at the price shown in the alert. Nothing bets automatically.")

    rows = core.load_ledger()

    st.subheader("Scoreboard (alerted bets)")
    table = [{"Trader": s["trader"], "Alerts": s["signals"], "Open": s["open"],
              "W-L": f"{s['wins']}-{s['losses']}", "P/L copying": _money(s["pl_copy"]),
              "ROI copying": _pct(s["roi_copy"]), "ROI at his price": _pct(s["roi_his"])}
             for s in core.summarize(rows)]
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("“ROI copying” is what matters: it uses the price you could get when the alert arrived. "
               "If it trails “ROI at his price” by a lot, the alerts are arriving too late.")

    st.subheader("By sport")
    by_sport = core.summarize_by_sport(rows)
    if by_sport:
        st.dataframe([{"Trader": s["trader"], "Sport": s["sport"], "Alerts": s["signals"],
                       "W-L": f"{s['wins']}-{s['losses']}", "P/L copying": _money(s["pl_copy"]),
                       "ROI copying": _pct(s["roi_copy"])} for s in by_sport], hide_index=True, width="stretch")
        st.caption("Wait for about 20 settled bets in a sport before cutting it; fewer than that is mostly luck.")
    else:
        st.caption("Fills in as alerts settle.")

    with st.expander("Who we follow"):
        for t in core.TRADERS:
            st.markdown(f"**{t.name}** — {t.note} · [profile](https://polymarket.com/profile/{t.wallet})")

    st.subheader("Recent signals")
    if not rows:
        st.write("No signals yet. Alerts start with his next big pre-game bet.")
        return
    recent = sorted(rows, key=lambda r: r.get("alert_time") or "", reverse=True)[:60]
    st.dataframe([{
        "When": (r.get("alert_time") or "")[:16].replace("T", " "),
        "Trader": r["trader"], "Bet": f"{r['outcome']} — {r['question']}",
        "His $": f"${float(r['his_usdc']):,.0f}", "His price": f"{float(r['his_price']) * 100:.0f}¢ ({core.american(float(r['his_price']))})",
        "Alert price": f"{float(r['copy_price']) * 100:.0f}¢" if r.get("copy_price") else "—",
        "In band": "✓" if str(r.get("in_band")) == "True" else "—",
        "Result": r.get("status", ""),
        "P/L ($10)": _money(core._float(r.get("pl_copy"))) if r.get("pl_copy") not in (None, "") else "—",
    } for r in recent], hide_index=True, width="stretch")
    st.caption("Out-of-band signals are logged but never pushed, so you can see if the price filter helps.")
