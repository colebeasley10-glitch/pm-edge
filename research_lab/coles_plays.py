"""Cole's Plays (PAPER lane, Nov 10, 2026): pick'em apps vs Pinnacle, the way Hunter finds his plays.

Hunter builds his cards with OddsJam's fantasy/pick'em tool: it compares the player lines on pick'em
apps against the sharp books' no-vig price and flags the sides the app has mispriced. This lane does
the same thing for free:

  * Pinnacle's player props (the Prop Board) give the fair probability of each Over/Under.
  * Each pick'em app gives its line and what one pick pays:
      - Sleeper: a payout multiplier on each side, which works like decimal odds.
      - Underdog: the LOWEST of the prices it shows for a pick (decimal price, its fantasy odds, and
        its payout multiplier times the 3x 2-pick payout split per pick), so an edge holds at the worst.
      - PrizePicks: standard lines only (no goblins/demons), each pick worth the 2-pick Power
        payout (3x) split per pick = sqrt(3) = 1.73 decimal (about -137).
  * Props Pinnacle posts with a limit under MIN_PIN_LIMIT are skipped: its price there is barely tested.
  * A side is a play when the app pays at least MIN_EV over Pinnacle's fair price. A different line
    only counts when it's a better number than Pinnacle's (Under higher / Over lower): Pinnacle's
    probability at its own line is then a floor for ours.

Every sport Pinnacle posts props for (NBA, NHL, NFL, college football, college basketball). One play
per Pinnacle prop and side, priced at the best app; the other apps that also cleared are listed.
Flat $10 paper per pick, graded from ESPN box scores; CLV = Pinnacle's last pre-game fair price for the
same line minus the fair price when the play was logged. Paper only, no orders. Since Nov 12, 2026 this is
the ONLY lane that sends phone alerts: every new play (pick'em or best-of) goes through alert_gate as
source "coles_plays", one push each, at most once (the play is only alerted in the round it's logged).

Pick'em apps don't take single bets: in real life these get paired into 2+ pick entries. The paper
record grades each pick on its own at its per-pick price, which is how the entry's value adds up.

Best of SharpIQ (Nov 10, 2026): Cole's Plays is also the elite list. Each round it takes the open plays
of the TOP_LANES best lanes on the scorecard (the same lanes the Bet Queue trusts: 30+ settled bets,
beating the close, positive sample-shrunk ROI, no bad verdict), keeps only plays with BEST_MIN_EV+ edge
over their fair price, and logs the best at most MAX_BEST_PER_DAY a day. They settle from their source
lane. As lanes prove or fail, what Cole's Plays borrows changes on its own. `via` says where each play
came from (an app or a lane), so the scorecard shows which part is making money.

status.json keeps each app's last fetch (counts, errors and a small sample) so a broken feed shows up.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt

DIR = "COLES_PLAYS"
UNIT = 10.0
MIN_EV = 0.02            # same 2% cushion as the Prop Board's "worst price"
MAX_DECIMAL = 3.0        # long shots are outside what Cole bets
MIN_LEAD = timedelta(minutes=10)
MAX_AHEAD = timedelta(hours=36)
PRIZEPICKS_PICK = math.sqrt(3.0)   # 2-pick Power pays 3x
UNDERDOG_PICK = math.sqrt(3.0)     # Underdog's standard 2-pick also pays 3x before multipliers
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept": "application/json"}
SLEEPER_SPORTS = ("nfl", "nba", "nhl", "cbb", "cfb")
TOP_LANES = 3            # best-of: only the top lanes on the scorecard
BEST_MIN_EV = 0.03       # ...and only their plays with 3%+ edge
MAX_BEST_PER_DAY = 8
MIN_PIN_LIMIT = 100      # Nov 10, 2026: skip props Pinnacle barely takes action on (same floor as the Prop Board)
# Nov 10, 2026: Underdog answered v5 with 426 "Upgrade Required" from GitHub; try newer versions first and
# PrizePicks' partner host when the app host refuses datacenter IPs (403). status.json records which worked.
UNDERDOG_URLS = ("https://api.underdogfantasy.com/beta/v7/over_under_lines",
                 "https://api.underdogfantasy.com/beta/v6/over_under_lines",
                 "https://api.underdogfantasy.com/v1/over_under_lines",
                 "https://api.underdogfantasy.com/beta/v5/over_under_lines")
PRIZEPICKS_URLS = ("https://partner-api.prizepicks.com/projections", "https://api.prizepicks.com/projections")


def _first_ok(session, urls, info, **kw):
    """GET the first URL that answers 200; remembers every failure in info['tried']."""
    tried, last = [], None
    for u in urls:
        try:
            r = session.get(u, **kw)
            r.raise_for_status()
            info["url"] = u
            if tried:
                info["tried"] = tried
            return r
        except Exception as exc:
            last = exc
            code = getattr(getattr(exc, "response", None), "status_code", None)
            tried.append(f"{u.split('//')[1].split('?')[0]}: {code or type(exc).__name__}")
    info["tried"] = tried
    raise last

FIELDS = ["play_id", "alert_time", "app", "league", "game", "player", "stat", "pick", "side", "line", "pin_line",
          "target_odds", "target_decimal", "fair_prob", "fair_odds", "ev", "also_at", "limit", "game_start",
          "close_prob", "clv", "actual", "status", "settled_time", "pl", "origin", "via", "source", "source_id"]


# ------------------------------------------------------------------ names and stats
def norm_name(s: str) -> str:
    from research_lab.sleeper import norm_name as _n
    return _n(s)


_SKIP = ("1h", "1q", "2h", "1st", "2nd", "first", "quarter", "half", "period", "fantasy", "combo", "longest",
         "anytime", "double", "triple", "first td", "kicking", "field goal", "extra point", "inning")
# one stat word/phrase -> canonical token
_ONE = {
    "points": "P", "point": "P", "pts": "P",
    "rebounds": "R", "rebound": "R", "rebs": "R", "reb": "R", "total rebounds": "R",
    "assists": "A", "assist": "A", "asts": "A", "ast": "A",
    "threes": "3PM", "threes made": "3PM", "3 pointers made": "3PM", "3pointers made": "3PM", "3pt made": "3PM",
    "3 pt made": "3PM", "3pm": "3PM", "three pointers made": "3PM", "3 point fg": "3PM", "3s made": "3PM",
    "3ptm": "3PM", "made threes": "3PM", "3 pointers": "3PM", "three pointers": "3PM",
    "steals": "STL", "steal": "STL", "stls": "STL", "blocks": "BLK", "blocked shots": "BLK", "blks": "BLK",
    "block": "BLK", "turnovers": "TO", "stocks": "BLK+STL",
    "goals": "G", "goal": "G", "shots on goal": "SOG", "shots": "SOG", "sog": "SOG", "saves": "SAVES",
    "goalie saves": "SAVES", "hockey points": "P", "hockey assists": "A", "hockey goals": "G", "goaltender saves": "SAVES", "hits": "HITS", "power play points": "PPP",
    "passing yards": "PASSYD", "pass yards": "PASSYD", "pass yds": "PASSYD", "passing yds": "PASSYD",
    "rushing yards": "RUSHYD", "rush yards": "RUSHYD", "rush yds": "RUSHYD", "rushing yds": "RUSHYD",
    "receiving yards": "RECYD", "rec yards": "RECYD", "rec yds": "RECYD", "receiving yds": "RECYD",
    "receptions": "REC", "rec": "REC", "catches": "REC",
    "pass attempts": "PASSATT", "passing attempts": "PASSATT", "pass att": "PASSATT",
    "pass completions": "PASSCMP", "passing completions": "PASSCMP", "completions": "PASSCMP", "pass cmp": "PASSCMP",
    "rush attempts": "RUSHATT", "rushing attempts": "RUSHATT", "carries": "RUSHATT", "rush att": "RUSHATT",
    "passing tds": "PASSTD", "pass tds": "PASSTD", "passing touchdowns": "PASSTD", "touchdown passes": "PASSTD",
    "pass td": "PASSTD", "passing td": "PASSTD", "td passes": "PASSTD",
    "interceptions": "INT", "interceptions thrown": "INT", "ints": "INT", "int": "INT",
    "touchdowns": "TD", "rush rec tds": "TD", "rushing receiving tds": "TD",
    "rush rec yds": "RUSHRECYD", "rush rec yards": "RUSHRECYD", "rushing receiving yards": "RUSHRECYD",
    "pass rush yds": "PASSRUSHYD", "passing rushing yards": "PASSRUSHYD", "pass rush yards": "PASSRUSHYD",
}
_SLEEPER = {"pts reb ast": "P+R+A", "pts reb": "P+R", "pts ast": "P+A", "reb ast": "R+A", "rec yd": "RECYD",
            "rush yd": "RUSHYD", "pass yd": "PASSYD", "pass td": "PASSTD", "rush rec yd": "RUSHRECYD",
            "pass rush yd": "PASSRUSHYD", "blocks and steals": "BLK+STL", "rush att": "RUSHATT",
            "pass att": "PASSATT", "pass cmp": "PASSCMP", "points and rebounds": "P+R",
            "points and assists": "P+A", "rebounds and assists": "R+A", "pra": "P+R+A"}


def canon_stat(stat: str) -> str | None:
    """'Pts & Rebs & Asts', 'Pts+Rebs+Asts', 'pts_reb_ast' -> 'A+P+R'. None for stats we skip."""
    s = str(stat or "").lower().replace("_", " ").replace("&", "+").replace(" and ", "+")
    s = re.sub(r"[^a-z0-9+ ]", " ", s)
    s = " ".join(s.split())
    if not s or any(w in s for w in _SKIP):
        return None
    if s in _SLEEPER:
        s = _SLEEPER[s]
        return "+".join(sorted(s.split("+")))
    joined = s.replace("+", " ")
    if " ".join(joined.split()) in _ONE:                       # 'rush rec yds' etc. as one phrase
        return _ONE[" ".join(joined.split())]
    parts = [p.strip() for p in s.split("+") if p.strip()]
    toks = []
    for p in parts:
        t = _ONE.get(p)
        if t is None:
            return None
        toks.extend(t.split("+"))
    return "+".join(sorted(toks)) if toks else None


def american(dec: float) -> str:
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


def dec_from_american(a) -> float | None:
    try:
        a = float(str(a).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if a >= 100:
        return 1 + a / 100
    if a <= -100:
        return 1 + 100 / -a
    return None


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ apps -> [{app, player, stat, line, over, under}]
def sleeper_lines(session, now: datetime, base: Path | None, info: dict) -> list[dict]:
    from research_lab import sleeper as slp
    lines = slp.fetch_lines(session)
    info["raw"] = len(lines)
    info["sample"] = lines[:2]
    names = {}
    for sport in sorted({str(ln.get("sport") or "") for ln in lines} & set(SLEEPER_SPORTS)):
        try:
            names[sport] = slp.player_names(sport, now, base, session)
        except Exception as exc:
            info.setdefault("errors", []).append(f"players {sport}: {type(exc).__name__}")
    out = []
    for p in slp.sleeper_props(lines, names):
        out.append({"app": "Sleeper", "player": p["player"], "stat": p["stat"], "line": p["line"],
                    "over": p["over_mult"], "under": p["under_mult"]})
    return out


def underdog_lines(session, now: datetime, base: Path | None, info: dict) -> list[dict]:
    r = _first_ok(session, UNDERDOG_URLS, info, headers=UA, timeout=40)
    data = r.json() or {}
    players = {p.get("id"): p for p in data.get("players") or []}
    apps = {a.get("id"): a for a in data.get("appearances") or []}
    ous = data.get("over_under_lines") or []
    info["raw"] = len(ous)
    info["sample"] = ous[:2]
    out = []
    for ln in ous:
        if str(ln.get("status") or "active") != "active":
            continue
        ou = ln.get("over_under") or {}
        st = ou.get("appearance_stat") or {}
        ap = apps.get(st.get("appearance_id")) or {}
        pl = players.get(ap.get("player_id")) or {}
        name = " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x)
        line = _f(ln.get("stat_value"))
        if not name or line is None:
            continue
        sides = {}
        for o in ln.get("options") or []:
            if str(o.get("status") or "active") != "active":
                continue
            choice = str(o.get("choice") or "").lower()
            side = {"higher": "over", "over": "over", "lower": "under", "under": "under"}.get(choice)
            if not side:
                continue
            # Nov 10, 2026: Underdog shows several prices for one pick (decimal_price, its "fantasy" odds and
            # the payout multiplier on a 3x 2-pick). Which one an entry really pays isn't certain, so take
            # the LOWEST: an edge only counts if it holds at the worst of them.
            fan = (o.get("odds") or {}).get("fantasy") or {}
            mult = _f(o.get("payout_multiplier"))
            prices = [_f(o.get("decimal_price")), dec_from_american(o.get("american_price")),
                      _f(fan.get("decimal")), dec_from_american(fan.get("american")),
                      UNDERDOG_PICK * mult if mult else None]
            prices = [p for p in prices if p and p > 1]
            dec = min(prices) if prices else None
            if dec and dec > 1:
                sides[side] = round(dec, 4)
        if sides:
            out.append({"app": "Underdog", "player": name, "stat": st.get("display_stat") or st.get("stat") or "",
                        "line": line, "over": sides.get("over"), "under": sides.get("under")})
    return out


def prizepicks_lines(session, now: datetime, base: Path | None, info: dict) -> list[dict]:
    r = _first_ok(session, PRIZEPICKS_URLS, info,
                  params={"per_page": 2000, "single_stat": "true", "game_mode": "pickem"},
                  headers={**UA, "Referer": "https://app.prizepicks.com/", "Origin": "https://app.prizepicks.com"},
                  timeout=40)
    data = r.json() or {}
    inc = {(i.get("type"), i.get("id")): i.get("attributes") or {} for i in data.get("included") or []}
    rows = data.get("data") or []
    info["raw"] = len(rows)
    info["sample"] = rows[:2]
    out = []
    for d in rows:
        a = d.get("attributes") or {}
        if str(a.get("odds_type") or "standard") != "standard" or a.get("is_promo"):
            continue
        if str(a.get("status") or "pre_game") not in ("pre_game", ""):
            continue
        rel = ((d.get("relationships") or {}).get("new_player") or {}).get("data") or {}
        pl = inc.get(("new_player", rel.get("id"))) or {}
        name = pl.get("display_name") or pl.get("name")
        line = _f(a.get("line_score"))
        if not name or pl.get("combo") or line is None:
            continue
        out.append({"app": "PrizePicks", "player": name, "stat": a.get("stat_type") or a.get("stat_display_name") or "",
                    "line": line, "over": round(PRIZEPICKS_PICK, 4), "under": round(PRIZEPICKS_PICK, 4)})
    return out


APPS = {"Sleeper": sleeper_lines, "Underdog": underdog_lines, "PrizePicks": prizepicks_lines}


def fetch_all(now: datetime, base: Path | None = None, session=requests, apps: dict | None = None) -> tuple[list[dict], dict]:
    out, status = [], {}
    for name, fn in (apps or APPS).items():
        info: dict = {"at": iso(now)}
        try:
            got = fn(session, now, base, info)
            info["props"] = len(got)
            out += got
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if "sample" in info:      # a small raw sample, so a changed feed format shows up in status.json
            s = json.dumps(info["sample"], default=str)
            info["sample"] = json.loads(s) if len(s) <= 4000 else s[:4000]
        status[name] = info
    return out, status


# ------------------------------------------------------------------ edges
def edges(app_props: list[dict], board_rows: list[dict], now: datetime, min_ev: float = MIN_EV) -> tuple[list[dict], dict]:
    """Best app price per Pinnacle prop and side that beats Pinnacle's fair by min_ev."""
    pin: dict = {}
    for r in board_rows:
        start = parse_dt(r.get("starts"))
        st = canon_stat(r.get("stat"))
        if not start or not st or not (now + MIN_LEAD <= start <= now + MAX_AHEAD):
            continue
        if (_f(r.get("limit")) or 0) < MIN_PIN_LIMIT:      # a tiny limit = a shaky fair price
            continue
        pin[(norm_name(r["player"]), st)] = r
    match = {"matched": 0, "by_app": {}, "closest": []}
    best: dict = {}
    near: list = []
    for a in app_props:
        st = canon_stat(a["stat"])
        r = pin.get((norm_name(a["player"]), st)) if st else None
        m = match["by_app"].setdefault(a["app"], {"props": 0, "matched": 0, "unmatched_stats": set()})
        m["props"] += 1
        if not r:
            if not st or not any(k[1] == st for k in pin):
                m["unmatched_stats"].add(str(a["stat"]))
            continue
        m["matched"] += 1
        match["matched"] += 1
        pl, al = float(r["line"]), float(a["line"])
        for side in ("over", "under"):
            dec = _f(a.get(side))
            if not dec or dec <= 1 or dec > MAX_DECIMAL:
                continue
            if (side == "over" and al > pl) or (side == "under" and al < pl):
                continue                       # worse number than Pinnacle's: no safe floor
            p = float(r["p_over"]) if side == "over" else 1 - float(r["p_over"])
            ev = p * dec - 1
            near.append((ev, f"{a['app']} {r['player']} {side.title()} {al:g} {r['stat']} {american(dec)} "
                             f"vs fair {american(1 / p)} ({ev * 100:+.1f}%)"))
            if ev < min_ev:
                continue
            k = (str(r["key"]), side)
            cand = {"app": a["app"], "dec": dec, "line": al, "ev": ev, "p": p, "row": r, "side": side}
            cur = best.get(k)
            if cur is None:
                best[k] = {**cand, "others": []}
            elif ev > cur["ev"]:
                best[k] = {**cand, "others": cur["others"] + [cur]}
            else:
                cur["others"].append(cand)
    for m in match["by_app"].values():
        m["unmatched_stats"] = sorted(m["unmatched_stats"])[:30]
    match["closest"] = [t for _, t in sorted(near, key=lambda x: -x[0])[:8]]   # how close the apps come
    out = []
    for (key, side), b in best.items():
        r = b["row"]
        seen, also = {b["app"]}, []
        for o in sorted(b["others"], key=lambda o: -o["ev"]):
            if o["app"] not in seen:
                seen.add(o["app"])
                also.append(f"{o['app']} {o['line']:g} {american(o['dec'])}")
        out.append({"key": key, "side": side, "app": b["app"], "dec": b["dec"], "line": b["line"], "ev": b["ev"],
                    "p": b["p"], "pin_line": float(r["line"]), "player": r["player"], "stat": r["stat"],
                    "league": r["league"], "game": r["game"], "starts": r["starts"], "limit": r.get("limit", 0),
                    "also_at": "; ".join(also)})
    out.sort(key=lambda e: -e["ev"])
    return out, match


