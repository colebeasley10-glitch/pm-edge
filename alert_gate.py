"""One gate for every phone alert SharpIQ sends (Nov 1, 2026).

Cole only wants his phone to buzz for the bets he'd actually put real money on. Since Nov 4, 2026
that's the two lanes he's taking every play from ($1 each):

  * prop_board -- every new Prop Board steam play (research_lab/prop_board.py)
  * bet_queue  -- every new Bet Queue play (research_lab/bet_queue.py), except ones that came
                  from Prop Board, which already buzzed

(Nov 1 - Nov 4 it was huge-EV Bet Queue plays + BreakTheBank NFL; BreakTheBank is now quiet.)

Everything else keeps logging and tracking on paper exactly as before; it just doesn't push.

How it's enforced: the workflows no longer hand PUSHOVER_USER_KEY / PUSHOVER_APP_TOKEN to any
job, so the old senders all hit their "no Pushover secrets -> skip" branch. The keys now arrive
as SHARPIQ_PUSH_USER / SHARPIQ_PUSH_TOKEN, which only this module reads, and it only sends for a
source on the allow list. To turn another lane's alerts back on later, add it to ALLOWED (or set
the SHARPIQ_ALERTS env var to a comma list) and route that sender through send().
"""
from __future__ import annotations

import os

import requests

PUSH_URL = "https://api.pushover.net/1/messages.json"
ALLOWED = frozenset({"prop_board", "bet_queue"})


def allowed() -> frozenset:
    raw = os.getenv("SHARPIQ_ALERTS", "").strip()
    if not raw:
        return ALLOWED
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def keys() -> tuple[str, str]:
    return os.getenv("SHARPIQ_PUSH_USER", "").strip(), os.getenv("SHARPIQ_PUSH_TOKEN", "").strip()


def send(source: str, title: str, message: str, url: str | None = None, *, url_title: str = "Open",
         priority: int = 1, post=requests.post) -> bool:
    """Push one alert if `source` is allowed. Returns True only when Pushover accepted it."""
    if source not in allowed():
        print(f"ALERT MUTED ({source}):", title)
        return False
    user, token = keys()
    if not user or not token:
        print(f"ALERT SKIPPED (no SHARPIQ_PUSH keys, {source}):", title, "|", message.replace("\n", " | "))
        return False
    payload = {"token": token, "user": user, "title": title[:250], "message": message[:1024],
               "priority": str(priority)}
    if url:
        payload["url"], payload["url_title"] = url, url_title
    try:
        r = post(PUSH_URL, data=payload, timeout=15)
    except requests.RequestException as exc:
        print("Pushover error:", exc)
        return False
    if getattr(r, "status_code", 500) >= 400:
        print("Pushover error:", r.status_code, getattr(r, "text", "")[:300])
        return False
    return True
