"""One-off probe: what does Kalshi's public social leaderboard / profile data look like? (READ-ONLY)

Saves status + the first part of each response to trader_follow/data/scout/kalshi_probe.json,
and any API paths the pages reference, so the Kalshi side of the scout can be built on real shapes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout/kalshi_probe.json")
API = "https://api.elections.kalshi.com"
URLS = [
    "https://kalshi.com/ideas/leaderboard",
    "https://kalshi.com/ideas/profiles/DegenJohnny",
    f"{API}/v1/social/leaderboard?metric_name=projected_pnl&category=Sports&time_period=all_time&limit=25",
    f"{API}/v1/social/leaderboard?metric_name=projected_pnl&limit=25",
    f"{API}/v1/social/leaderboard",
    f"{API}/v1/leaderboard?metric=profit&category=Sports&frequency=all_time",
    f"{API}/v1/social/profile?nickname=DegenJohnny",
    f"{API}/v1/social/profiles/DegenJohnny",
]


def main() -> None:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (sharpiq-scout)"
    out = {}
    paths: set[str] = set()
    for u in URLS:
        try:
            r = s.get(u, timeout=25)
            body = r.text
            paths |= set(re.findall(r"/(?:trade-api/v2|v1|v2)/social/[A-Za-z0-9_/\-{}]+", body))
            paths |= set(re.findall(r"https://[a-z.\-]*kalshi\.com/[A-Za-z0-9_/\-]*social[A-Za-z0-9_/\-?=&]*", body))
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
            out[u] = {"status": r.status_code, "type": r.headers.get("content-type", ""),
                      "head": body[:6000], "next_data": m.group(1)[:30000] if m else None}
            # follow JS bundles on the HTML pages, looking for the API calls they make
            if "html" in r.headers.get("content-type", ""):
                for js in re.findall(r'src="(/_next/static/[^"]+\.js)"', body)[:60]:
                    try:
                        jt = s.get("https://kalshi.com" + js, timeout=20).text
                    except Exception:
                        continue
                    for hit in re.findall(r'["`](/[a-z0-9_/\-${}.]*(?:social|leaderboard|profile)[a-z0-9_/\-${}.?=&]*)["`]', jt):
                        paths.add(hit)
        except Exception as exc:
            out[u] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    out["paths_seen"] = sorted(paths)[:300]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print({u: v.get("status") for u, v in out.items() if isinstance(v, dict)})


if __name__ == "__main__":
    main()
