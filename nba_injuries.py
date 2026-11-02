"""NBA injury report from ESPN's free feed (no key), for the Bet Queue's prop check (Nov 1, 2026).

Why: the NBA props model doesn't know who's hurt, so a "huge edge" can just be a player who's already
ruled out. Before an NBA prop gets into the Bet Queue:
  * Out / Doubtful / Suspended -> it isn't queued at all.
  * Questionable / Day-To-Day / Game-Time Decision -> queued on paper, but never sent as a phone alert.
  * Feed unreachable -> queued on paper, never alerted (fail safe: no injury check, no buzz).

Only the player's own status is checked. Teammates' injuries also move props; that's not covered.
"""
from __future__ import annotations

import re
import unicodedata

import requests

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
BLOCK = ("out", "doubtful", "suspended", "suspension", "inactive")
WARN = ("questionable", "day-to-day", "day to day", "game time", "game-time", "gtd")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’`-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _status(item: dict) -> str:
    st = item.get("status")
    if isinstance(st, dict):
        st = st.get("name") or st.get("type") or st.get("description")
    if not st:
        t = item.get("type")
        st = t.get("description") or t.get("name") if isinstance(t, dict) else t
    return str(st or "").strip()


def parse(payload) -> dict[str, str]:
    """ESPN injuries JSON -> {normalized player name: status}. Walks the whole document so a small
    change in ESPN's nesting doesn't silently empty the report."""
    out: dict[str, str] = {}

    def walk(x):
        if isinstance(x, dict):
            ath = x.get("athlete")
            if isinstance(ath, dict) and (ath.get("displayName") or ath.get("fullName")):
                status = _status(x)
                if status:
                    out[norm(ath.get("displayName") or ath.get("fullName"))] = status
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(payload)
    return out


def fetch(session=None, timeout: int = 15) -> dict[str, str] | None:
    """{player: status}, or None when the feed can't be read (callers must treat None as 'unknown')."""
    try:
        r = (session or requests).get(URL, timeout=timeout)
        if getattr(r, "status_code", 500) != 200:
            return None
        rep = parse(r.json())
        return rep if rep else None     # an empty report is far more likely a feed problem than a healthy league
    except Exception:
        return None


def check(player: str, report: dict[str, str] | None) -> str:
    """'block', 'warn', 'ok', or 'unknown' (no report)."""
    if report is None:
        return "unknown"
    st = report.get(norm(player), "").lower()
    if not st or "probable" in st or "available" in st:
        return "ok"
    if any(b in st for b in BLOCK):
        return "block"
    if any(w in st for w in WARN):
        return "warn"
    return "warn"     # listed with a status we don't recognize: don't buzz
