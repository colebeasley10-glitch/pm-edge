"""SharpIQ home (redesigned Oct 30, 2026).

Every paper lane ranked best to worst, today's open plays, and what's running with real money.
Everything from before lives on the "Everything" page (pages/All_Tools.py), one tap away.
"""
from __future__ import annotations

import csv
import json
import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import streamlit as st

import data_sync  # noqa: E402  Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits
data_sync.sync()

sys.modules.pop("scorecard", None)   # Streamlit Cloud keeps old modules around
import scorecard  # noqa: E402
import fair_calibration  # noqa: E402
import ui_theme  # noqa: E402

st.set_page_config(page_title="SharpIQ", page_icon="🎯", layout="centered", initial_sidebar_state="collapsed")
sys.modules.pop("odds_calc", None)
import odds_calc  # noqa: E402
import live_status  # noqa: E402
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
HIDDEN = {"v", "error", "no_fill", "void", "expired", "rejected", "dry_run", "skipped", "canceled", "cancelled"}
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
    extra = str(r.get("tier") or "").replace("_", " ").title() if r.get("tier") not in (None, "", "NO_EDGE") else ""
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
            "fair": fair_value(r),
            "status": status or "open", "pl": pl, "settled": settled,
            "hidden": status in HIDDEN, "sort": str(when(game_at or placed) or "")}


def fair_value(r: dict) -> dict | None:
    """Fair win probability vs the price the bet is listed at, for any lane's row (Nov 2, 2026).
    Fair = Pinnacle no-vig (Bet Queue / Prop Board / Odds Fam / Trader Follow) or the lane's model."""
    p = _num(r.get("fair_prob"))
    src = r.get("fair_src") or "Pinnacle"
    if p is None and _num(r.get("model_prob")) is not None:
        p, src = _num(r.get("model_prob")), "Model"
    if p is None or not 0 < p < 1:
        return None
    cents = False
    dec = None
    for k in ("decimal", "bet_at_decimal", "target_decimal", "best_decimal"):
        if (x := _num(r.get(k))) and x > 1:
            dec = x
            break
    if dec is None:
        for k in ("copy_price", "avg_price", "entry_price", "price"):
            if (c := _num(r.get(k))) is not None and 0 < c < 1:
                dec, cents = 1 / c, True
                break
    if dec is None:
        for k in ("bet_at", "target_odds", "odds"):
            if (d := _dec_from_american(r.get(k))) is not None:
                dec = d
                break
    if src == "Copy price":       # no outside price to compare against: nothing honest to show
        return None
    fair = f"{p * 100:.0f}¢" if cents else _am(1 / p)
    ev = p * dec - 1 if dec else None
    return {"p": p, "fair": fair, "ev": ev, "src": "our model's estimate" if src == "Model" else "Pinnacle no-vig"}


def fair_html(fv: dict | None) -> str:
    if not fv:
        return ""
    txt = f"Fair {fv['fair']} ({fv['p'] * 100:.1f}%, {fv['src']})"
    if fv["ev"] is None:
        return f"<br><span class='fair'>{esc(txt)}</span>"
    good = fv["ev"] > 0
    verdict = (f"+EV — worth it at {fv['fair']} or better" if good
               else f"no value here — only worth it at {fv['fair']} or better")
    return (f"<br><span class='fair'>{esc(txt)} · <b class='{'up' if good else 'down'}'>EV {fv['ev'] * 100:+.1f}%</b>"
            f" · {esc(verdict)}</span>")


def stamp(v) -> str:
    d = when(v)
    return d.astimezone(ET).strftime("%a %b %-d, %-I:%M %p") if d else ""


def bet_day(v: dict):
    """The ET calendar day a settled bet counts on: its game day, else the day it was placed."""
    d = when(v["game_at"]) or when(v["placed"])
    return d.astimezone(ET).date() if d else None


