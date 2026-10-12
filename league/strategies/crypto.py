"""Crypto: Kalshi hourly BTC/ETH "above $X at 5pm" markets vs a simple
volatility model on the live Coinbase price.

Fair = P(price at expiry > strike) under a lognormal walk with volatility
from the last 72 hourly candles, inflated for fat tails. Fast bots trade
these, so this lane is expected to struggle; it's in the league to prove it.
"""
from __future__ import annotations

import math
from datetime import datetime

import requests

from league.book import Pick, f, parse_dt

NAME = "crypto"
DIAG: dict = {}
MIN_EDGE = 0.05
MAX_PICKS = 4
TAIL_INFLATE = 1.25
ASSETS = {"KXBTCD": "BTC-USD", "KXETHD": "ETH-USD"}


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def hourly_vol(candles: list) -> float | None:
    """Std dev of hourly log returns. Coinbase candles: [time, low, high, open, close, vol], newest first."""
    closes = [c[4] for c in sorted(candles, key=lambda c: c[0])][-73:]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 24:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def prob_above(spot: float, strike: float, hours: float, vol_h: float) -> float:
    sd = vol_h * TAIL_INFLATE * math.sqrt(max(hours, 1e-6))
    if sd <= 0:
        return 1.0 if spot > strike else 0.0
    return _phi((math.log(spot / strike) - 0.5 * sd * sd) / sd)


def run(kalshi, now: datetime, session=None) -> list[Pick]:
    s = session or requests.Session()
    picks: list[Pick] = []
    DIAG.clear()
    DIAG.update({"markets_priced": 0, "vol": {}, "best": []})
    for series, product in ASSETS.items():
        try:
            spot = float(s.get(f"https://api.exchange.coinbase.com/products/{product}/ticker", timeout=15).json()["price"])
            candles = s.get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                            params={"granularity": 3600}, timeout=15).json()
            vol = hourly_vol(candles)
        except Exception as exc:
            print("CRYPTO_PRICE_ERROR", product, type(exc).__name__, str(exc)[:120])
            continue
        DIAG["vol"][product] = vol
        if not vol:
            continue
        for ev in kalshi.open_events(series, limit=6):
            for m in ev["markets"]:
                strike = f(m.get("floor_strike"))
                expiry = parse_dt(m.get("close_time"))
                if strike is None or expiry is None or str(m.get("strike_type")) != "greater":
                    continue
                hours = (expiry - now).total_seconds() / 3600.0
                if not (0.75 <= hours <= 10):
                    continue
                p = prob_above(spot, strike, hours, vol)
                DIAG["markets_priced"] += 1
                ya = m.get("yes_ask")
                if ya is not None:
                    DIAG["best"].append((round(p - ya, 3), m.get("ticker")))
                if not (0.08 <= p <= 0.92):
                    continue
                for side, fair in (("YES", p), ("NO", 1.0 - p)):
                    pk = Pick(NAME, m, side, fair,
                              f"{product[:3]} ${spot:,.0f} now, {hours:.1f}h to go, model {p:.0%} above ${strike:,.0f}",
                              category="Crypto", min_edge=MIN_EDGE,
                              extra={"title": f"{product[:3]} {'above' if side == 'YES' else 'below'} ${strike:,.0f} — {ev.get('title')}"})
                    e = pk.edge()
                    if e is not None and e >= MIN_EDGE:
                        picks.append(pk)
    DIAG["best"] = sorted(DIAG["best"], key=lambda x: -abs(x[0]))[:6]
    picks.sort(key=lambda p: -(p.edge() or 0))
    return picks[:MAX_PICKS]
