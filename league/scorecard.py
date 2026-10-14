"""Running scorecard for one league strategy: is it good enough to risk real money?

Pure pandas, no I/O, so the app and tests share it.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")

# The bar a strategy has to clear before a small real-money test makes sense.
READY = {
    "settled": 150,     # enough bets that a hot streak can't fake it
    "days": 28,         # at least four weeks of slates
    "clv_bets": 50,     # bets with a closing price to compare against
    "clv_cents": 1.0,   # average beat-the-close, in cents
}

_SPORT_RULES = [
    (("ATP", "WTA", "ITF", "TENNIS"), "Tennis"),
    (("NCAAF", "CFB"), "College football"),
    (("NFL",), "NFL"),
    (("NCAAMB", "NCAAWB", "NCAAB"), "College basketball"),
    (("NBA", "WNBA"), "Basketball"),
    (("MLB",), "MLB"),
    (("NHL",), "NHL"),
    (("SERIEA", "EPL", "LALIGA", "BUNDESLIGA", "LIGUE1", "MLS", "UCL", "UEL", "SOCCER",
      "SLGREECE", "EREDIVISIE", "LIGAMX", "CHAMPIONS", "AFCCL"), "Soccer"),
    (("CRICKET", "T20", "ODI", "IPL"), "Cricket"),
    (("LOL", "CS2", "DOTA", "VALORANT", "ESPORT"), "Esports"),
    (("UFC", "MMA", "BOXING"), "Fighting"),
    (("PGA", "GOLF", "LPGA"), "Golf"),
    (("NASCAR", "F1", "INDY"), "Racing"),
]


def market_group(event_ticker: str, category: str = "") -> str:
    series = str(event_ticker or "").split("-")[0].upper()
    series = series[2:] if series.startswith("KX") else series
    for keys, label in _SPORT_RULES:
        if any(k in series for k in keys):
            return label
    return str(category or "Other") or "Other"


def _prep(trades: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if trades is None or trades.empty or "strategy" not in trades:
        return pd.DataFrame()
    g = trades[trades["strategy"] == strategy].copy()
    if g.empty:
        return g
    g["status"] = g.get("status", pd.Series(dtype=str)).fillna("")
    for col in ("pl", "cost", "fee", "clv_cents"):
        g[col] = pd.to_numeric(g.get(col), errors="coerce")
    g["staked"] = g["cost"].fillna(0) + g["fee"].fillna(0)
    g["opened"] = pd.to_datetime(g.get("opened_at"), errors="coerce", utc=True)
    g["settled"] = pd.to_datetime(g.get("settled_at"), errors="coerce", utc=True)
    g["group"] = [market_group(e, c) for e, c in zip(g.get("event_ticker", ""), g.get("category", ""))]
    return g


def _summary(s: pd.DataFrame) -> dict:
    won, lost = int((s["status"] == "won").sum()), int((s["status"] == "lost").sum())
    staked, pl = float(s["staked"].sum()), float(s["pl"].fillna(0).sum())
    return {"won": won, "lost": lost, "bets": won + lost, "staked": staked, "pl": pl,
            "roi": pl / staked if staked else None,
            "win_rate": won / (won + lost) if won + lost else None}


def scorecard(trades: pd.DataFrame, strategy: str, now: pd.Timestamp | None = None) -> dict:
    """Everything the Sharp scorecard shows. Empty dict if the strategy has no bets."""
    g = _prep(trades, strategy)
    if g.empty:
        return {}
    now = now or pd.Timestamp.now(tz="UTC")
    s = g[g["status"].isin(["won", "lost"])]
    out = _summary(s) if not s.empty else {"won": 0, "lost": 0, "bets": 0, "staked": 0.0, "pl": 0.0,
                                             "roi": None, "win_rate": None}
    out["open"] = int((g["status"] == "open").sum())

    clv = s["clv_cents"].dropna()
    out["clv_n"] = int(len(clv))
    out["clv"] = float(clv.mean()) if len(clv) else None
    out["beat_close"] = float((clv > 0).mean()) if len(clv) else None

    first = g["opened"].min()
    out["since"] = first
    out["days"] = int((now - first).total_seconds() // 86400) + 1 if pd.notna(first) else 0

    # Daily results by the Eastern date each bet settled, with a running total.
    daily = pd.DataFrame(columns=["day", "won", "lost", "pl", "staked", "roi", "total"])
    if not s.empty:
        d = s.assign(day=s["settled"].dt.tz_convert(EASTERN).dt.date)
        daily = (d.groupby("day")
                 .agg(won=("status", lambda x: int((x == "won").sum())),
                      lost=("status", lambda x: int((x == "lost").sum())),
                      pl=("pl", "sum"), staked=("staked", "sum"))
                 .reset_index().sort_values("day"))
        daily["roi"] = daily["pl"] / daily["staked"].where(daily["staked"] > 0)
        daily["total"] = daily["pl"].cumsum()
    out["daily"] = daily

    # Bet-by-bet running profit for the chart.
    curve = s.sort_values("settled")[["settled", "pl"]].dropna(subset=["settled"])
    curve = curve.assign(total=curve["pl"].fillna(0).cumsum())
    out["curve"] = curve[["settled", "total"]].rename(columns={"settled": "when", "total": "Profit"})

    groups = []
    for name, part in s.groupby("group"):
        row = _summary(part)
        row["group"] = name
        groups.append(row)
    out["groups"] = sorted(groups, key=lambda r: -r["bets"])
    out["top_share"] = (out["groups"][0]["bets"] / out["bets"]) if groups and out["bets"] else None

    out["checks"] = [
        ("Settled bets", out["bets"], READY["settled"], out["bets"] >= READY["settled"],
         f"{out['bets']} of {READY['settled']}"),
        ("Days of results", out["days"], READY["days"], out["days"] >= READY["days"],
         f"{out['days']} of {READY['days']}"),
        ("Bets with a closing price", out["clv_n"], READY["clv_bets"], out["clv_n"] >= READY["clv_bets"],
         f"{out['clv_n']} of {READY['clv_bets']}"),
        ("Beats the close", out["clv"] or 0.0, READY["clv_cents"],
         out["clv"] is not None and out["clv_n"] >= READY["clv_bets"] and out["clv"] >= READY["clv_cents"],
         f"{out['clv']:+.1f}¢ avg on {out['clv_n']} bets (need +{READY['clv_cents']:.0f}¢ on {READY['clv_bets']})" if out["clv"] is not None else "no closing prices yet"),
        ("Profitable after fees", out["pl"], 0, out["bets"] >= READY["settled"] and out["pl"] > 0,
         f"{out['roi']:+.1%} ROI" if out["roi"] is not None else "no settled bets yet"),
    ]
    out["ready"] = all(c[3] for c in out["checks"])
    return out
