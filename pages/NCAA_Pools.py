"""NCAA Pools (Nov 1, 2026): this week's Clay's / Chappy's card with the key picks up top, and every
past week graded. The model is CPM v1 (ncaa_pool/model.py); the cards come from ncaa_pool/weekly.py.
Paper only."""
from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

import data_sync  # noqa: E402  Nov 4, 2026: pull fresh CSVs; Streamlit Cloud misses bot commits
data_sync.sync()

for _m in [k for k in sys.modules if k == "ncaa_pool" or k.startswith("ncaa_pool.")]:
    del sys.modules[_m]   # Streamlit Cloud keeps old modules in memory across code updates

import ui_theme  # noqa: E402
from ncaa_pool import weekly as wk  # noqa: E402
from ncaa_pool.run_card import POOLS  # noqa: E402

st.set_page_config(page_title="NCAA Pools · SharpIQ", page_icon="🏈", layout="centered",
                   initial_sidebar_state="collapsed")
ui_theme.apply()
st.markdown("""<style>
.np-card { border:1px solid var(--line); background:var(--panel); border-radius:16px; padding:.25rem .9rem; margin:.4rem 0 .8rem; }
.np-card.keys { border-color:rgba(245,184,0,.45); background:linear-gradient(180deg, rgba(245,184,0,.07), var(--panel) 70%); }
.np-row { display:flex; align-items:center; gap:.7rem; padding:.7rem 0; border-top:1px solid var(--line); }
.np-row:first-child { border-top:none; }
.np-rank { width:1.6rem; text-align:center; font-weight:800; color:var(--muted); font-variant-numeric:tabular-nums; }
.keys .np-rank { color:var(--gold); }
.np-main { flex:1; min-width:0; }
.np-pick { font-weight:700; font-size:1rem; }
.np-sub { color:var(--muted); font-size:.78rem; margin-top:.1rem; }
.np-right { text-align:right; font-variant-numeric:tabular-nums; }
.np-right b { font-size:1.05rem; }
.np-right small { display:block; color:var(--muted); font-size:.7rem; }
.np-key { font-size:.62rem; font-weight:800; letter-spacing:.06em; color:#111; background:var(--gold);
          border-radius:999px; padding:.1rem .45rem; margin-left:.4rem; vertical-align:middle; }
.np-held { font-size:.62rem; font-weight:800; letter-spacing:.04em; color:var(--text); background:var(--deep);
           border:1px solid var(--line, rgba(127,127,127,.3)); border-radius:999px; padding:.12rem .45rem; margin-left:.4rem; }
.np-res { font-size:.66rem; font-weight:800; border-radius:999px; padding:.12rem .5rem; margin-left:.4rem; }
.np-res.W { color:var(--up); background:rgba(34,197,94,.1); } .np-res.L { color:var(--down); background:rgba(240,82,82,.1); }
.np-res.P { color:var(--muted); background:var(--deep); }
[class*="st-key-npcard_"] { border:1px solid var(--line); background:var(--panel); border-radius:16px;
  padding:.1rem .6rem .1rem .9rem; margin:.4rem 0 .8rem; gap:0 !important; }
[class*="st-key-npcard_keys"] { border-color:rgba(245,184,0,.45);
  background:linear-gradient(180deg, rgba(245,184,0,.07), var(--panel) 70%); }
[class*="st-key-npcard_keys"] .np-rank { color:var(--gold); }
[class*="st-key-nprow_"] { border-top:1px solid var(--line); gap:.4rem !important; }
[class*="st-key-nprow_first_"] { border-top:none; }
[class*="st-key-nprow_"] .np-row { border-top:none; }
[class*="st-key-nprow_"] .stButton button, [class*="st-key-nprow_"] [data-testid="stPopover"] button {
  min-height:2.2rem; padding:.2rem .55rem; }
.np-lockdim { color:var(--muted); font-size:.95rem; padding:0 .55rem; }
.np-kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:.5rem; margin:.3rem 0 .6rem; }
.np-kpi { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:.6rem .7rem; }
.np-kpi .l { color:var(--muted); font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }
.np-kpi .v { font-size:1.25rem; font-weight:800; font-variant-numeric:tabular-nums; margin-top:.15rem; }
.np-note { border:1px dashed var(--line); border-radius:14px; padding:.7rem .9rem; color:var(--soft); font-size:.85rem; margin:.4rem 0 .8rem; }
.np-wk { display:flex; justify-content:space-between; align-items:baseline; padding:.65rem 0; border-top:1px solid var(--line); }
.np-wk:first-child { border-top:none; }
.np-wk .t { font-weight:700; } .np-wk .s { color:var(--muted); font-size:.76rem; }
.np-wk .v { font-weight:800; font-variant-numeric:tabular-nums; text-align:right; }
</style>""", unsafe_allow_html=True)

