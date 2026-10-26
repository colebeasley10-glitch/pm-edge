"""One-off probe (READ-ONLY): how much player-prop / totals depth do free exchanges carry?"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout/props_probe.json")
S = requests.Session()


def main() -> None:
    out: dict = {}
    # Polymarket: upcoming sports events and their market types
    try:
        evs = []
        for tag in ("nfl", "nba", "cfb", "nhl"):
            r = S.get("https://gamma-api.polymarket.com/events", params={"tag_slug": tag, "closed": "false",
                                                                       "limit": 60, "order": "startDate"}, timeout=25)
            evs += [dict(e, _tag=tag) for e in (r.json() or [])]
        types = collections.Counter()
        samples = {}
        liq = collections.defaultdict(list)
        for e in evs:
            for m in e.get("markets") or []:
                t = m.get("sportsMarketType") or "none"
                types[f"{e['_tag']}:{t}"] += 1
                liq[t].append(float(m.get("liquidityNum") or 0))
                if t not in samples:
                    samples[t] = {k: m.get(k) for k in ("question", "groupItemTitle", "outcomes", "bestBid", "bestAsk",
                                                         "liquidityNum", "line", "gameStartTime", "slug")}
        out["poly_types"] = dict(types.most_common(60))
        out["poly_liq_median"] = {t: sorted(v)[len(v) // 2] for t, v in liq.items()}
        out["poly_samples"] = samples
    except Exception as exc:
        out["poly_error"] = repr(exc)[:300]
    # Kalshi: sports series that look like player props or totals
    try:
        r = S.get("https://api.elections.kalshi.com/trade-api/v2/series", params={"category": "Sports"}, timeout=30)
        series = r.json().get("series") or []
        out["kalshi_series_n"] = len(series)
        out["kalshi_prop_series"] = [(s.get("ticker"), s.get("title")) for s in series
                                     if any(w in (s.get("ticker") or "") for w in ("NFL", "NBA", "NCAAF", "NHL"))][:200]
    except Exception as exc:
        out["kalshi_error"] = repr(exc)[:300]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print("ok")


if __name__ == "__main__":
    main()
