"""Prop Board + prop steam alerts: the free version of the OddsJam prop cards (ALERTS + PAPER).

Source: Pinnacle player props from the free PinnWire key (include_specials=1 on the same fixtures
call the lab already makes). For every NBA, NFL, college football and NHL player prop:
  * fair Over/Under price (Pinnacle's two prices with the vig removed),
  * the worst odds still worth taking on each side (+2% EV) -- the number to shop on Betr/Rebet,
  * Pinnacle's max bet ("P limit"; bigger = a line Pinnacle trusts more),
  * how far the line has moved in the last 12 hours.
When Pinnacle moves a prop hard (sharp money), the moved-to side is pushed as a play: soft books
are slow to move props, so the old number is often still up. Plays are paper 1u at the target
price, graded from ESPN box scores where the stat can be read, and scored by CLV either way.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt
from research_lab.pinnacle import two_way

DIR = "PROP_BOARD"
UNIT = 10.0
EDGE_CUSHION = 0.02
MAX_OVERROUND = 1.12           # props carry a little more vig than sides
MAX_PER_DAY = 10
MIN_HISTORY = timedelta(hours=2)
WINDOW = timedelta(hours=12)
MAX_AHEAD = timedelta(hours=36)
PROB_MOVE = 0.06
MIN_LIMIT = 100                # ignore props Pinnacle barely takes action on
LEAGUES = {"NBA": "basketball/nba", "NFL": "football/nfl", "NCAA": "football/college-football",
           "NHL": "hockey/nhl"}
FIELDS = ["play_id", "alert_time", "league", "game", "player", "stat", "pick", "side", "line", "open_line",
          "move", "limit", "fair_prob", "fair_odds", "target_odds", "target_decimal", "game_start", "pushed",
          "close_prob", "clv", "actual", "status", "settled_time", "pl"]


def _dir(base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / DIR


def american(dec: float) -> str:
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


def league_of(name: str) -> str | None:
    for k in LEAGUES:
        if name == k or name.startswith(k + " "):
            return k
    return None


def player_and_stat(special: str, units: str) -> tuple[str, str]:
    s = str(special or "").strip()
    if " Total " in s:
        return s.split(" Total ", 1)[0].strip(), s.split(" Total ", 1)[1].strip() or units
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if units and s.lower().endswith(units.lower()):
        return s[: -len(units)].strip(), units
    return s, units or ""


def big_stat(stat: str) -> bool:
    return "yard" in stat.lower()


# ------------------------------------------------------------------ parse Pinnacle props
def props_from(payloads: dict, now: datetime) -> list[dict]:
    out = []
    for payload in (payloads or {}).values():
        events = payload.get("events") or []
        parents = {e.get("event_id"): e for e in events if e.get("home") and e.get("away")}
        for e in events:
            if str(e.get("special_category") or "") != "Player Props":
                continue
            league = league_of(str(e.get("league_name") or ""))
            starts = parse_dt(e.get("starts") or e.get("start_ts"))
            if not league or not starts or not (now < starts <= now + MAX_AHEAD):
                continue
            par = parents.get(e.get("parent_id")) or {}
            player, stat = player_and_stat(e.get("special"), e.get("special_units"))
            for mk in ((e.get("special_markets") or {}).get("num_0") or []):
                if mk.get("type") != "total":
                    continue
                prices = {str(p.get("name") or "").lower(): p for p in mk.get("prices") or []}
                o, u = prices.get("over"), prices.get("under")
                if not o or not u or o.get("points") is None or o.get("points") != u.get("points"):
                    continue
                d = two_way(o.get("price"), u.get("price"), MAX_OVERROUND)
                if not d:
                    continue
                p_over = d["power"]
                out.append({"key": str(e.get("event_id")), "league": league, "player": player, "stat": stat,
                            "home": par.get("home", ""), "away": par.get("away", ""),
                            "game": f"{par.get('away', '?')} at {par.get('home', '?')}", "starts": iso(starts),
                            "line": float(o["points"]), "p_over": round(p_over, 4),
                            "limit": int(mk.get("max_risk") or 0)})
                break        # main line only
    return out


def board_row(p: dict, first: dict | None) -> dict:
    po, pu = p["p_over"], 1 - p["p_over"]
    return {**p, "fair_over": american(1 / po), "fair_under": american(1 / pu),
            "bet_over_at": american((1 + EDGE_CUSHION) / po), "bet_under_at": american((1 + EDGE_CUSHION) / pu),
            "open_line": first["line"] if first else p["line"],
            "open_p_over": first["p_over"] if first else p["p_over"]}


# ------------------------------------------------------------------ steam
def steam(first: dict, now: dict) -> dict | None:
    """'over' / 'under' when Pinnacle moved hard; None otherwise."""
    move = now["line"] - first["line"]
    need = 4.0 if big_stat(now["stat"]) else 1.0
    if abs(move) >= need:
        return {"side": "over" if move > 0 else "under", "why": f"line {first['line']:g} → {now['line']:g}"}
    if move == 0:
        dp = now["p_over"] - first["p_over"]
        if abs(dp) >= PROB_MOVE:
            side = "over" if dp > 0 else "under"
            p0 = first["p_over"] if side == "over" else 1 - first["p_over"]
            p1 = now["p_over"] if side == "over" else 1 - now["p_over"]
            return {"side": side, "why": f"{side.title()} {now['line']:g} {american(1 / p0)} → {american(1 / p1)}"}
    return None


def make_play(p: dict, first: dict, st: dict, now: datetime) -> dict:
    side = st["side"]
    prob = p["p_over"] if side == "over" else 1 - p["p_over"]
    target = (1 + EDGE_CUSHION) / prob
    return {"play_id": f"{p['key']}", "alert_time": iso(now), "league": p["league"], "game": p["game"],
            "player": p["player"], "stat": p["stat"], "pick": f"{p['player']} {side.title()} {p['line']:g} {p['stat']}",
            "side": side, "line": p["line"], "open_line": first["line"], "move": f"Pinnacle {st['why']}",
            "limit": p["limit"], "fair_prob": round(prob, 4), "fair_odds": american(1 / prob),
            "target_odds": american(target), "target_decimal": round(target, 4), "game_start": p["starts"],
            "pushed": False, "close_prob": "", "clv": "", "actual": "", "status": "open", "settled_time": "", "pl": ""}


def alert_text(play: dict) -> tuple[str, str]:
    from trader_follow.core import _eastern
    start = parse_dt(play["game_start"])
    old = ""
    if float(play["open_line"]) != float(play["line"]):
        old = (f"\nBest: a book still showing the old {float(play['open_line']):g} "
               f"({'Over' if play['side'] == 'over' else 'Under'} it).")
    return (f"🎯 Prop: {play['pick']}",
            f"{play['game']} · {play['league']} · {_eastern(start) if start else ''}\n"
            f"Why: {play['move']} (sharp money) · P limit ${int(play['limit']):,}\n"
            f"Fair {play['fair_odds']} · worst price {play['target_odds']}: bet at {play['target_odds']} or better, pass on anything lower (Betr, Rebet, any book)\n"
            f"Size: 1u{old}")


# ------------------------------------------------------------------ storage
def load(base: Path | None = None) -> list[dict]:
    p = _dir(base) / "plays.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_board(base: Path | None = None) -> dict:
    p = _dir(base) / "board.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def _save(rows: list[dict], base: Path | None) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    _atomic_write_text(_dir(base) / "plays.csv", buf.getvalue())


def _hist(base: Path | None) -> dict:
    p = _dir(base) / "history.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


# ------------------------------------------------------------------ one round
def update(payloads: dict, now: datetime, base: Path | None = None, *, push=None, box=None) -> dict:
    """payloads: raw PinnWire fixtures fetched this round. box(play) -> actual stat value or None."""
    hist, rows = _hist(base), load(base)
    by_id = {r["play_id"]: r for r in rows}
    info = {"props": 0, "new_plays": 0, "pushed": 0, "settled": 0, "new": []}
    props = props_from(payloads, now)
    info["props"] = len(props)
    if props:
        board = load_board(base).get("rows") or []
        fresh_sports = {p["league"] for p in props}
        keep = [r for r in board if r.get("league") not in fresh_sports
                and (parse_dt(r.get("starts")) or now) > now]      # other sports keep their last read
        for p in props:
            h = hist.setdefault(p["key"], {"starts": p["starts"], "snaps": []})
            h["snaps"] = [x for x in h["snaps"] if now - (parse_dt(x["at"]) or now) <= WINDOW]
            first = h["snaps"][0]["p"] if h["snaps"] else None
            first_at = parse_dt(h["snaps"][0]["at"]) if h["snaps"] else None
            h["snaps"].append({"at": iso(now), "p": {k: p[k] for k in ("line", "p_over", "stat")}})
            keep.append(board_row(p, first))
            r = by_id.get(p["key"])
            if r and r["status"] == "open" and float(r["line"]) == p["line"]:
                prob = p["p_over"] if r["side"] == "over" else 1 - p["p_over"]
                r["close_prob"], r["clv"] = round(prob, 4), round(prob - float(r["fair_prob"]), 4)
            p["_first"], p["_first_at"] = first, first_at
        _atomic_write_text(_dir(base) / "board.json", json.dumps({"at": iso(now), "rows": keep}, indent=0))

        today = now.strftime("%Y-%m-%d")
        sent = sum(1 for r in rows if (r.get("alert_time") or "")[:10] == today)
        cands = []
        for p in props:
            first, at = p.pop("_first"), p.pop("_first_at")
            if (not first or not at or now - at < MIN_HISTORY or p["key"] in by_id or p["limit"] < MIN_LIMIT):
                continue
            st = steam({**first, "stat": p["stat"]}, p)
            if st:
                prob_jump = abs(p["p_over"] - first["p_over"]) + abs(p["line"] - first["line"]) / (8 if big_stat(p["stat"]) else 2)
                cands.append((prob_jump * (1 + min(p["limit"], 2000) / 2000), make_play(p, first, st, now)))
        players_today = {(r["player"], r["game"]) for r in rows if (r.get("alert_time") or "")[:10] == today}
        for _, play in sorted(cands, key=lambda c: -c[0]):
            if sent >= MAX_PER_DAY or (play["player"], play["game"]) in players_today:
                continue
            if push:
                t, m = alert_text(play)
                try:
                    play["pushed"] = bool(push(t, m))
                except Exception:
                    play["pushed"] = False
            info["pushed"] += play["pushed"] is True
            rows.append(play)
            info["new"].append(play)
            players_today.add((play["player"], play["game"]))
            sent += 1
            info["new_plays"] += 1
    else:
        for p in props:
            p.pop("_first", None)
    # grade finished props from box scores
    if box is not None:
        for r in rows:
            if r["status"] != "open":
                continue
            start = parse_dt(r["game_start"])
            if not start or now - start < timedelta(hours=4):
                continue
            try:
                actual = box(r)
            except Exception:
                actual = None
            if actual is None:
                if now - start > timedelta(days=3):
                    r.update(status="ungraded", settled_time=iso(now))
                continue
            line = float(r["line"])
            if actual == line:
                res = "void"
            else:
                res = "won" if (actual > line) == (r["side"] == "over") else "lost"
            pl = round(UNIT * (float(r["target_decimal"]) - 1), 2) if res == "won" else (-UNIT if res == "lost" else 0.0)
            r.update(actual=actual, status=res, pl=pl, settled_time=iso(now))
            info["settled"] += 1
    cutoff = now - timedelta(days=1)
    hist = {k: v for k, v in hist.items() if (parse_dt(v.get("starts")) or now) >= cutoff}
    _atomic_write_text(_dir(base) / "history.json", json.dumps(hist, separators=(",", ":")))
    _save(rows, base)
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


# ------------------------------------------------------------------ ESPN box scores
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/{kind}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", str(s or "").lower().replace(".", "")).strip()


def stat_value(stat: str, cats: dict[str, dict[str, str]]) -> float | None:
    """cats: ESPN category name (lowercase) -> {label: value} for one player."""
    s = stat.lower()

    def num(cat, label):
        v = (cats.get(cat) or {}).get(label)
        if v is None:
            return None
        v = str(v).split("-")[0].split("/")[0]
        try:
            return float(v)
        except ValueError:
            return None

    def frac(cat, label, i):
        v = (cats.get(cat) or {}).get(label)
        try:
            return float(str(v).replace("-", "/").split("/")[i]) if v is not None else None
        except (ValueError, IndexError):
            return None

    if "rush attempt" in s or "carries" in s:
        return num("rushing", "CAR")
    if "pass completion" in s or "completions" in s:
        return frac("passing", "C/ATT", 0)
    if "pass attempt" in s:
        return frac("passing", "C/ATT", 1)
    if "interception" in s:
        return num("passing", "INT")
    if s in {"touchdowns", "anytime touchdowns", "total touchdowns"}:
        vals = [num(c, "TD") for c in ("rushing", "receiving")]
        return sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
    if "yard" in s:
        cat = "passing" if "pass" in s else "rushing" if "rush" in s else "receiving" if "rec" in s else None
        return num(cat, "YDS") if cat else None
    if "reception" in s:
        return num("receiving", "REC")
    if "passing td" in s or "passing touchdown" in s:
        return num("passing", "TD")
    any_cat = {}
    for c in cats.values():
        any_cat.update(c)
    def g(label):
        v = any_cat.get(label)
        try:
            return float(str(v).split("-")[0]) if v is not None else None
        except ValueError:
            return None
    if any(k in cats for k in ("forwards", "defenses", "defense", "skaters")) or ("G" in any_cat and "A" in any_cat
                                                                                  and "PTS" not in any_cat):
        sog = g("SOG") if g("SOG") is not None else g("S")
        hockey = {"goals": g("G"), "assists": g("A"), "shots on goal": sog,
                  "points": (g("G") + g("A")) if g("G") is not None and g("A") is not None else None}
        if s in hockey:
            return hockey[s]
    parts = []
    for word, label in (("point", "PTS"), ("rebound", "REB"), ("assist", "AST"), ("three", "3PT"),
                        ("steal", "STL"), ("block", "BLK"), ("shots on goal", "SOG"), ("goal", "G")):
        if word in s or (word == "point" and "pts" in s) or (word == "rebound" and "reb" in s) or (word == "assist" and "ast" in s):
            parts.append(label)
    if "shots on goal" in s:
        parts = ["SOG"]
    if not parts:
        return None
    vals = [g(x) for x in parts]
    return None if any(v is None for v in vals) else sum(vals)


class BoxScores:
    """Callable(play) -> the player's actual stat, or None if the game isn't final / can't be read."""

    def __init__(self, session=requests):
        self.s, self.cache = session, {}

    def _get(self, path, kind, **params):
        key = (path, kind, tuple(sorted(params.items())))
        if key not in self.cache:
            r = self.s.get(ESPN.format(path=path, kind=kind), params=params, timeout=20)
            r.raise_for_status()
            self.cache[key] = r.json()
        return self.cache[key]

    def __call__(self, play: dict) -> float | None:
        from trader_follow.scores import team_matches
        path = LEAGUES.get(play["league"])
        start = parse_dt(play["game_start"])
        if not path or not start:
            return None
        home = play["game"].split(" at ")[-1]
        event = None
        for day in {start.strftime("%Y%m%d"), (start - timedelta(hours=8)).strftime("%Y%m%d")}:
            board = self._get(path, "scoreboard", dates=day, limit=300)
            for ev in board.get("events") or []:
                comp = (ev.get("competitions") or [{}])[0]
                done = ((comp.get("status") or {}).get("type") or {}).get("completed")
                teams = [c.get("team") or {} for c in comp.get("competitors") or []]
                if done and any(team_matches(home, t) for t in teams):
                    event = ev.get("id")
        if not event:
            return None
        summ = self._get(path, "summary", event=event)
        want = _norm(play["player"])
        for team in (summ.get("boxscore") or {}).get("players") or []:
            cats: dict = {}
            for cat in team.get("statistics") or []:
                labels = cat.get("labels") or cat.get("keys") or []
                for a in cat.get("athletes") or []:
                    name = _norm((a.get("athlete") or {}).get("displayName"))
                    if name == want or (want and name and (want in name or name in want)):
                        cats.setdefault(str(cat.get("name") or cat.get("text") or "").lower(), {}).update(
                            dict(zip(labels, a.get("stats") or [])))
            if cats:
                return stat_value(play["stat"], cats)
        return None
