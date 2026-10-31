"""Streamlit view for the NBA Game Model (read-only, PAPER results)."""
from __future__ import annotations

import json
import sys

import streamlit as st

from nba_grader import core


def _u(x) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def _rate(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _table(stats: list[dict], label: str) -> list[dict]:
    return [{label: s.get("group") or s.get("market"), "Picks": s["picks"],
             "W-L-P": f"{s['w']}-{s['l']}-{s['p']}", "Units": _u(s["units"]), "ROI": _pct(s["roi"]),
             "Win %": _rate(s["win_pct"]), "CLV (pts)": _u(s["clv_pts"]), "Beat close": _rate(s["beat_close"]),
             "Open": s["open"]} for s in stats]


def _edge(r: dict) -> str:
    if r.get("edge_pts") not in ("", None):
        return f"{float(r['edge_pts']):.1f} pts"
    return f"{float(r['edge_prob']) * 100:.1f}%"


def render() -> None:
    st.set_page_config(page_title="Research Lab: NBA Game Model", page_icon="🏀", layout="centered")
    sys.modules.pop("odds_calc", None)
    import odds_calc  # noqa: E402
    odds_calc.render()
    try:
        st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
    except Exception:
        pass
    st.title("NBA Game Model")
    st.info("📝 **PAPER ONLY.** Every NBA game gets the model's side of the spread, total and moneyline, "
            "frozen at the line when it's logged (usually the morning of). 1 unit flat per pick. "
            "Only **Strong + Lean** are recommendations; the rest are tracked to test the model honestly.")

    try:
        status = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        status = {}
    rows = core.load_ledger()

    upcoming = sorted([r for r in rows if r.get("source") == "live" and not r.get("result")
                       and r.get("tier") in core.RECOMMENDED + ("LONG_SHOT",)], key=lambda r: r.get("kickoff") or "")
    st.subheader("Today's Strong + Lean plays")
    if upcoming:
        st.dataframe([{"Tip": (r.get("kickoff") or "")[:16].replace("T", " "), "Game": f"{r['away']} @ {r['home']}",
                       "Pick": r["side"], "Odds": core.fmt_odds(float(r["odds"])),
                       "Tier": r["tier"].replace("_", " ").title(), "Edge": _edge(r),
                       "Model": r.get("proj", ""), "Now": r.get("close_line") or r.get("close_odds") or ""}
                      for r in upcoming], hide_index=True, width="stretch")
        st.caption("Tip times in UTC. **Now** is the latest line read; if it moved toward the pick, that's CLV. "
                   "The model doesn't see injuries: a big gap on a night a star sits is usually the model being wrong.")
    else:
        st.write("None open right now. Picks log when the day's lines post.")

    sel = [r for r in rows if r.get("source") == "live"]
    if any(r.get("result") for r in sel):
        st.subheader("Record")
        st.dataframe(_table(core.summarize(rows, source="live", model="nba_v1"), "Group"), hide_index=True, width="stretch")
        st.dataframe(_table(core.by_market(rows, source="live", model="nba_v1", recommended_only=True),
                            "Market (Strong + Lean)"), hide_index=True, width="stretch")
    st.caption("**How to read this:** under ~200 settled picks, W-L is mostly luck. CLV (did the line move "
               "your way before tip?) shows an edge much sooner. Needs to clear the go-live bar before real money.")

    done = sorted([r for r in sel if r.get("result")], key=lambda r: r.get("kickoff") or "", reverse=True)[:60]
    if done:
        st.subheader("Recently graded")
        st.dataframe([{"Game": f"{r['away']} {r.get('away_pts', '')} @ {r['home']} {r.get('home_pts', '')}",
                       "Pick": r["side"], "Tier": r["tier"].replace("_", " ").title(), "Result": r["result"],
                       "Units": r.get("units"), "CLV": r.get("clv")} for r in done], hide_index=True, width="stretch")

    if status.get("ratings"):
        with st.expander("Power ratings (points per game vs an average team)"):
            st.caption(f"Home court worth {status.get('home_court', '—')} pts. Off > 0 scores more than average; "
                       "Def > 0 allows more (worse). Net = Off − Def.")
            st.dataframe([{"Team": r["team"], "Net": f"{r['net']:+.1f}", "Off": f"{r['off']:+.1f}",
                           "Def": f"{r['def']:+.1f}"} for r in status["ratings"]], hide_index=True, width="stretch")
    if status:
        st.caption(f"Last run {status.get('at', '—')} · {status.get('finals_this_season', 0)} finals this season "
                   f"+ {status.get('prior_season_games', 0)} late-season games from last year (down-weighted).")