ET = ZoneInfo("America/New_York")
NOW = datetime.now(timezone.utc)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def tone(x: float) -> str:
    return "up" if x > 0 else "down" if x < 0 else ""


def money(x: float) -> str:
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.2f}"


def kick(v) -> str:
    d = wk._dt(v)
    return d.astimezone(ET).strftime("%a %-I:%M %p") if d else ""


@st.cache_data(ttl=300, show_spinner=False)
def load_feed():
    return wk.game_feed()


UNLOCK_CODE = "10"                            # Nov 6, 2026: Cole types this to unlock a locked pick


def pick_rows(rows: list[dict], show_stake: bool = True) -> str:
    return "".join(row_html(r, show_stake) for r in rows)


def row_html(r: dict, show_stake: bool = True) -> str:
    out = []
    for r in [r]:
        key = r.get("key") == "Y"
        res = r.get("result") or ""
        prob = float(r.get("prob") or 0)
        val = r.get("value_pts")
        sub = []
        if r.get("away") and r.get("home"):
            sub.append(f"{r['away']} at {r['home']}")
        if r.get("kickoff"):
            sub.append(kick(r["kickoff"]))
        try:
            mk = float(r.get("market"))
            sub.append(f"market now {mk:+g}".replace("+0", "PK"))
        except (TypeError, ValueError):
            pass
        if res and r.get("home_pts") not in (None, ""):
            sub.append(f"final {r['away']} {float(r['away_pts']):g} – {r['home']} {float(r['home_pts']):g}")
        elif r.get("final"):
            sub.append(f"final {r['final']}")
        right = f"<b>{prob * 100:.0f}%</b><small>chance</small>" if prob else ""
        try:
            v = float(val)
            right += f"<small>{v:+.1f} pts value</small>"
        except (TypeError, ValueError):
            pass
        if show_stake and r.get("rank"):
            right += f"<small>${wk.stake(int(r['rank'])):.0f}</small>"
        res_html = f"<span class='np-res {res}'>{res}</span>" if res else ""
        key_html = "<span class='np-key'>KEY ×2</span>" if key else ""
        if r.get("held"):                         # Nov 6, 2026: locked in its spot
            key_html += "<span class='np-held'>🔒 IN</span>"
        out.append(
            f"<div class='np-row'><div class='np-rank'>{esc(r.get('rank'))}</div><div class='np-main'>"
            f"<div class='np-pick'>{esc(r['pick'])}{key_html}{res_html}</div>"
            f"<div class='np-sub'>{esc(' · '.join(sub))}</div></div><div class='np-right'>{right}</div></div>")
    return "".join(out)


def show_tiebreaker(pool: str, season: int, week: int, slate: list[dict]) -> None:
    """Nov 5, 2026: the model's tiebreaker guess (market total of the tiebreaker game, rounded)."""
    tb = wk.load_tiebreaker(pool, season, week) or (wk.tiebreaker(slate, games, NOW) if slate else None)
    if not tb:
        return
    src = {"pinnacle": "Pinnacle", "espn": "ESPN", "pool": "pool"}.get(tb.get("src"), tb.get("src") or "")
    st.markdown(f"<div class='np-note'>🎯 <b>Tiebreaker:</b> {esc(tb['away'])} @ {esc(tb['home'])} — enter "
                f"<b>{tb['guess']}</b> <span style='color:var(--muted)'>({src} total {tb['total']:g}"
                f"{' · locked' if tb.get('frozen') else ''})</span></div>", unsafe_allow_html=True)


