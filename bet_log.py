"""Save bets you log in the app to research_lab/data/my_bets_log.csv in the GitHub repo.

Streamlit Cloud's disk is wiped on every restart, so the app writes the log through GitHub's
contents API instead (free). It needs a fine-grained GitHub token, limited to this one repo with
"Contents: read and write", saved in the app's Streamlit secrets as GITHUB_TOKEN.
The Research Lab job reads the same file and settles each bet from Kalshi's official result.
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone

import requests

from research_lab.my_bets import LOG_NAME, parse_log, render_log

REPO = os.getenv("SHARPIQ_REPO", "colebeasley10-glitch/pm-edge")
PATH = f"research_lab/data/{LOG_NAME}"
API = f"https://api.github.com/repos/{REPO}/contents/{PATH}"


class BetLogError(RuntimeError):
    pass


def token() -> str:
    return os.getenv("GITHUB_TOKEN", "").strip()


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def read_log(session=None) -> tuple[list[dict], str | None]:
    """Current log straight from GitHub (so a bet shows up right away). Returns (rows, sha)."""
    s = session or requests
    r = s.get(API, headers=_headers(), params={"ref": "main"}, timeout=15)
    if r.status_code == 404:
        return [], None
    if r.status_code != 200:
        raise BetLogError(f"GitHub read failed (HTTP {r.status_code})")
    data = r.json()
    return parse_log(base64.b64decode(data.get("content") or "").decode("utf-8")), data.get("sha")


def append_bet(bet: dict, session=None) -> dict:
    """Append one bet. Retries if the file changed in between (each write names the version it replaces)."""
    if not token():
        raise BetLogError("GITHUB_TOKEN is not set in the app's secrets")
    s = session or requests
    bet = {**bet, "bet_id": bet.get("bet_id") or uuid.uuid4().hex[:12],
           "logged_at": bet.get("logged_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    for _ in range(3):
        rows, sha = read_log(s)
        body = {"message": f"Log bet: {bet.get('label', '')} [skip ci]", "branch": "main",
                "content": base64.b64encode(render_log(rows + [bet]).encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = s.put(API, headers=_headers(), json=body, timeout=20)
        if r.status_code in (200, 201):
            return bet
        if r.status_code not in (409, 422):
            raise BetLogError(f"GitHub write failed (HTTP {r.status_code})")
    raise BetLogError("GitHub write kept conflicting; try again")
