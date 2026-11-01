"""Live status for in-progress paper plays: score, clock, and whether the bet is winning right now.

ESPN's free scoreboard (no key) for the game and its live box score for player props. Read only;
nothing here settles a bet (the lanes still settle from official results). A bet is described by a
small dict built in app.open_plays():
  {"league": "NFL", "kind": "ml" | "spread" | "total" | "prop", "team": "Browns", "line": 3.5,
   "side": "over"/"under" (totals, props), "player": ..., "stat": ..., "teams": ["Browns", "Steelers"],
   "start": iso time}
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from trader_follow.scores import LEAGUES as POLY_LEAGUES, canon, team_matches

ET = ZoneInfo("America/New_York")
BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/{kind}"
PATHS = {"NFL": "football/nfl", "NCAA": "football/college-football", "NCAAF": "football/college-football",
         "NBA": "basketball/nba", "NHL": "hockey/nhl", "MLB": "baseball/mlb"}


def path_for(league: str) -> str | None:
    lg = str(league or "").strip()
    return PATHS.get(lg.upper()) or POLY_LEAGUES.get(lg.lower())


class Live:
    """One instance per page render; caches each scoreboard and box score it fetches."""

    def __init__(self, session=requests, timeout: int = 8):
        self.s, self.timeout, self.cache = session, timeout, {}

    def _get(self, path: str, kind: str, **params) -> dict:
        key = (path, kind, tuple(sorted(params.items())))
        if key not in self.cache:
            try:
                r = self.s.get(BASE.format(path=path, kind=kind), params=params, timeout=self.timeout)
                self.cache[key] = r.json() if r.status_code == 200 else {}
            except Exception:
                self.cache[key] = {}
        return self.cache[key]

    def game(self, bet: dict) -> dict | None:
        path = path_for(bet.get("league"))
        start = _dt(bet.get("start"))
        if not path or not start:
            return None
        names = [n for n in bet.get("teams") or [] if n] or [bet.get("team")]
        best = None
        for day in sorted({start.astimezone(ET).strftime("%Y%m%d"), start.strftime("%Y%m%d")}):
            extra = {"groups": 80} if "college-football" in path else {}
            board = self._get(path, "scoreboard", dates=day, limit=300, **extra)
            for ev in board.get("events") or []:
                comp = (ev.get("competitions") or [{}])[0]
                cs = comp.get("competitors") or []
                hits = sum(any(team_matches(n, c.get("team") or {}) for c in cs) for n in names if n)
                if hits and (best is None or hits > best[0]):
                    best = (hits, ev, comp, cs)
        if not best:
            return None
        _, ev, comp, cs = best
        st_ = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        teams = []
        for c in cs:
            t = c.get("team") or {}
            teams.append({"team": t, "abbr": t.get("abbreviation") or t.get("shortDisplayName") or "?",
                          "score": _num(c.get("score")) or 0.0, "home": c.get("homeAway") == "home"})
        return {"id": ev.get("id"), "path": path, "state": st_.get("state") or "",
                "detail": st_.get("shortDetail") or st_.get("detail") or "", "teams": teams}

    def stat(self, g: dict, player: str, stat: str) -> float | None:
        from research_lab.prop_board import _norm, stat_value
        summ = self._get(g["path"], "summary", event=g["id"])
        want = _norm(player)
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
                return stat_value(stat, cats)
        return None


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _status(x: float) -> str:
    return "winning" if x > 0 else "losing" if x < 0 else "push"


def evaluate(bet: dict, live: Live) -> dict | None:
    """{'state', 'score', 'clock', 'status': winning/losing/push/None, 'note'} or None if the game isn't found."""
    g = live.game(bet)
    if not g or len(g["teams"]) != 2:
        return None
    away, home = sorted(g["teams"], key=lambda t: t["home"])
    out = {"state": g["state"], "clock": g["detail"],
           "score": f"{away['abbr']} {away['score']:g} – {home['abbr']} {home['score']:g}",
           "status": None, "note": ""}
    if g["state"] == "pre":
        return out
    kind, line = bet.get("kind"), _num(bet.get("line"))
    if kind in ("ml", "spread"):
        mine = next((t for t in g["teams"] if team_matches(bet.get("team") or "", t["team"])), None)
        if not mine:
            return out
        other = home if mine is away else away
        margin = mine["score"] - other["score"]
        if kind == "ml":
            out["status"] = _status(margin)
            out["note"] = f"{mine['abbr']} {'up' if margin > 0 else 'down' if margin < 0 else 'tied'}" + \
                (f" {abs(margin):g}" if margin else "")
        elif line is not None:
            cover = margin + line
            out["status"] = _status(cover)
            out["note"] = f"{mine['abbr']} {line:+g}: covering by {cover:g}" if cover > 0 else \
                f"{mine['abbr']} {line:+g}: short by {-cover:g}" if cover < 0 else f"{mine['abbr']} {line:+g}: on the number"
    elif kind == "total" and line is not None:
        tot = away["score"] + home["score"]
        over = bet.get("side") == "over"
        if over:
            out["status"] = "winning" if tot > line else "losing"
            out["note"] = f"{tot:g} of {line:g}" + ("" if tot > line else f", needs {line - tot:g}")
        else:
            out["status"] = "winning" if tot < line else "losing"
            out["note"] = f"{tot:g} of {line:g}" + (f", {line - tot:g} to spare" if tot < line else "")
    elif kind == "prop" and line is not None:
        try:
            v = live.stat(g, bet.get("player") or "", bet.get("stat") or "")
        except Exception:
            v = None
        if v is None:
            out["note"] = "no stats yet"
            return out
        over = bet.get("side") == "over"
        if over:
            out["status"] = "winning" if v > line else "losing"
            out["note"] = f"{v:g} {bet.get('stat', '')} of {line:g}" + ("" if v > line else f", needs {line - v + 0.5:g}")
        else:
            out["status"] = "winning" if v < line else "losing"
            out["note"] = f"{v:g} {bet.get('stat', '')} of {line:g}" + (f", {line - v:g} to spare" if v < line else "")
    if g["state"] == "post" and out["status"] in ("winning", "losing"):
        out["status"] = "won" if out["status"] == "winning" else "lost"
    return out


def describe_polymarket(question: str, outcome: str) -> dict:
    """Trader Follow question -> bet fields (kind, team, line, side, teams)."""
    import re
    q = question or ""
    m = re.match(r"^Spread:\s*(.+?)\s*\(([-+]?\d+(?:\.\d+)?)\)", q)
    if m:
        fav, ln = m.group(1), float(m.group(2))
        same = canon(fav) in canon(outcome) or canon(outcome) in canon(fav)
        return {"kind": "spread", "team": outcome, "line": ln if same else -ln, "teams": [fav, outcome]}
    m = re.search(r"O/U\s*(\d+(?:\.\d+)?)", q)
    teams = [t.strip() for t in re.split(r"\s+vs\.?\s+", q.split(":")[0]) if t.strip()]
    if m:
        return {"kind": "total", "line": float(m.group(1)), "side": "over" if outcome.lower().startswith("o") else "under",
                "teams": teams}
    if outcome.lower() in {"yes", "no", "draw"}:
        return {"kind": "", "teams": teams}
    return {"kind": "ml", "team": outcome, "teams": teams}
