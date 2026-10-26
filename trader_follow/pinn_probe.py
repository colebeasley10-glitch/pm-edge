"""One-off probe (READ-ONLY): shape of Pinnacle player props in PinnWire fixtures (2 calls)."""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout/pinn_probe.json")


def main() -> None:
    key = os.getenv("PINNWIRE_KEY", "").strip()
    out: dict = {}
    for sid in (3, 5):
        r = requests.get("https://pinnwire.com/kit/v1/prematch/fixtures",
                         params={"sport_id": sid, "include_specials": 1}, headers={"x-api-key": key}, timeout=40)
        d = r.json()
        evs = d.get("events") or []
        prop = [e for e in evs if re.search(r"Total|Yards|Points|Assists|Rebounds|Receptions|Touchdowns",
                                            json.dumps(e)) and e.get("parent_id")]
        out[str(sid)] = {
            "n": len(evs), "n_with_parent": sum(1 for e in evs if e.get("parent_id")),
            "event_types": collections.Counter(str(e.get("event_type")) for e in evs).most_common(),
            "leagues": collections.Counter(e.get("league_name") for e in evs).most_common(30),
            "prop_samples": prop[:3],
            "parent_sample": next((e for e in evs if prop and e.get("event_id") == prop[0].get("parent_id")), None),
            "special_keys": sorted({k for e in evs for k in e if k not in
                                    ("event_id", "sport_id", "league_id", "league_name", "home", "away", "starts",
                                     "start_ts", "event_type", "units", "parent_id", "is_have_odds",
                                     "is_have_periods", "periods")}),
        }
        sp = [e for e in evs if "special" in json.dumps(list(e.get("periods", {}).get("num_0", {}).keys()))]
        out[str(sid)]["num0_keys"] = collections.Counter(k for e in evs for k in (e.get("periods") or {}).get("num_0", {}) ).most_common()
        out[str(sid)]["specials_in_num0"] = sp[:2]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str)[:600_000])
    print("ok")


if __name__ == "__main__":
    main()
