"""CLV checkpoint capture for OPEN paper positions in every lane.

For each open position we find its CLV target time (event start if a
trustworthy one is recorded on the thesis, otherwise the Kalshi market close
time) and capture the public orderbook once at:

  T_MINUS_10      ~10 minutes before the target
  START_OR_CLOSE  just before (or at) the target

The capture is read-only: it never touches a portfolio. Rows are written to
the recorder's ``ob_top`` (so the point-in-time ``book_at`` used by learning
can see them) and to ``clv_checkpoints`` with an explicit checkpoint label.
These observations are only used to EVALUATE entries after the fact.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .storage import load_portfolio

T_MINUS_10 = "T_MINUS_10"
START_OR_CLOSE = "START_OR_CLOSE"

# Windows sized for the ~60s recorder cycle so each checkpoint is hit once.
WINDOWS = {
    T_MINUS_10: (timedelta(seconds=-660), timedelta(seconds=-540)),
    START_OR_CLOSE: (timedelta(seconds=-120), timedelta(seconds=60)),
}


def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None  # never guess a zone
    return dt.astimezone(timezone.utc)


def default_lane_portfolios():
    """Portfolio files for every paper lane (same env names as the worker)."""
    return {
        "value_edge": os.environ.get("AUTOTRADER_STATE", "/data/autotrader-paper.json"),
        "whale_flow": os.environ.get("WHALE_PAPER_PORTFOLIO_STATE", "/data/whale-paper-portfolio.json"),
        "quick_whale": os.environ.get("WHALE_QUICK_PORTFOLIO_STATE", "/data/quick-whale-paper-portfolio.json"),
        "quick_whale_v2": os.environ.get(
            "WHALE_QUICK_V2_PORTFOLIO_STATE", "/data/quick-whale-v2-paper-portfolio.json"
        ),
        "quick_whale_v3": os.environ.get(
            "WHALE_QUICK_V3_PORTFOLIO_STATE", "/data/quick-whale-v3-paper-portfolio.json"
        ),
    }


def position_key(lane, position):
    thesis = getattr(position, "thesis", None) or {}
    ref = (
        thesis.get("created_at")
        or thesis.get("source_trade_id")
        or getattr(position, "fair_snapshot_id", None)
        or f"{position.entry}x{position.contracts}"
    )
    return f"{lane}:{position.ticker}:{position.side}:{ref}"


def clv_target(lane, position):
    """(target_datetime, provenance) or (None, reason). Never fabricated."""
    thesis = getattr(position, "thesis", None) or {}
    start = _parse(thesis.get("event_start_at"))
    if start is not None:
        provenance = thesis.get("event_start_provenance") or (
            "sportsbook_commence_time" if lane == "value_edge" else "thesis_event_start_at"
        )
        return start, str(provenance)
    close = _parse(thesis.get("close_time"))
    if close is not None:
        return close, str(thesis.get("close_time_provenance") or "kalshi_market_close_time")
    return None, "no_event_start_or_close_time_on_thesis"


def due_checkpoint(target, now):
    for name, (lo, hi) in WINDOWS.items():
        if target + lo <= now <= target + hi:
            return name
    return None


def _top_from_book(orderbook):
    book = (orderbook or {}).get("orderbook_fp") or {}

    def best(levels):
        top = None
        for level in levels or []:
            try:
                price, size = Decimal(str(level[0])), Decimal(str(level[1]))
            except Exception:
                continue
            if Decimal("0") < price < Decimal("1") and size > 0 and (top is None or price > top):
                top = price
        return top

    yes_bid = best(book.get("yes_dollars") or book.get("yes"))
    no_bid = best(book.get("no_dollars") or book.get("no"))
    return {
        "yes_bid": None if yes_bid is None else float(yes_bid),
        "no_bid": None if no_bid is None else float(no_bid),
        "yes_ask": None if no_bid is None else float(Decimal("1") - no_bid),
        "no_ask": None if yes_bid is None else float(Decimal("1") - yes_bid),
    }


def capture_clv_checkpoints(store, client, *, now=None, lane_portfolios=None,
                            close_time_cache=None, max_books=40):
    """Capture due CLV checkpoints for open positions. Returns a summary dict."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lane_portfolios = lane_portfolios or default_lane_portfolios()
    cache = close_time_cache if close_time_cache is not None else {}
    summary = {"open_positions": 0, "captured": 0, "already_captured": 0,
               "no_target": 0, "errors": 0, "by_lane": {}}
    books = 0
    for lane, path in lane_portfolios.items():
        try:
            if not path or not os.path.exists(path):
                continue
            portfolio = load_portfolio(path)
        except Exception:
            summary["errors"] += 1
            continue
        lane_stats = summary["by_lane"].setdefault(lane, {"open": 0, "captured": 0, "no_target": 0})
        for position in list(portfolio.positions.values()):
            summary["open_positions"] += 1
            lane_stats["open"] += 1
            target, provenance = clv_target(lane, position)
            if target is None:
                # Legacy positions without timing: use the market's own close time
                # (labelled as a lookup, never as an event start).
                close = cache.get(position.ticker)
                if close is None and client is not None:
                    try:
                        close = _parse((client.get_market(position.ticker) or {}).get("close_time"))
                    except Exception:
                        close = None
                    cache[position.ticker] = close
                if close is None:
                    summary["no_target"] += 1
                    lane_stats["no_target"] += 1
                    continue
                target, provenance = close, "kalshi_market_close_time_lookup"
            checkpoint = due_checkpoint(target, now)
            if checkpoint is None:
                continue
            key = position_key(lane, position)
            if store.has_clv_checkpoint(key, checkpoint):
                summary["already_captured"] += 1
                continue
            if books >= max_books:
                break
            try:
                book = client.get_orderbook(position.ticker, depth=20)
                books += 1
                received_at = now
                top = _top_from_book(book)
                store.record_snapshot(position.ticker, book, received_at=received_at,
                                      origin=f"clv:{checkpoint}")
                store.record_top(
                    {"ticker": position.ticker, **top},
                    received_at=received_at, origin=f"clv:{checkpoint}",
                )
                if store.record_clv_checkpoint(
                    position_key=key, checkpoint=checkpoint, lane=lane,
                    ticker=position.ticker, side=position.side,
                    target_at=target, target_provenance=provenance,
                    entry_price=position.entry, received_at=received_at, **top,
                ):
                    summary["captured"] += 1
                    lane_stats["captured"] += 1
            except Exception:
                summary["errors"] += 1
    return summary