def lockable_rows(rows: list[dict], section: str, ctl: dict) -> None:
    """Nov 6, 2026: every pick on the card carries its own lock. Tap 🔒 to lock it in its spot;
    a locked pick only unlocks with the code."""
    pool, season, week, held = ctl["pool"], ctl["season"], ctl["week"], ctl["held"]
    held_picks = {h["pick"] for h in held}
    with st.container(key=f"npcard_{section}_{pool}"):
        for i, r in enumerate(rows):
            is_held = r["pick"] in held_picks
            started = bool(r.get("frozen")) and not is_held
            slug = re.sub(r"\W+", "_", r["pick"])
            with st.container(horizontal=True, vertical_alignment="center", wrap=False,
                              key=f"nprow_{'first_' if i == 0 else ''}{section}_{pool}_{slug}"):
                st.markdown(row_html(r), unsafe_allow_html=True, width="stretch")
                if started:
                    st.markdown("<span class='np-lockdim' title='Game started'>⏱</span>", unsafe_allow_html=True,
                                width="content")
                elif is_held:
                    with st.popover("🔒", key=f"unpop_{pool}_{slug}", help="Locked — enter the code to unlock"):
                        code = st.text_input("Code to unlock", type="password", key=f"code_{pool}_{slug}")
                        if st.button("Unlock", key=f"unlock1_{pool}_{slug}", use_container_width=True):
                            if code.strip() != UNLOCK_CODE:
                                st.error("Wrong code.")
                            else:
                                _save_hold(pool, season, week, r, held, release=True)
                else:
                    if st.button("Lock", key=f"hold_{pool}_{slug}", help="Lock this pick in its spot"):
                        _save_hold(pool, season, week, r, held, release=False)


def _save_hold(pool: str, season: int, week: int, r: dict, held: list[dict], release: bool) -> None:
    try:
        if release:
            new = wk.release_pick(pool, season, week, r["pick"], held)
            msg = f"Unlocked {r['pick']}. It can move again."
        else:
            new = wk.hold_pick(pool, season, week, r, held)
            msg = f"Locked {r['pick']} at #{r.get('rank')}."
        st.session_state[f"held_{pool}"] = new
        st.session_state[f"msg_{pool}"] = ("ok", msg)
    except Exception as exc:
        st.session_state[f"msg_{pool}"] = ("warn", f"Couldn't save that lock: {exc}")
    st.rerun()


def show_card(pool: str, rows: list[dict], ctl: dict | None = None) -> None:
    keys = [r for r in rows if r.get("key") == "Y"]
    rest = [r for r in rows if r.get("key") != "Y"]
    exp, mx = wk.expected_points(rows) if rows else (0, 0)
    w, l, p = wk.record(rows)
    graded = w + l + p
    pts = wk.points(rows)
    kpis = [("Expected", f"{exp:.1f}<span style='color:var(--muted);font-size:.8rem'> / {mx}</span>"),
            ("Record", f"{w}-{l}{f'-{p}' if p else ''}" if graded else "—"),
            ("Points", f"{pts}" if graded else "—")]
    if any(r.get("src") for r in rows):         # Nov 4, 2026: which market line the card read
        n_pin = sum(r.get("src") == "pinnacle" for r in rows)
        kpis.append(("Pinnacle line", f"{n_pin}<span style='color:var(--muted);font-size:.8rem'> / {len(rows)}</span>"))
    st.markdown("<div class='np-kpis'>" + "".join(f"<div class='np-kpi'><div class='l'>{k}</div><div class='v'>{v}</div></div>"
                                                 for k, v in kpis) + "</div>", unsafe_allow_html=True)
    if keys:
        st.markdown(f"<div class='section'><span>Key picks</span><span class='n'>double points</span></div>",
                    unsafe_allow_html=True)
        if ctl:
            lockable_rows(keys, "keys", ctl)
        else:
            st.markdown(f"<div class='np-card keys'>{pick_rows(keys)}</div>", unsafe_allow_html=True)
    if rest:
        st.markdown(f"<div class='section'><span>The rest of the card</span><span class='n'>{len(rest)}</span></div>",
                    unsafe_allow_html=True)
        if ctl:
            lockable_rows(rest, "rest", ctl)
        else:
            st.markdown(f"<div class='np-card'>{pick_rows(rest)}</div>", unsafe_allow_html=True)


