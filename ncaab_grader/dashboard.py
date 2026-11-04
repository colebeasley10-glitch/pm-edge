"""NCAA Basketball Model page: today's bets, open/settled bets, P/L by period, ratings (read-only, PAPER)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import streamlit as st

from nba_grader.dashboard import ET, esc, kpi, pct, quiet, section, table, tip, tone, upcoming
from ncaab_grader import core


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(x) -> str:
    return "—" if x is None else (f"+${x:,.2f}" if x > 0 else f"-${abs(x):,.2f}" if x < 0 else "$0.00")


def bet_cards(rows: list[dict]) -> None:
    out = []
    for r in rows:
        kind = "good" if r["tier"] in ("BET", "TEST") else "mute"
        sub = (f"{r['away']} at {r['home']} · model {r['model_line']} · proj {r['proj']}")
        chips = (f"<span class='chip {kind}'>EV {float(r['ev']) * 100:+.1f}%</span>"
                 f"<span class='chip mute'>win {float(r['model_prob']) * 100:.1f}% vs {float(r['breakeven']) * 100:.1f}% BE</span>"
                 f"<span class='chip mute'>min {core.fmt_odds(float(r['min_odds']))}</span>")
        stake = f"${float(r.get('stake') or 0):.2f}" if r["tier"] in ("BET", "TEST") else "watch"
        out.append(f"<div class='play'><div class='play-l'><span class='lg'>{esc(r['tier'].title())}</span>"
                   f"<span class='tm'>{esc(tip(r['tipoff']))}</span></div>"
                   f"<div class='play-m'><div class='pt'>{esc(r['side'])} ({esc(core.fmt_odds(float(r['odds'])))})</div>"
                   f"<div class='ps'>{esc(sub)}</div><div class='lb-chips'>{chips}</div></div>"
                   f"<div class='play-r'>{esc(stake)}<small>stake</small></div></div>")
    st.markdown("<div class='plays'>" + "".join(out) + "</div>", unsafe_allow_html=True)


def cells(s: dict, money_mode: bool) -> list:
    rec = f"{s['w']}-{s['l']}" + (f"-{s['p']}" if s.get("p") else "")
    pl = money(s["profit"]) if money_mode else f"{s['profit']:+.2f}u"
    c = s["clv_pts"] if s["clv_pts"] is not None else (s["clv_ml"] * 100 if s["clv_ml"] is not None else None)
    return [s["picks"], rec, (pl, tone(s["profit"])), (pct(s["roi"]), tone(s["roi"])),
            ("—" if c is None else f"{c:+.2f}", tone(c)), s["open"]]


def render() -> None:
    st.set_page_config(page_title="NCAAB Model · SharpIQ", page_icon="🏀", layout="centered",
                       initial_sidebar_state="collapsed")
    sys.modules.pop("ui_theme", None)
    import ui_theme  # noqa: E402
    ui_theme.apply()
    try:
        status = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        status = {}
    try:
        card = core.load_card()
    except Exception:
        card = {"version": "?", "rules": {}, "staking": {"start_bankroll": 500}}
    rows = core.load_ledger()
    bets = [r for r in rows if r.get("tier") == "BET"]

    st.page_link("app.py", label="SharpIQ", icon=":material/arrow_back:")
    st.markdown("<div class='pe-mark'>NCAAB <i>Model</i></div>", unsafe_allow_html=True)
    ran = ""
    if status.get("at"):
        try:
            ran = datetime.fromisoformat(status["at"]).astimezone(ET).strftime("%a %-I:%M %p")
        except ValueError:
            pass
    st.markdown(f"<div class='pe-sub'>Paper lane · $500 bankroll · model {esc(card.get('version'))} · "
                f"last run {esc(ran or '—')} ET</div>", unsafe_allow_html=True)
    if status.get("suspended"):
        st.warning(f"Bets suspended this run: {status['suspended']}")

    bank = core.bankroll(rows, card) if card.get("staking") else status.get("bankroll", {})
    s = core.stats(bets, pl="pnl", risk="stake")
    open_today = [r for r in rows if not r.get("result") and upcoming(r.get("tipoff"))]
    st.markdown("<div class='kpis'>"
                + kpi("Bankroll", f"${bank.get('balance', 500):,.2f}", f"{money(bank.get('pnl'))} · at risk ${bank.get('at_risk', 0):,.2f}",
                      tone(bank.get("pnl")))
                + kpi("Bets", f"{s['w']}-{s['l']}", f"{pct(s['roi'])} ROI", tone(s["roi"]))
                + kpi("Today", str(sum(r['tier'] == 'BET' for r in open_today)),
                      f"{sum(r['tier'] == 'WATCH' for r in open_today)} watching")
                + kpi("Beat the close", pct(s["beat_close"], signed=False), "bets") + "</div>",
                unsafe_allow_html=True)

    view = st.segmented_control("View", ["Today", "Record", "Ratings", "Method"], default="Today",
                                key="ncaab_view", label_visibility="collapsed", width="stretch") or "Today"
    if view == "Today":
        b = sorted([r for r in open_today if r["tier"] == "BET"], key=lambda r: -float(r["ev"]))
        section("Qualified bets", f"{len(b)} open")
        if b:
            bet_cards(b)
        else:
            quiet("No qualified bets", "Nothing cleared a tested threshold. Zero-bet days are expected.")
        w = sorted([r for r in open_today if r["tier"] == "WATCH"], key=lambda r: -float(r["ev"]))[:25]
        section("Watch list", f"{len(w)}")
        if w:
            bet_cards(w)
        ht_open = sorted([r for r in core.load_ledger(core.HOME_TEST_PATH)
                          if not r.get("result") and upcoming(r.get("tipoff"))], key=lambda r: -float(r["ev"]))
        section("Home-court test (paper, separate $500)", f"{len(ht_open)} open")
        if ht_open:
            bet_cards(ht_open)
        else:
            quiet("No home-court test plays", "Logs home spreads at +3% EV or better. Not part of the $5 bets.")
        gaps = status.get("insufficient") or []
        if gaps:
            section("Insufficient data", str(len(gaps)))
            st.markdown("<div class='fine'>" + "<br>".join(esc(x) for x in gaps[:30]) + "</div>", unsafe_allow_html=True)
        st.markdown("<div class='fine'>Injuries and lineups aren't modeled — check news before any bet. "
                    "<b>Min</b> is the worst price that still clears the threshold.</div>", unsafe_allow_html=True)
    elif view == "Record":
        section("Bets (paper money)")
        body = [["All bets · flat $5"] + cells(s, True),
                ["All bets · ¼ Kelly (compare)"] + cells(core.stats(bets, pl="pnl_kelly", risk="stake_kelly"), True)]
        for m, lab in (("SPREAD", "Spread"), ("TOTAL", "Total"), ("ML", "Moneyline")):
            body.append([f"  {lab}"] + cells(core.stats([r for r in bets if r["market"] == m], pl="pnl", risk="stake"), True))
        table(["Group", "Bets", "W-L", "P/L", "ROI", "CLV", "Open"], body, minor={1, 2, 3, 4})
        ht_rows = core.load_ledger(core.HOME_TEST_PATH)
        htb = core.home_test_bankroll(ht_rows, card)
        section("Home-court test (paper, own $500)", f"bankroll ${htb['balance']:,.2f}")
        table(["Group", "Bets", "W-L", "P/L", "ROI", "CLV", "Open"],
              [["Home spreads · flat $5"] + cells(core.stats(ht_rows, pl="pnl", risk="stake"), True)],
              minor={1, 2, 3, 4})
        st.markdown("<div class='fine'>Unproven: earns a place in the $5 lane only if it beats the closing line "
                    "over a real sample. Home court fixed at "
                    f"{esc(card.get('home_court', {}).get('pts', '—'))} pts.</div>", unsafe_allow_html=True)
        section("Model test (every game, 1u, not bets)")
        body = []
        for t in ("BET", "WATCH", "PASS"):
            body.append([t.title()] + cells(core.stats([r for r in rows if r.get("tier") == t]), False))
        table(["Tier", "Picks", "W-L", "Units", "ROI", "CLV", "Open"], body)
        per = core.periods(rows)
        for k, lab in (("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")):
            items = list(per.get(k, {}).items())[-14:]
            if items:
                section(f"{lab} P/L")
                table([lab, "P/L"], [[kk, (money(v), tone(v))] for kk, v in reversed(items)])
        done = sorted([r for r in bets if r.get("result")], key=lambda r: r.get("tipoff") or "", reverse=True)[:40]
        if done:
            section("Settled bets")
            table(["Bet", "Price", "Final", "Result", "P/L", "CLV"],
                  [[r["side"], core.fmt_odds(float(r["odds"])),
                    f"{r.get('away_pts', '')}–{r.get('home_pts', '')}", r["result"],
                    (money(_f(r.get("pnl"))), tone(_f(r.get("pnl")))), r.get("clv") or "—"] for r in done])
    elif view == "Ratings":
        rs = status.get("ratings") or []
        section("Adjusted efficiency", f"home court {status.get('home_court_pts', '—')} pts (fixed)")
        if rs:
            table(["Team", "Net", "Off", "Def", "Tempo", "G", "Prior"],
                  [[r["team"], (f"{r['net']:+.1f}", tone(r["net"])), r["adj_o"], r["adj_d"], r["tempo"],
                    r["games"], f"{r['prior_net']:+.1f}"] for r in rs[:120]])
            st.markdown("<div class='fine'>Points per 100 possessions vs an average D1 team. Net = offense minus "
                        "defense allowed. Prior = preseason rating from last season + returning minutes + transfers.</div>",
                        unsafe_allow_html=True)
        else:
            quiet("No ratings yet", "They appear after the first run.")
    else:
        bt = card.get("backtest", {})
        st.markdown(f"<div class='fine' style='font-size:.9rem'>{esc(card.get('summary', ''))}</div>",
                    unsafe_allow_html=True)
        if bt:
            section("Backtest (untouched test seasons)")
            table(["Market", "Bets", "W-L", "ROI", "95% CI"],
                  [[m, v["bets"], f"{v['W']}-{v['L']}", f"{v['roi']:+.1f}%", f"{v['roi_lo95']:+.1f} to {v['roi_hi95']:+.1f}"]
                   for m, v in bt.items()])
        st.markdown("<div class='fine'>Full method and every number: docs/NCAAB_MODEL.md in the repo.</div>",
                    unsafe_allow_html=True)
    st.markdown("<div class='fine'>📝 Paper only. Separate from the NCAA football model. Every game is logged at "
                "a frozen line and price with a timestamp and model version.</div>", unsafe_allow_html=True)
