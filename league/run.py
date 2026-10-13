"""Run one league round: settle and mark open paper trades, then let every
strategy look for new picks. Safe to run every 30 minutes; paper only."""
from __future__ import annotations

import json
import os
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

from league import book
from league.kalshi import LeagueKalshi
from league.strategies import books, crypto, polymarket, sharp, weather

STATUS = Path(os.getenv("LEAGUE_STATUS_PATH", "league_status.json"))
DAILY_CAP = int(os.getenv("LEAGUE_DAILY_CAP_PER_STRATEGY", "12"))
# Per-strategy overrides of DAILY_CAP; env LEAGUE_DAILY_CAP_<NAME> (e.g. LEAGUE_DAILY_CAP_SHARP) wins.
STRATEGY_CAPS = {"sharp": 30}
# Benched strategies still run and report what they would have picked (status "shadow"),
# but open no paper trades. Crypto benched 2026-10-13: 3-14, -30% ROI, -4.9c avg CLV.
# Override with env LEAGUE_BENCHED (comma list; empty string un-benches everything).
BENCHED = {"crypto"}


def benched() -> set[str]:
    env = os.getenv("LEAGUE_BENCHED")
    if env is None:
        return set(BENCHED)
    return {x.strip().lower() for x in env.split(",") if x.strip()}


def daily_cap(name: str) -> int:
    env = os.getenv(f"LEAGUE_DAILY_CAP_{name.upper()}")
    return int(env) if env else STRATEGY_CAPS.get(name, DAILY_CAP)
STRATEGIES = [weather, polymarket, books, sharp, crypto]
LABELS = {"weather": "Weather", "polymarket": "Polymarket", "books": "Sportsbooks",
          "sharp": "Sharp money", "crypto": "Crypto"}


def opened_today(rows: list[dict], strategy: str, now: datetime) -> int:
    return sum(1 for r in rows if r.get("strategy") == strategy
               and (book.parse_dt(r.get("opened_at")) or now).date() == now.date())


def push(new_rows: list[dict]) -> None:
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not new_rows or not user or not token or os.getenv("LEAGUE_PUSH", "1") in {"0", "false", "off"}:
        return
    counts = Counter(LABELS.get(r["strategy"], r["strategy"]) for r in new_rows)
    lines = [f"{r['side']} {r['title'][:70]} at {float(r['entry_price']) * 100:.0f}¢" for r in new_rows[:6]]
    msg = "\n".join(lines) + (f"\n+{len(new_rows) - 6} more" if len(new_rows) > 6 else "")
    title = f"📋 {len(new_rows)} new paper pick{'s' if len(new_rows) != 1 else ''}: " + ", ".join(f"{k} {v}" for k, v in counts.items())
    payload = {"token": token, "user": user, "title": title[:250], "message": msg[:1000]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip()
    if url:
        payload.update({"url": url, "url_title": "Open PM Edge"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    except Exception as exc:
        print("LEAGUE_PUSH_ERROR", type(exc).__name__)


def push_settled(rows: list[dict], before: set[str]) -> None:
    """One push when paper bets finish: record and profit for that batch."""
    done = [r for r in rows if r.get("trade_id") in before and r.get("status") in {"won", "lost"}]
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not done or not user or not token or os.getenv("LEAGUE_PUSH", "1") in {"0", "false", "off"}:
        return
    pl = sum(book.f(r.get("pl"), 0.0) or 0.0 for r in done)
    w = sum(r["status"] == "won" for r in done)
    lines = [f"{'✅' if r['status'] == 'won' else '❌'} {LABELS.get(r['strategy'], r['strategy'])}: "
             f"{r['side']} {str(r.get('title') or r['ticker'])[:60]} {float(r.get('pl') or 0):+.2f}" for r in done[:8]]
    payload = {"token": token, "user": user,
               "title": f"{len(done)} paper bet{'s' if len(done) != 1 else ''} settled: {w}-{len(done) - w}, {pl:+.2f}",
               "message": "\n".join(lines)[:1000]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip()
    if url:
        payload.update({"url": url, "url_title": "Open PM Edge"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    except Exception as exc:
        print("LEAGUE_PUSH_ERROR", type(exc).__name__)


def run_round(now: datetime | None = None, strategies=None, kalshi=None) -> dict:
    now = now or datetime.now(timezone.utc)
    kalshi = kalshi or LeagueKalshi()
    rows = book.load()
    was_open = {r.get("trade_id") for r in rows if r.get("status") == "open"}
    status = {"ran_at": book.iso(now), "strategies": {}}
    try:
        status["update"] = book.update(rows, kalshi.markets, now=now)
    except Exception as exc:
        status["update"] = {"error": f"{type(exc).__name__}: {exc}"}
    session = requests.Session()
    session.headers["User-Agent"] = "pm-edge-league (github.com/colebeasley10-glitch/pm-edge)"
    new_rows = []
    for strat in strategies or STRATEGIES:
        st = {"picks_found": 0, "opened": 0, "skipped": {}}
        try:
            picks = strat.run(kalshi, now, session)
            st["picks_found"] = len(picks)
            if strat.NAME in benched():
                st["benched"] = True
                st["shadow"] = [{"ticker": pk.market.get("ticker"), "side": pk.side, "ask": pk.ask(),
                                 "fair": None if pk.fair is None else round(pk.fair, 4),
                                 "edge": None if pk.edge() is None else round(pk.edge(), 4)}
                                for pk in picks]
                picks = []
            room =max(0, daily_cap(strat.NAME) - opened_today(rows, strat.NAME, now))
            for pk in picks:
                if st["opened"] >= room:
                    st["skipped"]["daily_cap"] = st["skipped"].get("daily_cap", 0) + 1
                    continue
                ok, why = book.gate(pk, rows, now=now)
                if not ok:
                    st["skipped"][why] = st["skipped"].get(why, 0) + 1
                    continue
                new_rows.append(book.open_trade(pk, rows, now=now))
                st["opened"] += 1
        except Exception as exc:
            st["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            traceback.print_exc()
        if getattr(strat, "DIAG", None):
            st["diag"] = strat.DIAG
        status["strategies"][strat.NAME] = st
    book.save(rows)
    status["standings"] = book.standings(rows)
    STATUS.write_text(json.dumps(status, indent=1, default=str))
    push(new_rows)
    push_settled(rows, was_open)
    print("LEAGUE_ROUND", json.dumps({k: v for k, v in status.items() if k != "standings"}, default=str))
    return status


if __name__ == "__main__":
    run_round()
    sys.exit(0)
