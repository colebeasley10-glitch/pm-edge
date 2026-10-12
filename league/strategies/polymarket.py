"""Polymarket vs Kalshi: same game, two prices.

Polymarket's sports moneylines are deep and free to read. When the same game
on Kalshi can be bought for less than Polymarket's mid-price (after Kalshi's
fee), we take the Kalshi side on paper. Only liquid, tight Polymarket markets
count as a reference, and both teams plus the date must match.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import requests
from rapidfuzz import fuzz

from league.book import Pick, f, parse_dt

NAME = "polymarket"
DIAG: dict = {}
MIN_EDGE = 0.015
MAX_PICKS = 10
MIN_LIQUIDITY = 5000.0
MAX_POLY_SPREAD = 0.03
GAMMA = "https://gamma-api.polymarket.com"
GAME_SERIES_RE = re.compile(r"^KX[A-Z0-9]*(GAME|MATCH|FIGHT)$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
_KDATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})?([A-Z0-9]+)$")
_DROP = {"fc", "cf", "sc", "ac", "afc", "the", "club", "de", "cd", "ud", "united", "city", "vs"}


def norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower().replace("st.", "state"))
    return " ".join(t for t in s.split() if t not in _DROP)


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b) * 0.95)


def halves(title: str) -> list[str] | None:
    t = re.sub(r"^(game \d+:|contender series:)\s*", "", str(title or ""), flags=re.I)
    parts = re.split(r"\s+vs\.?\s+|\s+@\s+|\s+at\s+", t, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    second = re.split(r"\s+-\s+|\s+\(", parts[1])[0]
    return [parts[0].strip(), second.strip()]


def mid(m: dict) -> tuple[float | None, float | None]:
    bid, ask = f(m.get("bestBid")), f(m.get("bestAsk"))
    if bid is None or ask is None or not (0 < bid <= ask < 1):
        return None, None
    return (bid + ask) / 2.0, ask - bid


def _list(value):
    if isinstance(value, list):
        return value
    try:
        return json.loads(value or "[]")
    except Exception:
        return []


def poly_games(events: list[dict], now: datetime) -> list[dict]:
    """Flatten Polymarket events into games with a fair probability per team."""
    games = []
    for e in events:
        sides = halves(e.get("title") or "")
        if not sides or "more markets" in str(e.get("title") or "").lower():
            continue
        teams: dict[str, float] = {}
        start = None
        for m in e.get("markets") or []:
            if m.get("sportsMarketType") != "moneyline":
                continue
            start = start or parse_dt(str(m.get("gameStartTime") or "").replace(" ", "T").replace("+00", "+00:00"))
            p, spread = mid(m)
            if p is None or spread > MAX_POLY_SPREAD or (f(m.get("liquidityNum"), 0.0) or 0.0) < MIN_LIQUIDITY:
                continue
            outs = [str(x) for x in _list(m.get("outcomes"))]
            if len(outs) == 2 and outs[0].lower() != "yes":
                teams[outs[0]] = p
                teams[outs[1]] = 1.0 - p
            elif outs[:1] == ["Yes"] and "draw" not in str(m.get("groupItemTitle") or "").lower():
                teams[str(m.get("groupItemTitle") or "")] = p
        if teams and start and start > now + timedelta(minutes=15):
            games.append({"title": e.get("title"), "sides": sides, "teams": teams, "start": start})
    return games


def kalshi_start(event_ticker: str) -> tuple[datetime, float] | None:
    """Kalshi game start from the ticker and how sure we are of it (window in hours).

    Tickers carry the US Eastern date, sometimes with HHMM: 26OCT122000.
    """
    from zoneinfo import ZoneInfo
    m = _KDATE.search(str(event_ticker or "").upper())
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        y, mo, d = 2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
        if m.group(4):
            hh, mm = int(m.group(4)[:2]), int(m.group(4)[2:])
            local = datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))
            return local.astimezone(timezone.utc), 4.0
        local = datetime(y, mo, d, 15, 0, tzinfo=ZoneInfo("America/New_York"))
        return local.astimezone(timezone.utc), 15.0
    except ValueError:
        return None


def match_game(k_event: dict, games: list[dict]) -> dict | None:
    ks = halves(k_event.get("title") or "")
    ks_start = kalshi_start(k_event.get("event_ticker") or "")
    if not ks or not ks_start:
        return None
    when, window = ks_start
    best, best_key = None, None
    for g in games:
        gap_h = abs((g["start"] - when).total_seconds()) / 3600.0
        if gap_h > window:
            continue
        straight = min(sim(ks[0], g["sides"][0]), sim(ks[1], g["sides"][1]))
        crossed = min(sim(ks[0], g["sides"][1]), sim(ks[1], g["sides"][0]))
        score = max(straight, crossed)
        if score < 72:
            continue
        key = (score, -gap_h)
        if best_key is None or key > best_key:
            best, best_key = g, key
    return best


def team_prob(k_market: dict, k_event: dict, game: dict) -> float | None:
    """Polymarket probability for the team this Kalshi market pays on."""
    label = str(k_market.get("yes_sub_title") or "")
    if label.strip().lower() in {"tie", "draw"}:
        return None
    ks = halves(k_event.get("title") or "") or []
    # which half of the Kalshi title is this market's team? (ticker suffix vs event code)
    code = str(k_market.get("ticker") or "").rsplit("-", 1)[-1]
    ev_code = str(k_event.get("event_ticker") or "").rsplit("-", 1)[-1]
    ev_code = re.sub(r"^\d{2}[A-Z]{3}\d{2}(\d{4})?", "", ev_code)
    if ks and code and ev_code.startswith(code):
        label = f"{ks[0]} {label}"
    elif ks and code and ev_code.endswith(code):
        label = f"{ks[1]} {label}"
    scored = sorted(((sim(label, t), t) for t in game["teams"]), reverse=True)
    if not scored or scored[0][0] < 70:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 8:
        return None  # ambiguous: don't guess which team
    return game["teams"][scored[0][1]]


def fetch_poly_events(session: requests.Session, now: datetime) -> list[dict]:
    out = []
    for page in range(6):
        r = session.get(f"{GAMMA}/events", timeout=25, params={
            "closed": "false", "active": "true", "tag_slug": "sports", "limit": 100, "offset": page * 100,
            "end_date_max": (now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "order": "volume24hr", "ascending": "false"})
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out += batch
    return out


def run(kalshi, now: datetime, session=None) -> list[Pick]:
    s = session or requests.Session()
    raw = fetch_poly_events(s, now)
    games = poly_games(raw, now)
    DIAG.clear()
    DIAG.update({"poly_events": len(raw), "poly_games": len(games), "kalshi_events": 0, "matched": 0,
                 "priced": 0, "best_gaps": []})
    if not games:
        return []
    series = [x.get("ticker") for x in kalshi.series("Sports") if GAME_SERIES_RE.match(str(x.get("ticker") or ""))]
    picks: list[Pick] = []
    for ser in series:
        for ev in kalshi.open_events(ser, limit=40):
            DIAG["kalshi_events"] += 1
            g = match_game(ev, games)
            if not g:
                continue
            DIAG["matched"] += 1
            for m in ev["markets"]:
                p = team_prob(m, ev, g)
                if p is None:
                    continue
                DIAG["priced"] += 1
                ya = m.get("yes_ask")
                if ya is not None:
                    DIAG["best_gaps"].append((round(p - ya, 3), m.get("ticker")))
                for side, fair in (("YES", p), ("NO", 1.0 - p)):
                    pk = Pick(NAME, m, side, fair,
                              f"Polymarket has this at {fair:.0%} ({g['title']})",
                              category="Sports", min_edge=MIN_EDGE,
                              extra={"title": f"{ev.get('title')}: {m.get('yes_sub_title')} wins"
                                     if side == "YES" else f"{ev.get('title')}: {m.get('yes_sub_title')} does not win"})
                    e = pk.edge()
                    if e is not None and e >= MIN_EDGE:
                        picks.append(pk)
    DIAG["best_gaps"] = sorted(DIAG["best_gaps"], reverse=True)[:8]
    picks.sort(key=lambda p: -(p.edge() or 0))
    return picks[:MAX_PICKS]
