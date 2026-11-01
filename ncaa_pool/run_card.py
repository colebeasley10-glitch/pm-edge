"""Build a pool card from the pool's frozen lines + the current market.

  python -m ncaa_pool.run_card ncaa_pool/data/clay_2026_w10.csv --pool clay
  python -m ncaa_pool.run_card ncaa_pool/data/chappy_2026_w10.csv --pool chappy

Pool file columns: away,home,home_spread[,total][,exclude]
  home_spread: the pool's HOME line (home -7.5 -> -7.5; home +3 -> 3)
  exclude: put any text to keep a game off the card (e.g. a QB situation you don't trust)
Market: latest ESPN line logged hourly by the NCAA grader (ncaa_grader/data/picks.csv), unless a
--market CSV (away,home,home_spread[,total]) is given. Run it as close to the pick lock as possible:
the later the market read, the bigger the edge on Clay's frozen Tuesday lines.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from ncaa_pool.model import Game, build_card

ROOT = Path(__file__).resolve().parent.parent
POOLS = {"clay": (12, 3), "chappy": (6, 1)}


def _norm(name: str, aliases: dict) -> str:
    n = (name or "").strip()
    return aliases.get(n.lower(), n)


def _aliases() -> dict:
    out = {}
    p = ROOT / "data" / "team_aliases.csv"
    if p.exists():
        for r in csv.DictReader(p.open()):
            vals = [v for v in r.values() if v]
            if len(vals) >= 2:
                for v in vals:
                    out[v.strip().lower()] = vals[0].strip()
    out.update({"miami": "Miami", "miami (fl)": "Miami", "ul monroe": "UL Monroe", "louisiana-monroe": "UL Monroe",
                "hawaii": "Hawai'i", "umass": "Massachusetts", "san jose state": "San José State",
                "southern miss": "Southern Miss", "southern mississippi": "Southern Miss", "nc state": "NC State",
                "fiu": "Florida International", "utsa": "UTSA", "uconn": "UConn", "app state": "App State",
                "appalachian state": "App State", "sam houston state": "Sam Houston"})
    return out


def _f(v):
    try:
        return None if v in (None, "") else float(str(v).replace("PK", "0").replace("pk", "0"))
    except ValueError:
        return None


def market_from_grader(aliases: dict) -> dict:
    """(away, home) -> {'spread': home spread, 'total': total} from the newest grader rows."""
    out: dict = {}
    p = ROOT / "ncaa_grader" / "data" / "picks.csv"
    rows = sorted(csv.DictReader(p.open()), key=lambda r: r.get("logged_at") or "")
    for r in rows:
        if r.get("result"):
            continue
        key = (_norm(r["away"], aliases), _norm(r["home"], aliases))
        cl = _f(r.get("close_line")) if r.get("close_line") not in (None, "") else _f(r.get("line"))
        if cl is None:
            continue
        m = out.setdefault(key, {})
        if r["market"] == "SPREAD":
            m["spread"] = cl if r["pick_team"] == r["home"] else -cl
        elif r["market"] == "TOTAL":
            m["total"] = cl
    return out


def market_from_csv(path: str, aliases: dict) -> dict:
    out = {}
    for r in csv.DictReader(open(path)):
        out[(_norm(r["away"], aliases), _norm(r["home"], aliases))] = {"spread": _f(r.get("home_spread")),
                                                                       "total": _f(r.get("total"))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pool_file")
    ap.add_argument("--pool", choices=POOLS, default="clay")
    ap.add_argument("--market")
    ap.add_argument("--totals", action="store_true", help="also consider totals (off by default: spreads did better on real Clay slates)")
    a = ap.parse_args(argv)
    aliases = _aliases()
    market = market_from_csv(a.market, aliases) if a.market else market_from_grader(aliases)
    games, missing, excluded = [], [], []
    for r in csv.DictReader(open(a.pool_file)):
        away, home = _norm(r["away"], aliases), _norm(r["home"], aliases)
        m = market.get((away, home))
        flipped = False
        if m is None and (home, away) in market:          # pool lists the teams the other way round
            mm = market[(home, away)]
            m, flipped = {"spread": -mm["spread"] if mm.get("spread") is not None else None,
                          "total": mm.get("total")}, True
        if r.get("exclude"):
            excluded.append(f"{away} at {home} ({r['exclude']})")
            continue
        if not m or m.get("spread") is None:
            missing.append(f"{away} at {home}")
            continue
        games.append(Game(away, home, _f(r["home_spread"]), m["spread"], _f(r.get("total")), m.get("total")))
    n, k = POOLS[a.pool]
    card = build_card(games, n, k, allow_totals=a.totals)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"# {a.pool.title()}'s pool card — CPM v1 — market read {stamp}\n")
    for i, o in enumerate(card["picks"], 1):
        tag = "KEY ×2" if i <= k else ""
        print(f"{i:2d}. {o.label:38s} {o.prob*100:5.1f}%  value {o.gap:+.1f} pts (market {o.extra['market']:+g})  {tag}")
    print(f"\nExpected pool points: {card['expected_points']:.1f} of {card['max_points']}")
    print("Chance of: " + ", ".join(f"{t}+ pts {p*100:.0f}%" for t, p in card["p_at_least"].items() if t <= card["max_points"]))
    if card["bench"]:
        print("\nNext best: " + "; ".join(f"{o.label} {o.prob*100:.1f}%" for o in card["bench"]))
    if missing:
        print(f"\nNo market line found (left off): {', '.join(missing)}")
    if excluded:
        print(f"Excluded by you: {', '.join(excluded)}")
    log = ROOT / "ncaa_pool" / "data" / "cards.csv"
    new = not log.exists()
    with log.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["made_at", "pool", "pool_file", "rank", "key", "pick", "kind", "prob", "value_pts", "market"])
        for i, o in enumerate(card["picks"], 1):
            w.writerow([stamp, a.pool, Path(a.pool_file).name, i, "Y" if i <= k else "N", o.label, o.kind,
                        round(o.prob, 4), round(o.gap, 2), o.extra["market"]])


if __name__ == "__main__":
    main()
