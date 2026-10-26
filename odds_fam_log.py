"""Save bets Cole logs on the Odds Fam page to research_lab/data/ODDS_FAM/my_bets.csv in the repo.

Same approach as bet_log.py: Streamlit Cloud's disk is wiped on restart, so the app writes through
GitHub's contents API with the GITHUB_TOKEN already in the app's secrets. The Research Lab job reads
the file and settles each bet from the play's result (at the line he actually took).
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone

import requests

import bet_log
from research_lab.odds_fam import DIR, parse_bets, render_bets

PATH = f"research_lab/data/{DIR}/my_bets.csv"
API = f"https://api.github.com/repos/{bet_log.REPO}/contents/{PATH}"


def read(session=None) -> tuple[list[dict], str | None]:
    s = session or requests
    r = s.get(API, headers=bet_log._headers(), params={"ref": "main"}, timeout=15)
    if r.status_code == 404:
        return [], None
    if r.status_code != 200:
        raise bet_log.BetLogError(f"GitHub read failed (HTTP {r.status_code})")
    data = r.json()
    return parse_bets(base64.b64decode(data.get("content") or "").decode("utf-8")), data.get("sha")


def append(bet: dict, session=None) -> dict:
    if not bet_log.token():
        raise bet_log.BetLogError("GITHUB_TOKEN is not set in the app's secrets")
    s = session or requests
    bet = {**bet, "bet_id": bet.get("bet_id") or uuid.uuid4().hex[:12],
           "logged_at": bet.get("logged_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    for _ in range(3):
        rows, sha = read(s)
        body = {"message": f"Odds Fam bet: {bet.get('pick', '')} [skip ci]", "branch": "main",
                "content": base64.b64encode(render_bets(rows + [bet]).encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = s.put(API, headers=bet_log._headers(), json=body, timeout=20)
        if r.status_code in (200, 201):
            return bet
        if r.status_code not in (409, 422):
            raise bet_log.BetLogError(f"GitHub write failed (HTTP {r.status_code})")
    raise bet_log.BetLogError("GitHub write kept conflicting; try again")
