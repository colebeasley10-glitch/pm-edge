"""Read-only NBA schedule, scores and odds from ESPN's public scoreboard.

Free, no key, no quota. Same shapes as espn_cfb.py, so the grader code is shared:

  games  -> {id, season, week, startDate, completed, homeTeam, awayTeam,
             homePoints, awayPoints, neutralSite}
  lines  -> {id, lines: [{spread, spreadOpen, overUnder, homeMoneyline, ...}]}

Team names are ESPN's full displayName ("Los Angeles Lakers"), because the city alone
is ambiguous in the NBA (two LA teams). `week` holds the game's ET date (YYYY-MM-DD).
ESPN drops odds once a game is final, so lines are only read before tip-off.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from espn_cfb import _get, _num, parse_odds

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ET = timezone(timedelta(hours=-5))   # day bucketing only; an hour of DST drift doesn't matter here


def season_start_year(today: date) -> int:
    """NBA seasons start in October: 2026-27 is season 2027 at ESPN, start year 2026."""
    return today.year if today.month >= 8 else today.year - 1


def parse_event(ev: dict) -> tuple[dict, dict | None] | None:
    comp = (ev.get("competitions") or [{}])[0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    h, a = teams.get("home"), teams.get("away")
    if not h or not a:
        return None
    if _get(ev, "season", "type") not in (None, 2) and _get(ev, "season", "slug") not in (None, "regular-season"):
        return None   # preseason / All-Star / playoffs (playoffs are fine to skip for a ratings model)
    completed = bool(_get(comp, "status", "type", "completed") or _get(ev, "status", "type", "completed"))

    def name(c):
        t = c.get("team") or {}
        return t.get("displayName") or t.get("name") or t.get("location") or ""

    def pts(c):
        v = _num(c.get("score") if not isinstance(c.get("score"), dict) else c["score"].get("value"))
        return int(v) if (completed and v is not None) else None

    start = ev.get("date") or comp.get("date") or ""
    try:
        day = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET).date().isoformat()
    except ValueError:
        day = ""
    gid = str(ev.get("id"))
    game = {"id": gid, "season": _get(ev, "season", "year"), "week": day, "startDate": start,
            "completed": completed, "homeTeam": name(h), "awayTeam": name(a),
            "homePoints": pts(h), "awayPoints": pts(a), "neutralSite": bool(comp.get("neutralSite"))}
    line = None if completed else parse_odds(comp)
    return game, ({"id": gid, "lines": [line]} if line else None)


class ESPNNBA:
    """File-cached scoreboard by day. Finished days are kept for weeks; today and tomorrow refresh."""

    def __init__(self, path=".espn_nba_cache.json", session=None, clock=time.time, timeout=20):
        self.path, self.clock, self.timeout = Path(path), clock, timeout
        self.session = session or requests.Session()
        self.calls, self.errors = 0, []
        try:
            self.cache = json.loads(self.path.read_text())
        except Exception:
            self.cache = {}

    def day(self, d: date, *, final: bool) -> tuple[list[dict], dict]:
        key = d.strftime("%Y%m%d")
        ttl = 30 * 86400 if final else 45 * 60
        hit = self.cache.get(key)
        if hit and self.clock() - hit.get("at", 0) < ttl:
            return hit["data"]["games"], hit["data"]["lines"]
        try:
            r = self.session.get(BASE, params={"dates": key, "limit": 100}, timeout=self.timeout)
            r.raise_for_status()
            games, lines = [], {}
            for ev in r.json().get("events") or []:
                parsed = parse_event(ev)
                if not parsed:
                    continue
                g, gl = parsed
                games.append(g)
                if gl:
                    lines[g["id"]] = gl
            self.calls += 1
            self.cache[key] = {"at": self.clock(), "data": {"games": games, "lines": lines}}
            return games, lines
        except Exception as exc:
            self.errors.append(f"{key}: {type(exc).__name__}: {str(exc)[:160]}")
            if hit:
                return hit["data"]["games"], hit["data"]["lines"]
            return [], {}

    def window(self, first: date, last: date, today: date) -> tuple[list[dict], dict]:
        games, lines, d = [], {}, first
        while d <= last:
            gs, ls = self.day(d, final=d < today - timedelta(days=1))
            games += gs
            lines.update(ls)
            d += timedelta(days=1)
        return games, lines

    def save(self):
        self.path.write_text(json.dumps(self.cache))
