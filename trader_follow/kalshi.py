"""Kalshi side of the Trader Scout (READ-ONLY): rank Kalshi's public sports traders.

python -m trader_follow.kalshi  -> trader_follow/data/scout/kalshi_report.json

Uses Kalshi's public social endpoints (no keys):
  /v1/social/leaderboard        sports profit by period (all time, monthly, weekly)
  /v1/social/profile/metrics    overall P/L, dollars traded, markets traded
  /v1/social/trades             recent trades, only when the trader lets them be seen
  /v1/social/profile/holdings   open positions (same visibility rule)
Money fields from /profile/metrics are in 1/10,000 of a dollar.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.elections.kalshi.com/v1/social"
OUT = Path("trader_follow/data/scout/kalshi_report.json")
PERIODS = ("all_time", "monthly", "weekly")
S = requests.Session()
S.headers["User-Agent"] = "sharpiq-scout/1.0"
SPORT_OF = (("NFL", ("KXNFL",)), ("College football", ("KXNCAAF",)), ("NBA", ("KXNBA",)),
            ("MLB", ("KXMLB",)), ("NHL", ("KXNHL",)), ("College basketball", ("KXNCAAMB", "KXNCAAB")),
            ("Tennis", ("KXATP", "KXWTA", "KXITF")), ("UFC/MMA", ("KXUFC",)), ("Golf", ("KXPGA",)),
            ("Soccer", ("KXEPL", "KXUCL", "KXLALIGA", "KXSERIEA", "KXBUNDES", "KXMLS", "KXLIGA", "KXUEL")))


def get(path: str, **params):
    for i in range(3):
        r = S.get(f"{API}{path}", params=params, timeout=25)
        if r.status_code == 429:
            time.sleep(4 * (i + 1))
            continue
        if r.status_code >= 400:
            return {"_status": r.status_code, "_body": r.text[:300]}
        return r.json()
    return {"_status": 429}


def board(metric: str, period: str, limit: int = 100) -> list[dict]:
    d = get("/leaderboard", metric_name=metric, category="Sports", time_period=period, limit=limit)
    return d.get("rank_list") or [] if isinstance(d, dict) else []


def sport_of(ticker: str) -> str:
    t = str(ticker or "").upper()
    for name, prefixes in SPORT_OF:
        if t.startswith(prefixes):
            return name
    return "Other"


def main() -> None:
    now = datetime.now(timezone.utc)
    out: dict = {"at": now.isoformat(), "boards": {}, "traders": []}
    pnl: dict[str, dict] = {}
    for period in PERIODS:
        rows = board("projected_pnl", period)
        out["boards"][f"pnl_{period}"] = len(rows)
        for r in rows:
            pnl.setdefault(r["nickname"], {})[period] = r.get("value")
    vol = {r["nickname"]: r.get("value") for r in board("volume", "all_time")}
    # candidates: top all-time sports profit, plus anyone top-100 this month
    names = [n for n, v in sorted(pnl.items(), key=lambda kv: -(kv[1].get("all_time") or 0))][:60]
    names += [n for n, v in pnl.items() if v.get("monthly") and n not in names][:20]
    sample_done = False
    for n in names:
        if not n or n.lower().startswith("anonymous"):
            continue
        rec = {"nickname": n, "sports_pnl": pnl.get(n, {}), "sports_volume_rank_value": vol.get(n)}
        m = get("/profile/metrics", nickname=n)
        met = (m or {}).get("metrics") or {}
        if met:
            rec["overall"] = {"pnl": round(met.get("pnl", 0) / 10_000), "traded": round(met.get("dollars_traded", 0) / 10_000),
                              "markets": met.get("num_markets_traded"), "volume_contracts": met.get("volume")}
            t = rec["overall"]["traded"]
            rec["overall"]["roi_on_traded"] = round(rec["overall"]["pnl"] / t, 4) if t else None
        prof = get("/profile", nickname=n)
        sp = (prof or {}).get("social_profile") or {}
        rec["joined"], rec["top_categories"] = sp.get("joined_at"), sp.get("top_categories")
        tr = get("/trades", nickname=n, limit=200)
        trades = (tr or {}).get("trades") or []
        rec["trades_visible"] = (tr or {}).get("visibility_state", "?") != "hidden" and bool(trades)
        if trades and not sample_done:
            out["trade_sample"] = trades[:3]
            sample_done = True
        if trades:
            by: dict[str, int] = {}
            for x in trades:
                k = sport_of(x.get("ticker") or x.get("market_ticker") or "")
                by[k] = by.get(k, 0) + 1
            rec["recent_trade_mix"] = dict(sorted(by.items(), key=lambda kv: -kv[1]))
            ts = [x.get("created_time") or x.get("ts") for x in trades if x.get("created_time") or x.get("ts")]
            rec["last_trade"] = max(ts) if ts else None
        h = get("/profile/holdings", nickname=n, limit=50)
        if isinstance(h, dict) and "_status" not in h:
            out.setdefault("holdings_sample", h if not out.get("holdings_sample") else out["holdings_sample"])
            rec["holdings_keys"] = list(h.keys())[:6]
        out["traders"].append(rec)
        time.sleep(0.4)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str)[:3_000_000])
    print(f"kalshi traders scouted: {len(out['traders'])}")


if __name__ == "__main__":
    main()
