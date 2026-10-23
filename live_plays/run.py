"""One Live Today's Plays round: python -m live_plays.run

Runs right after each Research Lab round (which writes the paper Today's Plays picks).
Real Kalshi orders only when LIVE_PLAYS_ENABLED=1 and Kalshi write keys are present;
otherwise a dry run. Polymarket picks always go out as phone alerts to bet by hand.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import requests

from live_plays import core


def _when(v) -> str:
    dt = core.parse_dt(v)
    if dt is None:
        return ""
    return dt.astimezone(core.EASTERN).strftime("%a %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def pushover(new_rows: list[dict]) -> None:
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not user or not token:
        return
    url = os.getenv("STREAMLIT_APP_URL", "").strip().rstrip("/")
    poly = [r for r in new_rows if r.get("venue") == "Polymarket"]
    kal = [r for r in new_rows if r.get("venue") == "Kalshi"]
    if poly:
        lines = [f"{r['title'].split(': ', 1)[-1]} at {r.get('paper_price')} · {r['game']} · {_when(r.get('commence_time'))}"
                 f" · {r.get('note', '').split(' by hand')[0]}" for r in poly]
        payload = {"token": token, "user": user, "priority": 1,
                   "title": f"🎯 1u (${core.per_bet():.0f}) on Polymarket ({len(poly)}) · Today's Plays",
                   "message": ("\n".join(lines) + "\nOnly bet at this price or better. Then log it on the "
                               "Home tab (Log a bet) so it counts.")[:1024]}
        if url:
            payload.update({"url": url, "url_title": "Open SharpIQ to log it"})
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    if kal:
        lines = [f"{r['title'].split(': ', 1)[-1]} · {r['filled']} @ {float(r['avg_price']) * 100:.0f}¢ "
                 f"(${float(r['cost']) + float(r['fee']):.2f}) · {r['game']}" for r in kal]
        payload = {"token": token, "user": user,
                   "title": f"✅ Live Today's Plays: {len(kal)} Kalshi bet{'s' if len(kal) != 1 else ''} placed",
                   "message": "\n".join(lines)[:1024]}
        if url:
            payload.update({"url": url.rstrip("/") + "/Live_Todays_Plays", "url_title": "Open live record"})
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)


def main() -> dict:
    now = datetime.now(timezone.utc)
    rows = core.load()
    live = core.enabled()
    status: dict = {"at": core.iso(now), "mode": "live" if live else "dry", "per_bet": core.per_bet(),
                    "daily_budget": core.daily_budget()}

    client = None
    if live:
        from kalshi_submit import KalshiSubmitClient
        client = KalshiSubmitClient.from_env()
        try:
            from league import live_sharp
            status["reconciled"] = live_sharp.reconcile_unconfirmed(
                [r for r in rows if r.get("venue") == "Kalshi"], client, now=now)
        except Exception as exc:
            status["reconcile_error"] = type(exc).__name__

    # settle from Kalshi's official results (public data)
    open_t = sorted({r["ticker"] for r in rows if r.get("venue") == "Kalshi" and r.get("status") == "open"})
    if open_t:
        from research_lab.public_data import PublicKalshi
        try:
            status["settled"] = len(core.settle(rows, PublicKalshi().markets(open_t), now))
        except Exception as exc:
            status["settle_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    picks, skips = core.new_picks(core.paper_positions(), rows, now)
    status["new_picks"] = len(picks)
    res = core.place(picks, rows, client, now, live=live, notify=pushover)
    res["skipped"] = {**skips, **res.get("skipped", {})}
    res.pop("new", None)
    status.update(res)
    core.save(rows)
    (core.DATA / "status.json").write_text(json.dumps(status, indent=1, default=str))
    print("LIVE_PLAYS_ROUND", json.dumps(status, default=str))
    return status


if __name__ == "__main__":
    main()
    sys.exit(0)
