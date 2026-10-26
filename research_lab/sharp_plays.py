"""Sharp Plays: free version of the OddsJam-style "sharp bet" cards (ALERTS + PAPER, no orders).

What those cards do: take the price the sharpest markets agree on (Pinnacle, exchanges with real
money resting), and flag soft books (Rebet, Betr...) still offering a better price on the same side.
The soft-book prices are the paid part. This module does the free half:

1. Watches Pinnacle's main line for every NFL, college football, NBA and NHL game (PinnWire, free).
2. When the line moves hard one way (steam: sharp money hitting it), that side becomes a play.
3. The alert gives the fair price and the worst odds still worth taking (+2% EV), so the bet can
   be shopped by hand on any book. Soft books often lag a Pinnacle move, which is the edge.

Every play is paper-tracked at the target price (1 unit = $10), settled from ESPN final scores,
and scored by CLV: did Pinnacle's closing price move further our way?
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt
from research_lab.pinnacle import two_way

DIR = "SHARP_PLAYS"
UNIT = 10.0
EDGE_CUSHION = 0.02          # target price = fair price with 2% EV left over
MAX_OVERROUND = 1.10
MAX_PER_DAY = 10
MIN_HISTORY = timedelta(hours=2)     # need a real "open" before calling anything a move
WINDOW = timedelta(hours=12)         # only fresh moves count: soft books lag recent steam, not old news
MAX_AHEAD = timedelta(hours=36)
PROB_MOVE = 0.04             # fair win-probability shift of the original line's side
# line moves (points) that count as steam on their own, by league
POINT_MOVE = {"NFL": {"total": 1.5, "spread": 1.0}, "NCAA": {"total": 2.0, "spread": 1.5},
              "NBA": {"total": 2.5, "spread": 1.5}, "NHL": {"total": 0.5, "spread": 99}}
ESPN_CODE = {"NFL": "nfl", "NCAA": "cfb", "NBA": "nba", "NHL": "nhl"}
SPORT_IDS = {5, 3, 4}        # football, basketball, hockey
FIELDS = ["play_id", "alert_time", "league", "game", "home", "away", "game_start", "market", "pick", "side",
          "line", "open_line", "move", "fair_prob", "fair_odds", "target_odds", "target_decimal", "pushed",
          "close_prob", "clv", "status", "settled_time", "pl"]


def _dir(base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / DIR


def american(dec: float) -> str:
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


def league_of(league_name: str) -> str | None:
    for k in ESPN_CODE:
        if league_name == k or league_name.startswith(k + " "):
            return k
    return None


# ------------------------------------------------------------------ Pinnacle market reading
def _p(a, b) -> float | None:
    d = two_way(a, b, MAX_OVERROUND)
    return d["power"] if d else None


def totals(full: dict) -> dict[float, float]:
    """{points: fair P(over)} for every total Pinnacle offers."""
    out = {}
    for v in (full.get("totals") or {}).values():
        try:
            pts = float(v.get("points"))
        except (TypeError, ValueError, AttributeError):
            continue
        p = _p(v.get("over"), v.get("under"))
        if p is not None:
            out[pts] = p
    return out


def spreads(full: dict) -> dict[float, float]:
    """{home handicap: fair P(home covers)}."""
    out = {}
    for v in (full.get("spreads") or {}).values():
        try:
            hdp = float(v.get("hdp"))
        except (TypeError, ValueError, AttributeError):
            continue
        p = _p(v.get("home"), v.get("away"))
        if p is not None:
            out[hdp] = p
    return out


def moneyline(full: dict) -> float | None:
    ml = full.get("money_line") or {}
    if ml.get("draw") is not None:
        return None
    return _p(ml.get("home"), ml.get("away"))


def main_line(lines: dict[float, float]) -> tuple[float, float] | None:
    """The line Pinnacle prices closest to a coin flip."""
    if not lines:
        return None
    k = min(lines, key=lambda x: abs(lines[x] - 0.5))
    return k, lines[k]


def snapshot(full: dict) -> dict:
    s = {}
    t, sp, ml = main_line(totals(full)), main_line(spreads(full)), moneyline(full)
    if t:
        s["total"] = {"line": t[0], "p": t[1], "all": {str(k): v for k, v in totals(full).items()}}
    if sp:
        s["spread"] = {"line": sp[0], "p": sp[1], "all": {str(k): v for k, v in spreads(full).items()}}
    if ml is not None:
        s["ml"] = {"line": 0.0, "p": ml, "all": {"0.0": ml}}
    return s


# ------------------------------------------------------------------ steam detection
def steam(market: str, league: str, first: dict, now: dict) -> dict | None:
    """Which side got hit, and how hard. Positive direction = over / home."""
    move_pts = now["line"] - first["line"]
    if market == "spread":
        move_pts = -move_pts          # home -3 -> -4.5 means money on home
    p_then = first["p"]
    p_now_same = now["all"].get(str(first["line"]))
    prob_move = (p_now_same - p_then) if p_now_same is not None else None
    if market == "ml":
        prob_move = now["p"] - first["p"]
    pts_hit = market != "ml" and abs(move_pts) >= POINT_MOVE.get(league, {}).get(market, 99)
    prob_hit = prob_move is not None and abs(prob_move) >= PROB_MOVE
    if not (pts_hit or prob_hit):
        return None
    direction = (move_pts if pts_hit else prob_move) > 0
    return {"up": direction, "pts": round(move_pts, 2), "prob": round(prob_move, 4) if prob_move is not None else None}


def make_play(ev, league: str, market: str, first: dict, now_m: dict, st: dict, now: datetime) -> dict:
    up = st["up"]
    line = now_m["line"]
    if market == "total":
        side, pick = ("over", f"Over {line:g}") if up else ("under", f"Under {line:g}")
        p = now_m["p"] if up else 1 - now_m["p"]
        moved = f"Pinnacle total {first['line']:g} → {line:g}" if line != first["line"] else \
            f"Pinnacle {'Over' if up else 'Under'} {line:g} price moved {abs(st['prob'] or 0) * 100:.0f} pts"
    elif market == "spread":
        team = ev.home if up else ev.away
        hdp = line if up else -line
        side, pick = ("home" if up else "away"), f"{team} {hdp:+g}"
        p = now_m["p"] if up else 1 - now_m["p"]
        moved = f"Pinnacle {ev.home} {first['line']:+g} → {line:+g}"
    else:
        team = ev.home if up else ev.away
        side, pick = ("home" if up else "away"), f"{team} ML"
        p = now_m["p"] if up else 1 - now_m["p"]
        moved = f"{team} win chance {(first['p'] if up else 1 - first['p']) * 100:.0f}% → {p * 100:.0f}% at Pinnacle"
    fair_dec = 1 / p
    target_dec = (1 + EDGE_CUSHION) / p
    return {"play_id": f"{ev.id}-{market}", "alert_time": iso(now), "league": league,
            "game": f"{ev.away} at {ev.home}", "home": ev.home, "away": ev.away, "game_start": iso(ev.starts),
            "market": market, "pick": pick, "side": side, "line": line, "open_line": first["line"],
            "move": moved, "fair_prob": round(p, 4), "fair_odds": american(fair_dec),
            "target_odds": american(target_dec), "target_decimal": round(target_dec, 4), "pushed": False,
            "close_prob": "", "clv": "", "status": "open", "settled_time": "", "pl": ""}


def alert_text(play: dict) -> tuple[str, str]:
    from trader_follow.core import _eastern
    start = parse_dt(play["game_start"])
    old = ""
    if play["market"] != "ml" and float(play["open_line"]) != float(play["line"]):
        old = f"\nStill see the old {float(play['open_line']):g} line somewhere? That's even better."
    return (f"🎯 Sharp play: {play['pick']}",
            f"{play['game']} · {play['league']} {play['market']} · {_eastern(start) if start else ''}\n"
            f"Why: {play['move']} (sharp money)\n"
            f"Fair {play['fair_odds']} · bet at {play['target_odds']} or better on any book\n"
            f"Size: 1u{old}")


# ------------------------------------------------------------------ storage
def load(base: Path | None = None) -> list[dict]:
    p = _dir(base) / "plays.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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
def update(pinnacle_events: dict, now: datetime, base: Path | None = None, *, push=None, scores=None) -> dict:
    """pinnacle_events: {sport_id: [PinEvent]} fetched this round (may be empty)."""
    hist, rows = _hist(base), load(base)
    by_id = {r["play_id"]: r for r in rows}
    info = {"games": 0, "new_plays": 0, "pushed": 0, "settled": 0, "new": []}
    today = now.strftime("%Y-%m-%d")
    sent_today = sum(1 for r in rows if (r.get("alert_time") or "")[:10] == today)
    games_alerted = {r["play_id"].rsplit("-", 1)[0] for r in rows}
    candidates = []
    for sid, events in (pinnacle_events or {}).items():
        if sid not in SPORT_IDS:
            continue
        for ev in events:
            league = league_of(ev.league)
            if not league or not ev.starts or not (now < ev.starts <= now + MAX_AHEAD):
                continue
            info["games"] += 1
            snap = snapshot(ev.full)
            h = hist.setdefault(ev.id, {"starts": iso(ev.starts), "snaps": []})
            h["snaps"] = [x for x in h["snaps"] if now - (parse_dt(x["at"]) or now) <= WINDOW]
            base_snap = h["snaps"][0] if h["snaps"] else None
            h["snaps"].append({"at": iso(now), "s": snap})
            # keep each open play's closing price current (last read before the game)
            for mk in snap:
                r = by_id.get(f"{ev.id}-{mk}")
                if r and r["status"] == "open":
                    p_side = snap[mk]["all"].get(str(float(r["line"])))
                    if p_side is not None:
                        p = p_side if r["side"] in {"over", "home"} else 1 - p_side
                        r["close_prob"] = round(p, 4)
                        r["clv"] = round(p - float(r["fair_prob"]), 4)
            if base_snap is None or now - (parse_dt(base_snap["at"]) or now) < MIN_HISTORY or ev.id in games_alerted:
                continue
            for mk, cur in snap.items():
                first = base_snap["s"].get(mk)
                st = steam(mk, league, first, cur) if first else None
                if st:
                    size = abs(st["prob"] or 0) + abs(st["pts"]) / 10
                    candidates.append((size, make_play(ev, league, mk, first, cur, st, now)))
    # strongest moves first, one play per game, daily cap
    for _, play in sorted(candidates, key=lambda c: -c[0]):
        gid = play["play_id"].rsplit("-", 1)[0]
        if gid in games_alerted or sent_today >= MAX_PER_DAY:
            continue
        games_alerted.add(gid)
        sent_today += 1
        if push:
            title, msg = alert_text(play)
            try:
                play["pushed"] = bool(push(title, msg))
            except Exception:
                play["pushed"] = False
        info["pushed"] += play["pushed"] is True
        rows.append(play)
        info["new"].append(play)
        info["new_plays"] += 1
    # settle finished games from ESPN
    if scores is not None:
        for r in rows:
            if r["status"] != "open":
                continue
            start = parse_dt(r["game_start"])
            if not start or now - start < timedelta(hours=3):
                continue
            res = scores(espn_row(r))
            if res in {"won", "lost", "void"}:
                pl = round(UNIT * (float(r["target_decimal"]) - 1), 2) if res == "won" else (-UNIT if res == "lost" else 0.0)
                r.update(status=res, pl=pl, settled_time=iso(now))
                info["settled"] += 1
    # forget games that started more than a day ago
    cutoff = now - timedelta(days=1)
    hist = {k: v for k, v in hist.items() if (parse_dt(v.get("starts")) or now) >= cutoff}
    _atomic_write_text(_dir(base) / "history.json", json.dumps(hist, indent=1, sort_keys=True))
    _save(rows, base)
    return info


def espn_row(r: dict) -> dict:
    """Shape a play like a Polymarket signal so trader_follow.scores can grade it."""
    code = ESPN_CODE.get(r["league"], "")
    base = {"sport": code, "event_slug": code, "game_start": r["game_start"]}
    if r["market"] == "total":
        return {**base, "question": f"{r['home']} vs. {r['away']}: O/U {float(r['line']):g}",
                "outcome": "Over" if r["side"] == "over" else "Under", "market_type": "totals"}
    if r["market"] == "spread":
        line = float(r["line"])
        return {**base, "question": f"Spread: {r['home']} ({line:+g})",
                "outcome": r["home"] if r["side"] == "home" else r["away"], "market_type": "spreads"}
    return {**base, "question": f"{r['home']} vs. {r['away']}",
            "outcome": r["home"] if r["side"] == "home" else r["away"], "market_type": "moneyline"}


def summary(rows: list[dict]) -> dict:
    done = [r for r in rows if r["status"] in {"won", "lost"}]
    pl = sum(float(r["pl"] or 0) for r in done)
    clvs = [float(r["clv"]) for r in rows if r.get("clv") not in ("", None)]
    return {"won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
            "open": sum(r["status"] == "open" for r in rows), "pl": round(pl, 2),
            "roi": pl / (UNIT * len(done)) if done else None,
            "clv_avg": sum(clvs) / len(clvs) * 100 if clvs else None,   # win-prob points
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None}