def paste_form(pool: str, season: int, week: int) -> None:
    label = "Clay's" if pool == "clay" else "Chappy's"
    with st.expander(f"Enter {label} lines for week {week}", icon=":material/edit_note:"):
        st.caption("One game per line, the HOME team's spread: `Ohio at Akron -3.5` or `Ohio @ Akron +7`. "
                   "CSV `away,home,home_spread` works too. End the tiebreaker game's line with `tiebreaker`. "
                   "Or just send Claude a screenshot of the pool.")
        text = st.text_area("Lines", key=f"paste_{pool}", height=180, label_visibility="collapsed",
                            placeholder="Ohio at Akron -3.5\nMissouri at Texas A&M -6.5\n…")
        if st.button("Save lines & build card", key=f"save_{pool}", type="primary", use_container_width=True):
            rows, bad = wk.parse_slate(text)
            if bad:
                st.error("Couldn't read: " + "; ".join(bad[:5]))
            elif not rows:
                st.error("No games found.")
            else:
                st.session_state[f"slate_{pool}"] = rows
                try:
                    wk.save_slate(pool, season, week, rows)
                    st.session_state[f"msg_{pool}"] = ("ok", f"Saved {len(rows)} games. The card refreshes hourly "
                                                       "with the newest market until Saturday noon, then locks.")
                except Exception as exc:
                    st.session_state[f"msg_{pool}"] = ("warn", f"Built the card, but couldn't save the lines: {exc}")
                st.rerun()


# ------------------------------------------------------------------ page
c1, c2 = st.columns([5, 1], vertical_alignment="center")
with c1:
    st.markdown("<div class='pe-mark'>NCAA Pools</div>", unsafe_allow_html=True)
with c2:
    st.page_link("app.py", label="Home", icon=":material/home:")

feed = load_feed()
cw = wk.current_week(feed, NOW)
season, week = cw if cw else (NOW.year, 0)
games = feed.get(cw, {}) if cw else {}
lock = wk.lock_time(games)
locked = bool(lock and NOW >= lock)
if cw:
    st.markdown(f"<div class='pe-sub'>Week {week} · picks {'locked' if locked else 'lock'} "
                f"{lock.astimezone(ET).strftime('%a %b %-d, %-I %p ET') if lock else ''} · CPM v1 model</div>",
                unsafe_allow_html=True)

tab_now, tab_past = st.tabs(["This week", "Past weeks"])

