"""Streamlit view for Live $5 Today's Plays (read-only)."""
from __future__ import annotations

import json

import streamlit as st

from live_plays import core


def _money(x) -> str:
    return "—" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def _rec(s: dict) -> str:
    return f"{s['won']}-{s['lost']}"


def _logged_bets():
    """Bets you logged by hand on the Home tab, plus their settlements."""
    try:
        from research_lab import my_bets
        logged = []
        try:
            import bet_log
            if bet_log.token():
                logged = bet_log.read_log()[0]
        except Exception:
            logged = []
        return logged or my_bets.load_log(), my_bets.load_results()
    except Exception:
        return [], {}


def render() -> None:
    st.set_page_config(page_title="Live Today's Plays", page_icon="💵", layout="centered")
    try:
        st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
    except Exception:
        pass
    st.title("Live Today's Plays")
    rows = core.load()
    try:
        status = json.loads((core.DATA / "status.json").read_text())
    except Exception:
        status = {}
    live = status.get("mode") == "live"
    (st.warning if live or not status else st.info)(
        ("💵 **LIVE, real money.** " if live or not status else "📝 **DRY RUN** (Kalshi keys missing or switched off). ")
        + f"Copies both Today's Plays lanes, **one bet per game**. Kalshi picks are bought automatically at "
          f"${core.per_bet():.0f}; Polymarket picks send a phone alert to bet ${core.per_bet():.0f} by hand. "
          f"${core.daily_budget():.0f}/day cap on Kalshi spend.")

    logged, results = _logged_bets()
    poly = core.match_logged(rows, logged, results)
    lv = core.live_record(rows, poly)
    paper = core.paper_record(core.paper_positions())
    pa = paper["all"]

    st.subheader("Live (real money)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("P/L", _money(lv["pl"]))
    c2.metric("Record", _rec(lv))
    c3.metric("ROI", _pct(lv["roi"]))
    c4.metric("Open", lv["open"])
    if lv["poly_unlogged"]:
        st.caption(f"⚠️ {lv['poly_unlogged']} Polymarket alert(s) not logged yet. Log them on the Home tab "
                   "(Log a bet) so they count; unlogged alerts are left out of the record.")

    st.subheader("Paper record (before going live)")
    st.caption(f"Both lanes combined, one bet per game, scaled to ${core.CEIL_PER_BET:.0f} a bet, since "
               f"{paper['since'][:10] or '—'}. Simulated fills at the best price seen; nothing was actually bet.")
    table = [{"": "All", "W-L": _rec(pa), "P/L": _money(pa["pl"]), "ROI": _pct(pa["roi"]), "Open": pa["open"]}]
    for v, s in paper["by_venue"].items():
        table.append({"": v + (" (auto)" if v == "Kalshi" else " (by hand)"), "W-L": _rec(s),
                      "P/L": _money(s["pl"]), "ROI": _pct(s["roi"]), "Open": s["open"]})
    st.dataframe(table, hide_index=True, width="stretch")

    if status:
        st.subheader("Last round")
        st.write(f"{status.get('at', '')} · {status.get('new_picks', 0)} new picks · "
                 f"{status.get('placed', 0)} Kalshi orders · {status.get('alerts', 0)} Polymarket alerts")
        if status.get("spent_today") is not None:
            st.caption(f"Kalshi spend today: ${status['spent_today']:.2f} of ${status.get('daily_budget', 0):.2f}")
        if status.get("skipped"):
            st.caption("Skipped: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in status["skipped"].items()))

    shown = [r for r in rows if r.get("venue") == "Kalshi"] + poly
    shown.sort(key=lambda r: r.get("placed_at") or "", reverse=True)
    if shown:
        st.subheader("Bets")
        out = []
        for r in shown[:80]:
            pl = core.f(r.get("pl"))
            if r.get("venue") == "Polymarket":
                bet = f"${r['stake']:.2f} at {r.get('odds')}" if r.get("logged") else "not logged"
            elif r.get("status") in {"open", "won", "lost", "void"}:
                bet = f"{r.get('filled')} @ {core.f(r.get('avg_price'), 0) * 100:.0f}¢"
            else:
                bet = r.get("note") or ""
            out.append({"Placed": (r.get("placed_at") or "")[:16].replace("T", " "), "Pick": r.get("title"),
                        "Where": r.get("venue"), "Bet": bet, "Status": r.get("status"),
                        "P/L": _money(pl) if r.get("status") in {"won", "lost", "void"} else ""})
        st.dataframe(out, hide_index=True, width="stretch")
    else:
        st.caption("No live bets yet. They start with the next new Today's Plays pick.")
