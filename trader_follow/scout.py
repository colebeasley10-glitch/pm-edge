"""Scout: find other sharp Polymarket sports bettors worth following (READ-ONLY, no alerts).

python -m trader_follow.scout  -> trader_follow/data/scout/report.json (+ raw samples)

1. Pull Polymarket's sports leaderboard (all-time profit, and by volume).
2. For each candidate wallet, page through its closed positions and score them by sport:
   positions, dollars bought, realized P/L, ROI, win rate, typical size, how recent.
Only single games count (event slugs that end in a date, e.g. nba-lac-okc-2026-10-25);
season futures and props are tallied separately so one lucky futures hit can't carry a resume.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

OUT = Path("trader_follow/data/scout")
DATA = "https://data-api.polymarket.com"
GAME_SLUG = re.compile(r"^([a-z0-9]+)-.+-(\d{4}-\d{2}-\d{2})(?:-.*)?$")
SOCCER = {"epl", "ucl", "uel", "uecl", "lal", "sea", "bun", "fl1", "unl", "efl", "mls", "ere", "por",
          "facup", "fifwc", "bra", "arg", "mex", "tur", "spl", "jap", "kor"}
NAMES = {"nfl": "NFL", "cfb": "College football", "ncaaf": "College football", "nba": "NBA",
         "cbb": "College basketball", "wnba": "WNBA", "nhl": "NHL", "mlb": "MLB", "atp": "Tennis",
         "wta": "Tennis", "ufc": "UFC/MMA", "mma": "UFC/MMA", "boxing": "Boxing"}
MAX_CANDIDATES = 40
MAX_PAGES = 40          # x 50 positions = 2,000 per wallet
S = requests.Session()
S.headers["User-Agent"] = "sharpiq-scout/1.0"


def get(path: str, **params):
    for i in range(3):
        try:
            r = S.get(DATA + path, params=params, timeout=25)
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise last  # noqa: F821


def sport_of(slug: str) -> str | None:
    m = GAME_SLUG.match(slug or "")
    if not m:
        return None
    code = m.group(1)
    return "Soccer" if code in SOCCER else NAMES.get(code, code.upper())


def leaderboard(raw: dict) -> list[dict]:
    """Union of sports leaderboards (profit + volume, all-time + month). Tries the known endpoints."""
    seen: dict[str, dict] = {}
    for order in ("PNL", "VOL"):
        for period in ("ALL", "MONTH"):
            for path, params in (("/v1/leaderboard", {"category": "SPORTS", "timePeriod": period,
                                                      "orderBy": order, "limit": 50}),):
                try:
                    data = get(path, **params)
                except Exception as exc:
                    raw[f"lb_error_{order}_{period}"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    continue
                rows = data if isinstance(data, list) else data.get("data") or data.get("leaderboard") or []
                raw.setdefault("lb_sample", rows[:2])
                for r in rows:
                    w = r.get("proxyWallet") or r.get("wallet") or r.get("address")
                    if w:
                        e = seen.setdefault(w.lower(), {"wallet": w, "name": r.get("userName") or r.get("name") or "",
                                                         "lb_pnl": None, "lb_vol": None, "boards": []})
                        e["boards"].append(f"{order}/{period}")
                        if period == "ALL":
                            e["lb_pnl"] = r.get("pnl", e["lb_pnl"])
                            e["lb_vol"] = r.get("vol", e["lb_vol"])
    return list(seen.values())


def closed_positions(wallet: str, raw: dict) -> list[dict]:
    out = []
    for page in range(MAX_PAGES):
        rows = get("/closed-positions", user=wallet, limit=50, offset=page * 50,
                   sortBy="TIMESTAMP", sortDirection="DESC")
        if not isinstance(rows, list) or not rows:
            break
        raw.setdefault("closed_sample", rows[:2])
        out += rows
        if len(rows) < 50:
            break
    return out


def resolved_open(wallet: str, raw: dict) -> list[dict]:
    """Settled positions still sitting in the wallet (mostly losers that expired at 0, which never
    show up as 'closed'). Converted to the closed-position shape so they can be scored together."""
    out = []
    for page in range(20):
        rows = get("/positions", user=wallet, limit=500, offset=page * 500, sizeThreshold=0)
        if not isinstance(rows, list) or not rows:
            break
        raw.setdefault("open_sample", rows[:2])
        for r in rows:
            cur = float(r.get("curPrice") or 0)
            if cur not in (0.0, 1.0) and not r.get("redeemable"):
                continue          # still live: not a result yet
            out.append({**r, "totalBought": r.get("totalBought") or r.get("size"),
                        "realizedPnl": r.get("cashPnl"), "timestamp": r.get("timestamp") or _end_ts(r)})
        if len(rows) < 500:
            break
    return out


def _end_ts(r: dict) -> float | None:
    try:
        return datetime.fromisoformat(str(r.get("endDate"))[:10]).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def score(positions: list[dict], now: datetime) -> dict:
    def block(ps):
        bought = sum(float(p.get("totalBought") or 0) * float(p.get("avgPrice") or 0) for p in ps)
        pnl = sum(float(p.get("realizedPnl") or 0) for p in ps)
        sizes = [float(p.get("totalBought") or 0) * float(p.get("avgPrice") or 0) for p in ps]
        wins = sum(float(p.get("realizedPnl") or 0) > 0 for p in ps)
        return {"positions": len(ps), "bought": round(bought), "pnl": round(pnl),
                "roi": round(pnl / bought, 4) if bought else None,
                "win_rate": round(wins / len(ps), 3) if ps else None,
                "median_size": round(statistics.median(sizes)) if sizes else None}

    games = [p for p in positions if sport_of(p.get("eventSlug") or p.get("slug") or "")]
    other = [p for p in positions if p not in games]
    by_sport: dict[str, list] = {}
    for p in games:
        by_sport.setdefault(sport_of(p.get("eventSlug") or p.get("slug") or ""), []).append(p)
    cutoff = (now - timedelta(days=60)).timestamp()
    recent = [p for p in games if float(p.get("timestamp") or 0) >= cutoff]
    ts = [float(p.get("timestamp") or 0) for p in positions if p.get("timestamp")]
    return {"games": block(games), "games_last_60d": block(recent), "futures_props": block(other),
            "by_sport": {k: block(v) for k, v in sorted(by_sport.items(), key=lambda kv: -len(kv[1]))},
            "newest": datetime.fromtimestamp(max(ts), timezone.utc).isoformat()[:10] if ts else None,
            "oldest": datetime.fromtimestamp(min(ts), timezone.utc).isoformat()[:10] if ts else None,
            "capped": len(positions) >= MAX_PAGES * 50}


def main() -> None:
    now = datetime.now(timezone.utc)
    OUT.mkdir(parents=True, exist_ok=True)
    raw: dict = {"at": now.isoformat()}
    cands = leaderboard(raw)
    cands.sort(key=lambda c: -(float(c.get("lb_pnl") or 0)))
    extra = [w for w in sys.argv[1:] if w.startswith("0x")]
    for w in extra:
        if w.lower() not in {c["wallet"].lower() for c in cands}:
            cands.insert(0, {"wallet": w, "name": "", "lb_pnl": None, "lb_vol": None, "boards": ["manual"]})
    report = []
    for c in cands[:MAX_CANDIDATES + len(extra)]:
        try:
            closed = closed_positions(c["wallet"], raw)
            ids = {(p.get("conditionId"), p.get("outcomeIndex")) for p in closed}
            held = [p for p in resolved_open(c["wallet"], raw) if (p.get("conditionId"), p.get("outcomeIndex")) not in ids]
            res = score(closed + held, now)
            res["closed_n"], res["held_resolved_n"] = len(closed), len(held)
            report.append({**c, **res})
        except Exception as exc:
            report.append({**c, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        time.sleep(0.5)
    (OUT / "report.json").write_text(json.dumps({"at": now.isoformat(), "traders": report}, indent=1))
    (OUT / "raw_samples.json").write_text(json.dumps(raw, indent=1, default=str)[:200_000])
    print(f"scouted {len(report)} wallets; leaderboard candidates {len(cands)}")


if __name__ == "__main__":
    main()
