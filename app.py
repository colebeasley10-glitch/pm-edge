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


# ------------------------------------------------- tap a lane to see its bets --
HIDDEN = {"error", "no_fill", "void", "expired", "rejected", "dry_run", "skipped", "canceled", "cancelled"}
SETTLED_SHOWN = 150


def _num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _first(r: dict, *keys) -> str:
    for k in keys:
        if r.get(k) not in (None, ""):
            return str(r[k])
    return ""


def bet_view(r: dict, pl_col: str) -> dict:
    """One ledger row (any lane's format) -> what the list shows."""
    if r.get("outcome") and r.get("question"):
        title = f"{r['outcome']} — {r['question']}"
    else:
        title = _first(r, "pick", "label", "title", "side", "ticker")
    side = str(r.get("side") or "").upper()
    if side in {"YES", "NO"} and title != side:
        title = f"{side} · {title}"
    game = _first(r, "game") or (f"{r['away']} at {r['home']}" if r.get("away") and r.get("home") else "")
    if game and game in title:
        game = ""
    game_at = _first(r, "game_start", "commence_time", "kickoff", "starts", "ends_at", "close_time")
    placed = _first(r, "placed_at", "opened_at", "alert_time", "sent_at", "logged_at", "first_buy")
    cents = _num(_first(r, "avg_price", "entry_price", "copy_price", "price", "limit_price"))
    price = f"{cents * 100:.0f}¢" if cents is not None and 0 < cents < 1 else ""
    if not price:
        o = _first(r, "bet_at", "target_odds", "odds")
        if (x := _num(o)) is not None:
            o = f"+{x:.0f}" if x > 0 else f"{x:.0f}"
        price = o
    status = str(r.get("status") or r.get("result") or "").lower()
    pl = _num(r.get(pl_col))
    settled = pl is not None and status not in HIDDEN | {"open", "alert"}
    if settled:
        status = "won" if pl > 0 else "lost" if pl < 0 else "push"
    extra = ""
    if (his := _num(r.get("his_usdc"))) is not None:
        hp = _num(r.get("his_price"))
        extra = f"He bet ${his:,.0f}" + (f" at {hp * 100:.0f}¢" if hp else "")
        ph = _num(r.get("pl_his"))
        if ph is not None and settled:
            his_pl = his * ph / 10.0          # pl_his is per $10 at his price
            extra += f" → he {'won' if his_pl > 0 else 'lost'} ~${abs(his_pl):,.0f}"
        if r.get("sport"):
            extra = f"{r['sport']} · {extra}"
    return {"title": title, "game": game, "extra": extra, "game_at": game_at, "placed": placed, "price": price,
            "status": status or "open", "pl": pl, "settled": settled,
            "hidden": status in HIDDEN, "sort": str(when(game_at or placed) or "")}


def stamp(v) -> str:
    d = when(v)
    return d.astimezone(ET).strftime("%a %b %-d, %-I:%M %p") if d else ""


def lane_bets(lane: dict) -> None:
    unit = "u" if lane.get("pl_col") == "units" else ""
    views = [bet_view(r, lane.get("pl_col", "pl")) for r in lane.get("rows") or []]
    views = [v for v in views if not v["hidden"]]
    open_ = sorted([v for v in views if not v["settled"]], key=lambda v: v["sort"])
    done = sorted([v for v in views if v["settled"]], key=lambda v: v["sort"], reverse=True)

    def money(x):
        return f"{x:+,.2f}{unit}" if unit else f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"

    def item(v, show_pl: bool):
        bits = [b for b in (esc(v["extra"]), esc(v["game"]), ("Game " if v["game_at"] else "Placed ")
                            + esc(stamp(v["game_at"] or v["placed"])), esc(v["price"])) if b.strip()]
        if show_pl:
            return (esc(v["title"]), " · ".join(bits + [esc(v["status"])]), money(v["pl"]), tone(v["pl"]))
        late = v["game_at"] and not upcoming(v["game_at"])
        state = ("alert only (bet by hand)" if v["status"] == "alert"
                 else "waiting to settle" if late else esc(v["status"]))
        return (esc(v["title"]), " · ".join(bits + [state]), "", "")

    rows_ = lane.get("rows") or []
    stakes = [(_num(r.get("his_usdc")), _num(r.get("pl_his"))) for r in rows_]
    if any(h for h, _ in stakes):
        tot = sum(h or 0 for h, _ in stakes)
        his_pl = sum((h or 0) * p / 10.0 for h, p in stakes if p is not None)
        st.markdown(f"**He's put ${tot:,.0f} on these {len(rows_)} bets** · his estimated result "
                    f"**{'+' if his_pl >= 0 else '−'}${abs(his_pl):,.0f}** · "
                    f"your $10 copies: **{lane['pl']:+,.2f}**")
    st.markdown(f"**Open bets ({len(open_)})**")
    if open_:
        row_list([item(v, False) for v in open_])
    else:
        st.caption("No open bets right now.")
    st.markdown(f"**Settled bets ({len(done)})** — newest first")
    if done:
        row_list([item(v, True) for v in done[:SETTLED_SHOWN]])
        if len(done) > SETTLED_SHOWN:
            st.caption(f"Newest {SETTLED_SHOWN} of {len(done)} shown.")
    else:
        st.caption("Nothing settled yet.")


def lane_label(l: dict, extra: str = "") -> str:
    rec = f"{l['won']}-{l['lost']}" if l["n"] else "no settled bets"
    pl = f" · {l['pl']:+,.2f}{'u' if l.get('pl_col') == 'units' else ''}" if l["n"] else ""
    roi = f" · ROI {roi_txt(l['roi'])}" if l["n"] and l["roi"] is not None else ""
    return f"**{l['name']}** · {rec}{pl}{roi}{(' · ' + extra) if extra else ''}"


def lane_expander(l: dict, extra: str = "") -> None:
    with st.expander(lane_label(l, extra)):
        if l.get("note") or l.get("verdict"):
            st.caption(" · ".join(x for x in (l.get("note"), l.get("verdict")) if x))
        lane_bets(l)


def _refresh() -> None:
    st.cache_data.clear()


with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.markdown("<div class='pe-mark'>Sharp<i>IQ</i></div>", unsafe_allow_html=True, width="content")
    st.button("↻ Refresh", key="home_refresh", type="secondary", on_click=_refresh,
              help="Reload the newest bets and results")
st.caption(f"Updated {datetime.now(ET).strftime('%a %-I:%M %p')} · tap any lane to see its open and settled bets")
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
for l in real:
    lane_expander(l)
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
        st.progress(done / scorecard.MIN_BETS, text=l["verdict"])
        lane_expander(l)

section("Not working")
for l in bad:
    lane_expander(l)
small = [l for l in lanes if l["money"] == "paper" and l["n"] < MIN_SHOWN]
if small:
    section("Too few bets to call yet")
    for l in small:
        lane_expander(l)

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