with tab_now:
    if not cw:
        st.markdown("<div class='np-note'>No upcoming college football games in the feed.</div>", unsafe_allow_html=True)
    for pool in ("clay", "chappy"):
        n, k = POOLS[pool]
        name = "Clay's pool" if pool == "clay" else "Chappy's pool"
        st.markdown(f"<div class='section'><span>{name}</span><span class='n'>{n} picks · {k} key"
                    f"{'s' if k > 1 else ''}</span></div>", unsafe_allow_html=True)
        if not cw:
            continue
        saved = wk._csv(wk.card_path(pool, season, week))
        pasted = st.session_state.get(f"slate_{pool}")
        slate = wk._csv(wk.slate_path(pool, season, week))
        held = st.session_state.get(f"held_{pool}")
        if held is None:
            held = wk.load_held(pool, season, week)
        if pasted:
            built = wk.build(pool, pasted, games, now=NOW,
                             pinned=wk.live_pinned(pool, season, week, saved, pasted, NOW, held))
            rows = built["rows"]
            wk.grade_rows(rows, games)
            note = "Built just now from the lines you entered and the newest market read."
        elif saved and held and slate and not locked:
            # Nov 6, 2026: show held picks right away instead of waiting for the hourly refresh
            built = wk.build(pool, slate, games, now=NOW,
                             pinned=wk.live_pinned(pool, season, week, saved, slate, NOW, held))
            rows = built["rows"]
            wk.grade_rows(rows, games)
            note = ("Your locked picks stay put; the open spots refresh hourly with the newest market until "
                    "Saturday noon.")
        elif saved:
            rows = saved
            note = ("Locked at Saturday noon." if locked else
                    "Refreshes hourly with the newest market until Saturday noon; picks freeze as their games kick off.")
            built = None
        elif slate:
            built = wk.build(pool, slate, games, now=NOW, pinned=wk.merge_held([], held))
            rows = built["rows"]
            wk.grade_rows(rows, games)
            note = "Built live from the saved lines and the newest market read."
        elif pool == "clay":
            built = wk.build(pool, wk.preview_slate(games), games, now=NOW, preview=True)
            rows = built["rows"]
            note = ("<b>Early look.</b> Clay's lines aren't in yet, so this uses each game's early-week market line "
                    "as a stand-in and covers every FBS game, not just Clay's slate. Enter Clay's lines below for the "
                    "real card.")
        else:
            rows, built = [], None
            note = ("Enter Chappy's lines to get the 6-pick card. Chappy's lines move with the market, so the edge "
                    "comes from catching a line before it moves.")
        preview = bool(built and built.get("preview"))
        msg = st.session_state.pop(f"msg_{pool}", None)
        if msg:
            (st.success if msg[0] == "ok" else st.warning)(msg[1])

        # ---- "Lock my picks": once you've submitted on Splash, this is your entry (Nov 1, 2026)
        mine = st.session_state.get(f"mine_{pool}")
        if mine is None:
            mine = wk._csv(wk.mine_path(pool, season, week))
        if mine:
            wk.grade_rows(mine, games)
            at = kick(mine[0].get("locked_at")) or "earlier"
            st.markdown(f"<div class='np-note'>🔒 <b>Your picks — locked {esc(at)}.</b> This is your entry; it won't "
                        "change and it's what gets graded under Past weeks.</div>", unsafe_allow_html=True)
            show_card(pool, mine)
            show_tiebreaker(pool, season, week, slate)
            if rows and not preview and not locked:
                d = wk.diff(mine, rows)
                if d["dropped"] or d["keys_changed"]:
                    bits = []
                    if d["dropped"]:
                        bits.append(f"now prefers {', '.join(d['added'])} over {', '.join(d['dropped'])}")
                    if d["keys_changed"]:
                        bits.append(f"keys now {', '.join(d['model_keys'])}")
                    st.info("Since you locked, the model " + "; ".join(bits) + ". If Splash still lets you edit, "
                            "update there, then Unlock and Lock again.")
            if rows:
                with st.expander("Model's current card", icon=":material/insights:"):
                    st.markdown(f"<div class='np-note'>{note}</div>", unsafe_allow_html=True)
                    show_card(pool, rows)
            if not locked and st.button("Unlock (I changed my picks)", key=f"unlock_{pool}", use_container_width=True):
                try:
                    wk.unlock_picks(pool, season, week)
                    st.session_state[f"mine_{pool}"] = []
                    st.session_state[f"msg_{pool}"] = ("ok", "Unlocked. Lock again once your entry is final.")
                except Exception as exc:
                    st.session_state[f"msg_{pool}"] = ("warn", f"Couldn't unlock: {exc}")
                st.rerun()
            continue

        st.markdown(f"<div class='np-note'>{note}</div>", unsafe_allow_html=True)
        if rows:
            ctl = None if preview or locked else {"pool": pool, "season": season, "week": week, "held": held}
            if ctl:
                st.caption("Tap Lock on a pick once it's in the pool — it locks in its spot and won't move. "
                           "Tap 🔒 and enter your code to unlock.")
            show_card(pool, rows, ctl)
            if not preview:
                show_tiebreaker(pool, season, week, pasted or slate)
            if preview:
                st.caption("Lock becomes available once Clay's real lines are in (this is an early look).")
            elif st.button("🔒 Lock my picks — I submitted this card", key=f"lock_{pool}", type="primary",
                           use_container_width=True):
                try:
                    st.session_state[f"mine_{pool}"] = wk.lock_picks(pool, season, week, rows)
                    st.session_state[f"msg_{pool}"] = ("ok", "Locked. These are your picks for the week.")
                except Exception as exc:
                    st.session_state[f"msg_{pool}"] = ("warn", f"Couldn't save your locked picks: {exc}")
                st.rerun()
        if not locked:
            paste_form(pool, season, week)
    st.markdown("<div class='fine'>Chance = the model's chance the pick covers Clay's frozen line, using the current "
                "market as the fair line (key numbers 3 and 7 included). Value = points of line you're getting vs the "
                "market now. Stakes: $10 on ranks 1-3, $5 on 4-12. Paper only.</div>", unsafe_allow_html=True)