# ------------------------------------------------------------------ ledger
def _dir(base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / DIR


def load(base: Path | None = None) -> list[dict]:
    p = _dir(base) / "plays.csv"
    if not p.exists():
        return []
    with p.open(newline="") as fh:
        return [{k: r.get(k, "") for k in FIELDS} for r in csv.DictReader(fh)]


def _save(rows: list[dict], base: Path | None = None) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    _atomic_write_text(_dir(base) / "plays.csv", buf.getvalue())


def make_play(e: dict, now: datetime) -> dict:
    pick = f"{e['player']} {e['side'].title()} {e['line']:g} {e['stat']}"
    return {"play_id": f"{e['key']}:{e['side']}", "alert_time": iso(now), "app": e["app"], "league": e["league"],
            "game": e["game"], "player": e["player"], "stat": e["stat"], "pick": pick, "side": e["side"],
            "line": e["line"], "pin_line": e["pin_line"], "target_odds": american(e["dec"]),
            "target_decimal": round(e["dec"], 4), "fair_prob": round(e["p"], 4), "fair_odds": american(1 / e["p"]),
            "ev": round(e["ev"], 4), "also_at": e["also_at"], "limit": e["limit"], "game_start": e["starts"],
            "close_prob": "", "clv": "", "actual": "", "status": "open", "settled_time": "", "pl": "",
            "origin": "pickem", "via": e["app"], "source": "", "source_id": ""}


def alert_text(r: dict) -> tuple[str, str]:
    """Phone alert for one new Cole's Plays pick."""
    from research_lab.bet_queue import ET
    start = parse_dt(r.get("game_start"))
    when = start.astimezone(ET).strftime("%a %-I:%M %p ET") if start else "?"
    ev = float(r.get("ev") or 0)
    where = r.get("app") or r.get("via") or ""
    title = f"⭐ Cole's Plays {ev * 100:+.1f}% EV: {r['pick']}"[:250]
    lines = [r["pick"], f"{r.get('league') or ''} · {r.get('game') or ''}".strip(" ·")]
    if is_pickem(r):
        lines.append(f"Take it on {where.title()} at {r['target_odds']} per pick (fair {r['fair_odds']})")
        if r.get("also_at"):
            lines.append(f"Also clears on: {r['also_at']}")
    else:
        lines.append(f"Bet at {r['target_odds']} or better (fair {r['fair_odds']})")
        lines.append(f"From: {where}")
    lines.append(f"Starts {when}")
    return title, "\n".join(lines)


def _default_push(title: str, message: str) -> bool:
    import os
    import alert_gate
    return alert_gate.send("coles_plays", title, message, os.getenv("STREAMLIT_APP_URL") or None,
                           url_title="Open SharpIQ")


def is_pickem(r: dict) -> bool:
    return (r.get("origin") or "pickem") == "pickem"


def track_close(rows: list[dict], board_rows: list[dict], now: datetime) -> int:
    """Keep CLV at Pinnacle's latest pre-game fair for the same line (its last read before tip = the close)."""
    by_key = {str(r["key"]): r for r in board_rows}
    n = 0
    for r in rows:
        if r["status"] != "open" or not is_pickem(r):
            continue
        start = parse_dt(r["game_start"])
        if start and start <= now:
            continue
        b = by_key.get(r["play_id"].rsplit(":", 1)[0])
        if not b or _f(b.get("line")) != _f(r["pin_line"]):
            continue
        prob = float(b["p_over"]) if r["side"] == "over" else 1 - float(b["p_over"])
        r["close_prob"], r["clv"] = round(prob, 4), round(prob - float(r["fair_prob"]), 4)
        n += 1
    return n


def grade(rows: list[dict], now: datetime, box, base: Path | None = None, root: Path | None = None) -> int:
    """Pick'em picks from ESPN box scores; best-of plays from the lane they came from."""
    from research_lab import bet_queue, prop_board
    mine = [r for r in rows if is_pickem(r)]
    n = prop_board.grade_rows(mine, now, box) if box is not None else 0   # same line/side/target_decimal, $10
    lab, root = Path(base or config.DATA_DIR), Path(root or bet_queue.ROOT)
    for r in rows:
        if r["status"] != "open" or is_pickem(r):
            continue
        got = bet_queue._source_result({"source": r["source"], "source_id": r["source_id"]}, root, lab)
        if not got:
            continue
        res, clv = got
        dec = float(r["target_decimal"])
        pl = round(UNIT * (dec - 1), 2) if res == "won" else (-UNIT if res == "lost" else 0.0)
        r.update(status=res, pl=pl, settled_time=iso(now), clv="" if clv is None else round(clv, 4))
        n += 1
    return n


def best_of(rows: list[dict], now: datetime, base: Path | None = None, root: Path | None = None,
            lanes: list[dict] | None = None, injury_report=None) -> dict:
    """Add the best open plays from SharpIQ's top lanes (see module docstring). Returns info."""
    from research_lab import bet_queue as bq
    root = Path(root or bq.ROOT)
    if lanes is None:
        import scorecard
        lanes = scorecard.lanes()
    ok = {k: v for k, v in bq.lane_scores(lanes).items()
          if not k.startswith(("Cole's Plays", "Bet Queue"))}
    top = dict(sorted(ok.items(), key=lambda kv: -kv[1])[:TOP_LANES])
    info = {"top_lanes": list(top), "added": 0}
    day = bq.et_day(now)
    today = sum(1 for r in rows if not is_pickem(r) and bq.et_day(parse_dt(r["alert_time"]) or now) == day)
    seen = {f"{r['source']}:{r['source_id']}" for r in rows if not is_pickem(r)}
    seen_props = {f"prop:{r['pick']}|{r['game']}" for r in rows}
    pool = []
    for c in bq.candidates(root, base):
        start = parse_dt(c["game_start"])
        if c["lane"] not in top or not start or not (now + MIN_LEAD <= start <= now + MAX_AHEAD):
            continue
        if c["ev"] < BEST_MIN_EV or not c.get("decimal") or c["decimal"] > MAX_DECIMAL:
            continue
        if f"{c['source']}:{c['source_id']}" in seen or c["_key"] in seen_props:
            continue
        if c["source"] == "nba_props":          # same injury check as the Bet Queue: Out/Doubtful never
            import nba_injuries
            if injury_report is None:
                injury_report = nba_injuries.fetch()          # None = no report: props then skipped
            if injury_report is None or nba_injuries.check(bq._prop_player(c["pick"]), injury_report) in ("block", "unknown"):
                continue
        pool.append((top[c["lane"]] + 0.5 * min(c["ev"], bq.EV_CAP), c))
    for _, c in sorted(pool, key=lambda x: -x[0]):
        if today >= MAX_BEST_PER_DAY:
            break
        if c["_key"] in seen_props:
            continue
        dec, fair = c["decimal"], c["fair_prob"]
        rows.append({"play_id": f"lane:{c['source']}:{c['source_id']}", "alert_time": iso(now), "app": "",
                     "league": c.get("league") or "", "game": c.get("game") or "", "player": "", "stat": "",
                     "pick": c["pick"], "side": "", "line": "", "pin_line": "", "target_odds": american(dec),
                     "target_decimal": round(dec, 4), "fair_prob": round(fair, 4), "fair_odds": american(1 / fair),
                     "ev": round(c["ev"], 4), "also_at": "", "limit": "", "game_start": c["game_start"],
                     "close_prob": "", "clv": "", "actual": "", "status": "open", "settled_time": "", "pl": "",
                     "origin": "lane", "via": c["lane"], "source": c["source"], "source_id": c["source_id"]})
        seen_props.add(c["_key"])
        today += 1
        info["added"] += 1
    return info


# ------------------------------------------------------------------ one round
def update(board_rows: list[dict], now: datetime, base: Path | None = None, *, box=None, session=requests,
           apps: dict | None = None, root: Path | None = None, lanes: list[dict] | None = None,
           push=None) -> dict:
    rows = load(base)
    before = len(rows)
    have = {r["play_id"] for r in rows}
    taken = {r["play_id"].rsplit(":", 1)[0] for r in rows}          # never both sides of one prop
    props, status = fetch_all(now, base, session, apps)
    found, match = edges(props, board_rows, now)
    info = {"app_props": len(props), "matched": match["matched"], "edges": len(found), "new_plays": 0, "settled": 0,
            "closest": match["closest"],
            "apps": {k: {x: v.get(x) for x in ("props", "error", "url", "tried")} for k, v in status.items()}}
    for e in found:
        pid = f"{e['key']}:{e['side']}"
        if pid in have or e["key"] in taken:
            continue
        rows.append(make_play(e, now))
        have.add(pid)
        taken.add(e["key"])
        info["new_plays"] += 1
    info["clv_tracked"] = track_close(rows, board_rows, now)
    try:
        info["best_of"] = best_of(rows, now, base, root, lanes)
    except Exception as exc:              # the borrowed half never breaks the pick'em half
        info["best_of"] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    info["pushed"] = 0
    if push:                              # Nov 12, 2026: the only lane that buzzes Cole's phone
        for r in rows[before:]:
            try:
                info["pushed"] += bool(push(*alert_text(r)))
            except Exception as exc:
                print("Cole's Plays alert failed:", exc)
    info["settled"] = grade(rows, now, box, base, root)
    _save(rows, base)
    for k, m in match["by_app"].items():
        status.setdefault(k, {}).update(matched=m["matched"], unmatched_stats=m["unmatched_stats"])
    _atomic_write_text(_dir(base) / "status.json",
                       json.dumps({"at": iso(now), "board_props": len(board_rows), **info, "detail": status},
                                  indent=1, default=str))
    return info


def summary(rows: list[dict]) -> dict:
    done = [r for r in rows if r["status"] in {"won", "lost"}]
    pl = sum(float(r["pl"] or 0) for r in done)
    clvs = [float(r["clv"]) for r in rows if r.get("clv") not in ("", None)]
    return {"won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
            "open": sum(r["status"] == "open" for r in rows), "pl": round(pl, 2),
            "roi": pl / (UNIT * len(done)) if done else None,
            "clv_avg": sum(clvs) / len(clvs) * 100 if clvs else None,
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None}
