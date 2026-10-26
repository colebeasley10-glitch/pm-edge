"""Sleeper pick'em vs Pinnacle: the Hunter-style prop edge, from free public data (READ-ONLY).

Sleeper Picks shows a payout multiplier on each side of each player line (e.g. Over 2.06x /
Under 1.33x); an entry pays the product of its picks' multipliers, so each multiplier works like
decimal odds. When a multiplier beats Pinnacle's fair price for the same player and stat, that
pick is +EV, the same way Hunter's cards flag Betr/Rebet beating Novig.

A different line counts too, conservatively: an Under at a HIGHER number than Pinnacle's (or an
Over at a LOWER one) wins at least as often as Pinnacle's own line, so Pinnacle's probability at its
line is a floor for ours.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import requests

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt

LINES_URL = "https://api.sleeper.app/lines/available"
PLAYERS_URL = "https://api.sleeper.app/v1/players/{sport}"
SPORTS = ("nfl", "nba")
MIN_EV = 0.03
MAX_MULT = 3.0                       # ignore long-shot sides (outside what Cole bets)
PLAYER_TTL = timedelta(hours=20)
UA = {"User-Agent": "Mozilla/5.0 (sharpiq)", "Accept": "application/json"}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return " ".join(re.sub(r"[^a-z ]", "", s).split())


def norm_stat(s: str) -> str:
    s = str(s or "").lower().replace("&", "+")
    aliases = {"threes made": "threes", "three pointers made": "threes", "3 point fg": "threes",
               "pts+rebs+asts": "pra", "points + rebounds + assists": "pra", "rush attempts": "rushing attempts",
               "pass completions": "passing completions", "pass attempts": "passing attempts",
               "passing tds": "passing touchdowns", "receptions": "receptions"}
    s = aliases.get(s.strip(), s)
    s = s.replace("_", " ")
    sleeper = {"rec": "receptions", "threes made": "threes", "pts reb ast": "pra", "points rebounds assists": "pra",
               "rush att": "rushing attempts", "pass att": "passing attempts", "pass cmp": "passing completions",
               "pass completions": "passing completions", "rush yd": "rushing yards", "rec yd": "receiving yards",
               "pass yd": "passing yards", "pass td": "passing touchdowns", "passing td": "passing touchdowns"}
    s = sleeper.get(s.strip(), s)
    return re.sub(r"[^a-z]", "", s)


def _players_path(sport: str, base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / "SLEEPER" / f"players_{sport}.json"


def player_names(sport: str, now: datetime, base: Path | None = None, session=requests) -> dict[str, str]:
    """{sleeper player id: 'First Last'}, cached ~a day (the full list is a few MB)."""
    p = _players_path(sport, base)
    try:
        cached = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        cached = {}
    if cached and now - (parse_dt(cached.get("at")) or now - PLAYER_TTL * 2) < PLAYER_TTL:
        return cached.get("names") or {}
    r = session.get(PLAYERS_URL.format(sport=sport), headers=UA, timeout=60)
    r.raise_for_status()
    names = {}
    for pid, info in (r.json() or {}).items():
        full = info.get("full_name") or " ".join(x for x in (info.get("first_name"), info.get("last_name")) if x)
        if full:
            names[str(pid)] = full
    _atomic_write_text(p, json.dumps({"at": iso(now), "names": names}, separators=(",", ":")))
    return names


def fetch_lines(session=requests) -> list[dict]:
    r = session.get(LINES_URL, headers=UA, timeout=40)
    r.raise_for_status()
    return r.json() or []


def sleeper_props(lines: list[dict], names: dict[str, dict[str, str]]) -> list[dict]:
    """[{player, stat, line, over_mult, under_mult, sport}] for active pre-game player over/unders."""
    out = []
    for ln in lines:
        sport = str(ln.get("sport") or "")
        if (sport not in names or ln.get("status") != "active" or ln.get("subject_type") != "player"
                or ln.get("outcome_type") != "over_under" or ln.get("line_type") not in (None, "normal")
                or ln.get("game_status") not in (None, "pre_game")):
            continue
        name = names[sport].get(str(ln.get("subject_id")))
        if not name:
            continue
        sides = {}
        for o in ln.get("options") or []:
            if o.get("status") != "active":
                continue
            try:
                sides[o.get("outcome")] = (float(o.get("outcome_value")), float(o.get("payout_multiplier")))
            except (TypeError, ValueError):
                continue
        if "over" not in sides and "under" not in sides:
            continue
        line = (sides.get("over") or sides.get("under"))[0]
        out.append({"player": name, "stat": str(ln.get("wager_type") or ""), "line": line, "sport": sport,
                    "over_mult": sides["over"][1] if "over" in sides and sides["over"][0] == line else None,
                    "under_mult": sides["under"][1] if "under" in sides and sides["under"][0] == line else None})
    return out


def edges(sleeper: list[dict], board_rows: list[dict], now: datetime, min_ev: float = MIN_EV) -> list[dict]:
    """Sleeper picks that beat Pinnacle's fair price. One (best) side per player+stat."""
    pin = {}
    for r in board_rows:
        if (parse_dt(r.get("starts")) or now) <= now + timedelta(minutes=10):
            continue
        pin[(norm_name(r["player"]), norm_stat(r["stat"]))] = r
    out = []
    for s in sleeper:
        r = pin.get((norm_name(s["player"]), norm_stat(s["stat"])))
        if not r:
            continue
        pl, sl = float(r["line"]), float(s["line"])
        best = None
        for side, mult in (("over", s["over_mult"]), ("under", s["under_mult"])):
            if not mult or mult > MAX_MULT:
                continue
            if (side == "over" and sl > pl) or (side == "under" and sl < pl):
                continue                  # worse number than Pinnacle's: no safe floor
            p = r["p_over"] if side == "over" else 1 - r["p_over"]
            ev = p * mult - 1
            if ev >= min_ev and (best is None or ev > best["ev"]):
                best = {"side": side, "mult": mult, "p": p, "ev": ev}
        if best:
            out.append({**best, "player": r["player"], "stat": r["stat"], "league": r["league"], "game": r["game"],
                        "starts": r["starts"], "sleeper_line": sl, "pin_line": pl, "limit": r.get("limit", 0),
                        "key": f"{norm_name(r['player'])}|{norm_stat(r['stat'])}|{best['side']}|{sl:g}"})
    out.sort(key=lambda e: -e["ev"])
    return out


def scan(board_rows: list[dict], now: datetime, base: Path | None = None, session=requests) -> tuple[list[dict], dict]:
    info = {"lines": 0, "matched_props": 0, "edges": 0, "errors": []}
    try:
        lines = fetch_lines(session)
    except Exception as exc:
        info["errors"].append(f"lines: {type(exc).__name__}")
        return [], info
    names = {}
    for sport in SPORTS:
        try:
            names[sport] = player_names(sport, now, base, session)
        except Exception as exc:
            info["errors"].append(f"players {sport}: {type(exc).__name__}")
    props = sleeper_props(lines, names)
    info["lines"] = len(props)
    pin_keys = {(norm_name(r["player"]), norm_stat(r["stat"])) for r in board_rows}
    info["matched_props"] = sum((norm_name(p["player"]), norm_stat(p["stat"])) in pin_keys for p in props)
    found = edges(props, board_rows, now)
    info["edges"] = len(found)
    info["unmatched_stats"] = sorted({p["stat"] for p in props
                                      if (norm_name(p["player"]), norm_stat(p["stat"])) not in pin_keys})[:40]
    return found, info
