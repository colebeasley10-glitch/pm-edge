"""Read-only Kalshi market discovery for the paper AutoTrader."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable

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
    observed_at: datetime | None = None


def discover(markets: Iterable[dict], *, max_spread=Decimal("0.08"),
             min_volume=Decimal("100"), min_depth=Decimal("1")) -> list[Candidate]:
    """Quote-quality screen only; no EV claim until an independent probability exists."""
    found = []
    for m in markets:
        if str(m.get("status", "")).lower() not in ("active", "open"):
            continue
        try:
            ticker = str(m["ticker"])
            volume = D(m.get("volume_24h") or "0")
            if not ticker or volume < min_volume:
                continue
            observed_at = m.get("_quote_observed_at")
            for side in ("YES", "NO"):
                prefix = side.lower()
                ask = m.get(prefix + "_ask")
                bid = m.get(prefix + "_bid")
                depth = D(m.get(prefix + "_ask_size") or "0")
                if ask is None or bid is None:
                    continue
                ask, bid = D(ask), D(bid)
                spread = ask - bid
                if not (D("0") < bid <= ask < D("1")) or spread > max_spread or depth < min_depth:
                    continue
                found.append(Candidate(
                    ticker, str(m.get("title") or ticker), side,
                    ask, depth, spread, volume, observed_at,
                ))
        except (KeyError, ValueError, TypeError, InvalidOperation, ArithmeticError):
            continue
    return sorted(found, key=lambda c: (c.spread, -c.volume_24h, c.ticker, c.side))


def _best_bid(levels):
    """Return (price, size) for the highest fixed-point orderbook bid."""
    best = None
    for level in levels or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            price, size = D(level[0]), D(level[1])
        except (ValueError, TypeError, InvalidOperation):
            continue
        if not (D("0") < price < D("1")) or size <= 0:
            continue
        if best is None or price > best[0]:
            best = (price, size)
    return best


def _with_orderbook_depth(market, orderbook, *, observed_at=None):
    """Attach executable asks/depth using reciprocal YES/NO bids."""
    enriched = dict(market)
    book = orderbook.get("orderbook_fp") or {}
    yes = _best_bid(book.get("yes_dollars") or book.get("yes"))
    no = _best_bid(book.get("no_dollars") or book.get("no"))

    if yes is not None:
        yes_bid, yes_size = yes
        enriched["yes_bid"] = yes_bid
        enriched["no_ask"] = D("1") - yes_bid
        enriched["no_ask_size"] = yes_size
    if no is not None:
        no_bid, no_size = no
        enriched["no_bid"] = no_bid
        enriched["yes_ask"] = D("1") - no_bid
        enriched["yes_ask_size"] = no_size
    enriched["_quote_observed_at"] = observed_at
    return enriched


def _normalized_market(raw, normalize_market):
    """Normalize with conservative fallbacks for public API field variants."""
    m = normalize_market(raw)
    if not m.get("volume_24h"):
        for key in ("volume_24h_fp", "volume_24h"):
            value = raw.get(key)
            if value not in (None, ""):
                try:
                    m["volume_24h"] = D(value)
                    break
                except (InvalidOperation, ValueError, TypeError):
                    pass
    return m


def scan_public(client, *, max_markets=3000, max_orderbooks=250,
                max_spread=Decimal("0.08"), min_volume=Decimal("100"),
                min_depth=Decimal("1"), clock: Callable[[], datetime] | None = None,
                ticker_filter=None, diagnostics=None):
    """Discover executable public quotes with real top-of-book depth.

    Market-list prices are only a cheap prefilter. Executable asks and sizes are
    derived from the public orderbook. Each orderbook observation is timestamped
    so the decision layer can fail closed on stale quotes.
    """
    from pmedge.kalshi import normalize_market

    clock = clock or (lambda: datetime.now(timezone.utc))
    wanted = set(ticker_filter or [])
    diag = diagnostics if diagnostics is not None else {}
    market_fetch_errors = 0
    if wanted:
        raw_markets = []
        for ticker in sorted(wanted):
            try:
                row = client.get_market(ticker)
                if isinstance(row, dict) and row:
                    raw_markets.append(row)
            except Exception:
                market_fetch_errors += 1
        market_source = "targeted_get_market"
    else:
        raw_markets = client.list_open_markets(max_markets=max_markets)
        market_source = "open_market_list"
    normalized = [_normalized_market(m, normalize_market) for m in raw_markets]
    diag.update({
        "market_source": market_source,
        "market_rows": len(normalized),
        "market_fetch_errors": market_fetch_errors,
        "wanted": len(wanted),
        "wanted_found": 0,
        "status_rejected": 0,
        "volume_rejected": 0,
        "quote_missing_or_invalid": 0,
        "spread_rejected": 0,
        "prefiltered": 0,
        "orderbooks_attempted": 0,
        "orderbooks_ok": 0,
        "orderbooks_error": 0,
        "candidates": 0,
    })
    prefiltered = []
    for m in normalized:
        if wanted and m.get("ticker") not in wanted:
            continue
        if wanted:
            diag["wanted_found"] += 1
        if str(m.get("status", "")).lower() not in ("active", "open"):
            diag["status_rejected"] += 1
            continue
        try:
            if not m.get("ticker") or D(m.get("volume_24h") or "0") < min_volume:
                diag["volume_rejected"] += 1
                continue
            viable = False
            saw_valid_pair = False
            saw_wide_pair = False
            for side in ("yes", "no"):
                bid, ask = m.get(side + "_bid"), m.get(side + "_ask")
                if bid is None or ask is None:
                    continue
                bid, ask = D(bid), D(ask)
                if D("0") < bid <= ask < D("1"):
                    saw_valid_pair = True
                    if ask - bid <= max_spread:
                        viable = True
                        break
                    saw_wide_pair = True
            if viable:
                prefiltered.append(m)
            elif saw_wide_pair:
                diag["spread_rejected"] += 1
            elif not saw_valid_pair:
                diag["quote_missing_or_invalid"] += 1
        except (ValueError, TypeError, InvalidOperation, ArithmeticError):
            diag["quote_missing_or_invalid"] += 1
            continue
    diag["prefiltered"] = len(prefiltered)

    prefiltered.sort(key=lambda m: D(m.get("volume_24h") or "0"), reverse=True)
    enriched = []
    for m in prefiltered[:max(0, int(max_orderbooks))]:
        diag["orderbooks_attempted"] += 1
        try:
            book = client.get_orderbook(m["ticker"], depth=1)
            observed_at = clock().astimezone(timezone.utc)
            enriched.append(_with_orderbook_depth(m, book, observed_at=observed_at))
            diag["orderbooks_ok"] += 1
        except Exception:
            diag["orderbooks_error"] += 1
            continue

    found = discover(enriched, max_spread=max_spread, min_volume=min_volume, min_depth=min_depth)
    diag["candidates"] = len(found)
    return found
