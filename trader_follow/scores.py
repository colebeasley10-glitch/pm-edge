"""Settle Trader Follow signals from final scores (ESPN's free scoreboard, no key).

Polymarket can take a day or more to officially resolve a game market after it ends,
which left finished bets showing as open. Once ESPN marks the game final, this decides
the bet from the score; Polymarket's own resolution is still used whenever it's there.

Handles: moneyline (team outcome or "Will X win on ...?" Yes/No, and "...draw?"),
spreads ("Spread: Knicks (-4.5)") and totals ("... O/U 220.5"). Anything it can't
read confidently returns None and waits for Polymarket.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Callable

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
# Polymarket slug prefix -> ESPN sport/league path
LEAGUES = {
    "nba": "basketball/nba", "wnba": "basketball/wnba", "cbb": "basketball/mens-college-basketball",
    "nfl": "football/nfl", "cfb": "football/college-football", "ncaaf": "football/college-football",
    "nhl": "hockey/nhl", "mlb": "baseball/mlb",
    "epl": "soccer/eng.1", "ucl": "soccer/uefa.champions", "uel": "soccer/uefa.europa",
    "uecl": "soccer/uefa.europa.conf", "lal": "soccer/esp.1", "sea": "soccer/ita.1", "bun": "soccer/ger.1",
    "fl1": "soccer/fra.1", "mls": "soccer/usa.1", "ere": "soccer/ned.1", "por": "soccer/por.1",
    "efl": "soccer/eng.2", "facup": "soccer/eng.fa",
}
STOP = {"fc", "cf", "sc", "ac", "afc", "club", "de", "the", "city", "united", "real", "sporting", "and"}
MATCH_WINDOW = timedelta(hours=3)


def canon(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("munchen", "munich").replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def _names(team: dict) -> list[str]:
    return [canon(team.get(k)) for k in ("displayName", "shortDisplayName", "name", "location", "abbreviation")
            if team.get(k)]


def team_matches(name: str, team: dict) -> bool:
    n = canon(name)
    if not n:
        return False
    names = _names(team)
    if any(n == x or (len(n) >= 4 and n in x) or (len(x) >= 4 and x in n) for x in names):
        return True
    tokens = {t for t in n.split() if len(t) >= 4 and t not in STOP}
    return bool(tokens) and any(tokens & {t for t in x.split() if t not in STOP} for x in names)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_events(payload: dict) -> list[dict]:
    out = []
    for ev in payload.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            continue
        status = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        out.append({"start": ev.get("date"), "final": bool(status.get("completed")),
                    "teams": [{"team": teams[s].get("team") or {}, "score": _num(teams[s].get("score"))}
                              for s in ("home", "away")]})
    return out


def _parse_dt(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def key_team(row: dict) -> str | None:
    q, outcome = str(row.get("question") or ""), str(row.get("outcome") or "")
    m = re.match(r"\s*will (.+?) win\b", q, re.I)
    if m:
        return m.group(1)
    m = re.match(r"\s*spread:\s*(.+?)\s*\(", q, re.I)
    if m:
        return m.group(1)
    if outcome.lower() not in {"yes", "no", "over", "under"}:
        return outcome
    m = re.match(r"\s*(.+?)\s+vs\.?\s", q, re.I)        # totals: "76ers vs. Knicks: O/U 220.5"
    return m.group(1) if m else None


def find_game(row: dict, events: list[dict]) -> tuple[dict, int] | None:
    """(event, index of the key team) for the one ESPN game matching this signal."""
    start, team = _parse_dt(row.get("game_start")), key_team(row)
    if start is None or not team:
        return None
    hits = []
    for ev in events:
        es = _parse_dt(ev["start"])
        if es is None or abs(es - start) > MATCH_WINDOW:
            continue
        idx = [i for i, t in enumerate(ev["teams"]) if team_matches(team, t["team"])]
        if len(idx) == 1:
            hits.append((ev, idx[0]))
    return hits[0] if len(hits) == 1 else None


def decide(row: dict, ev: dict, i: int) -> str | None:
    """'won' / 'lost' / 'void' for the side the trader bought, or None if unsure."""
    if not ev["final"]:
        return None
    me, opp = ev["teams"][i]["score"], ev["teams"][1 - i]["score"]
    if me is None or opp is None:
        return None
    q, outcome, mtype = str(row.get("question") or ""), str(row.get("outcome") or ""), str(row.get("market_type") or "")
    yes = outcome.lower() == "yes"
    if outcome.lower() in {"yes", "no"}:
        if "draw" in q.lower():
            hit = me == opp
        elif re.match(r"\s*will .+? win\b", q, re.I):
            hit = me > opp
        else:
            return None
        return "won" if hit == yes else "lost"
    if mtype == "spreads" or q.lower().startswith("spread"):
        m = re.search(r"\(([+-]?\d+(?:\.\d+)?)\)", q)
        if not m:
            return None
        cover = (me - opp) + float(m.group(1))       # me = the team named in the question
        if cover == 0:
            return "void"
        named_bought = team_matches(outcome, ev["teams"][i]["team"])
        return "won" if (cover > 0) == named_bought else "lost"
    if outcome.lower() in {"over", "under"}:
        m = re.search(r"o/u\s*(\d+(?:\.\d+)?)", q, re.I)
        if not m:
            return None
        diff = me + opp - float(m.group(1))
        if diff == 0:
            return "void"
        return "won" if (diff > 0) == (outcome.lower() == "over") else "lost"
    if mtype in {"moneyline", ""}:                    # outcome is a team name
        if me == opp:
            return "lost"                              # a draw loses a team-to-win bet
        return "won" if me > opp else "lost"
    return None


def fetch_scoreboard(path: str, day: str, session=requests) -> dict:
    r = session.get(ESPN.format(path=path), params={"dates": day, "limit": 300}, timeout=20)
    r.raise_for_status()
    return r.json()


class ScoreResolver:
    """Callable(row) -> 'won'/'lost'/'void'/None, fetching each league-day once per round."""

    def __init__(self, fetch: Callable[[str, str], dict] = fetch_scoreboard):
        self.fetch, self.cache, self.errors = fetch, {}, []

    def events(self, path: str, start: datetime) -> list[dict]:
        out = []
        for d in {start.strftime("%Y%m%d"), (start - timedelta(hours=8)).strftime("%Y%m%d")}:
            key = (path, d)
            if key not in self.cache:
                try:
                    self.cache[key] = parse_events(self.fetch(path, d))
                except Exception as exc:
                    self.errors.append(f"ESPN {path} {d}: {type(exc).__name__}")
                    self.cache[key] = []
            out += self.cache[key]
        return out

    def __call__(self, row: dict) -> str | None:
        code = str(row.get("event_slug") or row.get("event") or row.get("sport") or "").split("-")[0].lower()
        path = LEAGUES.get(code) or LEAGUES.get(str(row.get("sport") or "").lower())
        start = _parse_dt(row.get("game_start"))
        if not path or start is None:
            return None
        hit = find_game(row, self.events(path, start))
        return decide(row, *hit) if hit else None
