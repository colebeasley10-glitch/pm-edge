"""SharpIQ home (redesigned Oct 30, 2026).

Every paper lane ranked best to worst, today's open plays, and what's running with real money.
Everything from before lives on the "Everything" page (pages/All_Tools.py), one tap away.
"""
from __future__ import annotations

import csv
import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import streamlit as st

sys.modules.pop("scorecard", None)   # Streamlit Cloud keeps old modules around
import scorecard  # noqa: E402
import ui_theme  # noqa: E402

st.set_page_config(page_title="SharpIQ", page_icon="🎯", layout="centered", initial_sidebar_state="collapsed")
sys.modules.pop("odds_calc", None)
import odds_calc  # noqa: E402
odds_calc.render()
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
    t_open, t_done = st.tabs([f"Open ({len(open_)})", f"Closed ({len(done)})"])
    with t_open:
        if open_:
            row_list([item(v, False) for v in open_])
        else:
            st.caption("No open bets right now.")
    with t_done:
        if done:
            won = sum(1 for v in done if v["status"] == "won")
            lost = sum(1 for v in done if v["status"] == "lost")
            st.caption(f"{won}-{lost} · {money(sum(v['pl'] or 0 for v in done))} · newest first")
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


def fmt_american(v) -> str:
    x = _num(v)
    return "" if x is None else (f"+{x:.0f}" if x > 0 else f"{x:.0f}")


def chip(text: str, kind: str = "") -> str:
    return f"<span class='chip {kind}'>{esc(text)}</span>"


def status_chip(l: dict) -> str:
    if not l["n"]:
        return chip("Not started", "mute")
    if l["n"] < MIN_SHOWN:
        return chip("Too early", "mute")
    if l["verdict"].startswith("Clears"):
        return chip("Clears bar", "good")
    if l["verdict"].startswith("Proven"):
        return chip("Proven loser", "bad")
    if (l["roi"] or 0) > 0:
        return chip("Ahead", "good")
    return chip("Behind", "bad")


def clv_chip(l: dict) -> str:
    if l.get("clv") is None:
        return ""
    return chip("Beats close", "good") if l["clv"] > 0 else chip("Loses to close", "bad") if l["clv"] < 0 else chip("Even w/ close", "mute")


def money_txt(l: dict) -> str:
    return f"{l['pl']:+,.2f}u" if l.get("pl_col") == "units" else f"{'+' if l['pl'] >= 0 else '−'}${abs(l['pl']):,.2f}"


def lane_row(l: dict, rank: int | None = None, sub: bool = False) -> str:
    prog = min(l["n"], scorecard.MIN_BETS) / scorecard.MIN_BETS * 100
    rk = (f"<div class='lb-rank{' top' if rank and rank <= 3 and (l['roi'] or 0) > 0 else ''}'>{rank}</div>"
          if rank is not None else "")
    rec = f"{l['won']}-{l['lost']}" if l["n"] else "—"
    meta = " · ".join(x for x in (f"{l['n']} bet{'s' if l['n'] != 1 else ''}" if l["n"] else "No settled bets", rec if l["n"] else "",
                                  money_txt(l) if l["n"] else "") if x)
    return (f"<a class='lb-row{' sub' if sub else ''}' href='?lane={quote(l['name'])}' target='_self'>{rk}<div class='lb-main'>"
            f"<div class='lb-name'>{esc(('↳ ' + l['name'].split(' — ', 1)[1]) if sub else l['name'])}</div>"
            f"<div class='lb-meta'>{esc(meta)}</div>"
            f"<div class='lb-chips'>{status_chip(l)}{clv_chip(l)}</div>"
            f"<div class='lb-bar' title='{l['n']} of {scorecard.MIN_BETS} bets toward the go-live bar'>"
            f"<span style='width:{prog:.0f}%'></span></div></div>"
            f"<div class='lb-roi {tone(l['roi'])}'>{roi_txt(l['roi'])}<small>ROI</small></div></a>")


def lane_details(options: list[dict], key: str) -> None:
    """A tapped lane row lands here as ?lane=<name>; show that lane's open and closed bets in a popup."""
    pick = st.query_params.get("lane")
    if not pick:
        return
    l = next((x for x in options if x["name"] == pick), None)
    if l is None:
        return
    del st.query_params["lane"]   # so later reruns (switching views, refresh) don't pop it back open

    @st.dialog(l["name"], width="large")
    def _show():
        bits = [x for x in (l.get("verdict"), l.get("note")) if x]
        if bits:
            st.caption(" · ".join(bits))
        lane_bets(l)

    _show()


