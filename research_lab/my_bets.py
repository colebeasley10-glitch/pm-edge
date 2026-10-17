"""Your own bets, logged from the app, settled automatically from Kalshi's official results.

Two files, one writer each, so they never collide:
* my_bets_log.csv      - written only by the app (one row per bet you log; never edited)
* my_bets_results.json - written only by the Research Lab job (settlement per bet id)

A bet settles from the Kalshi market for the same result (game winner, spread or total);
until Kalshi posts an official result it stays open. Your stake and odds are exactly what you
typed, so P/L reflects the price you actually got.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso
from research_lab.settle import leg_outcome

LOG_NAME = "my_bets_log.csv"
RESULTS_NAME = "my_bets_results.json"
LOG_FIELDS = ["bet_id", "logged_at", "label", "game", "league", "market", "venue", "odds", "decimal", "stake",
              "fair", "ev_at_log", "settle_ticker", "settle_side", "starts"]


def american_to_decimal(text) -> float | None:
    """'-110' / '+150' / '150' -> decimal odds; None if not valid American odds."""
    t = str(text or "").strip().replace("−", "-").replace(" ", "")
    try:
        v = float(t)
    except ValueError:
        return None
    if v <= -100:
        return 1 + 100 / -v
    if v >= 100:
        return 1 + v / 100
    return None


def decimal_to_american(d: float) -> str:
    if d >= 2.0:
        return f"+{round((d - 1) * 100)}"
    return f"-{round(100 / (d - 1))}"


def log_path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / LOG_NAME


def results_path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / RESULTS_NAME


def parse_log(text: str) -> list[dict]:
    if not text.strip():
        return []
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def render_log(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LOG_FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in LOG_FIELDS})
    return buf.getvalue()


def load_log(base: Path | None = None) -> list[dict]:
    p = log_path(base)
    return parse_log(p.read_text(encoding="utf-8")) if p.exists() else []


def load_results(base: Path | None = None) -> dict:
    p = results_path(base)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def settle(rows: list[dict], results: dict, markets: dict, now: datetime) -> int:
    """Fill in results for logged bets whose Kalshi market has an official result. Returns how many settled."""
    n = 0
    for r in rows:
        bid = r.get("bet_id")
        if not bid or bid in results:
            continue
        outcome = leg_outcome(markets.get(r.get("settle_ticker") or ""))
        if outcome is None:
            continue
        stake = float(r.get("stake") or 0)
        dec = float(r.get("decimal") or 0)
        if outcome == "void":
            results[bid] = {"status": "void", "pl": 0.0, "settled_at": iso(now), "result": "void"}
        else:
            won = (outcome == "yes") == ((r.get("settle_side") or "YES") == "YES")
            results[bid] = {"status": "won" if won else "lost", "pl": round(stake * (dec - 1) if won else -stake, 2),
                            "settled_at": iso(now), "result": outcome}
        n += 1
    return n


def settle_files(markets: dict, now: datetime, base: Path | None = None) -> dict:
    rows, results = load_log(base), load_results(base)
    n = settle(rows, results, markets, now)
    if n:
        _atomic_write_text(results_path(base), json.dumps(results, indent=1, sort_keys=True))
    return {"logged": len(rows), "settled_now": n}


def open_tickers(base: Path | None = None) -> list[str]:
    results = load_results(base)
    return [r["settle_ticker"] for r in load_log(base) if r.get("settle_ticker") and r.get("bet_id") not in results]


def summary(rows: list[dict], results: dict) -> dict:
    won = lost = 0
    pl = risked = 0.0
    open_n, open_stake = 0, 0.0
    for r in rows:
        res = results.get(r.get("bet_id") or "")
        stake = float(r.get("stake") or 0)
        if not res:
            open_n += 1
            open_stake += stake
            continue
        if res["status"] in {"won", "lost"}:
            won += res["status"] == "won"
            lost += res["status"] == "lost"
            pl += float(res.get("pl") or 0)
            risked += stake
    return {"won": won, "lost": lost, "pl": round(pl, 2), "roi": (pl / risked) if risked else None,
            "open": open_n, "open_stake": round(open_stake, 2)}
