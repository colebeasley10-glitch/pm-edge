"""Heavy-flow Sharp: a paper-only forward test of one filter on the Sharp strategy.

Idea (from the paper Sharp history before START): picks backed by more whale trades did
better (120+ trades +23% ROI, under 30 trades -4%). Testing an idea on the same bets that
suggested it proves nothing, so this only scores Sharp picks opened from START on.

No new bets or orders: it is a filter over the league's existing paper Sharp ledger, so the
heavy and light groups get identical prices and fills and the comparison is fair.
"""
from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timezone
from pathlib import Path

START = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)  # 11:00 ET, Oct 19 2026
MIN_TRADES = 60
DECIDE_AT = 150  # settled heavy-flow bets before calling it

_TRADES = re.compile(r"\((\d+) trades\)")


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dt(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def flow_trades(row: dict) -> int | None:
    m = _TRADES.search(str(row.get("reason") or ""))
    return int(m.group(1)) if m else None


def load(path: str | Path = "league_trades.csv") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("strategy") == "sharp"]


def stats(rows: list[dict]) -> dict:
    """Settled count, stake-weighted ROI with a 95% range, P/L; plus open count."""
    done = [r for r in rows if r.get("status") in {"won", "lost"}]
    pairs = []
    for r in done:
        c = (_f(r.get("cost")) or 0.0) + (_f(r.get("fee")) or 0.0)
        if c > 0:
            pairs.append((c, _f(r.get("pl")) or 0.0))
    n = len(pairs)
    staked = sum(c for c, _ in pairs)
    pl = sum(p for _, p in pairs)
    roi = pl / staked if staked else None
    half = None
    if n >= 2 and staked:
        mc = staked / n
        half = 1.96 * math.sqrt(sum(((p - roi * c) / mc) ** 2 for c, p in pairs) / (n - 1) / n)
    return {"n": n, "open": sum(1 for r in rows if r.get("status") == "open"),
            "pl": round(pl, 2), "staked": round(staked, 2), "roi": roi, "roi_half": half}


def compare(rows: list[dict], *, since: datetime | None = START, before: datetime | None = None) -> dict:
    sel = []
    for r in rows:
        t = _dt(r.get("opened_at"))
        if t is None or (since and t < since) or (before and t >= before):
            continue
        sel.append(r)
    heavy = [r for r in sel if (flow_trades(r) or 0) >= MIN_TRADES]
    light = [r for r in sel if (flow_trades(r) or 0) < MIN_TRADES]
    return {"all": stats(sel), "heavy": stats(heavy), "light": stats(light)}


def verdict(cmp: dict) -> tuple[str, str]:
    h, a = cmp["heavy"], cmp["all"]
    if h["n"] < DECIDE_AT:
        return (f"Collecting — {h['n']} of {DECIDE_AT} heavy-flow bets settled since the test started.", "wait")
    if h["roi"] is not None and a["roi"] is not None and h["roi"] > 0 and h["roi"] > a["roi"]:
        return ("Heavy flow is beating regular Sharp on new bets — worth switching the live bot "
                f"to {MIN_TRADES}+ trades only.", "good")
    return ("Heavy flow isn't beating regular Sharp on new bets — keep live Sharp as it is.", "bad")
