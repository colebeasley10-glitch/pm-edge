"""Pinnacle fair price for a Trader Follow signal (Nov 1, 2026).

Reads the game rows of the Odds Fam Target Price Board (research_lab/data/ODDS_FAM/board.json,
committed by the Research Lab every ~15 minutes): Pinnacle's no-vig price, blended with the
exchange midpoint, for every Kalshi moneyline and spread line. A Polymarket moneyline or spread
is matched to the same game, team and line. Totals and lines Kalshi doesn't list get no fair price.
Plain JSON + re only, so the Trader Follow workflow needs no extra packages.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from trader_follow.scores import canon

BOARD = Path("research_lab/data/ODDS_FAM/board.json")
SPREAD_Q = re.compile(r"^Spread:\s*(.+?)\s*\(([-+−]?\d+(?:\.\d+)?)\)\s*$")


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_rows(path: Path = BOARD) -> list[dict]:
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        return []
    return [r for r in data.get("rows") or [] if r.get("kind") == "game" and r.get("fair_prob")]


def _num(s: str) -> float:
    return float(str(s).replace("−", "-"))


def _is(nick: str, full: str) -> bool:
    """'Browns' -> 'Cleveland Browns'; also full-name and school-name matches."""
    a, b = canon(nick), canon(full)
    return bool(a) and (a == b or b.endswith(" " + a) or b.startswith(a + " ") or a in b)


def _label_team(label: str, home: str, away: str) -> tuple[str, float | None] | None:
    """'Los Angeles C −3.5' -> ('Los Angeles Chargers', -3.5); 'Denver Broncos ML' -> (.., None)."""
    head, _, last = label.strip().rpartition(" ")
    if last == "ML":
        line = None
    else:
        try:
            line = _num(last)
        except ValueError:
            return None
    pre = canon(head)
    hits = [t for t in (home, away) if canon(t).startswith(pre) or canon(t) == pre]
    return (hits[0], line) if len(hits) == 1 else None


def fair_for(sig: dict, rows: list[dict]) -> float | None:
    start = _dt(sig.get("game_start"))
    outcome, q = sig.get("outcome") or "", sig.get("question") or ""
    if not start or not outcome or "O/U" in q:
        return None
    m = SPREAD_Q.match(q)
    want_line = None
    if m:
        fav, line = m.group(1), _num(m.group(2))
        want_line = line if _is(fav, outcome) or _is(outcome, fav) else -line
    for r in rows:
        rs = _dt(r.get("starts"))
        if not rs or abs(rs - start) > timedelta(hours=3):
            continue
        away, _, home = (r.get("game") or "").partition(" at ")
        team = next((t for t in (home, away) if _is(outcome, t)), None)
        if not team:
            continue
        lt = _label_team(r.get("pick") or "", home, away)
        if not lt or lt[0] != team:
            continue
        if m and r.get("market") == "spread" and lt[1] is not None and abs(lt[1] - want_line) < 1e-9:
            return float(r["fair_prob"])
        if not m and r.get("market") == "moneyline" and lt[1] is None:
            return float(r["fair_prob"])
    return None


def ev(fair: float | None, price: float | None) -> float | None:
    """EV per $1 buying at price (cents as 0-1) when the true chance is fair. Fees not included."""
    if not fair or not price or not 0 < price < 1:
        return None
    return fair / price - 1