def open_plays() -> list[dict]:
    plays = []

    def add(start, league, title, sub, price, tier, lane):
        plays.append({"start": start, "league": league, "title": title, "sub": sub, "price": price,
                      "tier": tier, "lane": lane})

    for r in rows("trader_follow/data/signals.csv"):
        if r.get("status") == "open" and upcoming(r.get("game_start")) and r.get("in_band") == "True":
            cp = _num(r.get("copy_price"))
            add(r["game_start"], r.get("sport") or "Follow", f"{r['outcome']} — {r['question']}",
                f"{r['trader']} bet ${float(r['his_usdc']):,.0f}", f"{cp * 100:.0f}¢" if cp else "", "", "Trader Follow")
    for r in rows("research_lab/data/PROP_BOARD/plays.csv"):
        if r.get("status") == "open" and upcoming(r.get("game_start")):
            add(r["game_start"], r.get("league") or "Prop", r["pick"], f"{r['game']} · {r['move']}",
                r.get("target_odds") or "", "", "Prop Board steam")
    for r in rows("research_lab/data/ODDS_FAM/plays.csv"):
        if r.get("status") == "open" and r.get("kind") == "Prop steam" and upcoming(r.get("game_start")):
            add(r["game_start"], r.get("league") or "Prop", r["pick"], r.get("game") or "", r.get("bet_at") or "",
                "", "Odds Fam")
    for path, league, lane in (("ncaa_grader/data/picks.csv", "NCAAF", "NCAA Grader"),
                               ("nba_grader/data/picks.csv", "NBA", "NBA Model")):
        for r in rows(path):
            if not r.get("result") and r.get("tier") == "STRONG" and upcoming(r.get("kickoff")) \
                    and r.get("source", "live") == "live":
                add(r["kickoff"], league, r["side"], f"{r['away']} at {r['home']}", fmt_american(r.get("odds")),
                    "STRONG", lane)
    for r in rows("nba_grader/data/props.csv"):
        if not r.get("result") and r.get("tier") == "STRONG" and upcoming(r.get("kickoff")):
            add(r["kickoff"], "NBA", r["side"], f"{r['away']} at {r['home']} · proj {r['proj']}",
                fmt_american(r.get("odds")), "STRONG", "NBA Props")
    return sorted(plays, key=lambda p: p["start"])


def play_cards(plays: list[dict]) -> None:
    html_ = []
    for p in plays:
        tier = chip(p["tier"].title(), "good") if p["tier"] else ""
        html_.append(
            f"<div class='play'><div class='play-l'><span class='lg'>{esc(p['league'])}</span>"
            f"<span class='tm'>{esc(starts(p['start']))}</span></div>"
            f"<div class='play-m'><div class='pt'>{esc(p['title'])}</div><div class='ps'>{esc(p['sub'])}</div>"
            f"<div class='lb-chips'>{chip(p['lane'], 'mute')}{tier}</div></div>"
            f"<div class='play-r'>{esc(p['price'])}<small>bet at</small></div></div>")
    st.markdown("<div class='plays'>" + "".join(html_) + "</div>", unsafe_allow_html=True)


# ================================================================== page
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", key="pe_head"):
    st.markdown("<div class='pe-mark'>Sharp<i>IQ</i></div>", unsafe_allow_html=True, width="content")
    st.button("↻", key="hdr_refresh", type="secondary", on_click=_refresh, help="Reload the newest bets and results")
st.markdown(f"<div class='pe-sub'>{datetime.now(ET).strftime('%A, %B %-d')} · updated "
            f"{datetime.now(ET).strftime('%-I:%M %p')} ET</div>", unsafe_allow_html=True)

lanes = scorecard.lanes()
paper_active, paper_idle, slices = scorecard.ranked(lanes, "paper")
real = [l for l in lanes if l["money"] == "real"]
plays = open_plays()
bal = rows("kalshi_balance_log.csv")
live_open = [r for r in rows("live_plays/data/bets.csv") if r.get("status") == "open"]
riding = sum(float(r.get("cost") or 0) for r in live_open)
cash = float(bal[-1]["cash"]) if bal else None
called = [l for l in paper_active if l["n"] >= MIN_SHOWN]
ahead = [l for l in called if (l["roi"] or 0) > 0]
FAB_CSS = ("<style>.st-key-oc_fab{bottom:calc(4.4rem + env(safe-area-inset-bottom)) !important}"
           ".st-key-oc_fab button{width:2.7rem !important;height:2.7rem !important;opacity:.92}</style>")