def lane_calendar(lane: dict, done: list[dict]) -> None:
    """Month calendar of a lane's settled bets: units and profit per day (Nov 3, 2026).
    Units = day P/L ÷ the lane's average stake, the same definition the board uses, so the days add
    up to the lane's Units total. Profit is shown the way the board shows it ($10 a unit for models)."""
    import calendar as _cal
    from datetime import date

    per_unit = (lane["risk"] / lane["n"]) if lane.get("n") and lane.get("risk") else None
    to_usd = 10.0 if lane.get("pl_col") == "units" else 1.0
    days: dict = {}
    for v in done:
        d = bet_day(v)
        if d is None:
            continue
        x = days.setdefault(d, {"pl": 0.0, "w": 0, "l": 0, "n": 0})
        x["pl"] += v["pl"] or 0
        x["n"] += 1
        x["w"] += v["status"] == "won"
        x["l"] += v["status"] == "lost"
    if not days:
        st.caption("Nothing settled yet, so there's nothing on the calendar.")
        return

    months = sorted({(d.year, d.month) for d in days}, reverse=True)
    labels = [date(y, m, 1).strftime("%B %Y") for y, m in months]
    pick = st.selectbox("Month", labels, key=f"cal_{lane['name']}", label_visibility="collapsed")
    y, m = months[labels.index(pick)]

    def u_txt(pl):
        return f"{pl / per_unit:+.1f}u" if per_unit else ""

    def usd(pl):
        x = pl * to_usd
        return f"{'+' if x >= 0 else '−'}${abs(x):,.0f}"

    in_month = {d: x for d, x in days.items() if (d.year, d.month) == (y, m)}
    mpl = sum(x["pl"] for x in in_month.values())
    up_days = sum(x["pl"] > 0 for x in in_month.values())
    dn_days = sum(x["pl"] < 0 for x in in_month.values())
    mw = sum(x["w"] for x in in_month.values())
    ml = sum(x["l"] for x in in_month.values())
    st.markdown(
        f"<div class='cal-sum'><span><small>Month</small><b class='{tone(mpl)}'>{esc(u_txt(mpl) or usd(mpl))}</b></span>"
        f"<span><small>Profit</small><b class='{tone(mpl)}'>{usd(mpl)}</b></span>"
        f"<span><small>Record</small><b>{mw}-{ml}</b></span>"
        f"<span><small>Days</small><b><span class='up'>{up_days}▲</span> <span class='down'>{dn_days}▼</span></b></span></div>",
        unsafe_allow_html=True)

    today = datetime.now(ET).date()
    cells = "".join(f"<div class='cal-h'>{d}</div>" for d in ("S", "M", "T", "W", "T", "F", "S"))
    for week in _cal.Calendar(firstweekday=6).monthdatescalendar(y, m):
        for d in week:
            if d.month != m:
                cells += "<div class='cal-d out'></div>"
                continue
            x = in_month.get(d)
            cls = "cal-d" + (" today" if d == today else "")
            if not x:
                cells += f"<div class='{cls}'><i>{d.day}</i></div>"
                continue
            t = tone(x["pl"])
            tip = f"{d:%a %b %-d}: {x['w']}-{x['l']} · {u_txt(x['pl'])} · {usd(x['pl'])} ({x['n']} bets)"
            cells += (f"<div class='{cls} {t or 'even'}' title='{esc(tip)}'><i>{d.day}</i>"
                      f"<b>{esc(u_txt(x['pl']) or usd(x['pl']))}</b><small>{usd(x['pl'])}</small>"
                      f"<em>{x['w']}-{x['l']}</em></div>")
    st.markdown(f"<div class='cal'>{cells}</div>", unsafe_allow_html=True)
    st.caption("Days count by game date (Eastern). Units = profit ÷ this lane's average stake; dollars "
               + ("at $10 a unit." if to_usd == 10 else "at the paper stake."))


def lane_breakdown(lane: dict) -> None:
    """Nov 4, 2026: how this lane does in each sport (NFL, NCAAF, NBA, NBA Props...) or bet type."""
    by = st.segmented_control("Split by", ["Sport", "Bet type"], default="Sport", key=f"split_{lane['name']}",
                              label_visibility="collapsed") or "Sport"
    groups = scorecard.split(lane, "category" if by == "Sport" else "type")
    rows = []
    for g in groups:
        rows.append({by: g["name"], "Bets": g["n"], "Record": f"{g['won']}-{g['lost']}" if g["n"] else "—",
                     "Profit": money_txt(g) if g["n"] else "—",
                     "Units": "—" if g.get("units") is None else f"{g['units']:+.1f}u",
                     "ROI": roi_txt(g["roi"]) if g["n"] else "—", "CLV": clv_txt(g),
                     "Open": len(g["rows"]) - g["n"]})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Same stakes and grading as the lane, just split up. Small samples swing a lot: "
               f"a slice needs about {scorecard.MIN_BETS} bets and a positive CLV before it means much.")


