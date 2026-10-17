"""Daily Research Lab summary (one Pushover message per Eastern day, paper only).

Sent by the first lab run at or after 11pm Eastern (or before 6am for the previous day if
those runs were skipped). Titled "Research Lab (PAPER)" so it can't be confused with the
real-money Live $1 Sharp messages.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests

from research_lab.metrics import f
from research_lab.portfolio import EASTERN, et_day


def due_day(now: datetime, last_sent: str | None) -> str | None:
    et = now.astimezone(EASTERN)
    if et.hour >= 23:
        day = et.date()
    elif et.hour < 6:
        day = et.date() - timedelta(days=1)
    else:
        return None
    key = day.strftime("%Y%m%d")
    return None if last_sent == key else key


def _money(x) -> str:
    x = f(x, 0.0) or 0.0
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):.2f}"


def _roi(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def build(day: str, summaries: dict, stores: dict, status: dict) -> tuple[str, list[str]]:
    """summaries: {label: summary}; stores: {label: Store} (research strategies only)."""
    lines = [f"PM EDGE STRATEGY RESEARCH LAB — {day[:4]}-{day[4:6]}-{day[6:]} (PAPER)"]
    for label, s in summaries.items():
        lines.append(f"{label}: {s['won']}-{s['lost']}, {_money(s['net_pl'])}, ROI {_roi(s['roi'])}")
    arb = status.get("structural_today") or {}
    if arb:
        lines.append(f"Structural: {arb.get('qualified', 0)} quote-level opportunities, "
                     f"{arb.get('filled', 0)} fully executable today")
    new_today, fees_today, open_exp = 0, 0.0, 0.0
    reasons: dict[str, int] = {}
    for st in stores.values():
        for p in st.positions:
            if p.get("day") == day:
                new_today += 1
                fees_today += f(p.get("fee"), 0.0) or 0.0
            if p.get("status") == "open":
                open_exp += f(p.get("committed"), 0.0) or 0.0
        for c in st.candidates:
            if c.get("decision") == "REJECTED" and str(c.get("run_at") or "")[:10].replace("-", "") == day:
                reasons[c.get("reason") or "?"] = reasons.get(c.get("reason") or "?", 0) + 1
    lines.append(f"New paper trades today: {new_today} · fees ${fees_today:.2f} · open exposure ${open_exp:.2f}")
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        lines.append("Top rejections: " + ", ".join(f"{k} {v}" for k, v in top))
    fails = status.get("data_failures_today") or 0
    snap = status.get("snapshot") or {}
    lines.append(f"Data: {fails} failed fetches today; sportsbook snapshot "
                 f"{'missing' if snap.get('missing') else 'stale' if snap.get('stale') else 'EMPTY (no sportsbook prices; check Odds API credits)' if snap.get('empty') else 'ok'}")
    lines.append("Records reconciled: " + ("yes" if status.get("reconciled") else "NO, check the lab page"))
    title = f"🧪 Research Lab (PAPER) {day[4:6]}/{day[6:]}"
    return title, lines


def send(title: str, lines: list[str]) -> bool:
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not user or not token or os.getenv("RESEARCH_LAB_PUSH", "1") in {"0", "false", "off"}:
        return False
    payload = {"token": token, "user": user, "title": title[:250], "message": "\n".join(lines)[:1024]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip()
    if url:
        payload.update({"url": url.rstrip("/") + "/Research_Lab_Compare", "url_title": "Open Research Lab"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
        return True
    except Exception:
        return False


def today_key(now: datetime) -> str:
    return et_day(now)