st.markdown(FAB_CSS, unsafe_allow_html=True)
best = paper_active[0] if paper_active else None

st.markdown(
    "<div class='kpis'>"
    f"<div class='kpi'><div class='l'>Kalshi cash</div><div class='v'>{'—' if cash is None else f'${cash:,.0f}'}</div>"
    f"<div class='s'>{f'${riding:,.0f} in {len(live_open)} open bets' if live_open else 'No open live bets'}</div></div>"
    f"<div class='kpi'><div class='l'>Open plays</div><div class='v'>{len(plays)}</div>"
    f"<div class='s'>from lanes on paper</div></div>"
    f"<div class='kpi'><div class='l'>Lanes ahead</div><div class='v'>{len(ahead)}<em>/{len(called)}</em></div>"
    f"<div class='s'>with {MIN_SHOWN}+ settled bets</div></div>"
    f"<div class='kpi'><div class='l'>Top lane</div><div class='v sm'>{esc(best['name']) if best else '—'}</div>"
    f"<div class='s {tone(best['roi']) if best else ''}'>{roi_txt(best['roi']) if best else ''} ROI</div></div>"
    "</div>", unsafe_allow_html=True)

view = st.segmented_control("View", ["Leaderboard", "Today", "Real money"], default="Leaderboard",
                            key="home_view", label_visibility="collapsed", width="stretch") or "Leaderboard"

if view == "Leaderboard":
    st.markdown(f"<div class='section'><span>Paper lanes, best to worst</span>"
                f"<span class='n'>{len(paper_active)} ranked</span></div>", unsafe_allow_html=True)
    board = []
    for i, l in enumerate(paper_active, 1):
        board.append(lane_row(l, i))
        board += [lane_row(k, sub=True) for k in slices.get(l["name"], []) if k["n"]]
    st.markdown("<div class='lb'>" + "".join(board) + "</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='fine'>Ranked by ROI adjusted for sample size (ROI × bets ÷ (bets + "
                f"{scorecard.RANK_PRIOR})), so a lane needs volume <i>and</i> results to sit on top. The bar under each "
                f"lane is progress to the {scorecard.MIN_BETS}-bet go-live bar. ↳ rows break a lane down (by sport, "
                f"tier or market) and are ranked within it.</div>", unsafe_allow_html=True)
    if paper_idle:
        st.markdown("<div class='section'><span>Waiting on first results</span>"
                    f"<span class='n'>{len(paper_idle)}</span></div>", unsafe_allow_html=True)
        st.markdown("<div class='idle'>" + "".join(f"<a href='?lane={quote(l['name'])}' target='_self'>{esc(l['name'])}</a>" for l in paper_idle) + "</div>",
                    unsafe_allow_html=True)

elif view == "Today":
    st.markdown(f"<div class='section'><span>Open plays</span><span class='n'>{len(plays)}</span></div>",
                unsafe_allow_html=True)
    if plays:
        play_cards(plays[:30])
        st.markdown("<div class='fine'>All paper. Bet one by hand at the listed price or better, then log it so it "
                    "gets graded. NBA plays don't account for injuries; check the report first.</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown("<div class='quiet'><strong>Nothing open right now</strong>New plays push to your phone "
                    "as they show up.</div>", unsafe_allow_html=True)

else:
    st.markdown("<div class='section'><span>Real money</span></div>", unsafe_allow_html=True)
    st.markdown("<div class='lb'>" + "".join(lane_row(l) for l in real) + "</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='fine'>Nothing new bets real money until a lane clears the bar: {scorecard.MIN_BETS}+ "
                "bets, ROI above zero, CLV at or above zero.</div>", unsafe_allow_html=True)

lane_details([x for l in paper_active + paper_idle for x in [l] + slices.get(l["name"], [])] + real, "lane_pick")

st.markdown("<div class='gap'></div>", unsafe_allow_html=True)
c1, c2 = st.columns(2)
with c1:
    st.page_link("pages/Odds_Fam.py", label="Log a bet", icon=":material/edit_note:", use_container_width=True)
with c2:
    st.page_link("pages/Scorecard.py", label="Full scorecard", icon=":material/fact_check:", use_container_width=True)
st.page_link("pages/All_Tools.py", label="Every tool & research lane", icon=":material/apps:", use_container_width=True)
