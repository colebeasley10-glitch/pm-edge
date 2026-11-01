"""One-off: pull NCAA FBS history from CFBD for the Claude NCAA model rebuild (Nov 1, 2026).

Writes JSON files to ncaa_history_export/ (uploaded as a workflow artifact, never committed).
About 5 calls per season; stops a season early on any error and records it in errors.json.
"""
import json, os, sys, time
from pathlib import Path
import requests

BASE = "https://api.collegefootballdata.com"
KEY = os.environ.get("CFBD_API_KEY")
OUT = Path("ncaa_history_export"); OUT.mkdir(exist_ok=True)
years = [int(y) for y in (sys.argv[1:] or ["2022", "2023", "2024", "2025", "2026"])]
errors, calls = {}, 0


def get(path, **params):
    global calls
    calls += 1
    r = requests.get(BASE + path, params=params, headers={"Authorization": f"Bearer {KEY}"}, timeout=60)
    r.raise_for_status()
    return r.json()


jobs = []
for y in years:
    jobs += [(f"games_{y}", "/games", {"year": y, "seasonType": "both", "classification": "fbs"}),
             (f"lines_{y}", "/lines", {"year": y, "seasonType": "both"}),
             (f"ppa_{y}", "/ppa/games", {"year": y, "excludeGarbageTime": "true"}),
             (f"talent_{y}", "/talent", {"year": y}),
             (f"returning_{y}", "/player/returning", {"year": y})]
jobs += [("fpi_2026", "/ratings/fpi", {"year": 2026}), ("sp_2026", "/ratings/sp", {"year": 2026})]
for name, path, params in jobs:
    try:
        data = get(path, **params)
        (OUT / f"{name}.json").write_text(json.dumps(data))
        print(name, len(data) if hasattr(data, "__len__") else "?")
    except Exception as exc:
        errors[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print("ERR", name, errors[name])
        if "429" in str(exc):
            break
    time.sleep(0.5)
(OUT / "errors.json").write_text(json.dumps({"calls": calls, "errors": errors}, indent=1))
print("calls", calls, "errors", len(errors))
