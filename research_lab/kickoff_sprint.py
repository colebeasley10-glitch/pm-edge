"""Kickoff Sprint: extra Pinnacle prop reads in the last minutes before a football kickoff (PAPER + PUSH).

Why (Nov 1, 2026): Hunter's George Pickens Under 4.5 receptions card came from Pinnacle moving the
Under from about -146 to -156 in the final ~10 minutes before the 1:00 ET kickoff, while Onyx still
had -118. Our last read was 12:47 ET (Pinnacle fair -114), the next one landed after kickoff, so the
move was never seen. The regular rounds run every ~15 minutes; this step runs right after a round and,
when a football kickoff is less than ~16 minutes away, reads Pinnacle again at about T-9 and T-3
minutes. Each read goes through the same Prop Board steam check and Odds Fam alert path as a
normal round. Paper only: no orders, only phone alerts and the paper record.

Budget: PinnWire's free key allows 100 requests a day; normal rounds stop at 90. A sprint read may
use up to SPRINT_BUDGET (98), so at most ~8 sprint reads a day, which covers Sunday's three
slates plus Thursday/Monday night with room to spare.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from research_lab import config, pinnacle
from research_lab.ledger import iso, now_utc, parse_dt, run_lock

FOOTBALL = 5
LEAGUES = {"NFL", "NCAA"}
LOOKAHEAD = timedelta(minutes=16)      # a kickoff this close won't get another regular pre-game read
OFFSETS = (timedelta(minutes=9), timedelta(minutes=3))
MIN_GAP = timedelta(minutes=4)          # skip a target this close to the last read (nothing new yet)
SPRINT_BUDGET = 98


def next_kickoff(board_rows, now: datetime) -> datetime | None:
    starts = {parse_dt(r.get("starts")) for r in board_rows or [] if r.get("league") in LEAGUES}
    soon = sorted(s for s in starts if s and now + timedelta(minutes=2) < s <= now + LOOKAHEAD)
    return soon[0] if soon else None


def read_times(kick: datetime, now: datetime, last_read: datetime | None) -> list[datetime]:
    """Sprint read times for one kickoff: T-9 and T-3, dropping any that are past or too close together."""
    out, prev = [], last_read or (now - timedelta(hours=1))
    for off in OFFSETS:
        t = kick - off
        if t < now or t - prev < MIN_GAP:
            continue
        out.append(t)
        prev = t
    return out


def _fetch(client, now: datetime, base: Path) -> dict | None:
    u = pinnacle.load_usage(now, base)
    if u["requests"] >= SPRINT_BUDGET:
        return None
    u["requests"] += 1
    try:
        payload = client.fixtures(FOOTBALL)
    except Exception as exc:
        pinnacle.save_usage(u, base)
        print("SPRINT_FETCH_ERROR", type(exc).__name__, str(exc)[:120])
        return None
    u["last_fetch"][str(FOOTBALL)] = iso(now)
    pinnacle.save_usage(u, base)
    return payload


def one_read(now: datetime, base: Path, client, push) -> dict:
    from research_lab import odds_fam, prop_board
    payload = _fetch(client, now, base)
    if payload is None:
        return {"at": iso(now), "skipped": "budget or fetch error"}
    pb = prop_board.update({FOOTBALL: payload}, now, base, push=None)
    of = odds_fam.update(now, base, new_props=pb.pop("new", []), push=push)
    try:
        odds_fam.update_board(now, base, prop_board_rows=prop_board.load_board(base).get("rows") or [])
    except Exception as exc:
        print("SPRINT_BOARD_ERROR", type(exc).__name__, str(exc)[:120])
    return {"at": iso(now), "props": pb.get("props"), "steam": pb.get("new_plays"), "sent": of.get("sent"),
            "pushed": of.get("pushed")}


def run(*, now: datetime | None = None, base: Path | None = None, client=None, push=None,
        clock=now_utc, sleep=time.sleep) -> dict:
    base = Path(base or config.DATA_DIR)
    now = now or clock()
    from research_lab import prop_board
    kick = next_kickoff(prop_board.load_board(base).get("rows") or [], now)
    if not kick:
        return {"kickoff": None, "reads": []}
    last = parse_dt(pinnacle.load_usage(now, base)["last_fetch"].get(str(FOOTBALL)))
    times = read_times(kick, now, last)
    out = {"kickoff": iso(kick), "planned": [iso(t) for t in times], "reads": []}
    client = client or pinnacle.PinnWire()
    for t in times:
        wait = (t - clock()).total_seconds()
        if wait > 0:
            sleep(wait)
        at = clock()
        if at >= kick:
            break
        with run_lock(base):
            out["reads"].append(one_read(at, base, client, push))
    return out


def main() -> int:
    from trader_follow.core import send_push
    out = run(push=lambda t, m, u: send_push(t, m, u, url_title="Open"))
    print("KICKOFF_SPRINT", json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
