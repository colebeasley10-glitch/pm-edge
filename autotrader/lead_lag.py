"""Point-in-time sportsbook-to-Kalshi lead/lag study.

Research only. This module uses the timestamp at which AutoTrader actually
processed/received the sportsbook fair snapshot. Bookmaker source timestamps
are provenance/staleness metadata and never move a signal earlier in time.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

SHARP_BOOK_TOKENS = (
    "pinnacle",
    "circa",
    "bookmaker",
    "lowvig",
    "betonline",
)


def _dt(value):
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except Exception:
        return None


def _sharp_key(row):
    key = str(row.get("book_key") or "").lower()
    title = str(row.get("book_title") or "").lower()
    blob = f"{key} {title}"
    if any(token in blob for token in SHARP_BOOK_TOKENS):
        return key or title
    return None


def _median(values):
    values = [float(x) for x in values if x is not None]
    return statistics.median(values) if values else None


def analyze_lead_lag(
    db_path: str,
    *,
    min_sharp_move=0.02,
    horizons_seconds=(30, 60, 180, 300),
    max_book_age_seconds=900,
    max_quote_lookback_seconds=180,
):
    """Measure whether received sharp-book moves lead later Kalshi movement."""
    if not Path(db_path).exists():
        return {"status": "no_database", "n_triggers": 0}

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        snapshots = con.execute(
            """SELECT market_ticker,processed_at,inputs_json,source_age_s,
                      p_fair,method_version
               FROM fair_snapshots
               WHERE valid=1
               ORDER BY market_ticker,processed_at"""
        ).fetchall()

        previous = {}
        triggers = []
        for snap in snapshots:
            signal_at = _dt(snap["processed_at"])
            if signal_at is None:
                continue
            try:
                inputs = json.loads(snap["inputs_json"] or "[]")
            except Exception:
                continue
            if not isinstance(inputs, list):
                continue

            ticker = str(snap["market_ticker"])
            for book in inputs:
                if not isinstance(book, dict):
                    continue
                book_key = _sharp_key(book)
                if not book_key:
                    continue
                try:
                    probability = float(book.get("probability"))
                except (TypeError, ValueError):
                    continue
                if not (0 < probability < 1):
                    continue

                key = (ticker, book_key)
                old = previous.get(key)
                previous[key] = {
                    "probability": probability,
                    "processed_at": signal_at,
                    "source_ts": _dt(book.get("source_ts")),
                }
                if old is None:
                    continue
                move = probability - old["probability"]
                if abs(move) < float(min_sharp_move):
                    continue

                source_ts = _dt(book.get("source_ts"))
                source_age_s = (
                    max(0.0, (signal_at - source_ts).total_seconds())
                    if source_ts is not None else snap["source_age_s"]
                )
                delivered_late = (
                    source_age_s is not None
                    and float(source_age_s) > float(max_book_age_seconds)
                )

                lookback = (
                    signal_at - timedelta(seconds=max_quote_lookback_seconds)
                ).isoformat()
                signal_iso = signal_at.isoformat()
                base = con.execute(
                    """SELECT received_at,mid,yes_bid,yes_ask
                       FROM ob_top
                       WHERE market_ticker=? AND valid=1
                         AND received_at<=? AND received_at>=?
                       ORDER BY received_at DESC LIMIT 1""",
                    (ticker, signal_iso, lookback),
                ).fetchone()
                if not base or base["mid"] is None:
                    continue
                base_mid = float(base["mid"])
                direction = 1.0 if move > 0 else -1.0

                marks = {}
                for horizon in horizons_seconds:
                    target = (
                        signal_at + timedelta(seconds=int(horizon))
                    ).isoformat()
                    mark = con.execute(
                        """SELECT received_at,mid,yes_bid,yes_ask
                           FROM ob_top
                           WHERE market_ticker=? AND valid=1
                             AND received_at>=?
                           ORDER BY received_at ASC LIMIT 1""",
                        (ticker, target),
                    ).fetchone()
                    if mark and mark["mid"] is not None:
                        delta = float(mark["mid"]) - base_mid
                        marks[str(int(horizon))] = {
                            "mid": float(mark["mid"]),
                            "signed_move": direction * delta,
                            "same_direction": direction * delta > 0,
                            "received_at": mark["received_at"],
                        }

                triggers.append({
                    "ticker": ticker,
                    "book": book_key,
                    "signal_received_at": signal_iso,
                    "source_ts": source_ts.isoformat() if source_ts else None,
                    "source_age_s": source_age_s,
                    "delivered_late": delivered_late,
                    "sharp_move": move,
                    "direction": "UP" if move > 0 else "DOWN",
                    "kalshi_mid_at_signal": base_mid,
                    "kalshi_quote_received_at": base["received_at"],
                    "marks": marks,
                })

        horizons = {}
        for horizon in horizons_seconds:
            key = str(int(horizon))
            rows = [
                t["marks"][key]["signed_move"]
                for t in triggers if key in t["marks"]
            ]
            horizons[key] = {
                "n": len(rows),
                "mean_signed_move": sum(rows) / len(rows) if rows else None,
                "median_signed_move": _median(rows),
                "same_direction_rate": (
                    sum(1 for x in rows if x > 0) / len(rows) if rows else None
                ),
            }

        timely = [t for t in triggers if not t["delivered_late"]]
        late = [t for t in triggers if t["delivered_late"]]
        source_ages = [
            float(t["source_age_s"])
            for t in triggers if t["source_age_s"] is not None
        ]
        return {
            "status": "ok",
            "study_version": "lead_lag_v1",
            "n_triggers": len(triggers),
            "n_timely": len(timely),
            "n_delivered_late": len(late),
            "median_source_age_s": _median(source_ages),
            "min_sharp_move": float(min_sharp_move),
            "max_book_age_seconds": int(max_book_age_seconds),
            "horizons": horizons,
            "promotion_ready": False,
            "promotion_blocker": (
                "requires preregistered minimum sample and positive timely "
                "lead-lag after execution costs"
            ),
            "recent_triggers": triggers[-50:],
        }
    finally:
        con.close()
