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


def fetch(dates: str) -> list[dict]:
    r = requests.get(BASE, params={"dates": dates, "groups": FBS_GROUP, "limit": 500}, timeout=30)
    r.raise_for_status()
    out = []
    for ev in r.json().get("events") or []:
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