def lane_bets(lane: dict) -> None:
    # Nov 4, 2026: filter a lane down to one sport (NFL, NCAAF, NBA, NBA Props...)
    cats = scorecard.split(lane) if lane.get("rows") else []
    filtered = False
    if len(cats) > 1:
        names = [c["name"] for c in cats]
        sel = st.pills("Sport", ["All"] + names, default="All", key=f"cat_{lane['name']}",
                       label_visibility="collapsed") or "All"
        if sel != "All":
            g = cats[names.index(sel)]
            rec = f"{g['won']}-{g['lost']}" if g["n"] else "no settled bets"
            bits = [f"**{sel}** · {rec}"]
            if g["n"]:
                bits += [money_txt(g), f"ROI {roi_txt(g['roi'])}", f"CLV {clv_txt(g)}"]
            st.markdown(" · ".join(bits))
            lane = {**lane, **{k: v for k, v in g.items() if k != "name"}, "name": f"{lane['name']} · {sel}"}
            filtered = True
    unit = "u" if lane.get("pl_col") == "units" else ""
    views = [bet_view(r, lane.get("pl_col", "pl")) for r in lane.get("rows") or []]
    views = [v for v in views if not v["hidden"]]
    pending = sorted([v for v in views if not v["settled"]], key=lambda v: v["sort"])
    # Nov 3, 2026: unsettled bets split into Open (not started yet) and Live (game under way / awaiting the final)
    open_ = [v for v in pending if not v["game_at"] or upcoming(v["game_at"])]
    live_ = [v for v in pending if v["game_at"] and not upcoming(v["game_at"])]
    done = sorted([v for v in views if v["settled"]], key=lambda v: v["sort"], reverse=True)

    def money(x):
        return f"{x:+,.2f}{unit}" if unit else f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"

    def item(v, show_pl: bool):
        bits = [b for b in (esc(v["extra"]), esc(v["game"]), ("Game " if v["game_at"] else "Placed ")
                            + esc(stamp(v["game_at"] or v["placed"])), esc(v["price"])) if b.strip()]
        if show_pl:
            return (esc(v["title"]), " · ".join(bits + [esc(v["status"])]) + fair_html(v["fair"]),
                    money(v["pl"]), tone(v["pl"]))
        late = v["game_at"] and not upcoming(v["game_at"])
        state = ("alert only (bet by hand)" if v["status"] == "alert"
                 else "live · waiting to settle" if late else esc(v["status"]))
        return (esc(v["title"]), " · ".join(bits + [state]) + fair_html(v["fair"]), "", "")

    rows_ = lane.get("rows") or []
    stakes = [(_num(r.get("his_usdc")), _num(r.get("pl_his"))) for r in rows_]
    if any(h for h, _ in stakes):
        tot = sum(h or 0 for h, _ in stakes)
        his_pl = sum((h or 0) * p / 10.0 for h, p in stakes if p is not None)
        st.markdown(f"**He's put ${tot:,.0f} on these {len(rows_)} bets** · his estimated result "
                    f"**{'+' if his_pl >= 0 else '−'}${abs(his_pl):,.0f}** · "
                    f"your $10 copies: **{lane['pl']:+,.2f}**")
    tabs = ["Calendar", f"Open ({len(open_)})", f"Live ({len(live_)})", f"Closed ({len(done)})"]
    if len(cats) > 1 and not filtered:
        tabs.append("By sport")
    which = st.segmented_control("Bets", tabs,
                                 default="Calendar", key=f"bets_{lane['name']}",
                                 label_visibility="collapsed", width="stretch") or "Calendar"
    if which == "By sport":
        lane_breakdown(lane)
    elif which == "Calendar":
        lane_calendar(lane, done)
    elif which.startswith("Open"):
        if open_:
            row_list([item(v, False) for v in open_])
        else:
            st.caption("No open bets right now.")
    elif which.startswith("Live"):
        if live_:
            row_list([item(v, False) for v in live_])
            st.caption("Started and waiting on the final score; they move to Closed once settled.")
        else:
            st.caption("Nothing live right now.")
    elif done:
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
    data_sync.sync(force=True)
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
    if l["verdict"].startswith("Demote"):
        return chip("Demote to paper", "bad")
    if l["verdict"].startswith("Reference"):
        return chip("Reference only", "mute")
    if (l["roi"] or 0) > 0:
        return chip("Ahead", "good")
    return chip("Behind", "bad")


