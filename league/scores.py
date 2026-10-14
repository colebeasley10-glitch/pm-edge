"""Live scores for open bets, from ESPN's free public scoreboard (no key).

Given a Kalshi market (ticker + title) this finds the matching game on ESPN and
returns the score, clock and state, so the dashboard can show a bet like Kalshi does:
"LIVE  DEL 23  26 MTU  Q4 · 12:35".

Covered: NFL, college football, NBA, WNBA, college basketball, MLB, NHL and the
main soccer leagues. Tennis, cricket and esports aren't on ESPN's scoreboard, so
those return None and the dashboard shows the bet without a score.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"

# Kalshi series (after "KX") -> ESPN sport/league path. Longest prefix wins.
LEAGUES = {
    "NCAAF": "football/college-football",
    "NFL": "football/nfl",
    "WNBA": "basketball/wnba",
    "NCAAMB": "basketball/mens-college-basketball",
    "NCAAWB": "basketball/womens-college-basketball",
    "NBA": "basketball/nba",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
    "UCL": "soccer/uefa.champions",
    "UEL": "soccer/uefa.europa",
    "UECL": "soccer/uefa.europa.conf",
    "AFCCL": "soccer/afc.champions",
    "EPL": "soccer/eng.1",
    "LALIGA": "soccer/esp.1",
    "SERIEA": "soccer/ita.1",
    "BUNDESLIGA": "soccer/ger.1",
    "LIGUE1": "soccer/fra.1",
    "MLS": "soccer/usa.1",
    "EFLCH": "soccer/eng.2",
    "EFLL1": "soccer/eng.3",
    "EFLL2": "soccer/eng.4",
    "SAUDIPL": "soccer/ksa.1",
    "LIGAMX": "soccer/mex.1",
    "EREDIVISIE": "soccer/ned.1",
    "LIGAPORTUGAL": "soccer/por.1",
}

# Kalshi team codes that differ from ESPN's abbreviation (Kalshi -> ESPN).
ALIASES = {
    "GSW": "GS", "NYK": "NY", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH",
    "JAC": "JAX", "CWS": "CHW", "WSN": "WSH", "TBR": "TB", "SDP": "SD", "SFG": "SF", "KCR": "KC",
}

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_EVENT_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})?([A-Z0-9]+)$")


def league_path(ticker: str) -> str | None:
    series = str(ticker or "").split("-", 1)[0].upper()
    series = series[2:] if series.startswith("KX") else series
    for key in sorted(LEAGUES, key=len, reverse=True):
        if series.startswith(key):
            return LEAGUES[key]
    return None


def parse_event(ticker: str) -> tuple[date, str] | None:
    """('KXNCAAFGAME-26OCT13FIUJVST-JVST') -> (2026-10-13, 'FIUJVST')."""
    parts = str(ticker or "").upper().split("-")
    if len(parts) < 2:
        return None
    m = _EVENT_RE.match(parts[1])
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))), m.group(5)
    except ValueError:
        return None


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\bst\.?(?=\s|$)", "state", t)
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def _team_from_title(title: str) -> str:
    """'Jacksonville St. wins' -> 'jacksonville state'; '' when the title isn't 'X wins'.

    Paper titles can lead with the matchup ('Mugla vs Bursa: Bursa wins'), so that is dropped.
    """
    title = str(title or "").rsplit(": ", 1)[-1]
    m = re.match(r"^(?:yes\s+|no\s+)?(.+?)\s+wins(?:\s+(?:1st half|2nd half|by .*))?\??$", str(title or "").strip(), re.I)
    return norm(m.group(1)) if m else ""


def _codes(team: dict) -> set[str]:
    abbr = str(team.get("abbreviation") or "").upper()
    out = {abbr} if abbr else set()
    out |= {k for k, v in ALIASES.items() if v == abbr}
    return out


def _names(team: dict) -> set[str]:
    return {n for n in (norm(team.get(k, "")) for k in
                        ("displayName", "shortDisplayName", "location", "name", "nickname")) if n}


def _lum(hex6: str) -> float | None:
    h = str(hex6 or "").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return None
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def team_color(team: dict, fallback: str = "#8796AD") -> str:
    """The team's color, readable on a dark background: its main color unless that is near-black
    navy or too close to white, then its alternate color, else a neutral grey."""
    for key in ("color", "alternateColor"):
        c = str(team.get(key) or "").lstrip("#")
        lum = _lum(c)
        if lum is not None and 0.045 <= lum <= 0.85:
            return "#" + c.upper()
    return fallback


def _game(ev: dict) -> dict | None:
    comp = (ev.get("competitions") or [{}])[0]
    teams = comp.get("competitors") or []
    if len(teams) != 2:
        return None
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[0])
    home = next((t for t in teams if t is not away), teams[1])
    st = (ev.get("status") or comp.get("status") or {})
    typ = st.get("type") or {}
    return {"away": away, "home": home, "state": str(typ.get("state") or ""),
            "detail": str(typ.get("shortDetail") or typ.get("detail") or ""),
            "completed": bool(typ.get("completed")), "start": ev.get("date"),
            "name": ev.get("name") or ev.get("shortName") or ""}


def _score(g: dict, team_code: str, title_team: str, event_code: str) -> int:
    a, h = g["away"].get("team") or {}, g["home"].get("team") or {}
    ac, hc = _codes(a), _codes(h)
    s = 0
    if any(x + y == event_code or y + x == event_code for x in ac for y in hc):
        s += 3
    if team_code and team_code in (ac | hc):
        s += 1
    if title_team and any(title_team == n or title_team in n.split(" ") or n.startswith(title_team + " ")
                          or title_team.startswith(n + " ") for n in (_names(a) | _names(h))):
        s += 2
    return s


def match(ticker: str, title: str, events: list[dict]) -> dict | None:
    """Best ESPN game for a Kalshi market, or None if nothing matches clearly."""
    parsed = parse_event(ticker)
    if not parsed:
        return None
    _, event_code = parsed
    parts = str(ticker).upper().split("-")
    team_code = parts[2] if len(parts) > 2 and parts[2].isalpha() else ""
    title_team = _team_from_title(title)
    scored = []
    for ev in events:
        g = _game(ev)
        if g:
            scored.append((_score(g, team_code, title_team, event_code), g))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < 2:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None  # ambiguous: two games look equally right
    g = scored[0][1]
    a, h = g["away"].get("team") or {}, g["home"].get("team") or {}
    mine = None
    if title_team:
        if title_team in " ".join(_names(a)) or (team_code and team_code in _codes(a)):
            mine = "away"
        elif title_team in " ".join(_names(h)) or (team_code and team_code in _codes(h)):
            mine = "home"
    return {
        "state": g["state"],  # pre / in / post
        "detail": g["detail"],
        "completed": g["completed"],
        "start": g["start"],
        "away": a.get("abbreviation") or a.get("shortDisplayName") or "",
        "home": h.get("abbreviation") or h.get("shortDisplayName") or "",
        "away_name": a.get("location") or a.get("shortDisplayName") or a.get("displayName") or "",
        "home_name": h.get("location") or h.get("shortDisplayName") or h.get("displayName") or "",
        "away_color": team_color(a),
        "home_color": team_color(h),
        "away_score": str(g["away"].get("score") or "0"),
        "home_score": str(g["home"].get("score") or "0"),
        "mine": mine,
    }


def fetch_events(path: str, day: date, get: Callable | None = None) -> list[dict]:
    """ESPN scoreboard events for one league and date. Network errors return []."""
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(URL.format(path=path), params={"dates": day.strftime("%Y%m%d"), "limit": 400}, timeout=5)
        r.raise_for_status()
        return list((r.json() or {}).get("events") or [])
    except Exception:
        return []


def for_bets(rows: list[dict], fetch: Callable[[str, date], list[dict]] | None = None) -> dict[str, dict]:
    """{ticker: score} for each row whose game ESPN covers. One request per league and date."""
    fetch = fetch or fetch_events
    cache: dict[tuple[str, date], list[dict]] = {}
    out: dict[str, dict] = {}
    for r in rows:
        t = str(r.get("ticker") or "")
        path, parsed = league_path(t), parse_event(t)
        if not path or not parsed:
            continue
        key = (path, parsed[0])
        if key not in cache:
            cache[key] = fetch(path, parsed[0])
        g = match(t, str(r.get("title") or ""), cache[key])
        if g:
            out[t] = g
    return out


TAGS = [("NCAAF", "NCAAF"), ("NFL", "NFL"), ("WNBA", "WNBA"), ("NCAAMB", "NCAAB"), ("NCAAWB", "NCAAW"),
        ("NBA", "NBA"), ("MLB", "MLB"), ("NHL", "NHL"), ("UCL", "UCL"), ("UEL", "UEL"), ("EPL", "EPL"),
        ("LALIGA", "LA LIGA"), ("SERIEA", "SERIE A"), ("BUNDESLIGA", "BUNDESLIGA"), ("LIGUE1", "LIGUE 1"),
        ("MLS", "MLS"), ("ATP", "ATP"), ("WTA", "WTA"), ("ITFW", "ITF"), ("ITF", "ITF"),
        ("HIGH", "WEATHER"), ("LOW", "WEATHER"), ("BTC", "CRYPTO"), ("ETH", "CRYPTO"),
        ("CS2", "ESPORTS"), ("DOTA", "ESPORTS"), ("LOL", "ESPORTS"), ("VALORANT", "ESPORTS"),
        ("T20", "CRICKET"), ("ODI", "CRICKET"), ("TEST", "CRICKET"), ("UFC", "UFC"), ("PGA", "GOLF")]


def league_tag(ticker: str) -> str:
    """Short label for a market's league ('KXNCAAFGAME-…' -> 'NCAAF'); soccer and others fall back."""
    series = str(ticker or "").split("-", 1)[0].upper()
    series = series[2:] if series.startswith("KX") else series
    for key, tag in TAGS:
        if series.startswith(key):
            return tag
    if league_path(ticker or "") and "soccer" in (league_path(ticker) or ""):
        return "SOCCER"
    return "SOCCER" if series.endswith("GAME") else series[:8]


def start_label(g: dict) -> str:
    try:
        dt = datetime.fromisoformat(str(g.get("start")).replace("Z", "+00:00")).astimezone(EASTERN)
        return dt.strftime("%-I:%M %p").replace(":00 ", " ")
    except Exception:
        return ""
