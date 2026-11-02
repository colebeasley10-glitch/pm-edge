"""Fetch ESPN's open/close lines and finals for a date range of FBS games -> CSV (Nov 1, 2026).

Used to replay the NCAA pool model on a past Clay's slate when the saved slate lacked market lines.
Reads requests from ncaa_pool/data/line_requests.txt ("label,YYYYMMDD-YYYYMMDD" per line) and writes
ncaa_pool/data/espn_lines/<label>.csv. Free ESPN scoreboard, no key.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from espn_cfb import BASE, FBS_GROUP, parse_event  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "ncaa_pool" / "data" / "line_requests.txt"
OUT = ROOT / "ncaa_pool" / "data" / "espn_lines"
FIELDS = ["away", "home", "kickoff", "close", "open", "ctot", "otot", "away_pts", "home_pts", "provider"]


def _days(dates: str) -> list[str]:
    from datetime import date, timedelta
    a, b = (dates.split("-") + [dates])[:2]
    d0, d1 = date(int(a[:4]), int(a[4:6]), int(a[6:])), date(int(b[:4]), int(b[4:6]), int(b[6:]))
    return [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range((d1 - d0).days + 1)]


def fetch(dates: str) -> list[dict]:
    events = []
    for day in _days(dates):            # one day at a time; a bad day is reported, not fatal
        try:
            r = requests.get(BASE, params={"dates": day, "groups": FBS_GROUP, "limit": 300},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            print(day, r.status_code, len(r.text))
            r.raise_for_status()
            events += r.json().get("events") or []
        except Exception as exc:
            print("ERROR", day, type(exc).__name__, str(exc)[:200])
    out = []
    for ev in events:
        p = parse_event(ev)
        if not p:
            continue
        g, gl = p
        ln = (gl or {}).get("lines", [{}])[0] if gl else {}
        out.append({"away": g["awayTeam"], "home": g["homeTeam"], "kickoff": g["startDate"],
                    "close": ln.get("spread"), "open": ln.get("spreadOpen"), "ctot": ln.get("overUnder"),
                    "otot": ln.get("overUnderOpen"), "away_pts": g["awayPoints"], "home_pts": g["homePoints"],
                    "provider": ln.get("provider")})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for line in REQ.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        label, dates = [x.strip() for x in line.split(",")]
        rows = fetch(dates)
        with (OUT / f"{label}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(label, dates, len(rows), "games")


if __name__ == "__main__":
    main()
