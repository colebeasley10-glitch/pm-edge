"""Read-only Kalshi market discovery for Phase 1. No order submission."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

D = lambda x: Decimal(str(x))

@dataclass(frozen=True)
class Candidate:
    ticker: str
    title: str
    side: str
    executable_ask: Decimal
    available: Decimal
    spread: Decimal
    volume_24h: Decimal

def discover(markets: Iterable[dict], *, max_spread=Decimal("0.08"),
             min_volume=Decimal("100"), min_depth=Decimal("1")) -> list[Candidate]:
    """Quote-quality screen only; no EV claim until an independent probability model exists."""
    found = []
    for m in markets:
        if str(m.get("status", "")).lower() not in ("active", "open"):
            continue
        try:
            ticker = str(m["ticker"])
            volume = D(m.get("volume_24h") or "0")
            if not ticker or volume < min_volume:
                continue
            for side in ("YES", "NO"):
                ask = m.get(side.lower() + "_ask")
                bid = m.get(side.lower() + "_bid")
                depth = D(m.get(side.lower() + "_ask_size") or "0")
                if ask is None or bid is None:
                    continue
                ask, bid = D(ask), D(bid)
                spread = ask - bid
                if not (D("0") < bid <= ask < D("1")) or spread > max_spread or depth < min_depth:
                    continue
                found.append(Candidate(ticker, str(m.get("title") or ticker), side,
                                       ask, depth, spread, volume))
        except (KeyError, ValueError, ArithmeticError):
            continue
    return sorted(found, key=lambda c: (c.spread, -c.volume_24h, c.ticker, c.side))

def scan_public(client, *, max_markets=3000):
    """Use the existing unauthenticated KalshiPublicClient, never KalshiAuthClient."""
    from pmedge.kalshi import normalize_market
    return discover(normalize_market(m) for m in client.list_open_markets(max_markets=max_markets))
