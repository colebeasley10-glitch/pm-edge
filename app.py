"""SharpIQ home (Oct 27, 2026): the new results first.

What's running with real money, which lanes are working on paper, which aren't, and today's
open plays from the lanes that are working. Everything from before lives on the
"Everything" page (pages/All_Tools.py), one tap away.
"""
from __future__ import annotations

import csv
import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

sys.modules.pop("scorecard", None)   # Streamlit Cloud keeps old modules around
import scorecard  # noqa: E402
import ui_theme  # noqa: E402

st.set_page_config(page_title="SharpIQ", page_icon="🎯", layout="centered", initial_sidebar_state="collapsed")
ui_theme.apply()

ET = ZoneInfo("America/New_York")
NOW = datetime.now(timezone.utc)
MIN_SHOWN = 10   # paper lanes need this many settled bets before they're called working / not working


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def tone(x) -> str:
    return "up" if (x or 0) > 0 else "down" if (x or 0) < 0 else ""


def rows(rel: str) -> list[dict]:
    p = Path(rel)
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def when(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def starts(v) -> str:
    d = when(v)
    return d.astimezone(ET).strftime("%a %-I:%M %p") if d else ""


def upcoming(v) -> bool:
    d = when(v)
    return d is not None and d > NOW


def roi_txt(r) -> str:
    return "—" if r is None else f"{r * 100:+.1f}%"


def section(title: str) -> None:
    st.markdown(f"<div class='section'><span>{esc(title)}</span></div>", unsafe_allow_html=True)


def row_list(items) -> None:
    """items: (title, subtitle, value, tone)"""
    st.markdown("<div class='row-list'>" + "".join(
        f"<div class='r'><div><div class='t'>{t}</div><div class='s'>{s}</div></div><div class='v {c}'>{v}</div></div>"
        for t, s, v, c in items) + "</div>", unsafe_allow_html=True)


st.markdown("<div class='pe-mark'>Sharp<i>IQ</i></div>", unsafe_allow_html=True)
st.page_link("pages/All_Tools.py", label="Everything (full dashboard, every tab from before)", icon="🗂️",
             use_container_width=True)

lanes = scorecard.lanes()

# ---------------------------------------------------------------- real money --
section("Real money")
bal = rows("kalshi_balance_log.csv")
live_open = [r for r in rows("live_plays/data/bets.csv") if r.get("status") == "open"]
riding = sum(float(r.get("cost") or 0) for r in live_open)
cash = float(bal[-1]["cash"]) if bal else None
st.markdown(
    "<div class='hm-tiles'>"
    f"<div class='hm-tile'><div class='v'>{'—' if cash is None else f'${cash:,.2f}'}</div>"
    f"<div class='l'>Kalshi cash{(' as of ' + esc(starts(bal[-1]['at']))) if bal else ''}</div></div>"
    f"<div class='hm-tile'><div class='v'>${riding:,.2f}</div><div class='l'>{len(live_open)} open live bets settling</div></div>"
    "</div>", unsafe_allow_html=True)
real = [l for l in lanes if l["money"] == "real"]
row_list([(esc(l["name"]), esc(l["note"] or l["verdict"]),
           f"{l['pl']:+,.2f}" if l["n"] else "—", tone(l["pl"])) for l in real])
st.caption("Nothing new bets real money until a lane clears the bar: "
           f"{scorecard.MIN_BETS}+ bets, ROI above zero, CLV at or above zero.")

# ------------------------------------------------------------------- paper --
paper = [l for l in lanes if l["money"] == "paper" and l["n"] >= MIN_SHOWN]
good = sorted([l for l in paper if (l["roi"] or 0) > 0], key=lambda l: -(l["roi"] or 0))
bad = sorted([l for l in paper if (l["roi"] or 0) <= 0], key=lambda l: (l["roi"] or 0))

section("Working on paper")
if not good:
    st.write("Nothing is ahead yet.")
for l in good:
    done = min(l["n"], scorecard.MIN_BETS)
    with st.container(border=True):
        st.markdown(f"**{esc(l['name'])}** · {l['won']}-{l['lost']} · ROI **{roi_txt(l['roi'])}** · "
                    f"{l['pl']:+,.2f}")
        st.progress(done / scorecard.MIN_BETS, text=l["verdict"])

section("Not working")
row_list([(esc(l["name"]), f"{l['won']}-{l['lost']} · {esc(l['verdict'])}", roi_txt(l["roi"]), "down")
          for l in bad])
small = [l for l in lanes if l["money"] == "paper" and l["n"] < MIN_SHOWN]
if small:
    st.caption("Too few bets to call yet: " + ", ".join(f"{l['name']} ({l['n']})" for l in small))

# ------------------------------------------------- open plays from what's working --
section("Open plays from lanes that are working")
plays = []
for r in rows("trader_follow/data/signals.csv"):
    if r.get("status") == "open" and upcoming(r.get("game_start")) and r.get("in_band") == "True":
        cp = float(r["copy_price"]) if r.get("copy_price") else None
        plays.append((r["game_start"], f"{esc(r['outcome'])} — {esc(r['question'])}",
                      f"Follow {esc(r['trader'])} · ${float(r['his_usdc']):,.0f} on it · {starts(r['game_start'])}",
                      f"{cp * 100:.0f}¢" if cp else "", "Research_Lab_Trader_Follow"))
for r in rows("research_lab/data/PROP_BOARD/plays.csv"):
    if r.get("status") == "open" and upcoming(r.get("game_start")):
        plays.append((r["game_start"], esc(r["pick"]), f"Prop steam · {esc(r['move'])} · {starts(r['game_start'])}",
                      esc(r["target_odds"]), "Research_Lab_Prop_Board"))
for r in rows("research_lab/data/ODDS_FAM/plays.csv"):
    if r.get("status") == "open" and r.get("kind") == "Prop steam" and upcoming(r.get("game_start")):
        plays.append((r["game_start"], esc(r["pick"]), f"Odds Fam prop steam · {starts(r['game_start'])}",
                      esc(r["bet_at"]), "Odds_Fam"))
for r in rows("ncaa_grader/data/picks.csv"):
    if not r.get("result") and r.get("tier") == "STRONG" and upcoming(r.get("kickoff")):
        odds = r.get("odds") or ""
        try:
            o = float(odds)
            odds = f"+{o:.0f}" if o > 0 else f"{o:.0f}"
        except ValueError:
            pass
        plays.append((r["kickoff"], esc(r["side"]), f"NCAA STRONG · {esc(r['away'])} at {esc(r['home'])} · "
                      f"{starts(r['kickoff'])}", esc(odds), "Research_Lab_NCAA_Grader"))
plays.sort(key=lambda p: p[0])
if not plays:
    st.write("No open plays from the working lanes right now. They push to your phone when they show up.")
else:
    row_list([(t, s, v, "") for _, t, s, v, _ in plays[:25]])
    st.caption("All paper. Bet one by hand if you want, then log it below so it gets graded.")

st.page_link("pages/Odds_Fam.py", label="Log a bet (Hunter's, yours, anything)", icon="📝", use_container_width=True)
st.page_link("pages/Scorecard.py", label="Full scorecard", icon="✅", use_container_width=True)
