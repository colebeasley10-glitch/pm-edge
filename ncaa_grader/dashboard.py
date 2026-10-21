"""Streamlit view for the NCAA Model Grader (read-only, PAPER results)."""
from __future__ import annotations

import streamlit as st

from ncaa_grader import core


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


def render() -> None:
    st.set_page_config(page_title="Research Lab: NCAA Model Grader", page_icon="🏈", layout="centered")
    try:
        st.page_link("app.py", label="Back to SharpIQ", icon="⬅️")
    except Exception:
        pass
    st.title("NCAA Model Grader")
    st.info("📝 **PAPER ONLY.** Every FBS game gets the model's side of the spread, total and moneyline, "
            "with the line frozen when it's logged. 1 unit flat per pick, spreads/totals at -110. "
            "Only **Strong + Lean** are recommendations; everything else is tracked to test the model honestly.")

    rows = core.load_ledger()
    models = sorted({r.get("model") for r in rows if r.get("model")}) or ["core_v1"]
    model = st.radio("Model", models, horizontal=True,
                     format_func=lambda m: {"core_v1": "SharpIQ (CFBD CORE)", "workbook": "My workbook"}.get(m, m))

    for source, title in (("live", "Live (frozen pre-game lines)"), ("backtest", "Backtest (weeks already played)")):
        sel = [r for r in rows if r.get("source") == source and r.get("model") == model]
        if not sel:
            continue
        st.subheader(title)
        st.dataframe(_table(core.summarize(rows, source=source, model=model), "Group"), hide_index=True, width="stretch")
        st.dataframe(_table(core.by_market(rows, source=source, model=model, recommended_only=True), "Market (Strong + Lean)"),
                     hide_index=True, width="stretch")
        if source == "backtest":
            st.caption("Backtest picks use ratings from before each week and the opening line. "
                       "Moneylines in the backtest are closing prices, so they get no CLV.")

    st.caption("**How to read this:** under ~200 settled picks, W-L is mostly luck. "
               "CLV (did the line move your way before kickoff?) shows an edge much sooner. "
               "Tracked picks hitting ~52% at -110 means the model is about as good as the market, not better.")

    upcoming = sorted([r for r in rows if r.get("source") == "live" and r.get("model") == model
                       and not r.get("result") and r.get("tier") in core.RECOMMENDED + ("LONG_SHOT",)],
                      key=lambda r: r.get("kickoff") or "")
    st.subheader("This week's Strong + Lean picks")
    if upcoming:
        st.dataframe([{"Kickoff": (r.get("kickoff") or "")[:16].replace("T", " "), "Game": f"{r['away']} @ {r['home']}",
                       "Pick": r["side"], "Tier": r["tier"].replace("_", " ").title(),
                       "Edge": (f"{float(r['edge_pts']):.1f} pts" if r.get("edge_pts") not in ("", None)
                                else f"{float(r['edge_prob']) * 100:.1f}%"),
                       "Model win %": _rate(float(r["model_prob"]))} for r in upcoming],
                     hide_index=True, width="stretch")
    else:
        st.write("None open right now. Picks are logged when the week's lines post.")

    done = sorted([r for r in rows if r.get("source") == "live" and r.get("model") == model and r.get("result")],
                  key=lambda r: r.get("kickoff") or "", reverse=True)[:60]
    if done:
        st.subheader("Recently graded")
        st.dataframe([{"Game": f"{r['away']} {r.get('away_pts', '')} @ {r['home']} {r.get('home_pts', '')}",
                       "Pick": r["side"], "Tier": r["tier"].replace("_", " ").title(), "Result": r["result"],
                       "Units": r.get("units"), "CLV": r.get("clv")} for r in done],
                     hide_index=True, width="stretch")

    with st.expander("Grade your own Excel model too"):
        st.markdown("Add `ncaa_grader/data/my_model.csv` to the repo with columns "
                    "`home,away,home_margin,total` (optional `home_win_prob`), one row per game, "
                    "before kickoff. `home_margin` is how many points you project the home team to win by. "
                    "The grader logs those as a separate **My workbook** model against the same frozen lines.")
