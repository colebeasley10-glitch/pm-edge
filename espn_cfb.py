"""Read-only college football schedule, scores and odds from ESPN's public scoreboard.

Free, no key, no monthly quota. Returns data in the same shape the NCAA code
already used from CollegeFootballData:

  games  -> {id, season, week, startDate, completed, homeTeam, awayTeam,
             homePoints, awayPoints, neutralSite}
  lines  -> {id, lines: [{provider, spread, spreadOpen, overUnder, overUnderOpen,
             homeMoneyline, awayMoneyline, homeSpreadOdds, awaySpreadOdds,
             overOdds, underOdds}]}

Spreads are the HOME line (negative = home favored), like CFBD. Team names are
ESPN's `location` ("Ohio State", "Miami (OH)"), which match CFBD's for most schools;
data/team_aliases.csv fixes the rest.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
FBS_GROUP = 80


def _num(x):
    if x is None:
        return None
    s = str(x).strip().lower()
    if not s or s in ("off", "even", "pk", "pick", "-"):
        return 0.0 if s in ("even", "pk", "pick") else None
    s = s.lstrip("ou")
    try:
        return float(s)
    except ValueError:
        return None


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_odds(comp: dict) -> dict | None:
    """First provider's odds -> one CFBD-style line row (home perspective)."""
    for o in comp.get("odds") or []:
        spread = _num(_get(o, "pointSpread", "home", "close", "line"))
        if spread is None:
            spread = _num(o.get("spread"))
        total = _num(_get(o, "total", "over", "close", "line"))
        if total is None:
            total = _num(o.get("overUnder"))
        hml = _num(_get(o, "moneyline", "home", "close", "odds"))
        aml = _num(_get(o, "moneyline", "away", "close", "odds"))
        if hml is None:
            hml = _num(_get(o, "homeTeamOdds", "moneyLine"))
        if aml is None:
            aml = _num(_get(o, "awayTeamOdds", "moneyLine"))
        row = {"provider": _get(o, "provider", "name") or "ESPN",
               "spread": spread,
               "spreadOpen": _num(_get(o, "pointSpread", "home", "open", "line")),
               "overUnder": total,
               "overUnderOpen": _num(_get(o, "total", "over", "open", "line")),
               "homeMoneyline": hml or None, "awayMoneyline": aml or None,
               "homeSpreadOdds": _num(_get(o, "pointSpread", "home", "close", "odds")),
               "awaySpreadOdds": _num(_get(o, "pointSpread", "away", "close", "odds")),
               "overOdds": _num(_get(o, "total", "over", "close", "odds")),
               "underOdds": _num(_get(o, "total", "under", "close", "odds"))}
        if any(row[k] is not None for k in ("spread", "overUnder", "homeMoneyline")):
            return row
    return None


def parse_event(ev: dict) -> tuple[dict, dict | None] | None:
    comp = (ev.get("competitions") or [{}])[0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    h, a = teams.get("home"), teams.get("away")
    if not h or not a:
        return None
    completed = bool(_get(comp, "status", "type", "completed") or _get(ev, "status", "type", "completed"))

    def name(c):
        t = c.get("team") or {}
        return t.get("location") or t.get("displayName") or t.get("name") or ""

    def pts(c):
        v = _num(c.get("score") if not isinstance(c.get("score"), dict) else c["score"].get("value"))
        return int(v) if (completed and v is not None) else None

    gid = str(ev.get("id"))
    game = {"id": gid, "season": _get(ev, "season", "year"), "week": _get(ev, "week", "number"),
            "startDate": ev.get("date") or comp.get("date"), "completed": completed,
            "homeTeam": name(h), "awayTeam": name(a), "homePoints": pts(h), "awayPoints": pts(a),
            "neutralSite": bool(comp.get("neutralSite"))}
    line = parse_odds(comp)
    return game, ({"id": gid, "lines": [line]} if line else None)


class ESPNCFB:
    """File-cached ESPN scoreboard. Finished weeks are kept for days; the live week refreshes hourly."""

    def __init__(self, path=".espn_cfb_cache.json", session=None, clock=time.time, timeout=20):
        self.path, self.clock, self.timeout = Path(path), clock, timeout
        self.session = session or requests.Session()
        self.calls, self.errors = 0, []
        try:
            self.cache = json.loads(self.path.read_text())
        except Exception:
            self.cache = {}

    def _fetch(self, key, params, ttl, transform=lambda d: d):
        hit = self.cache.get(key)
        if hit and self.clock() - hit.get("at", 0) < ttl:
            return hit["data"]
        try:
            r = self.session.get(BASE, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = transform(r.json())
            self.calls += 1
            self.cache[key] = {"at": self.clock(), "data": data}
            return data
        except Exception as exc:
            self.errors.append(f"{key}: {type(exc).__name__}: {str(exc)[:160]}")
            if hit:
                return hit["data"]
            raise RuntimeError("ESPN unavailable and nothing cached: " + self.errors[-1])

    def current(self) -> tuple[int, int]:
        """(season year, current regular-season week)."""
        d = self._fetch("current", {"groups": FBS_GROUP, "limit": 1}, 3600,
                        lambda j: {"season": j.get("season"), "week": j.get("week")})
        season = int(_get(d, "season", "year") or 0)
        week = int(_get(d, "week", "number") or 1)
        if _get(d, "season", "type") not in (None, 2):     # bowls / offseason: use last regular week
            week = 16
        return season, week

    def week(self, season: int, week: int, *, final: bool = False) -> tuple[list[dict], dict]:
        ttl = 3 * 86400 if final else 3600

        def parse(j):
            games, lines = [], {}
            for ev in j.get("events") or []:
                parsed = parse_event(ev)
                if not parsed:
                    continue
                g, gl = parsed
                g["season"] = g.get("season") or season
                g["week"] = g.get("week") or week
                games.append(g)
                if gl:
                    lines[g["id"]] = gl
            return {"games": games, "lines": lines}

        d = self._fetch(f"{season}:{week}", {"dates": season, "seasontype": 2, "week": week,
                                              "groups": FBS_GROUP, "limit": 500}, ttl, parse)
        return d["games"], d["lines"]

    def season(self, season: int, through_week: int) -> tuple[list[dict], dict]:
        games, lines = [], {}
        for w in range(1, through_week + 1):
            gs, ls = self.week(season, w, final=w < through_week - 1)
            games += gs
            lines.update(ls)
        return games, lines

    def save(self):
        self.path.write_text(json.dumps(self.cache))
