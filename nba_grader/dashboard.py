"""NBA Game Model page: game picks, player props and power ratings (read-only, PAPER results)."""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from nba_grader import core, props

ET = ZoneInfo("America/New_York")
REC = ("STRONG", "LEAN")


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tone(x) -> str:
    return "up" if (x or 0) > 0 else "down" if (x or 0) < 0 else ""


def pct(x, signed=True) -> str:
    return "—" if x is None else (f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%")


def tip(v) -> str:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.astimezone(ET).strftime("%a %-I:%M %p")
    except ValueError:
        return ""


def upcoming(v) -> bool:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except ValueError:
        return False


def kpi(label, value, sub="", sub_tone="") -> str:
    return (f"<div class='kpi'><div class='l'>{esc(label)}</div><div class='v'>{value}</div>"
            f"<div class='s {sub_tone}'>{esc(sub)}</div></div>")


def section(title, n="") -> None:
    st.markdown(f"<div class='section'><span>{esc(title)}</span><span class='n'>{esc(n)}</span></div>",
                unsafe_allow_html=True)


def table(head: list[str], body: list[list], minor: set[int] = frozenset()) -> None:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    trs = []
    for i, row in enumerate(body):
        tds = "".join(f"<td class='{x[1]}'>{esc(x[0])}</td>" if isinstance(x, tuple) else f"<td>{esc(x)}</td>"
                      for x in row)
        trs.append(f"<tr class='{'minor' if i in minor else ''}'>{tds}</tr>")
    st.markdown(f"<div class='tbl-wrap'><table class='tbl'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}"
                f"</tbody></table></div>", unsafe_allow_html=True)


def stat_cells(s: dict, clv_fmt) -> list:
    rec = f"{s['w']}-{s['l']}" + (f"-{s['p']}" if s.get("p") else "")
    return [s["picks"], rec, (f"{s['units']:+.2f}", tone(s["units"])), (pct(s["roi"]), tone(s["roi"])),
            (clv_fmt(s), tone(s.get("clv_pts") if "clv_pts" in s else s.get("clv"))), s["open"]]


def cards(rows: list[dict], title_fn, sub_fn, edge_fn) -> None:
    out = []
    for r in rows:
        tier = {"LONG_SHOT": "Dog"}.get(r["tier"], r["tier"].title())
        kind = "good" if r["tier"] == "STRONG" else "mute"
        out.append(f"<div class='play'><div class='play-l'><span class='lg'>{esc(tier)}</span>"
                   f"<span class='tm'>{esc(tip(r['kickoff']))}</span></div>"
                   f"<div class='play-m'><div class='pt'>{esc(title_fn(r))}</div><div class='ps'>{esc(sub_fn(r))}</div>"
                   f"<div class='lb-chips'><span class='chip {kind}'>{esc(edge_fn(r))}</span></div></div>"
                   f"<div class='play-r'>{esc(props.fmt_price(r.get('odds')))}<small>bet at</small></div></div>")
    st.markdown("<div class='plays'>" + "".join(out) + "</div>", unsafe_allow_html=True)


def quiet(title, body) -> None:
    st.markdown(f"<div class='quiet'><strong>{esc(title)}</strong>{esc(body)}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ views
def games_view(rows: list[dict]) -> None:
    live = [r for r in rows if r.get("source") == "live"]
    open_ = sorted([r for r in live if not r.get("result") and r.get("tier") in REC + ("LONG_SHOT",)
                    and upcoming(r.get("kickoff"))], key=lambda r: r.get("kickoff") or "")
    section("Today's game plays", f"{len(open_)} open")
    if open_:
        def edge(r):
            return (f"{float(r['edge_pts']):.1f} pt edge" if r.get("edge_pts") not in ("", None)
                    else f"{float(r['edge_prob']) * 100:.1f}% edge")
        def sub(r):
            a, h = r["away"].split()[-1], r["home"].split()[-1]
            try:
                ap, hp = (float(x.rsplit(" ", 1)[1]) for x in r["proj"].split(" - "))
                return f"{r['away']} at {r['home']} · model {a} {ap:.0f}–{hp:.0f} {h}"
            except (ValueError, IndexError):
                return f"{r['away']} at {r['home']}"
        cards(open_, lambda r: r["side"], sub, edge)
    else:
        quiet("No game plays open", "Picks log when the day's lines post, usually by late morning.")

    section("Record")
    stats = core.summarize(rows, source="live", model="nba_v1")
    table(["Group", "Picks", "W-L", "Units", "ROI", "CLV", "Open"],
          [[s["group"].strip()] + stat_cells(s, lambda s: "—" if s["clv_pts"] is None else f"{s['clv_pts']:+.2f}")
           for s in stats], minor={1, 2, 3, 4})
    mk = core.by_market(rows, source="live", model="nba_v1", recommended_only=True)
    table(["Strong + Lean", "Picks", "W-L", "Units", "ROI", "CLV", "Open"],
          [[{"SPREAD": "Spread", "TOTAL": "Total", "ML": "Moneyline"}[s["market"]]] +
           stat_cells(s, lambda s: "—" if s["clv_pts"] is None and s["clv_ml"] is None
                      else f"{(s['clv_pts'] if s['clv_pts'] is not None else s['clv_ml'] * 100):+.2f}")
           for s in mk])
    st.markdown("<div class='fine'>Spread/total CLV is points beaten vs the closing line; moneyline CLV is win-% "
                "points. 1 unit flat per pick at the posted price.</div>", unsafe_allow_html=True)

    done = sorted([r for r in live if r.get("result")], key=lambda r: r.get("kickoff") or "", reverse=True)[:40]
    if done:
        section("Recently graded")
        table(["Pick", "Final", "Tier", "Result", "Units"],
              [[r["side"], f"{r['away'].split()[-1]} {r.get('away_pts', '')}–{r.get('home_pts', '')} {r['home'].split()[-1]}",
                r["tier"].replace("_", " ").title(), r["result"],
                (f"{float(r['units']):+.2f}", tone(_f(r["units"])))] for r in done])


def props_view(rows: list[dict]) -> None:
    open_ = sorted([r for r in rows if not r.get("result") and r.get("tier") in REC and upcoming(r.get("kickoff"))],
                   key=lambda r: (r.get("kickoff") or "", -float(r.get("edge_prob") or 0)))
    section("Today's prop plays", f"{len(open_)} open")
    if open_:
        cards(open_[:40], lambda r: r["side"],
              lambda r: f"{r['away']} at {r['home']} · proj {r['proj']} in {r['proj_min']} min",
              lambda r: f"us {float(r['model_prob']) * 100:.0f}% vs Pinnacle {float(r['market_prob']) * 100:.0f}%")
        st.markdown("<div class='fine'><b>Bet at</b> is Pinnacle's fair price plus normal book juice: take it or "
                    "better on Betr, Rebet or any book. The model doesn't see teammates' injuries, which can swing "
                    "a player's minutes and usage.</div>", unsafe_allow_html=True)
    else:
        quiet("No prop plays open", "Props are priced every hour from Pinnacle's board once players have 3+ games.")

    section("Record")
    body = []
    for label, tiers in (("Strong + Lean", REC), ("Strong", ("STRONG",)), ("Lean", ("LEAN",)),
                         ("Tracked (no edge)", ("NO_EDGE",))):
        s = props.stats(rows, tiers)
        body.append([label] + stat_cells(s, lambda s: "—" if s["clv"] is None else f"{s['clv'] * 100:+.1f}"))
    table(["Group", "Picks", "W-L", "Units", "ROI", "CLV", "Open"], body, minor={1, 2, 3})
    by_stat = {}
    for r in rows:
        if r.get("tier") in REC:
            by_stat.setdefault(r["stat"], []).append(r)
    if by_stat:
        table(["By stat (S+L)", "Picks", "W-L", "Units", "ROI", "CLV", "Open"],
              [[k] + stat_cells(props.stats(v), lambda s: "—" if s["clv"] is None else f"{s['clv'] * 100:+.1f}")
               for k, v in sorted(by_stat.items())])
    st.markdown("<div class='fine'>CLV = win-% points gained vs Pinnacle's closing price (moved to your line when "
                "Pinnacle changes the number). Positive CLV is the earliest sign of a real edge. DNPs are voided.</div>",
                unsafe_allow_html=True)

    done = sorted([r for r in rows if r.get("result") in ("W", "L", "P")], key=lambda r: r.get("kickoff") or "",
                  reverse=True)[:40]
    if done:
        section("Recently graded")
        table(["Prop", "Proj", "Actual", "Result", "Units"],
              [[r["side"], r["proj"], r.get("actual", ""), r["result"],
                (f"{float(r['units']):+.2f}", tone(_f(r["units"])))] for r in done])


def ratings_view(status: dict) -> None:
    section("Power ratings", f"home court {status.get('home_court', '—')} pts")
    rs = status.get("ratings") or []
    if not rs:
        quiet("No ratings yet", "They appear after the first run.")
        return
    table(["Team", "Net", "Offense", "Defense"],
          [[r["team"], (f"{r['net']:+.1f}", tone(r["net"])), f"{r['off']:+.1f}",
            (f"{r['def']:+.1f}", tone(-r["def"]))] for r in rs])
    st.markdown("<div class='fine'>Points per game vs an average team on a neutral floor. Defense below zero means "
                "it allows fewer points than average (good). Built from this season's finals plus last season's "
                "final weeks at reduced weight; recent games count most.</div>", unsafe_allow_html=True)


def render() -> None:
    st.set_page_config(page_title="NBA Game Model · SharpIQ", page_icon="🏀", layout="centered",
                       initial_sidebar_state="collapsed")
    sys.modules.pop("odds_calc", None)
    import odds_calc  # noqa: E402
    odds_calc.render()
    sys.modules.pop("ui_theme", None)
    import ui_theme  # noqa: E402
    ui_theme.apply()
    st.markdown("<style>.st-key-oc_fab{bottom:calc(4.4rem + env(safe-area-inset-bottom)) !important}"
                ".st-key-oc_fab button{width:2.7rem !important;height:2.7rem !important;opacity:.92}</style>",
                unsafe_allow_html=True)

    try:
        status = json.loads(core.STATUS_PATH.read_text())
    except Exception:
        status = {}
    games = core.load_ledger()
    prows = props.load()

    st.page_link("app.py", label="SharpIQ", icon=":material/arrow_back:")
    st.markdown("<div class='pe-mark'>NBA <i>Model</i></div>", unsafe_allow_html=True)
    ran = ""
    if status.get("at"):
        try:
            ran = datetime.fromisoformat(status["at"]).astimezone(ET).strftime("%a %-I:%M %p")
        except ValueError:
            pass
    st.markdown(f"<div class='pe-sub'>Paper lane · games + player props · last run {esc(ran or '—')} ET</div>",
                unsafe_allow_html=True)

    g = core.summarize(games, source="live", model="nba_v1")[0]
    p = props.stats(prows, REC)
    gopen = sum(1 for r in games if not r.get("result") and r.get("tier") in REC and upcoming(r.get("kickoff")))
    popen = sum(1 for r in prows if not r.get("result") and r.get("tier") in REC and upcoming(r.get("kickoff")))
    st.markdown("<div class='kpis'>"
                + kpi("Games S+L", f"{g['w']}-{g['l']}", f"{pct(g['roi'])} ROI · {g['units']:+.2f}u", tone(g["roi"]))
                + kpi("Props S+L", f"{p['w']}-{p['l']}", f"{pct(p['roi'])} ROI · {p['units']:+.2f}u", tone(p["roi"]))
                + kpi("Open today", str(gopen + popen), f"{gopen} games · {popen} props")
                + kpi("Beat the close", pct(p["beat_close"], signed=False) if p["beat_close"] is not None
                      else pct(g["beat_close"], signed=False), "props" if p["beat_close"] is not None else "games")
                + "</div>", unsafe_allow_html=True)

    view = st.segmented_control("View", ["Props", "Games", "Ratings"], default="Props", key="nba_view",
                                label_visibility="collapsed", width="stretch") or "Props"
    if view == "Props":
        props_view(prows)
    elif view == "Games":
        games_view(games)
    else:
        ratings_view(status)
    st.markdown("<div class='fine'>📝 Paper only. Strong + Lean are the recommendations; everything else is tracked "
                "so the model is tested honestly. Under ~200 settled picks, trust CLV over W-L.</div>",
                unsafe_allow_html=True)
