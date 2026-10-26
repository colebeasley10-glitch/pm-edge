"""One-off probe (READ-ONLY): does the free PinnWire key return Pinnacle player props and line drops?"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout/pinn_probe.json")
BASE = "https://pinnwire.com"


def main() -> None:
    key = os.getenv("PINNWIRE_KEY", "").strip()
    s = requests.Session()
    out: dict = {}
    try:
        doc = s.get(f"{BASE}/docs.html", timeout=25).text
        text = re.sub(r"<[^>]+>", " ", doc)
        text = re.sub(r"\s+", " ", text)
        out["docs_paths"] = sorted(set(re.findall(r"/kit/v1/[a-z_/{}]+", doc)))
        idx = [m.start() for m in re.finditer(r"special|prop|drop", text, re.I)]
        out["docs_snippets"] = [text[max(0, i - 200): i + 300] for i in idx[:25]]
    except Exception as exc:
        out["docs_error"] = repr(exc)[:200]
    tries = [("fixtures_specials", "/kit/v1/prematch/fixtures", {"sport_id": 3, "include_specials": 1}),
             ("markets_specials", "/kit/v1/markets", {"sport_id": 3, "include_specials": 1}),
             ("drops", "/kit/v1/drops", {"sport_id": 3}),
             ("drops_football", "/kit/v1/drops", {"sport_id": 5})]
    for name, path, params in tries:
        try:
            r = s.get(BASE + path, params=params, headers={"x-api-key": key}, timeout=30)
            body = r.text
            entry = {"status": r.status_code, "len": len(body), "head": body[:1500]}
            if r.status_code == 200 and body.startswith("{"):
                d = r.json()
                entry["keys"] = list(d.keys())[:20]
                evs = d.get("events") or d.get("data") or []
                if isinstance(evs, list) and evs:
                    e0 = evs[0]
                    entry["event_keys"] = list(e0.keys())[:40]
                    sp = {k: v for k, v in e0.items() if re.search(r"special|prop|player", k, re.I)}
                    entry["special_fields"] = json.dumps(sp)[:4000]
                    n_special = sum(1 for e in evs for k in e if re.search(r"special|prop", k, re.I) and e[k])
                    entry["events_with_specials"] = n_special
                    entry["n_events"] = len(evs)
                found = re.findall(r'"[^"]*(?:Points|Rebounds|Assists|Yards|Receptions|Threes)[^"]*"', body)[:20]
                entry["prop_names_seen"] = found
            out[name] = entry
        except Exception as exc:
            out[name] = {"error": repr(exc)[:200]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1)[:400_000])
    print({k: (v.get("status") if isinstance(v, dict) else None) for k, v in out.items()})


if __name__ == "__main__":
    main()
