"""Nightly push: one summary of the Eastern day, live $1 vs paper.

Sent by the first league round at or after 11pm Eastern (or, if GitHub skipped
those runs, the first round before 6am for the day before). It covers:

* Live $1 Sharp: orders sent, filled, missed (price moved), blocked by Kalshi,
  bets settled that day and their profit, spend vs the $30 budget, all-time P/L;
* Paper: picks opened and bets settled that day per strategy, Sharp all-time;
* a warning when the Odds API is out of credits (Sportsbooks can't price anything).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from league import book, live_sharp

LABELS = {"sharp": "Sharp", "weather": "Weather", "books": "Sportsbooks", "polymarket": "Polymarket",
          "crypto": "Crypto"}
WATCH_STATE = Path(".pm_edge_watch_state.json")


def target_day(now: datetime, last_sent: str | None) -> str | None:
    """The Eastern day a summary is due for right now, or None."""
    et = now.astimezone(live_sharp.EASTERN)
    if et.hour >= 23:
        day = et.date()
    elif et.hour < 6:
        day = et.date() - timedelta(days=1)
    else:
        return None
    key = day.strftime("%Y%m%d")
    return None if last_sent == key else key


def _et_day(value) -> str | None:
    dt = book.parse_dt(value)
    return live_sharp.et_day(dt) if dt else None


def _money(x: float) -> str:
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):.2f}"


def odds_out_of_credits(state: dict | None) -> bool:
    """True when the main scanner has no sportsbook prices cached and spent nothing today."""
    if not state:
        return False
    return not state.get("fair_cache") and int((state.get("daily_usage") or {}).get("credits") or 0) == 0


def build(day: str, paper: list[dict], live: list[dict], watch_state: dict | None = None) -> tuple[str, list[str]]:
    f = book.f
    # ---- live $1 ----
    sent = [r for r in live if r.get("day") == day and r.get("status") != "skipped"]
    blocked = [r for r in sent if r.get("status") == "error" and "not_currently_allowed" in str(r.get("note") or "")]
    missed = [r for r in sent if r.get("status") == "no_fill"]
    errors = [r for r in sent if r.get("status") == "error" and r not in blocked]
    filled = [r for r in sent if r.get("status") not in {"no_fill", "error", "submitted"}]
    spent = sum((f(r.get("cost"), 0) or 0) + (f(r.get("fee"), 0) or 0) for r in filled)
    settled = [r for r in live if r.get("status") in {"won", "lost"} and _et_day(r.get("settled_at")) == day]
    l_pl = sum(f(r.get("pl"), 0) or 0 for r in settled)
    l_w = sum(r["status"] == "won" for r in settled)
    summ = live_sharp.summary(live)

    # ---- paper ----
    opened = [r for r in paper if _et_day(r.get("opened_at")) == day]
    p_set = [r for r in paper if r.get("status") in {"won", "lost"} and _et_day(r.get("settled_at")) == day]
    by: dict[str, list[dict]] = {}
    for r in p_set:
        by.setdefault(r.get("strategy") or "?", []).append(r)
    sharp_set = by.get("sharp", [])
    s_pl = sum(f(r.get("pl"), 0) or 0 for r in sharp_set)
    sharp_all = sum(f(r.get("pl"), 0) or 0 for r in paper if r.get("strategy") == "sharp" and r.get("status") in {"won", "lost"})

    title = (f"🌙 {datetime.strptime(day, '%Y%m%d'):%a %b %-d}: Live $1 {_money(l_pl)} ({l_w}-{len(settled) - l_w})"
             f" · Paper Sharp {_money(s_pl)} ({sum(r['status'] == 'won' for r in sharp_set)}-"
             f"{sum(r['status'] == 'lost' for r in sharp_set)})")
    lines = [
        "LIVE $1",
        f"Orders {len(sent)}: {len(filled)} filled, {len(missed)} missed (price moved)"
        + (f", {len(blocked)} blocked by Kalshi" if blocked else "")
        + (f", {len(errors)} other errors" if errors else ""),
        f"Spent {_money(spent).lstrip('+')} of ${live_sharp.daily_budget():.0f}",
        f"Settled {l_w}-{len(settled) - l_w} for {_money(l_pl)}; all-time {_money(summ['pl'])} "
        f"({summ['won']}-{summ['lost']}), {summ['open']} open",
        "",
        "PAPER",
        f"Opened {len(opened)}: " + (", ".join(f"{LABELS.get(k, k)} {v}" for k, v in sorted(
            {s: sum(1 for r in opened if r.get('strategy') == s) for s in {r.get('strategy') for r in opened}}.items(),
            key=lambda kv: -kv[1])) or "none"),
    ]
    for s, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        w = sum(r["status"] == "won" for r in rs)
        lines.append(f"{LABELS.get(s, s)} {w}-{len(rs) - w}, {_money(sum(f(r.get('pl'), 0) or 0 for r in rs))}")
    if not by:
        lines.append("Nothing settled")
    lines.append(f"Sharp all-time {_money(sharp_all)}")
    if odds_out_of_credits(watch_state):
        lines += ["", "⚠️ Odds API is out of credits: Sportsbooks can't price games until it resets."]
    return title, lines


def maybe_send(now: datetime, last_sent: str | None, paper: list[dict], live: list[dict], send) -> str | None:
    """Send the summary if one is due. Returns the day sent (YYYYMMDD) or None."""
    day = target_day(now, last_sent)
    if not day:
        return None
    try:
        state = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else None
    except Exception:
        state = None
    title, lines = build(day, paper, live, state)
    send(title, lines)
    return day