with tab_past:
    # Nov 4, 2026: four paper cards a week, same model, different market line / freeze time.
    vr = wk.variant_results()
    if vr["weeks"]:
        st.markdown("<div class='section'><span>Line source, lock time &amp; crowd-fade test</span>"
                    "<span class='n'>paper</span></div>", unsafe_allow_html=True)
        for pool, tot in vr["totals"].items():
            pname = "Clay" if pool == "clay" else "Chappy"
            tiles = []
            for v in wk.VARIANT_SPECS:
                t = tot.get(v)
                if not t:
                    continue
                rec = f"{t['w']}-{t['l']}" + (f"-{t['p']}" if t["p"] else "")
                tiles.append((f"{pname} · {wk.VARIANT_NAMES[v]}", f"{t['points']} pts",
                              f"{rec} · best {t['best']}/{t['weeks']} wk · {money(t['money'])}"))
            st.markdown("<div class='np-kpis'>" + "".join(
                f"<div class='np-kpi'><div class='l'>{esc(s)}</div><div class='v'>{v}</div>"
                f"<div class='np-sub'>{esc(sub)}</div></div>" for s, v, sub in tiles) + "</div>",
                unsafe_allow_html=True)
        for wkrow in vr["weeks"][:6]:
            pname = "Clay" if wkrow["pool"] == "clay" else "Chappy"
            parts = []
            for v in wk.VARIANT_SPECS:
                x = wkrow["variants"].get(v)
                if x:
                    w, l, pu = x["record"]
                    state = "" if x["done"] else (" · live" if not x["frozen"] else " · grading")
                    parts.append(f"<b>{esc(wk.VARIANT_NAMES[v])}</b> {x['points']} pts ({w}-{l}{f'-{pu}' if pu else ''}){state}")
            st.markdown(f"<div class='np-note'>Week {wkrow['week']} {pname}: " + " · ".join(parts) + "</div>",
                        unsafe_allow_html=True)
        st.markdown("<div class='fine'>Same model, five ways: the market line it reads (ESPN or Pinnacle), when "
                    "the card is frozen (Friday 8pm ET, your usual lock, or the Saturday noon lock), and a crowd-fade "
                    "version that swaps the coin-flip filler (never the keys) to the less-public side. Totals count "
                    "fully graded weeks only; <b>best</b> = weeks that version scored the most. The main "
                    "card above is Pinnacle · Sat lock.</div>", unsafe_allow_html=True)

    hist = wk.history()
    if not hist:
        st.markdown("<div class='np-note'>No past weeks yet.</div>", unsafe_allow_html=True)
    else:
        by_src: dict[str, list] = {}
        for e in hist:
            by_src.setdefault(e["source"], []).append(e)
        tiles = []
        for src, es in by_src.items():
            allr = [r for e in es for r in e["rows"]]
            w, l, p = wk.record(allr)
            tiles.append((src, f"{w}-{l}{f'-{p}' if p else ''}",
                          f"{sum(wk.points(e['rows']) for e in es)} pts · {len(es)} wk"))
        st.markdown("<div class='np-kpis'>" + "".join(
            f"<div class='np-kpi'><div class='l'>{esc(s)}</div><div class='v'>{v}</div>"
            f"<div class='np-sub'>{esc(sub)}</div></div>" for s, v, sub in tiles) + "</div>", unsafe_allow_html=True)
        weeks = sorted({(e["season"], e["week"]) for e in hist}, reverse=True)
        for sw in weeks:
            es = [e for e in hist if (e["season"], e["week"]) == sw]
            st.markdown(f"<div class='section'><span>Week {sw[1]}</span></div>", unsafe_allow_html=True)
            for e in es:
                w, l, p = wk.record(e["rows"])
                pl = wk.money(e["rows"])
                pool = "Clay" if e["pool"] == "clay" else "Chappy"
                title = (f"{e['source']} ({pool}) — {w}-{l}{f'-{p}' if p else ''} · {wk.points(e['rows'])} pts · "
                         f"{money(pl)}")
                with st.expander(title):
                    rows = sorted(e["rows"], key=lambda r: int(r.get("rank") or 99))
                    st.markdown(f"<div class='np-card'>{pick_rows(rows)}</div>", unsafe_allow_html=True)
        st.markdown("<div class='fine'><b>CPM v1 replay</b> = what this model would have picked on Clay's real slates "
                    "(weeks 1-9, spreads only like the live card; week 5 from the full Splash slate you sent, with ESPN's market lines). <b>Your picks</b> = your entries: weeks 5-7 from your Splash screenshots; weeks 2, 4 and 8 from recovered "
                    "cards whose grades match the records you reported (week 8's keys unconfirmed); weeks 1, 3 and 9 from "
                    "the old v51 log (keys assumed to be ranks 1-3 when the log didn't say). "
                    "<b>CPM card</b> = the weekly cards saved from week 10 on. $ = your $10/$5 staking at -110. "
                    "Points: a win is 1, a key win is 2.</div>", unsafe_allow_html=True)
