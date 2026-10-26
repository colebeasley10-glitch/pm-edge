"""One-off probe (READ-ONLY): can the free public pick'em boards be read from GitHub's runners?"""
from __future__ import annotations

import json
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout/dfs_probe.json")
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
      "Accept": "application/json"}
URLS = {
    "prizepicks_nba": ("https://api.prizepicks.com/projections", {"league_id": 7, "per_page": 250, "single_stat": "true"}),
    "prizepicks_nfl": ("https://api.prizepicks.com/projections", {"league_id": 9, "per_page": 250, "single_stat": "true"}),
    "underdog_v5": ("https://api.underdogfantasy.com/beta/v5/over_under_lines", {}),
    "underdog_v6": ("https://api.underdogfantasy.com/beta/v6/over_under_lines", {}),
    "betr_picks": ("https://api.betr.app/v1/picks/board", {}),
    "sleeper": ("https://api.sleeper.app/lines/available", {}),
}


def main() -> None:
    out = {}
    for name, (url, params) in URLS.items():
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            body = r.text
            e = {"status": r.status_code, "len": len(body), "type": r.headers.get("content-type", ""),
                 "head": body[:2500]}
            if r.status_code == 200 and body.lstrip().startswith(("{", "[")):
                d = r.json()
                e["keys"] = list(d.keys())[:20] if isinstance(d, dict) else f"list[{len(d)}]"
                for k in ("data", "over_under_lines", "appearances", "players", "included"):
                    if isinstance(d, dict) and isinstance(d.get(k), list):
                        e[f"n_{k}"] = len(d[k])
                        e[f"sample_{k}"] = d[k][:2]
            out[name] = e
        except Exception as exc:
            out[name] = {"error": repr(exc)[:300]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str)[:800_000])
    print({k: v.get("status", v.get("error")) for k, v in out.items()})


if __name__ == "__main__":
    main()
