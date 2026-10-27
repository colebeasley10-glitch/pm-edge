"""Quick bets: log ANY bet in a few taps (Hunter's picks, your own, anything) and grade it with one tap.

Stored at research_lab/data/quick_bets.csv in the repo, written through GitHub's contents API
with the app's GITHUB_TOKEN (same as bet_log.py, since Streamlit Cloud's disk is wiped).
These bets don't need a matching market: you tap Won / Lost / Push when the game ends.
The Scorecard grades each source (Hunter, Odds Fam, Me) on its own.
"""
from __future__ import annotations

import base64
import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

import bet_log

PATH = "research_lab/data/quick_bets.csv"
API = f"https://api.github.com/repos/{bet_log.REPO}/contents/{PATH}"
FIELDS = ["bet_id", "logged_at", "source", "pick", "book", "odds", "decimal", "stake", "status", "pl", "graded_at"]
SOURCES = ["Hunter", "Odds Fam", "Me"]
BOOKS = ["Betr", "Rebet", "Fliff", "Bovado", "Novig", "ProphetX", "Kalshi", "Polymarket", "PrizePicks",
         "Underdog", "Sleeper", "Other"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def american_to_decimal(txt) -> float | None:
    s = str(txt or "").strip().replace("−", "-").replace(" ", "")
    try:
        v = float(s)
    except ValueError:
        return None
    if -100 < v < 100:
        return None
    return round(1 + (v / 100 if v > 0 else 100 / -v), 4)


def parse(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text))) if text.strip() else []


def render(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def pl_for(bet: dict, status: str) -> float:
    stake = float(bet.get("stake") or 0)
    if status == "won":
        return round(stake * (float(bet.get("decimal") or 1) - 1), 2)
    if status == "lost":
        return -round(stake, 2)
    return 0.0


def new_bet(source: str, pick: str, book: str, odds: str, stake: float) -> dict:
    dec = american_to_decimal(odds)
    if dec is None:
        raise ValueError("Enter odds like -110 or +135.")
    if not str(pick).strip():
        raise ValueError("Enter the bet.")
    return {"bet_id": uuid.uuid4().hex[:12], "logged_at": now_iso(), "source": source, "pick": pick.strip(),
            "book": book, "odds": str(odds).strip().replace("−", "-"), "decimal": dec,
            "stake": round(float(stake), 2), "status": "open", "pl": "", "graded_at": ""}


def summary(rows: list[dict], source: str | None = None) -> dict:
    mine = [r for r in rows if source in (None, r.get("source"))]
    done = [r for r in mine if r.get("status") in {"won", "lost", "push"}]
    staked = sum(float(r.get("stake") or 0) for r in done)
    pl = sum(float(r.get("pl") or 0) for r in done)
    return {"won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
            "push": sum(r["status"] == "push" for r in done), "pl": round(pl, 2),
            "roi": (pl / staked) if staked else None, "open": sum(r.get("status") == "open" for r in mine)}


# ------------------------------------------------------------------ GitHub storage
def read(session=None) -> tuple[list[dict], str | None]:
    if not bet_log.token():   # local / no token: read the checked-out copy
        p = Path(PATH)
        return (parse(p.read_text(encoding="utf-8")) if p.exists() else []), None
    s = session or requests
    r = s.get(API, headers=bet_log._headers(), params={"ref": "main"}, timeout=15)
    if r.status_code == 404:
        return [], None
    if r.status_code != 200:
        raise bet_log.BetLogError(f"GitHub read failed (HTTP {r.status_code})")
    data = r.json()
    return parse(base64.b64decode(data.get("content") or "").decode("utf-8")), data.get("sha")


def _write(change, message: str, session=None) -> list[dict]:
    """Read, apply change(rows) -> rows, write back; retries when the file changed in between."""
    if not bet_log.token():
        raise bet_log.BetLogError("GITHUB_TOKEN is not set in the app's secrets")
    s = session or requests
    for _ in range(3):
        rows, sha = read(s)
        rows = change(rows)
        body = {"message": f"{message} [skip ci]", "branch": "main",
                "content": base64.b64encode(render(rows).encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = s.put(API, headers=bet_log._headers(), json=body, timeout=20)
        if r.status_code in (200, 201):
            return rows
        if r.status_code not in (409, 422):
            raise bet_log.BetLogError(f"GitHub write failed (HTTP {r.status_code})")
    raise bet_log.BetLogError("GitHub write kept conflicting; try again")


def append(bet: dict, session=None) -> dict:
    _write(lambda rows: rows + [bet], f"Quick bet: {bet['pick']}", session)
    return bet


def grade(bet_id: str, status: str, session=None) -> None:
    if status not in {"won", "lost", "push"}:
        raise ValueError(status)

    def change(rows):
        for r in rows:
            if r.get("bet_id") == bet_id:
                r.update({"status": status, "pl": pl_for(r, status), "graded_at": now_iso()})
        return rows
    _write(change, f"Grade quick bet {bet_id}: {status}", session)
