"""Read-only NCAA men's basketball (Division I) schedule, scores, odds and box scores from ESPN.

Free, no key, no quota. Same game/line shapes as espn_nba.py / espn_cfb.py so the grading code
is shared, plus the fields the basketball model needs:

  games -> {id, season, week (ET date), startDate, completed, homeTeam, awayTeam, homeId, awayId,
            homeConf, awayConf, homePoints, awayPoints, neutralSite, notes}
  lines -> {id, lines: [{spread, spreadOpen, overUnder, overUnderOpen, homeMoneyline, ...}]}

Teams are keyed by ESPN team id (stable across seasons and name changes); names are display only.
`groups=50` is ESPN's "all Division I" filter. ESPN drops odds once a game is final, so lines are
only read before tip-off. Possessions come from each final's box score (summary endpoint), fetched
once per game and cached forever.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from espn_cfb import _get, _num, parse_odds

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"
ET = timezone(timedelta(hours=-5))   # day bucketing only


def season_year(today: date) -> int:
    """ESPN labels a college basketball season by the year it ends: 2026-27 is 2027."""
    return today.year + 1 if today.month >= 8 else today.year


def season_start(today: date) -> date:
    return date(season_year(today) - 1, 11, 1)


def parse_event(ev: dict) -> tuple[dict, dict | None] | None:
    comp = (ev.get("competitions") or [{}])[0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    h, a = teams.get("home"), teams.get("away")
    if not h or not a:
        return None
    completed = bool(_get(comp, "status", "type", "completed") or _get(ev, "status", "type", "completed"))
    state = _get(comp, "status", "type", "state") or _get(ev, "status", "type", "state") or ""
    if _get(comp, "status", "type", "name") in ("STATUS_CANCELED", "STATUS_POSTPONED"):
        return None

    def name(c):
        t = c.get("team") or {}
        return t.get("displayName") or t.get("shortDisplayName") or t.get("location") or ""

    def pts(c):
        v = _num(c.get("score") if not isinstance(c.get("score"), dict) else c["score"].get("value"))
        return int(v) if (completed and v is not None) else None

    start = ev.get("date") or comp.get("date") or ""
    try:
        day = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET).date().isoformat()
    except ValueError:
        day = ""
    gid = str(ev.get("id"))
    notes = " ".join(str(n.get("headline") or "") for n in comp.get("notes") or [])
    game = {"id": gid, "season": _get(ev, "season", "year"), "week": day, "startDate": start,
            "completed": completed, "state": state,
            "homeTeam": name(h), "awayTeam": name(a),
            "homeId": str((h.get("team") or {}).get("id") or ""), "awayId": str((a.get("team") or {}).get("id") or ""),
            "homeConf": str((h.get("team") or {}).get("conferenceId") or ""),
            "awayConf": str((a.get("team") or {}).get("conferenceId") or ""),
            "homePoints": pts(h), "awayPoints": pts(a), "neutralSite": bool(comp.get("neutralSite")),
            "venue": _get(comp, "venue", "fullName") or "", "notes": notes}
    line = None if completed else parse_odds(comp)
    return game, ({"id": gid, "lines": [line]} if line else None)


class ESPNNCAAB:
    """File-cached scoreboard by day. Finished days are kept for weeks; today and tomorrow refresh."""

    def __init__(self, path=".espn_ncaab_cache.json", session=None, clock=time.time, timeout=25):
        self.path, self.clock, self.timeout = Path(path), clock, timeout
        self.session = session or requests.Session()
        self.calls, self.errors = 0, []
        try:
            self.cache = json.loads(self.path.read_text())
        except Exception:
            self.cache = {}

    def day(self, d: date, *, final: bool) -> tuple[list[dict], dict]:
        key = d.strftime("%Y%m%d")
        ttl = 30 * 86400 if final else 40 * 60
        hit = self.cache.get(key)
        if hit and self.clock() - hit.get("at", 0) < ttl:
            return hit["data"]["games"], hit["data"]["lines"]
        try:
            r = self.session.get(BASE, params={"dates": key, "groups": 50, "limit": 500}, timeout=self.timeout)
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
        self.path.write_text(json.dumps(self.cache, separators=(",", ":")))


def parse_possessions(summary: dict) -> float | None:
    """Box score -> game possessions: mean over both teams of FGA - ORB + TO + 0.475*FTA
    (the same formula the model was trained on)."""
    per_team = []
    for t in (summary.get("boxscore") or {}).get("teams") or []:
        s = {x.get("name"): x.get("displayValue") for x in t.get("statistics") or []}
        try:
            fga = float(str(s["fieldGoalsMade-fieldGoalsAttempted"]).split("-")[1])
            fta = float(str(s["freeThrowsMade-freeThrowsAttempted"]).split("-")[1])
            orb = float(s.get("offensiveRebounds") or 0)
            tov = float(s.get("totalTurnovers") or s.get("turnovers") or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        per_team.append(fga - orb + tov + 0.475 * fta)
    if len(per_team) != 2:
        return None
    p = sum(per_team) / 2
    return round(p, 2) if 45 <= p <= 100 else None


class PossCache:
    """Possessions by game id. A final's box never changes, so each is fetched once."""

    def __init__(self, path=".espn_ncaab_poss_cache.json", session=None, timeout=20, max_calls=400):
        self.path, self.timeout, self.max_calls = Path(path), timeout, max_calls
        self.session = session or requests.Session()
        self.calls, self.errors = 0, []
        try:
            self.cache = json.loads(self.path.read_text())
        except Exception:
            self.cache = {}

    def get(self, game_id: str) -> float | None:
        gid = str(game_id)
        if gid in self.cache:
            return self.cache[gid]
        if self.calls >= self.max_calls:
            return None
        try:
            r = self.session.get(SUMMARY, params={"event": gid}, timeout=self.timeout)
            r.raise_for_status()
            self.calls += 1
            p = parse_possessions(r.json())
            self.cache[gid] = p        # None is cached too: a box without the stats won't grow them later
            return p
        except Exception as exc:
            self.errors.append(f"box {gid}: {type(exc).__name__}: {str(exc)[:120]}")
            return None

    def save(self):
        self.path.write_text(json.dumps(self.cache, separators=(",", ":")))
