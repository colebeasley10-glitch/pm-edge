"""Append-only raw sportsbook capture for AutoTrader research.

This module records every Odds API fetch and every returned outcome, including
unchanged quotes. It never records API keys and does not influence trading
decisions; failures are swallowed so research logging cannot create a trade.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from .recorder_store import RecorderStore, utc_iso


def _to_int(value):
    try:
        return None if value in (None, "") else int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _american_from_decimal(value):
    try:
        d = float(value)
        if d <= 1:
            return None
        if d >= 2:
            return int(round((d - 1) * 100))
        return int(round(-100 / (d - 1)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _events(payload):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        # Event-specific endpoint returns one event object.
        if payload.get("id") or payload.get("bookmakers"):
            return [payload]
        rows = payload.get("events")
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []


def record_odds_response(
    *,
    db_path: str | None = None,
    endpoint: str,
    sport: str | None,
    regions: str | None,
    markets: str | None,
    sent_at,
    received_at,
    payload=None,
    usage: dict | None = None,
    http_status: int | None = None,
    error: str | None = None,
) -> dict:
    """Record one provider response and all outcome quotes.

    source_ts is bookmaker.last_update where supplied; received_at remains the
    only point at which the quote becomes knowable to replay/live logic.
    """
    db_path = db_path or os.getenv("AUTOTRADER_RECORDER_DB", "").strip()
    if not db_path:
        return {"enabled": False, "quotes": 0}

    fetch_id = str(uuid.uuid4())
    sent_at = utc_iso(sent_at)
    received_at = utc_iso(received_at)
    usage = usage or {}
    events = _events(payload)
    store = RecorderStore(db_path)

    with store.connect() as con:
        con.execute(
            """INSERT INTO odds_fetches(
                fetch_id,provider,endpoint,sport,regions,markets,request_sent_at,
                received_at,latency_ms,http_status,quota_used,quota_remaining,
                n_events,error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fetch_id,
                "the_odds_api",
                endpoint,
                sport,
                regions,
                markets,
                sent_at,
                received_at,
                _latency_ms(sent_at, received_at),
                http_status,
                _to_int(usage.get("requests_last")),
                _to_int(usage.get("requests_remaining")),
                len(events),
                error,
            ),
        )

        quote_count = 0
        for event in events:
            event_id = str(event.get("id") or "")
            commence_time = event.get("commence_time")
            for book in event.get("bookmakers") or []:
                if not isinstance(book, dict):
                    continue
                bookmaker = str(book.get("key") or book.get("title") or "")
                book_source_ts = book.get("last_update")
                for market in book.get("markets") or []:
                    if not isinstance(market, dict):
                        continue
                    market_key = str(market.get("key") or "")
                    market_source_ts = market.get("last_update") or book_source_ts
                    for outcome in market.get("outcomes") or []:
                        if not isinstance(outcome, dict):
                            continue
                        name = str(outcome.get("name") or "")
                        point = _to_float(outcome.get("point"))
                        dec = _to_float(outcome.get("price"))
                        try:
                            con.execute(
                                """INSERT OR IGNORE INTO odds_quotes(
                                    fetch_id,provider_event_id,commence_time,bookmaker,
                                    market_key,outcome_name,point,price_american,
                                    price_decimal,source_ts,received_at
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    fetch_id,
                                    event_id,
                                    commence_time,
                                    bookmaker,
                                    market_key,
                                    name,
                                    point,
                                    _american_from_decimal(dec),
                                    dec,
                                    market_source_ts,
                                    received_at,
                                ),
                            )
                            quote_count += 1
                        except sqlite3.Error:
                            continue

    return {"enabled": True, "fetch_id": fetch_id, "quotes": quote_count}


def _latency_ms(sent_at: str, received_at: str):
    try:
        s = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        r = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        return max(0, int((r - s).total_seconds() * 1000))
    except Exception:
        return None
