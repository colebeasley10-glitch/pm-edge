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
            "a big stake (his own minimum, below) on one side **before** a game starts, in a sport "
            "he's proven in, priced +150 to -200. "
            "Paper P/L uses each trader's unit (BreakTheBank: $1) at the price shown in the alert. "
            "Nothing bets automatically.")

    rows = core.load_ledger()

    st.subheader("Scoreboard (alerted bets)")
    table = [{"Trader": s["trader"], "Unit": _money(s["unit"]).lstrip("+"), "Alerts": s["signals"], "Open": s["open"],
              "W-L": f"{s['wins']}-{s['losses']}", "Units": f"{s['units']:+.2f}u",
              "P/L copying": _money(s["pl_unit"]),
              "ROI copying": _pct(s["roi_copy"]), "ROI at his price": _pct(s["roi_his"])}
             for s in core.summarize(rows)]
    st.dataframe(table, hide_index=True, width="stretch")
    st.markdown("**Sized like him (paper)**")
    st.dataframe([{"Trader": s["trader"], "W-L": f"{s['wins']}-{s['losses']}",
                   "Flat units": f"{s['units']:+.2f}u", "Flat ROI": _pct(s["roi_copy"]),
                   "Sized P/L": _money(s["pl_sized"]), "Sized ROI": _pct(s["roi_sized"]),
                   "Sized risked": _money(s["staked_sized"])} for s in core.summarize(rows)],
                 hide_index=True, width="stretch")
    st.caption(f"Sized: every ${core.SIZED_UNIT_USDC:,.0f} he bets = one ${core.PAPER_STAKE:.0f} paper unit, "
               f"capped at {core.SIZED_MAX_UNITS:.0f} units (so his $20k bet = 0.4u, his $300k bet = 3u). "
               "Compare the two ROIs after 100+ bets before changing anything real.")
    st.caption("“ROI copying” is what matters: it uses the price you could get when the alert arrived. "
               "If it trails “ROI at his price” by a lot, the alerts are arriving too late.")

    st.subheader("By sport")
    by_sport = core.summarize_by_sport(rows)
    if by_sport:
        st.dataframe([{"Trader": s["trader"], "Sport": s["sport"], "Alerts": s["signals"],
                       "W-L": f"{s['wins']}-{s['losses']}", "Units": f"{s['units']:+.2f}u",
                       "ROI copying": _pct(s["roi_copy"])} for s in by_sport], hide_index=True, width="stretch")
        st.caption("Wait for about 20 settled bets in a sport before cutting it; fewer than that is mostly luck.")
    else:
        st.caption("Fills in as alerts settle.")

    with st.expander("Who we follow"):
        for t in core.TRADERS:
            sports = ", ".join(sorted(t.sports)) or "all sports"
            st.markdown(f"**{t.name}** — alerts on {sports}, ${t.min_usdc:,.0f}+ · {t.note} · "
                        f"[profile](https://polymarket.com/profile/{t.wallet})")

    _big_bets_section(rows, "BreakTheBank")

    _kalshi_section()

    st.subheader("Recent signals")
    if not rows:
        st.write("No signals yet. Alerts start with his next big pre-game bet.")
        return
    recent = sorted(rows, key=lambda r: r.get("alert_time") or "", reverse=True)[:60]
    st.dataframe([{
        "When": (r.get("alert_time") or "")[:16].replace("T", " "),
        "Trader": r["trader"], "Bet": core.bet_label(r),
        "His $": f"${float(r['his_usdc']):,.0f}", "His price": f"{float(r['his_price']) * 100:.0f}¢ ({core.american(float(r['his_price']))})",
        "Alert price": f"{float(r['copy_price']) * 100:.0f}¢" if r.get("copy_price") else "—",
        "In band": "✓" if str(r.get("in_band")) == "True" else "—",
        "His sport": "—" if str(r.get("in_sports")) == "False" else "✓",
        "Result": r.get("status", ""),
        "Units": f"{core._float(r.get('pl_copy')) / core.PAPER_STAKE:+.2f}u" if r.get("pl_copy") not in (None, "") else "—",
        "Units (sized)": f"{core.sized_units(r):.1f}u",
    } for r in recent], hide_index=True, width="stretch")
    st.caption("Signals outside the price band, or outside a trader's proven sports, are logged on paper "
               "but never pushed, so you can see whether each filter helps.")


def _big_bets_section(rows: list[dict], trader: str) -> None:
    st.subheader(f"🐋 {trader}'s big bets")
    tiers = core.summarize_by_stake(rows, trader)
    if not tiers:
        st.caption("Fills in as he bets.")
        return
    st.dataframe([{"His stake": t["tier"], "Bets": t["signals"], "Open": t["open"],
                   "W-L": f"{t['wins']}-{t['losses']}", "Units (his price)": f"{t['units_his']:+.2f}u",
                   "ROI (his price)": _pct(t["roi_his"])} for t in tiers], hide_index=True, width="stretch")
    st.caption("Every bet we logged, any sport or price, so you can see whether his huge bets win more often "
               f"than his normal ones. Bets of ${core.HUGE_USDC:,.0f}+ get a 🐋 on the alert.")
    st.dataframe([{
        "When": (r.get("game_start") or "")[:10], "Sport": core.sport_group(r), "Bet": core.bet_label(r),
        "His $": f"${float(r['his_usdc']):,.0f}",
        "His price": f"{float(r['his_price']) * 100:.0f}¢ ({core.american(float(r['his_price']))})",
        "Alerted": "✓" if str(r.get("pushed")) == "True" else "—",
        "Result": r.get("status", ""),
    } for r in core.biggest_bets(rows, trader)], hide_index=True, width="stretch")


def _kalshi_section() -> None:
    from trader_follow import kalshi_follow as kf
    st.subheader("Kalshi traders")
    st.caption("Same rules on Kalshi's public feed (only traders who show their trades). Paper $10 at the "
               "Kalshi price in the alert; the alert also gives the worst odds worth taking on any book.")
    rows = kf.load_ledger()
    st.dataframe([{"Trader": s["trader"], "Alerts": s["signals"], "Open": s["open"],
                   "W-L": f"{s['wins']}-{s['losses']}", "P/L copying": _money(s["pl_copy"]),
                   "ROI copying": _pct(s["roi_copy"])} for s in kf.stats(rows)], hide_index=True, width="stretch")
    with st.expander("Who we follow on Kalshi"):
        for t in kf.KALSHI_TRADERS:
            st.markdown(f"**{t.nickname}** — ${t.min_usd:,.0f}+ pre-game · {t.note} · "
                        f"[profile](https://kalshi.com/ideas/profiles/{t.nickname})")
    if rows:
        recent = sorted(rows, key=lambda r: r.get("alert_time") or "", reverse=True)[:30]
        st.dataframe([{"When": (r.get("alert_time") or "")[:16].replace("T", " "), "Trader": r["trader"],
                       "Bet": r["bet"], "His $": f"${float(r['his_usd']):,.0f}",
                       "His price": f"{float(r['his_price']) * 100:.0f}¢ ({core.american(float(r['his_price']))})",
                       "Alert price": f"{float(r['copy_price']) * 100:.0f}¢" if r.get("copy_price") else "—",
                       "Result": r.get("status", ""),
                       "P/L ($10)": _money(core._float(r.get("pl_copy"))) if r.get("pl_copy") not in (None, "") else "—"}
                      for r in recent], hide_index=True, width="stretch")
