from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import csv
from rapidfuzz.fuzz import ratio, token_set_ratio

from .utils import normalize_text, parse_iso


@dataclass(frozen=True)
class EventMatch:
    event: dict | None
    confidence: float
    reason: str


def load_aliases(path: str = "data/team_aliases.csv") -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a = normalize_text(row.get("alias", ""))
            c = normalize_text(row.get("canonical", ""))
            if a and c:
                out[a] = c
    return out


def canon(name: str, aliases: dict[str, str]) -> str:
    n = normalize_text(name)
    return aliases.get(n, n)


def team_similarity(a: str, b: str, aliases: dict[str, str]) -> float:
    x, y = canon(a, aliases), canon(b, aliases)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    if x in y or y in x:
        return 0.96

    xt, yt = x.split(), y.split()
    # Handles labels such as "New York M" vs "New York Mets".
    if len(xt) == len(yt) and len(xt) >= 2 and xt[:-1] == yt[:-1]:
        if xt[-1][0] == yt[-1][0]:
            return 0.93

    return max(ratio(x, y), token_set_ratio(x, y)) / 100.0


def _pair_score(a: str, b: str, home: str, away: str, aliases: dict[str, str]) -> float:
    direct = (team_similarity(a, home, aliases) + team_similarity(b, away, aliases)) / 2
    reverse = (team_similarity(a, away, aliases) + team_similarity(b, home, aliases)) / 2
    return max(direct, reverse)


def match_event(spec, events: list[dict], aliases: dict[str, str], market_time: str | None = None) -> EventMatch:
    if not spec.participant_a or not spec.participant_b:
        return EventMatch(None, 0.0, "market participants not parsed")

    market_dt = parse_iso(market_time)
    ranked = []
    for e in events:
        home, away = e.get("home_team", ""), e.get("away_team", "")
        pair = _pair_score(spec.participant_a, spec.participant_b, home, away, aliases)
        if pair < 0.55:
            continue

        time_score = 1.0
        event_dt = parse_iso(e.get("commence_time"))
        if market_dt and event_dt:
            hours = abs((market_dt - event_dt).total_seconds()) / 3600
            # Kalshi close times can differ from kickoff; tolerate a day, but reward proximity.
            time_score = max(0.35, 1.0 - min(hours, 36) / 48)

        confidence = 0.86 * pair + 0.14 * time_score
        ranked.append((confidence, e, pair, time_score))

    if not ranked:
        return EventMatch(None, 0.0, "no sportsbook event match")

    ranked.sort(key=lambda x: x[0], reverse=True)
    best = ranked[0]
    if len(ranked) > 1 and best[0] - ranked[1][0] < 0.04:
        return EventMatch(None, best[0] * 0.8, "ambiguous sportsbook event match")
    return EventMatch(best[1], best[0], f"team={best[2]:.2f}, time={best[3]:.2f}")
