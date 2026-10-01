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
    # Shadow-only validation of the chosen event (never affects acceptance).
    shadow: dict | None = None


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
    try:
        shadow = shadow_validate_match(spec, best[1], aliases, market_time)
    except Exception as exc:  # shadow must never break production matching
        shadow = {"matcher_shadow_pass": None, "matcher_shadow_reason": f"shadow_error:{type(exc).__name__}"}
    return EventMatch(best[1], best[0], f"team={best[2]:.2f}, time={best[3]:.2f}", shadow)


# ---------------------------------------------------------------------------
# Shadow-only match validation (logged next to production matches; never used
# to accept or reject a match).
#
# team_similarity() scores any substring as 0.96, so "Georgia" vs
# "Georgia Tech" or "Miami" vs "Miami (OH)" look like near-perfect matches.
# The shadow validator requires a token-boundary match and treats extra
# school/city qualifier tokens on the longer name as a different team.

SHADOW_MATCHER_VERSION = "matcher_shadow_v1"
_SCHOOL_QUALIFIERS = {
    "tech", "state", "a", "m", "and", "southern", "northern", "eastern",
    "western", "central", "international", "christian", "atlantic", "poly",
    "polytechnic", "oh", "ohio", "fl", "florida", "north", "south", "east",
    "west", "city", "upstate", "pacific", "methodist", "baptist",
}
_MATCH_TZ = None


def _match_tz():
    global _MATCH_TZ
    if _MATCH_TZ is None:
        from zoneinfo import ZoneInfo
        _MATCH_TZ = ZoneInfo("America/New_York")
    return _MATCH_TZ


def _align(short: list[str], long: list[str]):
    """Align short tokens, in order, onto long tokens (gaps allowed).

    Each short token must be one of:
      * the identical long token;
      * an initialism of 2-3 consecutive long tokens ("ny" -> "new york");
      * a 3-4 letter team code that prefixes one long token ("pit" -> "pittsburgh");
      * as the final token, a single initial ("new york m" -> "new york mets").
    Returns (covered_indexes, used_abbreviation, exact_long_tokens) or None.
    Skipped long tokens are judged by the caller (qualifiers fail).
    """
    def walk(i, j, covered, abbrev, exact):
        if i == len(short):
            return covered, abbrev, exact
        tok = short[i]
        for jj in range(j, len(long)):
            word = long[jj]
            if tok == word:
                got = walk(i + 1, jj + 1, covered + [jj], abbrev, exact + (1 if len(word) >= 3 else 0))
                if got:
                    return got
            for k in (2, 3):
                run = long[jj:jj + k]
                if len(run) == k and len(tok) == k and "".join(w[0] for w in run) == tok:
                    got = walk(i + 1, jj + k, covered + list(range(jj, jj + k)), True, exact)
                    if got:
                        return got
            if 3 <= len(tok) <= 4 and len(word) > len(tok) and word.startswith(tok):
                got = walk(i + 1, jj + 1, covered + [jj], True, exact)
                if got:
                    return got
            if len(tok) == 1 and i == len(short) - 1 and word.startswith(tok):
                return covered + [jj], True, exact
        return None

    return walk(0, 0, [], False, 0)


def shadow_team_match(a: str, b: str, aliases: dict[str, str]) -> tuple[float, str]:
    """Token-boundary team comparison: (score, reason). Credible when >= 0.90."""
    x, y = canon(a, aliases), canon(b, aliases)
    if not x or not y:
        return 0.0, "missing_name"
    if x == y:
        return 1.0, "exact"
    xt, yt = x.split(), y.split()
    short, long = (xt, yt) if len(xt) <= len(yt) else (yt, xt)
    aligned = _align(short, long)
    if aligned is None:
        return 0.0, "no_token_boundary_match"
    covered, abbrev, exact = aligned
    covered = set(covered)
    extra = [tok for idx, tok in enumerate(long) if idx not in covered]
    qualifiers = [tok for tok in extra if tok in _SCHOOL_QUALIFIERS]
    if qualifiers:
        return 0.5, "qualifier_token_mismatch:" + "+".join(qualifiers)
    if abbrev:
        # A code alone ("pit") is too weak; also require a full shared word,
        # unless the abbreviation covers the whole name ("ny" == "new york").
        if exact == 0 and extra:
            return 0.85, "abbreviation_without_full_word"
        return 0.90, "abbreviation"
    return (1.0 if not extra else 0.95), ("token_exact" if not extra else "token_subset")


def _local_date(value):
    dt = parse_iso(value) if isinstance(value, str) or value is None else value
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Fail closed on naive timestamps: do not guess a zone.
        return None
    return dt.astimezone(_match_tz()).date()


def shadow_validate_match(spec, event: dict | None, aliases: dict[str, str],
                          market_time: str | None = None) -> dict:
    """Shadow validation of an accepted production match. Logging only."""
    out = {
        "matcher_shadow_version": SHADOW_MATCHER_VERSION,
        "matcher_shadow_pass": False,
        "matcher_shadow_reason": "",
        "matcher_shadow_team_score": 0.0,
        "matcher_shadow_date_match": None,
    }
    if not event or not getattr(spec, "participant_a", None) or not getattr(spec, "participant_b", None):
        out["matcher_shadow_reason"] = "missing_participants"
        return out
    home, away = str(event.get("home_team", "")), str(event.get("away_team", ""))
    best = None
    for first, second in ((home, away), (away, home)):
        s1, r1 = shadow_team_match(spec.participant_a, first, aliases)
        s2, r2 = shadow_team_match(spec.participant_b, second, aliases)
        cand = (min(s1, s2), r1, r2)
        if best is None or cand[0] > best[0]:
            best = cand
    team_score, r1, r2 = best
    out["matcher_shadow_team_score"] = round(team_score, 4)
    market_day = _local_date(market_time)
    event_day = _local_date(event.get("commence_time"))
    date_match = None if market_day is None or event_day is None else market_day == event_day
    out["matcher_shadow_date_match"] = date_match
    if team_score < 0.90:
        weak = r1 if r1 not in {"exact", "token_exact", "token_subset", "abbreviation"} else r2
        out["matcher_shadow_reason"] = f"team:{weak}"
    elif date_match is False:
        out["matcher_shadow_reason"] = "date_mismatch"
    else:
        out["matcher_shadow_pass"] = True
        out["matcher_shadow_reason"] = "ok" if date_match else "ok_date_unverified"
    return out
