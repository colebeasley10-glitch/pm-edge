"""Append-only point-in-time recorder storage for paper AutoTrader research.

SQLite is the day-one persistence layer on the existing Railway volume.  The
schema mirrors Engineering Specs v1.1 closely enough to begin collecting data
immediately and can be migrated to Postgres without changing the recorder API.

Critical invariant: accessors filter on received_at/processed_at only. source_ts
is provenance and staleness metadata; it never makes information knowable early.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1


def utc_iso(value: datetime | str | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RecorderStore:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=30000")
            yield con
            con.commit()
        finally:
            con.close()

    def _init_schema(self):
        ddl = """
        CREATE TABLE IF NOT EXISTS schema_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fee_schedules(
            fee_schedule_id TEXT PRIMARY KEY,
            effective_from TEXT,
            effective_to TEXT,
            description TEXT,
            source_url TEXT,
            captured_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS markets_meta(
            market_ticker TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            event_ticker TEXT,
            series_ticker TEXT,
            title TEXT,
            subtitle TEXT,
            rules_primary TEXT,
            rules_secondary TEXT,
            open_time TEXT,
            close_time TEXT,
            expected_expiration_time TEXT,
            status TEXT,
            tick_size TEXT,
            fee_type TEXT,
            fee_schedule_id TEXT,
            content_hash TEXT NOT NULL,
            source_ts TEXT,
            received_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY(market_ticker, valid_from)
        );
        CREATE INDEX IF NOT EXISTS idx_markets_meta_ticker_received
            ON markets_meta(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS ob_snapshots(
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            conn_id TEXT,
            seq INTEGER,
            yes_bids TEXT NOT NULL,
            no_bids TEXT NOT NULL,
            origin TEXT NOT NULL,
            source_ts TEXT,
            received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ob_snapshots_ticker_received
            ON ob_snapshots(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS ob_top(
            top_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            received_at TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            bid_size REAL,
            ask_size REAL,
            mid REAL,
            spread REAL,
            depth_2c_bid REAL,
            depth_2c_ask REAL,
            valid INTEGER NOT NULL,
            origin TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ob_top_ticker_received
            ON ob_top(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS trades(
            trade_id TEXT PRIMARY KEY,
            market_ticker TEXT NOT NULL,
            yes_price REAL,
            no_price REAL,
            count REAL,
            taker_side TEXT,
            taker_book_side TEXT,
            source_ts TEXT,
            received_at TEXT NOT NULL,
            is_block_trade INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trades_ticker_received
            ON trades(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS odds_fetches(
            fetch_id TEXT PRIMARY KEY,
            provider TEXT,
            endpoint TEXT,
            sport TEXT,
            regions TEXT,
            markets TEXT,
            request_sent_at TEXT,
            received_at TEXT NOT NULL,
            latency_ms INTEGER,
            http_status INTEGER,
            quota_used INTEGER,
            quota_remaining INTEGER,
            n_events INTEGER,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS odds_quotes(
            quote_id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetch_id TEXT NOT NULL,
            provider_event_id TEXT,
            commence_time TEXT,
            bookmaker TEXT,
            market_key TEXT,
            outcome_name TEXT,
            point REAL,
            price_american INTEGER,
            price_decimal REAL,
            source_ts TEXT,
            received_at TEXT NOT NULL,
            UNIQUE(fetch_id, bookmaker, provider_event_id, market_key, outcome_name, point)
        );
        CREATE INDEX IF NOT EXISTS idx_odds_quotes_event_received
            ON odds_quotes(provider_event_id, received_at);

        CREATE TABLE IF NOT EXISTS fair_snapshots(
            fair_id TEXT PRIMARY KEY,
            market_ticker TEXT NOT NULL,
            map_id TEXT,
            method_version TEXT NOT NULL,
            input_fetch_ids TEXT NOT NULL,
            input_received_at_max TEXT NOT NULL,
            info_ts TEXT,
            processed_at TEXT NOT NULL,
            p_fair REAL NOT NULL,
            sigma REAL NOT NULL,
            sigma_base REAL NOT NULL,
            sigma_disagree REAL NOT NULL,
            sigma_age REAL NOT NULL,
            n_books INTEGER,
            n_sharp INTEGER,
            n_eff REAL,
            disagreement REAL,
            devig_method TEXT,
            inputs_json TEXT,
            valid INTEGER NOT NULL,
            invalid_reason TEXT,
            source_age_s REAL,
            system_age_s REAL,
            delivery_lag_s REAL
        );
        CREATE INDEX IF NOT EXISTS idx_fair_ticker_processed
            ON fair_snapshots(market_ticker, processed_at);

        CREATE TABLE IF NOT EXISTS intents(
            intent_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            lane TEXT NOT NULL,
            lane_version TEXT NOT NULL,
            code_sha TEXT,
            fill_model_version TEXT,
            book_id TEXT NOT NULL,
            market_ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            action TEXT NOT NULL,
            fair_id TEXT,
            ob_received_at TEXT,
            exec_style TEXT,
            limit_price REAL,
            qty INTEGER,
            edge_gross REAL,
            fees_est REAL,
            slippage_est REAL,
            exit_cost_est REAL,
            sigma REAL,
            required_edge REAL,
            z REAL,
            p_fill_est REAL,
            expected_hold_h REAL,
            ev_est REAL,
            score REAL,
            status TEXT NOT NULL,
            reject_reason TEXT,
            reject_detail TEXT,
            allocator_run_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_intents_lane_created
            ON intents(lane, lane_version, created_at);

        CREATE TABLE IF NOT EXISTS api_calls(
            call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            latency_ms INTEGER,
            status INTEGER,
            rate_limit_remaining INTEGER,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS data_quality_events(
            quality_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            type TEXT NOT NULL,
            market_ticker TEXT,
            detail TEXT,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_quality_open
            ON data_quality_events(type, market_ticker, resolved_at);

        CREATE TABLE IF NOT EXISTS run_manifest(
            run_id TEXT PRIMARY KEY,
            code_sha TEXT,
            config_hash TEXT,
            lane_versions TEXT,
            fill_model_version TEXT,
            started_at TEXT NOT NULL
        );
        """
        with self.connect() as con:
            con.executescript(ddl)
            con.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _market_content_hash(raw: dict, fee_schedule_id: str) -> str:
        watched = {
            "event_ticker": raw.get("event_ticker"),
            "series_ticker": raw.get("series_ticker"),
            "title": raw.get("title"),
            "subtitle": raw.get("subtitle"),
            "rules_primary": raw.get("rules_primary"),
            "rules_secondary": raw.get("rules_secondary"),
            "open_time": raw.get("open_time"),
            "close_time": raw.get("close_time"),
            "expected_expiration_time": raw.get("expected_expiration_time"),
            "status": raw.get("status"),
            "price_level_structure": raw.get("price_level_structure"),
            "price_ranges": raw.get("price_ranges"),
            "fee_type": raw.get("fee_type") or raw.get("fee_type_override"),
            "fee_schedule_id": fee_schedule_id,
        }
        blob = json.dumps(watched, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def record_market_meta(self, raw: dict, *, received_at, fee_schedule_id: str) -> bool:
        ticker = str(raw.get("ticker") or "").strip()
        if not ticker:
            return False
        received_at = utc_iso(received_at)
        content_hash = self._market_content_hash(raw, fee_schedule_id)
        with self.connect() as con:
            prior = con.execute(
                "SELECT content_hash FROM markets_meta WHERE market_ticker=? ORDER BY received_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            if prior and prior["content_hash"] == content_hash:
                return False
            con.execute(
                """INSERT INTO markets_meta(
                    market_ticker,valid_from,event_ticker,series_ticker,title,subtitle,
                    rules_primary,rules_secondary,open_time,close_time,
                    expected_expiration_time,status,tick_size,fee_type,fee_schedule_id,
                    content_hash,source_ts,received_at,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker, received_at, raw.get("event_ticker"), raw.get("series_ticker"),
                    raw.get("title"), raw.get("subtitle"), raw.get("rules_primary"),
                    raw.get("rules_secondary"), raw.get("open_time"), raw.get("close_time"),
                    raw.get("expected_expiration_time"), raw.get("status"),
                    json.dumps(raw.get("price_ranges") or [], separators=(",", ":")),
                    raw.get("fee_type") or raw.get("fee_type_override"), fee_schedule_id,
                    content_hash, raw.get("updated_time"), received_at,
                    json.dumps(raw, separators=(",", ":"), default=str),
                ),
            )
        return True

    def record_top(self, market: dict, *, received_at, processed_at=None, origin="rest_market_list"):
        ticker = str(market.get("ticker") or "").strip()
        if not ticker:
            return
        def f(name):
            value = market.get(name)
            try:
                return None if value in (None, "") else float(value)
            except (TypeError, ValueError):
                return None
        yes_bid, yes_ask = f("yes_bid"), f("yes_ask")
        no_bid, no_ask = f("no_bid"), f("no_ask")
        bid_size, ask_size = f("yes_bid_size"), f("yes_ask_size")
        valid = int(
            yes_bid is not None and yes_ask is not None
            and 0 < yes_bid <= yes_ask < 1
        )
        mid = (yes_bid + yes_ask) / 2 if valid else None
        spread = yes_ask - yes_bid if valid else None
        with self.connect() as con:
            con.execute(
                """INSERT INTO ob_top(
                    market_ticker,received_at,processed_at,yes_bid,yes_ask,no_bid,no_ask,
                    bid_size,ask_size,mid,spread,depth_2c_bid,depth_2c_ask,valid,origin
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker, utc_iso(received_at), utc_iso(processed_at or received_at),
                    yes_bid, yes_ask, no_bid, no_ask, bid_size, ask_size, mid, spread,
                    None, None, valid, origin,
                ),
            )

    def record_snapshot(self, ticker: str, orderbook: dict, *, received_at,
                        origin="rest", conn_id=None, seq=None):
        book = orderbook.get("orderbook_fp") or {}
        yes_bids = book.get("yes_dollars") or book.get("yes") or []
        no_bids = book.get("no_dollars") or book.get("no") or []
        with self.connect() as con:
            con.execute(
                """INSERT INTO ob_snapshots(
                    market_ticker,conn_id,seq,yes_bids,no_bids,origin,source_ts,received_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    ticker, conn_id, seq,
                    json.dumps(yes_bids, separators=(",", ":")),
                    json.dumps(no_bids, separators=(",", ":")),
                    origin, None, utc_iso(received_at),
                ),
            )

    def record_trades(self, rows: Iterable[dict], *, received_at) -> int:
        received_at = utc_iso(received_at)
        inserted = 0
        with self.connect() as con:
            for row in rows:
                trade_id = str(row.get("trade_id") or "").strip()
                ticker = str(row.get("ticker") or row.get("market_ticker") or "").strip()
                if not trade_id or not ticker:
                    continue
                try:
                    cur = con.execute(
                        """INSERT OR IGNORE INTO trades(
                            trade_id,market_ticker,yes_price,no_price,count,taker_side,
                            taker_book_side,source_ts,received_at,is_block_trade,raw_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            trade_id, ticker,
                            _float(row.get("yes_price_dollars")),
                            _float(row.get("no_price_dollars")),
                            _float(row.get("count_fp") or row.get("count")),
                            row.get("taker_outcome_side") or row.get("taker_side"),
                            row.get("taker_book_side"),
                            row.get("created_time"), received_at,
                            int(bool(row.get("is_block_trade"))),
                            json.dumps(row, separators=(",", ":"), default=str),
                        ),
                    )
                    inserted += int(cur.rowcount > 0)
                except sqlite3.Error:
                    continue
        return inserted

    def record_api_call(self, *, service, endpoint, sent_at, received_at,
                        latency_ms=None, status=None, rate_limit_remaining=None, error=None):
        with self.connect() as con:
            con.execute(
                """INSERT INTO api_calls(
                    service,endpoint,sent_at,received_at,latency_ms,status,
                    rate_limit_remaining,error
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    service, endpoint, utc_iso(sent_at), utc_iso(received_at),
                    latency_ms, status, rate_limit_remaining, error,
                ),
            )

    def quality_event(self, kind: str, *, market_ticker=None, detail=None):
        with self.connect() as con:
            con.execute(
                "INSERT INTO data_quality_events(ts,type,market_ticker,detail) VALUES(?,?,?,?)",
                (utc_iso(), kind, market_ticker, json.dumps(detail, default=str) if isinstance(detail, (dict, list)) else detail),
            )

    def book_at(self, ticker: str, at_time) -> dict | None:
        """Point-in-time accessor. Never filters by source_ts."""
        at_time = utc_iso(at_time)
        with self.connect() as con:
            row = con.execute(
                """SELECT * FROM ob_top
                   WHERE market_ticker=? AND received_at<=? AND processed_at<=?
                   ORDER BY received_at DESC, top_id DESC LIMIT 1""",
                (ticker, at_time, at_time),
            ).fetchone()
        return dict(row) if row else None

    def fair_at(self, ticker: str, at_time) -> dict | None:
        """Point-in-time fair accessor. Never filters by info_ts/source_ts."""
        at_time = utc_iso(at_time)
        with self.connect() as con:
            row = con.execute(
                """SELECT * FROM fair_snapshots
                   WHERE market_ticker=? AND processed_at<=?
                   ORDER BY processed_at DESC LIMIT 1""",
                (ticker, at_time),
            ).fetchone()
        return dict(row) if row else None

    def trades_between(self, ticker: str, t0, t1) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT * FROM trades WHERE market_ticker=?
                   AND received_at>=? AND received_at<=?
                   ORDER BY received_at, trade_id""",
                (ticker, utc_iso(t0), utc_iso(t1)),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict:
        names = ("markets_meta", "ob_top", "ob_snapshots", "trades", "odds_fetches",
                 "odds_quotes", "fair_snapshots", "intents", "data_quality_events")
        out = {}
        with self.connect() as con:
            for name in names:
                out[name] = int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        return out


def _float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None