def clv_chip(l: dict) -> str:
    if l.get("clv") is None:
        return ""
    return chip("Beats close", "good") if l["clv"] > 0 else chip("Loses to close", "bad") if l["clv"] < 0 else chip("Even w/ close", "mute")


def money_txt(l: dict) -> str:
    """Dollars at the paper stake. Unit-graded lanes (the models) are shown at $10 a unit, like the rest."""
    pl = l["pl"] * 10 if l.get("pl_col") == "units" else l["pl"]
    return f"{'+' if pl >= 0 else '−'}${abs(pl):,.2f}"


def clv_txt(l: dict) -> str:
    """Average CLV in the lane's own unit, plus how often it beat the close. Grader lanes mix
    spread points with ML probability, so they show only the beat-the-close share."""
    c, u, b = l.get("clv"), l.get("clv_unit"), l.get("beat_close")
    if c is None:
        return "—"
    beat = f" ({b * 100:.0f}% beat)" if b is not None else ""
    if u == "cents":
        return f"{c:+.1f}¢{beat}"
    if u == "prob":
        return f"{c * 100:+.1f}%{beat}"
    return f"{b * 100:.0f}% beat" if b is not None else "—"


def lane_row(l: dict, rank: int | None = None, sub: bool = False) -> str:
    prog = min(l["n"], scorecard.MIN_BETS) / scorecard.MIN_BETS * 100
    rk = (f"<div class='lb-rank{' top' if rank and rank <= 3 and (l['roi'] or 0) > 0 else ''}'>{rank}</div>"
          if rank is not None else "")
    rec = f"{l['won']}-{l['lost']}" if l["n"] else "—"
    meta = f"{l['n']} bet{'s' if l['n'] != 1 else ''} · {rec}" if l["n"] else "No settled bets"
    stats = ""
    if l["n"]:
        u = l.get("units")
        cells = (("Profit", money_txt(l), tone(l["pl"])),
                 ("Units", "—" if u is None else f"{u:+.1f}u", tone(u)),
                 ("CLV", clv_txt(l), tone(l.get("clv")) if l.get("clv_unit") in ("cents", "prob") else ""))
        stats = "<div class='lb-stats'>" + "".join(
            f"<span><small>{k}</small><b class='{c}'>{esc(v)}</b></span>" for k, v, c in cells) + "</div>"
    return (f"<a class='lb-row{' sub' if sub else ''}' href='?lane={quote(l['name'])}' target='_self'>{rk}<div class='lb-main'>"
            f"<div class='lb-name'>{esc(('↳ ' + l['name'].split(' — ', 1)[1]) if sub else l['name'])}</div>"
            f"<div class='lb-meta'>{esc(meta)}</div>{stats}"
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


LIVE_FOR = 6 * 3600          # a started play stays on "Live now" up to 6 hours, until it settles


def _dec_from_american(v) -> float | None:
    x = _num(v)
    if x is None or -100 < x < 100:
        return None
    return 1 + (x / 100 if x > 0 else 100 / -x)


def _am(dec: float) -> str:
    return f"+{(dec - 1) * 100:.0f}" if dec >= 2 else f"{-100 / (dec - 1):.0f}"


def _prop_bet(r: dict) -> dict:
    side = r.get("side") or ("over" if " Over " in f" {r.get('pick', '')} " else "under")
    return {"kind": "prop", "player": r.get("player"), "stat": r.get("stat"), "line": r.get("line"), "side": side,
            "teams": (r.get("game") or "").split(" at ")}


def _grader_bet(r: dict) -> dict:
    m = str(r.get("market") or "").upper()
    teams = [r.get("away"), r.get("home")]
    if m == "TOTAL":
        return {"kind": "total", "line": r.get("line"), "side": str(r.get("direction") or "").lower(), "teams": teams}
    return {"kind": "spread" if m == "SPREAD" else "ml", "team": r.get("pick_team"), "line": r.get("line"),
            "teams": teams}


@st.cache_data(ttl=60, show_spinner=False)
def live_info(bets_json: str) -> list:
    """Score + winning/losing for each live play (ESPN, refreshed at most once a minute)."""
    lv = live_status.Live()
    out = []
    for b in json.loads(bets_json):
        try:
            out.append(live_status.evaluate(b, lv))
        except Exception:
            out.append(None)
    return out


def open_plays() -> list[dict]:
    """Every open paper play, upcoming or in progress, with the price we took, the fair price and EV.

    Fair is Pinnacle's no-vig price for the market lanes and the model's own probability for the
    model lanes (NCAA Grader, NBA Model/Props); EV = fair chance x decimal odds - 1, before fees."""
    plays = []

    def add(start, league, title, sub, lane, *, price="", dec=None, fair=None, fair_src="Pinnacle", cents=False,
            bet=None):
        d = when(start)
        if d is None:
            return
        age = (NOW - d).total_seconds()
        if age > LIVE_FOR:
            return
        f = _num(fair)
        ev = f * dec - 1 if (f and dec) else None
        if f:
            fair_txt = f"{f * 100:.0f}¢" if cents else _am(1 / f)
        else:
            fair_txt = ""
        plays.append({"start": start, "league": league, "title": title, "sub": sub, "lane": lane, "price": price,
                      "fair": fair_txt, "fair_src": fair_src, "ev": ev, "live": age >= 0,
                      "bet": {**(bet or {}), "league": league, "start": start}})

    for r in rows("trader_follow/data/signals.csv"):
        if r.get("status") == "open" and r.get("in_band") == "True":
            cp = _num(r.get("copy_price"))
            add(r["game_start"], r.get("sport") or "Follow", f"{r['outcome']} — {r['question']}",
                f"{r['trader']} bet ${float(r['his_usdc']):,.0f} at {float(r['his_price']) * 100:.0f}¢",
                "Trader Follow", price=f"{cp * 100:.0f}¢" if cp else "", dec=(1 / cp) if cp else None,
                fair=r.get("fair_prob"), cents=True,
                bet=live_status.describe_polymarket(r.get("question") or "", r.get("outcome") or ""))
    for r in rows("research_lab/data/PROP_BOARD/plays.csv"):
        if r.get("status") == "open":
            add(r["game_start"], r.get("league") or "Prop", r["pick"], f"{r['game']} · {r['move']}",
                "Prop Board steam", price=r.get("target_odds") or "", dec=_num(r.get("target_decimal")),
                fair=r.get("fair_prob"), bet=_prop_bet(r))
    for r in rows("research_lab/data/ODDS_FAM/plays.csv"):
        if r.get("status") == "open" and r.get("kind") == "Prop steam":
            dec = _num(r.get("best_decimal")) or _num(r.get("bet_at_decimal"))
            add(r["game_start"], r.get("league") or "Prop", r["pick"], r.get("game") or "", "Odds Fam",
                price=_am(dec) if dec else (r.get("bet_at") or ""), dec=dec, fair=r.get("fair_prob"),
                bet=_prop_bet(r))
    for path, league, lane, src in (("ncaa_grader/data/picks.csv", "NCAAF", "NCAA Grader", "ncaa_grader"),
                                    ("nba_grader/data/picks.csv", "NBA", "NBA Model", "nba_grader")):
        for r in rows(path):
            if not r.get("result") and r.get("tier") == "STRONG" and r.get("source", "live") == "live":
                add(r["kickoff"], league, r["side"], f"{r['away']} at {r['home']}", lane,
                    price=fmt_american(r.get("odds")), dec=_dec_from_american(r.get("odds")),
                    fair=fair_calibration.calibrated(src, r), fair_src="Model", bet=_grader_bet(r))
    for r in rows("ncaab_grader/data/picks.csv"):   # NCAA basketball: BET tier only
        if not r.get("result") and r.get("tier") == "BET":
            add(r["tipoff"], "NCAAB", r["side"], f"{r['away']} at {r['home']} · min {r.get('min_odds', '')}",
                "NCAAB Model", price=fmt_american(r.get("odds")), dec=_dec_from_american(r.get("odds")),
                fair=fair_calibration.calibrated("ncaab_model", r), fair_src="Model", bet={})
    # Nov 4, 2026: the home-court spread TEST lane too (its own paper $500, unproven), labeled as a test
    for r in rows("ncaab_grader/data/home_test.csv"):
        if not r.get("result"):
            add(r["tipoff"], "NCAAB", r["side"], f"{r['away']} at {r['home']} · TEST · min {r.get('min_odds', '')}",
                "NCAAB Home Test", price=fmt_american(r.get("odds")), dec=_dec_from_american(r.get("odds")),
                fair=fair_calibration.calibrated("ncaab_model", r), fair_src="Model", bet={})
    for r in rows("nba_grader/data/props.csv"):
        if not r.get("result") and r.get("tier") == "STRONG":
            add(r["kickoff"], "NBA", r["side"], f"{r['away']} at {r['home']} · proj {r['proj']}", "NBA Props",
                price=fmt_american(r.get("odds")), dec=_dec_from_american(r.get("odds")),
                fair=r.get("model_prob"), fair_src="Model",
                bet={"kind": "prop", "player": r.get("player"), "stat": r.get("stat"), "line": r.get("line"),
                     "side": str(r.get("direction") or "").lower(), "teams": [r.get("away"), r.get("home")]})
    return sorted(plays, key=lambda p: p["start"])


def queue_plays() -> list[dict]:
    """Today's Bet Queue (paper): its open plays plus any it settled today, newest list first."""
    today = NOW.astimezone(ET).strftime("%Y-%m-%d")
    out = []
    for r in rows("research_lab/data/BET_QUEUE/queue.csv"):
        if r.get("day") != today and r.get("status") != "open":
            continue
        d = when(r.get("game_start"))
        f, dec = _num(r.get("fair_prob")), _num(r.get("decimal"))
        fair_txt = (f"{f * 100:.0f}¢" if r.get("fair_src") == "Copy price" else _am(1 / f)) if f else ""
        status = r.get("status") or "open"
        out.append({"start": r.get("game_start"), "league": r.get("league") or "", "title": r.get("pick") or "",
                    "sub": " · ".join(x for x in (r.get("game"), f"¼ Kelly ${float(r.get('stake_kelly') or 0):,.0f}")
                                      if x) + ("" if status == "open" else f" · {status}")
                           + (f" · ⚠️ {r['injury']}" if r.get("injury") else ""),
                    "lane": f"Queue · {r.get('lane')}", "price": r.get("bet_at") or "", "fair": fair_txt,
                    "fair_src": "Model" if r.get("fair_src") == "Model" else "Pinnacle",
                    "ev": (f * dec - 1) if (f and dec) else None, "live": bool(d) and d <= NOW and status == "open",
                    "bet": {}, "queue": True})
    return sorted(out, key=lambda p: p["start"] or "")


def play_cards(plays: list[dict]) -> None:
    html_ = []
    for p in plays:
        when_txt = ("<span class='live-dot'>LIVE</span>" if p["live"] else "") + \
            f"<span class='tm'>{esc(starts(p['start']))}</span>"
        if p["fair"]:
            ev = p["ev"]
            ev_txt = "" if ev is None else f" · <b class='{'up' if ev > 0 else 'down'}'>{ev * 100:+.1f}% EV</b>"
            fair_line = f"{esc(p['fair_src'])} fair {esc(p['fair'])}{ev_txt}"
        else:
            fair_line = "no fair price"
        fair_span = f"<span class='fair'>{fair_line}</span>"
        qv = ""
        if p.get("queue") and p["fair"]:
            # Nov 1, 2026: fair value + EV up front, so a better price at another book can be judged on sight
            ev = p["ev"]
            cents = p["fair"].endswith("¢")
            ev_b = "" if ev is None else (f"<span>EV <b class='{'up' if ev > 0 else 'down'}'>{ev * 100:+.1f}%</b>"
                                          f" at {esc(p['price'])}</span>")
            hint = (f"<span class='qh'>Any price better than {esc(p['fair'])} is +EV</span>" if not cents else "")
            qv = (f"<div class='qv'><span>{esc(p['fair_src'])} fair <b>{esc(p['fair'])}</b></span>{ev_b}{hint}</div>")
        lv = p.get("lv")
        live_line = ""
        if lv:
            stt = lv.get("status")
            lab = {"winning": ("Winning", "good"), "losing": ("Losing", "bad"), "push": ("Push", "mute"),
                   "won": ("Won", "good"), "lost": ("Lost", "bad")}.get(stt)
            bits = " · ".join(x for x in (lv.get("score"), lv.get("clock"), lv.get("note")) if x)
            live_line = (f"<div class='live-line'>{chip(lab[0], lab[1]) if lab else ''}"
                         f"<span>{esc(bits)}</span></div>")
        html_.append(
            f"<div class='play'><div class='play-l'><span class='lg'>{esc(p['league'])}</span>{when_txt}</div>"
            f"<div class='play-m'><div class='pt'>{esc(p['title'])}</div><div class='ps'>{esc(p['sub'])}</div>{qv}{live_line}"
            f"<div class='lb-chips'>{chip(p['lane'], 'mute')}</div></div>"
            f"<div class='play-r'>{esc(p['price'])}<small>{'took' if p['live'] else 'bet at'}</small>"
            f"{'' if qv else fair_span}</div></div>")
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
        subs = [lane_row(k, sub=True) for k in slices.get(l["name"], []) if k["n"]]
        if subs:   # Nov 4, 2026: breakdowns tucked away until you open them
            board.append(f"<details class='lb-sub'><summary><span>{len(subs)} breakdown"
                         f"{'s' if len(subs) != 1 else ''}</span></summary>" + "".join(subs) + "</details>")
    st.markdown("<div class='lb'>" + "".join(board) + "</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='fine'>Ranked by ROI adjusted for sample size (ROI × bets ÷ (bets + "
                f"{scorecard.RANK_PRIOR})), so a lane needs volume <i>and</i> results to sit on top. The bar under each "
                f"lane is progress to the {scorecard.MIN_BETS}-bet go-live bar. Profit is paper dollars at $10 a bet (model lanes at "
                f"$10 a unit); Units = profit ÷ stake; CLV = average edge over the closing price and how often the "
                f"bet beat it. Tap “breakdowns” under a lane to see it split (by sport, "
                f"tier or market), ranked within it.</div>", unsafe_allow_html=True)
    if paper_idle:
        st.markdown("<div class='section'><span>Waiting on first results</span>"
                    f"<span class='n'>{len(paper_idle)}</span></div>", unsafe_allow_html=True)
        st.markdown("<div class='idle'>" + "".join(f"<a href='?lane={quote(l['name'])}' target='_self'>{esc(l['name'])}</a>" for l in paper_idle) + "</div>",
                    unsafe_allow_html=True)

elif view == "Today":
    qp = queue_plays()
    st.markdown(f"<div class='section'><span>Bet Queue — today's list</span><span class='n'>{len(qp)} of "
                f"15</span></div>", unsafe_allow_html=True)
    if qp:
        play_cards(qp)
    else:
        st.markdown("<div class='quiet'><strong>Nothing queued yet today</strong>The queue fills as lanes that "
                    "are earning their place post plays.</div>", unsafe_allow_html=True)
    # newest tip-off first: games in progress lead, finished-but-unsettled ones fall to the bottom (and past
    # the 30-card cap) instead of pushing tonight's late games off the list
    live_now = sorted((p for p in plays if p["live"]), key=lambda p: p["start"], reverse=True)
    upcoming_ = [p for p in plays if not p["live"]]
    if live_now:
        infos = live_info(json.dumps([p["bet"] for p in live_now[:30]], default=str))
        for p, info in zip(live_now, infos):
            p["lv"] = info
        st.markdown(f"<div class='section'><span>Live now</span><span class='n'>{len(live_now)}</span></div>",
                    unsafe_allow_html=True)
        play_cards(live_now[:30])
    st.markdown(f"<div class='section'><span>Upcoming</span><span class='n'>{len(upcoming_)}</span></div>",
                unsafe_allow_html=True)
    if upcoming_:
        play_cards(upcoming_[:30])
        st.markdown("<div class='fine'>All paper. Bet one by hand at the listed price or better, then log it so it "
                    "gets graded. NBA plays don't account for injuries; check the report first.</div>",
                    unsafe_allow_html=True)
    if not plays:
        st.markdown("<div class='quiet'><strong>Nothing open right now</strong>Your phone only buzzes for huge-EV "
                    "Bet Queue plays and BreakTheBank's NFL bets.</div>", unsafe_allow_html=True)

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
st.page_link("pages/NCAA_Pools.py", label="NCAA Pools — this week's card & past weeks", icon=":material/sports_football:",
             use_container_width=True)
st.page_link("pages/All_Tools.py", label="Every tool & research lane", icon=":material/apps:", use_container_width=True)
