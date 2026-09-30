"""Append-only point-in-time recorder storage for paper AutoTrader research.

SQLite is the day-one persistence layer on the existing Railway volume.  The
schema mirrors Engineering Specs v1.1 closely enough to begin collecting data
immediately and can be migrated to Postgres without changing the recorder API.

Critical invariant: accessors filter on received_at/processed_at only. source_ts
is provenance and staleness metadata; it never makes information knowable early.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1


def _pack_json(value) -> str:
    """Compact raw provider payloads to protect the small Railway volume."""
    raw = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return "zlib64:" + base64.b64encode(compressed).decode("ascii")


def unpack_json(value: str):
    """Decode current compressed payloads and legacy plain JSON rows."""
    text = str(value or "")
    if text.startswith("zlib64:"):
        payload = base64.b64decode(text[len("zlib64:"):])
        return json.loads(zlib.decompress(payload).decode("utf-8"))
    return json.loads(text)


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
            con.execute("PRAGMA wal_autocheckpoint=500")
            con.execute("PRAGMA journal_size_limit=8388608")
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

        CREATE TABLE IF NOT EXISTS audit_runs(
            audit_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            violations INTEGER NOT NULL,
            detail TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS maker_observations(
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            received_at TEXT NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            spread REAL NOT NULL,
            queue_ahead REAL,
            volume_24h REAL,
            liquidity_dollars REAL,
            event_ticker TEXT,
            mode TEXT NOT NULL DEFAULT 'observe_only'
        );
        CREATE INDEX IF NOT EXISTS idx_maker_obs_ticker_received
            ON maker_observations(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS maker_probe_orders(
            probe_id TEXT PRIMARY KEY,
            market_ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            placed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            price REAL NOT NULL,
            queue_ahead REAL,
            probe_qty REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_at TEXT,
            fill_at TEXT,
            fill_price REAL,
            fill_reason TEXT,
            traded_at_level REAL NOT NULL DEFAULT 0,
            mid_at_place REAL,
            mid_5m REAL,
            mid_30m REAL,
            mode TEXT NOT NULL DEFAULT 'observe_only'
        );
        CREATE INDEX IF NOT EXISTS idx_maker_probe_status_expiry
            ON maker_probe_orders(status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_maker_probe_ticker_placed
            ON maker_probe_orders(market_ticker, placed_at);

        CREATE TABLE IF NOT EXISTS weather_market_observations(
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            event_ticker TEXT,
            received_at TEXT NOT NULL,
            title TEXT,
            subtitle TEXT,
            rules_primary TEXT,
            rules_secondary TEXT,
            yes_bid REAL,
            yes_ask REAL,
            volume_24h REAL,
            liquidity_dollars REAL,
            close_time TEXT,
            series_guess TEXT,
            city_guess TEXT,
            mode TEXT NOT NULL DEFAULT 'discovery_only'
        );
        CREATE INDEX IF NOT EXISTS idx_weather_obs_ticker_received
            ON weather_market_observations(market_ticker, received_at);

        CREATE TABLE IF NOT EXISTS structural_group_observations(
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_ticker TEXT NOT NULL,
            received_at TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            complete_yes_quotes INTEGER NOT NULL,
            sum_yes_bid REAL,
            sum_yes_ask REAL,
            gross_buy_all_gap REAL,
            gross_sell_all_gap REAL,
            title_sample TEXT,
            tickers_json TEXT NOT NULL,
            exhaustiveness_verified INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'research_only'
        );
        CREATE INDEX IF NOT EXISTS idx_structural_event_received
            ON structural_group_observations(event_ticker, received_at);

        CREATE TABLE IF NOT EXISTS weather_shadow_fairs(
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            event_ticker TEXT NOT NULL,
            received_at TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            bucket_label TEXT,
            variable TEXT,
            p_shadow REAL NOT NULL,
            sampling_sigma REAL,
            n_members INTEGER,
            ensemble_mean_f REAL,
            ensemble_stdev_f REAL,
            latitude REAL,
            longitude REAL,
            timezone TEXT,
            model TEXT,
            rounding_assumption TEXT,
            promotion_blocker TEXT,
            mode TEXT NOT NULL DEFAULT 'shadow_only'
        );
        CREATE INDEX IF NOT EXISTS idx_weather_shadow_ticker_received
            ON weather_shadow_fairs(ticker, received_at);

        CREATE TABLE IF NOT EXISTS weather_shadow_edges(
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            event_ticker TEXT NOT NULL,
            received_at TEXT NOT NULL,
            p_shadow REAL NOT NULL,
            sampling_sigma REAL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            yes_gross_edge REAL,
            no_gross_edge REAL,
            best_side TEXT,
            best_gross_edge REAL,
            settlement_source_hint TEXT,
            city TEXT,
            target_date TEXT,
            bucket_label TEXT,
            model TEXT,
            mode TEXT NOT NULL DEFAULT 'shadow_only'
        );
        CREATE INDEX IF NOT EXISTS idx_weather_edge_ticker_received
            ON weather_shadow_edges(ticker, received_at);

        CREATE TABLE IF NOT EXISTS weather_settlements(
            ticker TEXT PRIMARY KEY,
            event_ticker TEXT,
            observed_at TEXT NOT NULL,
            status TEXT,
            result TEXT,
            yes_settlement_value REAL,
            source_hint TEXT,
            target_date TEXT,
            bucket_label TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_weather_settlement_event
            ON weather_settlements(event_ticker, observed_at);
        """
        with self.connect() as con:
            con.executescript(ddl)
            con.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            con.execute(
                """INSERT OR REPLACE INTO schema_meta(key,value)
                   VALUES('raw_payload_format_version','zlib64-v1')"""
            )

    @staticmethod
    def _market_meta_payload(raw: dict, fee_schedule_id: str) -> dict:
        """Canonical market metadata, excluding fast-moving quote fields.

        Prices, sizes, volume, open interest and provider update timestamps belong
        in quote/tape observations. Including them here caused append-only
        metadata history to grow on every market-list refresh.
        """
        return {
            "ticker": raw.get("ticker"),
            "event_ticker": raw.get("event_ticker"),
            "series_ticker": raw.get("series_ticker"),
            "title": raw.get("title"),
            "subtitle": raw.get("subtitle"),
            "rules_primary": raw.get("rules_primary"),
            "rules_secondary": raw.get("rules_secondary"),
            "open_time": raw.get("open_time"),
            "close_time": raw.get("close_time"),
            "expected_expiration_time": raw.get("expected_expiration_time"),
            "fee_type": raw.get("fee_type") or raw.get("fee_type_override"),
            "fee_schedule_id": fee_schedule_id,
        }

    @classmethod
    def _market_content_hash(cls, raw: dict, fee_schedule_id: str) -> str:
        watched = cls._market_meta_payload(raw, fee_schedule_id)
        blob = json.dumps(watched, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def record_market_meta_batch(
        self, rows: Iterable[dict], *, received_at, fee_schedule_id: str
    ) -> dict:
        """Append changed market metadata using one SQLite transaction."""
        received_at = utc_iso(received_at)
        changed = 0
        skipped = 0
        errors = []
        with self.connect() as con:
            for raw in rows or []:
                ticker = str((raw or {}).get("ticker") or "").strip()
                if not ticker:
                    skipped += 1
                    continue
                try:
                    content_hash = self._market_content_hash(raw, fee_schedule_id)
                    prior = con.execute(
                        """SELECT content_hash FROM markets_meta
                           WHERE market_ticker=?
                           ORDER BY received_at DESC LIMIT 1""",
                        (ticker,),
                    ).fetchone()
                    if prior and prior["content_hash"] == content_hash:
                        skipped += 1
                        continue
                    con.execute(
                        """INSERT INTO markets_meta(
                            market_ticker,valid_from,event_ticker,series_ticker,
                            title,subtitle,rules_primary,rules_secondary,open_time,
                            close_time,expected_expiration_time,status,tick_size,
                            fee_type,fee_schedule_id,content_hash,source_ts,
                            received_at,raw_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            ticker, received_at, raw.get("event_ticker"),
                            raw.get("series_ticker"), raw.get("title"),
                            raw.get("subtitle"), raw.get("rules_primary"),
                            raw.get("rules_secondary"), raw.get("open_time"),
                            raw.get("close_time"),
                            raw.get("expected_expiration_time"), raw.get("status"),
                            json.dumps(raw.get("price_ranges") or [], separators=(",", ":")),
                            raw.get("fee_type") or raw.get("fee_type_override"),
                            fee_schedule_id, content_hash, raw.get("updated_time"),
                            received_at, _pack_json(
                                self._market_meta_payload(raw, fee_schedule_id)
                            ),
                        ),
                    )
                    changed += 1
                except Exception as exc:
                    if len(errors) < 10:
                        errors.append({
                            "ticker": ticker,
                            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                        })
        return {"changed": changed, "skipped": skipped, "errors": errors}

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
                    _pack_json(self._market_meta_payload(raw, fee_schedule_id)),
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

    def record_maker_observations(self, market: dict, *, received_at) -> int:
        """Log hypothetical maker queue conditions without creating a simulated fill."""
        ticker = str(market.get("ticker") or "").strip()
        if not ticker:
            return 0
        received_at = utc_iso(received_at)
        rows = []
        for side in ("YES", "NO"):
            prefix = side.lower()
            bid = _float(market.get(prefix + "_bid"))
            ask = _float(market.get(prefix + "_ask"))
            if bid is None or ask is None or not (0 < bid <= ask < 1):
                continue
            if side == "YES":
                queue_ahead = _float(market.get("yes_bid_size"))
            else:
                # In Kalshi's binary reciprocal book, NO bid depth corresponds
                # to the displayed YES ask depth.
                queue_ahead = _float(market.get("yes_ask_size"))
            rows.append((
                ticker, side, received_at, bid, ask, ask - bid, queue_ahead,
                _float(market.get("volume_24h")),
                _float(market.get("liquidity_dollars")),
                market.get("event_ticker"),
                "observe_only",
            ))
        if not rows:
            return 0
        with self.connect() as con:
            con.executemany(
                """INSERT INTO maker_observations(
                    market_ticker,side,received_at,bid,ask,spread,queue_ahead,
                    volume_24h,liquidity_dollars,event_ticker,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def create_maker_probes_from_orderbook(
        self, market: dict, orderbook: dict, *, received_at, ttl_seconds=300
    ) -> int:
        """Create one-contract observation probes at the actual best maker bids."""
        ticker = str(market.get("ticker") or "").strip()
        if not ticker:
            return 0
        book = orderbook.get("orderbook_fp") or {}
        yes_levels = book.get("yes_dollars") or book.get("yes") or []
        no_levels = book.get("no_dollars") or book.get("no") or []

        def best(levels):
            parsed = []
            for level in levels:
                if not isinstance(level, (list, tuple)) or len(level) < 2:
                    continue
                price = _float(level[0])
                size = _float(level[1])
                if price is not None and size is not None and 0 < price < 1 and size > 0:
                    parsed.append((price, size))
            return max(parsed, default=(None, None), key=lambda x: x[0] if x[0] is not None else -1)

        yes_bid, yes_q = best(yes_levels)
        no_bid, no_q = best(no_levels)
        placed = utc_iso(received_at)
        try:
            placed_dt = datetime.fromisoformat(placed.replace("Z", "+00:00"))
        except Exception:
            return 0
        expires = utc_iso(placed_dt + timedelta(seconds=max(60, int(ttl_seconds))))

        mid = None
        if yes_bid is not None and no_bid is not None:
            yes_ask = 1.0 - no_bid
            if 0 < yes_bid <= yes_ask < 1:
                mid = (yes_bid + yes_ask) / 2.0

        rows = []
        for side, price, queue in (("YES", yes_bid, yes_q), ("NO", no_bid, no_q)):
            if price is None or queue is None:
                continue
            raw_id = f"{ticker}|{side}|{placed}|{price}|{queue}"
            probe_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
            rows.append((
                probe_id, ticker, side, placed, expires, price, queue, 1.0,
                "pending", None, None, None, None, 0.0, mid, None, None,
                "observe_only",
            ))
        if not rows:
            return 0
        with self.connect() as con:
            before = con.total_changes
            con.executemany(
                """INSERT OR IGNORE INTO maker_probe_orders(
                    probe_id,market_ticker,side,placed_at,expires_at,price,
                    queue_ahead,probe_qty,status,resolved_at,fill_at,fill_price,
                    fill_reason,traded_at_level,mid_at_place,mid_5m,mid_30m,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            return int(con.total_changes - before)

    def record_maker_from_orderbook(
        self, market: dict, orderbook: dict, *, received_at
    ) -> int:
        """Record maker queue conditions from actual orderbook levels.

        YES maker bid queue comes from YES bids. NO maker bid queue comes from
        NO bids. Executable asks are reciprocal to the opposing best bid.
        """
        ticker = str(market.get("ticker") or "").strip()
        if not ticker:
            return 0
        book = orderbook.get("orderbook_fp") or {}
        yes_levels = book.get("yes_dollars") or book.get("yes") or []
        no_levels = book.get("no_dollars") or book.get("no") or []

        def best(levels):
            parsed = []
            for level in levels:
                if not isinstance(level, (list, tuple)) or len(level) < 2:
                    continue
                price = _float(level[0])
                size = _float(level[1])
                if price is not None and size is not None and 0 < price < 1 and size > 0:
                    parsed.append((price, size))
            return max(parsed, default=(None, None), key=lambda x: x[0] if x[0] is not None else -1)

        yes_bid, yes_q = best(yes_levels)
        no_bid, no_q = best(no_levels)
        received_at = utc_iso(received_at)
        rows = []
        if yes_bid is not None and no_bid is not None:
            yes_ask = 1.0 - no_bid
            no_ask = 1.0 - yes_bid
            if 0 < yes_bid <= yes_ask < 1:
                rows.append((
                    ticker, "YES", received_at, yes_bid, yes_ask, yes_ask - yes_bid,
                    yes_q, _float(market.get("volume_24h")),
                    _float(market.get("liquidity_dollars")),
                    market.get("event_ticker"), "observe_only",
                ))
            if 0 < no_bid <= no_ask < 1:
                rows.append((
                    ticker, "NO", received_at, no_bid, no_ask, no_ask - no_bid,
                    no_q, _float(market.get("volume_24h")),
                    _float(market.get("liquidity_dollars")),
                    market.get("event_ticker"), "observe_only",
                ))
        if not rows:
            return 0
        with self.connect() as con:
            con.executemany(
                """INSERT INTO maker_observations(
                    market_ticker,side,received_at,bid,ask,spread,queue_ahead,
                    volume_24h,liquidity_dollars,event_ticker,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def record_weather_market(self, market: dict, *, received_at) -> bool:
        """Record daily/hourly temperature markets for free-source research.

        This is discovery-only. It never converts weather data into a trading
        probability and therefore cannot create a paper fill.
        """
        ticker = str(market.get("ticker") or "").strip()
        title = str(
            market.get("_weather_event_title")
            or market.get("title")
            or ""
        )
        blob = f"{ticker} {title}".upper()
        is_weather = (
            ticker.upper().startswith(("KXHIGH", "KXLOW", "KXTEMP"))
            or "HIGHEST TEMPERATURE" in blob
            or "LOWEST TEMPERATURE" in blob
            or "TEMPERATURE IN " in blob
        )
        if not is_weather:
            return False

        series_guess = ""
        upper = ticker.upper()
        for prefix in ("KXHIGH", "KXLOW", "KXTEMP"):
            if upper.startswith(prefix):
                series_guess = prefix
                break

        city_guess = ""
        lower_title = title.lower()
        for phrase in ("highest temperature in ", "lowest temperature in ", "temperature in "):
            if phrase in lower_title:
                tail = title[lower_title.index(phrase) + len(phrase):]
                city_guess = tail.split(" on ")[0].split(" today")[0].split("?")[0].strip()
                break

        with self.connect() as con:
            con.execute(
                """INSERT INTO weather_market_observations(
                    market_ticker,event_ticker,received_at,title,subtitle,
                    rules_primary,rules_secondary,yes_bid,yes_ask,volume_24h,
                    liquidity_dollars,close_time,series_guess,city_guess,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker,
                    market.get("event_ticker"),
                    utc_iso(received_at),
                    title,
                    market.get("subtitle"),
                    market.get("rules_primary"),
                    market.get("rules_secondary"),
                    _float(market.get("yes_bid")),
                    _float(market.get("yes_ask")),
                    _float(market.get("volume_24h")),
                    _float(market.get("liquidity_dollars")),
                    market.get("close_time"),
                    series_guess,
                    city_guess,
                    "discovery_only",
                ),
            )
        return True

    def evaluate_maker_probes(self, *, now=None) -> dict:
        """Resolve hypothetical maker probes from the observed trade tape only."""
        now_iso = utc_iso(now)
        resolved = 0
        filled = 0
        expired = 0
        with self.connect() as con:
            probes = con.execute(
                """SELECT * FROM maker_probe_orders
                   WHERE status='pending' AND placed_at<=?
                   ORDER BY placed_at LIMIT 500""",
                (now_iso,),
            ).fetchall()
            for probe in probes:
                ticker = probe["market_ticker"]
                side = probe["side"]
                price_col = "yes_price" if side == "YES" else "no_price"
                trades = con.execute(
                    f"""SELECT trade_id,received_at,{price_col} AS px,count
                        FROM trades
                        WHERE market_ticker=? AND received_at>=?
                          AND received_at<=? AND {price_col} IS NOT NULL
                        ORDER BY received_at,trade_id""",
                    (ticker, probe["placed_at"], min(now_iso, probe["expires_at"])),
                ).fetchall()
                queue = float(probe["queue_ahead"] or 0)
                at_level = 0.0
                fill_row = None
                fill_reason = None
                for tr in trades:
                    px = float(tr["px"])
                    qty = float(tr["count"] or 0)
                    if px < float(probe["price"]):
                        fill_row = tr
                        fill_reason = "traded_through"
                        break
                    if abs(px - float(probe["price"])) < 1e-9:
                        at_level += qty
                        if at_level > queue:
                            fill_row = tr
                            fill_reason = "queue_cleared_at_level"
                            break
                if fill_row is not None:
                    con.execute(
                        """UPDATE maker_probe_orders
                           SET status='observed_fill',resolved_at=?,fill_at=?,
                               fill_price=?,fill_reason=?,traded_at_level=?
                           WHERE probe_id=?""",
                        (
                            now_iso, fill_row["received_at"], float(probe["price"]),
                            fill_reason, at_level, probe["probe_id"],
                        ),
                    )
                    resolved += 1
                    filled += 1
                elif now_iso >= probe["expires_at"]:
                    con.execute(
                        """UPDATE maker_probe_orders
                           SET status='expired',resolved_at=?,traded_at_level=?
                           WHERE probe_id=?""",
                        (now_iso, at_level, probe["probe_id"]),
                    )
                    resolved += 1
                    expired += 1

            # Add coarse post-fill marks from the first observed top-of-book at
            # or after +5m / +30m. REST cadence is not precise enough for a 1m mark.
            fills = con.execute(
                """SELECT probe_id,market_ticker,fill_at,mid_5m,mid_30m
                   FROM maker_probe_orders
                   WHERE status='observed_fill'
                     AND (mid_5m IS NULL OR mid_30m IS NULL)
                   ORDER BY fill_at LIMIT 500"""
            ).fetchall()
            marked_5m = 0
            marked_30m = 0
            for row in fills:
                try:
                    fill_dt = datetime.fromisoformat(str(row["fill_at"]).replace("Z","+00:00"))
                except Exception:
                    continue
                updates = {}
                for label, minutes in (("mid_5m", 5), ("mid_30m", 30)):
                    if row[label] is not None:
                        continue
                    target = utc_iso(fill_dt + timedelta(minutes=minutes))
                    if now_iso < target:
                        continue
                    mark = con.execute(
                        """SELECT mid FROM ob_top
                           WHERE market_ticker=? AND valid=1 AND received_at>=?
                           ORDER BY received_at ASC LIMIT 1""",
                        (row["market_ticker"], target),
                    ).fetchone()
                    if mark and mark["mid"] is not None:
                        updates[label] = float(mark["mid"])
                if updates:
                    sets = ",".join(f"{k}=?" for k in updates)
                    con.execute(
                        f"UPDATE maker_probe_orders SET {sets} WHERE probe_id=?",
                        tuple(updates.values()) + (row["probe_id"],),
                    )
                    marked_5m += int("mid_5m" in updates)
                    marked_30m += int("mid_30m" in updates)

        return {
            "pending_checked": len(probes),
            "resolved": resolved,
            "observed_fills": filled,
            "expired": expired,
            "marked_5m": marked_5m,
            "marked_30m": marked_30m,
        }

    def maker_probe_summary(self) -> dict:
        """Summarize observation-only maker fill realism and adverse selection."""
        with self.connect() as con:
            totals = {
                str(row["status"]): int(row["n"])
                for row in con.execute(
                    """SELECT status,COUNT(*) AS n FROM maker_probe_orders
                       GROUP BY status"""
                ).fetchall()
            }
            fills = con.execute(
                """SELECT side,price,mid_at_place,mid_5m,mid_30m
                   FROM maker_probe_orders
                   WHERE status='observed_fill'"""
            ).fetchall()

        def side_mid(yes_mid, side):
            if yes_mid is None:
                return None
            value = float(yes_mid)
            return value if str(side).upper() == "YES" else 1.0 - value

        drifts_5m, drifts_30m = [], []
        improvements = []
        for row in fills:
            entry = float(row["price"])
            initial = side_mid(row["mid_at_place"], row["side"])
            mark5 = side_mid(row["mid_5m"], row["side"])
            mark30 = side_mid(row["mid_30m"], row["side"])
            if initial is not None:
                improvements.append(entry - initial)
            if mark5 is not None:
                drifts_5m.append(mark5 - entry)
            if mark30 is not None:
                drifts_30m.append(mark30 - entry)

        resolved = totals.get("observed_fill", 0) + totals.get("expired", 0)
        fill_rate = (
            totals.get("observed_fill", 0) / resolved if resolved else None
        )
        return {
            "pending": totals.get("pending", 0),
            "observed_fills": totals.get("observed_fill", 0),
            "expired": totals.get("expired", 0),
            "resolved": resolved,
            "fill_rate_resolved": fill_rate,
            "avg_entry_vs_mid": (
                sum(improvements) / len(improvements) if improvements else None
            ),
            "avg_side_mid_drift_5m": (
                sum(drifts_5m) / len(drifts_5m) if drifts_5m else None
            ),
            "avg_side_mid_drift_30m": (
                sum(drifts_30m) / len(drifts_30m) if drifts_30m else None
            ),
            "marked_5m": len(drifts_5m),
            "marked_30m": len(drifts_30m),
            "interpretation": (
                "negative side-mid drift after fills indicates adverse selection"
            ),
        }

    def record_weather_settlements(self, rows: list[dict], *, observed_at) -> int:
        """Persist finalized weather outcomes for calibration of shadow forecasts."""
        if not rows:
            return 0
        observed_at = utc_iso(observed_at)
        values = []
        for row in rows:
            ticker = str(row.get("ticker") or "").strip()
            if not ticker:
                continue
            result = str(row.get("result") or "").upper().strip()
            settlement = _float(row.get("yes_settlement_value"))
            if settlement is None:
                if result == "YES":
                    settlement = 1.0
                elif result == "NO":
                    settlement = 0.0
            if settlement is None or not (0 <= settlement <= 1):
                continue
            values.append((
                ticker,
                str(row.get("event_ticker") or ""),
                observed_at,
                str(row.get("status") or ""),
                result,
                settlement,
                row.get("source_hint"),
                row.get("target_date"),
                row.get("bucket_label"),
            ))
        if not values:
            return 0
        with self.connect() as con:
            con.executemany(
                """INSERT OR REPLACE INTO weather_settlements(
                    ticker,event_ticker,observed_at,status,result,
                    yes_settlement_value,source_hint,target_date,bucket_label
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return len(values)

    def weather_calibration_summary(self) -> dict:
        """Evaluate the latest pre-settlement shadow fair for each settled ticker."""
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT s.ticker, s.yes_settlement_value AS y,
                       f.p_shadow AS p, f.received_at AS fair_at,
                       s.observed_at AS settled_at
                FROM weather_settlements s
                JOIN weather_shadow_fairs f
                  ON f.ticker=s.ticker
                WHERE f.received_at=(
                    SELECT MAX(f2.received_at)
                    FROM weather_shadow_fairs f2
                    WHERE f2.ticker=s.ticker
                      AND f2.received_at<=s.observed_at
                )
                """
            ).fetchall()
        if not rows:
            return {"n": 0, "brier": None, "log_loss": None}
        brier = 0.0
        log_loss = 0.0
        eps = 1e-6
        for row in rows:
            y = float(row["y"])
            p = min(1.0 - eps, max(eps, float(row["p"])))
            brier += (p - y) ** 2
            log_loss += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        n = len(rows)
        return {
            "n": n,
            "brier": brier / n,
            "log_loss": log_loss / n,
        }

    def record_weather_shadow_intents(self, rows: list[dict], *, received_at) -> int:
        """Log weather opportunities as blocked research intents, never fills."""
        if not rows:
            return 0
        received_at = utc_iso(received_at)
        inserted = 0
        with self.connect() as con:
            for row in rows:
                side = str(row.get("best_side") or "").upper()
                edge = _float(row.get("best_gross_edge"))
                ticker = str(row.get("ticker") or "").strip()
                if not ticker or side not in {"YES", "NO"} or edge is None or edge <= 0:
                    continue
                ask = _float(row.get("yes_ask") if side == "YES" else row.get("no_ask"))
                raw_id = "|".join([
                    "weather_shadow_v1", ticker, side, received_at,
                    str(row.get("p_shadow") or ""),
                ])
                intent_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
                cur = con.execute(
                    """INSERT OR IGNORE INTO intents(
                        intent_id,created_at,lane,lane_version,code_sha,
                        fill_model_version,book_id,market_ticker,side,action,
                        fair_id,ob_received_at,exec_style,limit_price,qty,
                        edge_gross,fees_est,slippage_est,exit_cost_est,sigma,
                        required_edge,z,p_fill_est,expected_hold_h,ev_est,score,
                        status,reject_reason,reject_detail,allocator_run_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        intent_id,
                        received_at,
                        "weather",
                        "weather_shadow_v1",
                        None,
                        "observation_only_v1",
                        "research:weather",
                        ticker,
                        side,
                        "OPEN",
                        None,
                        received_at,
                        "taker_observation",
                        ask,
                        None,
                        edge,
                        None,
                        None,
                        None,
                        _float(row.get("sampling_sigma")),
                        None,
                        None,
                        1.0,
                        None,
                        None,
                        None,
                        "shadow_blocked",
                        "calibration_required",
                        str(row.get("promotion_blocker") or "")[:500],
                        None,
                    ),
                )
                inserted += int(cur.rowcount > 0)
        return inserted

    def record_weather_shadow_edges(self, rows: list[dict], *, received_at) -> int:
        if not rows:
            return 0
        received_at = utc_iso(received_at)
        values = []
        for row in rows:
            p = _float(row.get("p_shadow"))
            if p is None or not (0 <= p <= 1):
                continue
            values.append((
                str(row.get("ticker") or ""),
                str(row.get("event_ticker") or ""),
                received_at,
                p,
                _float(row.get("sampling_sigma")),
                _float(row.get("yes_bid")),
                _float(row.get("yes_ask")),
                _float(row.get("no_bid")),
                _float(row.get("no_ask")),
                _float(row.get("yes_gross_edge")),
                _float(row.get("no_gross_edge")),
                row.get("best_side"),
                _float(row.get("best_gross_edge")),
                row.get("settlement_source_hint"),
                row.get("city"),
                row.get("target_date"),
                row.get("bucket_label"),
                row.get("model"),
                "shadow_only",
            ))
        if not values:
            return 0
        with self.connect() as con:
            con.executemany(
                """INSERT INTO weather_shadow_edges(
                    ticker,event_ticker,received_at,p_shadow,sampling_sigma,
                    yes_bid,yes_ask,no_bid,no_ask,yes_gross_edge,no_gross_edge,
                    best_side,best_gross_edge,settlement_source_hint,city,
                    target_date,bucket_label,model,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return len(values)

    def record_weather_shadow_fairs(self, rows: list[dict], *, received_at) -> int:
        if not rows:
            return 0
        received_at = utc_iso(received_at)
        values = []
        for row in rows:
            try:
                p = float(row.get("p_shadow"))
            except (TypeError, ValueError):
                continue
            if not (0 <= p <= 1):
                continue
            values.append((
                str(row.get("ticker") or ""),
                str(row.get("event_ticker") or ""),
                received_at,
                row.get("city"),
                row.get("target_date"),
                row.get("bucket_label"),
                row.get("variable"),
                p,
                _float(row.get("sampling_sigma")),
                int(row.get("n_members") or 0),
                _float(row.get("ensemble_mean_f")),
                _float(row.get("ensemble_stdev_f")),
                _float(row.get("latitude")),
                _float(row.get("longitude")),
                row.get("timezone"),
                row.get("model"),
                row.get("rounding_assumption"),
                row.get("promotion_blocker"),
                "shadow_only",
            ))
        if not values:
            return 0
        with self.connect() as con:
            con.executemany(
                """INSERT INTO weather_shadow_fairs(
                    ticker,event_ticker,received_at,city,target_date,bucket_label,
                    variable,p_shadow,sampling_sigma,n_members,ensemble_mean_f,
                    ensemble_stdev_f,latitude,longitude,timezone,model,
                    rounding_assumption,promotion_blocker,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return len(values)

    def record_structural_groups(self, markets: list[dict], *, received_at, max_groups: int = 200) -> int:
        """Record event-level basket consistency without claiming arbitrage.

        The event group is NOT assumed exhaustive. We log the price geometry and
        keep exhaustiveness_verified=0 until a later rules parser proves it.
        """
        groups = {}
        for market in markets or []:
            event = str(market.get("event_ticker") or "").strip()
            ticker = str(market.get("ticker") or "").strip()
            if not event or not ticker:
                continue
            groups.setdefault(event, []).append(market)

        received_at = utc_iso(received_at)
        rows = []
        for event, items in groups.items():
            if len(items) < 3:
                continue
            asks, bids = [], []
            complete = True
            tickers = []
            for m in items:
                tickers.append(str(m.get("ticker") or ""))
                ya = _float(m.get("yes_ask"))
                yb = _float(m.get("yes_bid"))
                if ya is None or yb is None or not (0 < yb <= ya < 1):
                    complete = False
                    continue
                asks.append(ya)
                bids.append(yb)
            sum_ask = sum(asks) if complete and len(asks) == len(items) else None
            sum_bid = sum(bids) if complete and len(bids) == len(items) else None
            # Incomplete baskets are not actionable and create enormous
            # storage volume. Keep only fully quoted groups, then cap each
            # observation cycle to the groups with the largest raw inconsistency.
            if not complete or sum_ask is None or sum_bid is None:
                continue
            rows.append((
                event,
                received_at,
                len(items),
                1,
                sum_bid,
                sum_ask,
                1.0 - sum_ask,
                sum_bid - 1.0,
                str(items[0].get("title") or "")[:300],
                json.dumps(tickers, separators=(",", ":")),
                0,
                "research_only",
            ))
        if not rows:
            return 0
        rows.sort(
            key=lambda r: max(abs(float(r[6] or 0)), abs(float(r[7] or 0))),
            reverse=True,
        )
        rows = rows[:max(1, int(max_groups))]
        with self.connect() as con:
            con.executemany(
                """INSERT INTO structural_group_observations(
                    event_ticker,received_at,contracts,complete_yes_quotes,
                    sum_yes_bid,sum_yes_ask,gross_buy_all_gap,gross_sell_all_gap,
                    title_sample,tickers_json,exhaustiveness_verified,mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

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
                            _pack_json(row),
                        ),
                    )
                    inserted += int(cur.rowcount > 0)
                except sqlite3.Error:
                    continue
        return inserted

    def record_fair_snapshot(self, ticker: str, fair_entry: dict, *, input_fetch_ids=None):
        """Append one derived fair snapshot with both source/system age metadata."""
        ticker = str(ticker or "").strip()
        fetched_at = str(fair_entry.get("fetched_at") or "").strip()
        if not ticker or not fetched_at:
            return None
        sigma = _float(fair_entry.get("sigma"))
        # This table stores the v2 shadow estimate, not the still-live legacy
        # Value Edge fair. Keeping the two distinct prevents a shadow model from
        # silently becoming the production decision input.
        p_fair = _float(fair_entry.get("fair_v2_weighted_yes_prob"))
        if sigma is None or p_fair is None or not (0 < p_fair < 1):
            return None
        fetch_ids = [str(x) for x in (input_fetch_ids or []) if str(x or "").strip()]
        raw = "|".join([
            ticker,
            fetched_at,
            str(p_fair),
            str(fair_entry.get("fair_v2_method_version") or ""),
            ",".join(fetch_ids),
        ])
        fair_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        input_received_max = fetched_at
        with self.connect() as con:
            con.execute(
                """INSERT OR IGNORE INTO fair_snapshots(
                    fair_id,market_ticker,map_id,method_version,input_fetch_ids,
                    input_received_at_max,info_ts,processed_at,p_fair,sigma,
                    sigma_base,sigma_disagree,sigma_age,n_books,n_sharp,n_eff,
                    disagreement,devig_method,inputs_json,valid,invalid_reason,
                    source_age_s,system_age_s,delivery_lag_s
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fair_id,
                    ticker,
                    fair_entry.get("map_id"),
                    str(fair_entry.get("fair_v2_method_version") or "fair_v2_shadow_1"),
                    json.dumps(fetch_ids, separators=(",", ":")),
                    input_received_max,
                    fair_entry.get("info_ts"),
                    fetched_at,
                    p_fair,
                    sigma,
                    _float(fair_entry.get("sigma_base")) or 0.0,
                    _float(fair_entry.get("sigma_disagree")) or 0.0,
                    _float(fair_entry.get("sigma_age")) or 0.0,
                    int(fair_entry.get("n_books") or 0),
                    int(fair_entry.get("n_sharp") or 0),
                    _float(fair_entry.get("n_eff")) or 0.0,
                    _float(fair_entry.get("disagreement")),
                    fair_entry.get("devig_method"),
                    json.dumps(fair_entry.get("book_inputs") or [], separators=(",", ":"), default=str),
                    1,
                    None,
                    _float(fair_entry.get("source_age_s")),
                    _float(fair_entry.get("system_age_s")) or 0.0,
                    _float(fair_entry.get("delivery_lag_s")),
                ),
            )
        return fair_id

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

    def latest_timestamp(self, table: str, column: str):
        allowed = {
            ("markets_meta", "received_at"),
            ("weather_market_observations", "received_at"),
            ("audit_runs", "completed_at"),
        }
        if (table, column) not in allowed:
            raise ValueError("Unsupported recorder timestamp lookup")
        with self.connect() as con:
            row = con.execute(
                f"SELECT MAX({column}) AS ts FROM {table}"
            ).fetchone()
        value = row["ts"] if row else None
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def storage_bytes(self) -> dict:
        out = {"db": 0, "wal": 0, "shm": 0, "total": 0}
        for key, suffix in (("db", ""), ("wal", "-wal"), ("shm", "-shm")):
            try:
                size = Path(self.path + suffix).stat().st_size
            except OSError:
                size = 0
            out[key] = int(size)
            out["total"] += int(size)
        return out

    def enforce_soft_limit(self, *, max_mb=350, now=None) -> dict:
        """Aggressively prune old telemetry before the 500 MB volume is full."""
        before = self.storage_bytes()
        max_bytes = max(50, int(max_mb)) * 1024 * 1024
        if before["total"] <= max_bytes:
            return {"triggered": False, "before": before, "after": before, "deleted": {}}

        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        aggressive = {
            "ob_top": ("received_at", 3),
            "ob_snapshots": ("received_at", 3),
            "trades": ("received_at", 14),
            "maker_observations": ("received_at", 3),
            "maker_probe_orders": ("placed_at", 3),
            "structural_group_observations": ("received_at", 3),
            "weather_market_observations": ("received_at", 14),
            "weather_shadow_fairs": ("received_at", 14),
            "weather_shadow_edges": ("received_at", 14),
            "weather_settlements": ("observed_at", 90),
            "odds_fetches": ("received_at", 14),
            "odds_quotes": ("received_at", 14),
            "fair_snapshots": ("processed_at", 14),
            "intents": ("created_at", 14),
            "api_calls": ("received_at", 3),
            "data_quality_events": ("ts", 14),
            "audit_runs": ("completed_at", 14),
            "markets_meta": ("received_at", 30),
        }
        deleted = {}
        with self.connect() as con:
            for table, (column, days) in aggressive.items():
                cutoff = utc_iso(now_dt - timedelta(days=days))
                cur = con.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
                deleted[table] = int(cur.rowcount if cur.rowcount is not None else 0)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        after = self.storage_bytes()
        return {"triggered": True, "before": before, "after": after, "deleted": deleted}

    def prune_retention(self, *, now=None) -> dict:
        """Bound recorder growth on the small Railway volume.

        Deletes free SQLite pages for reuse; no VACUUM is attempted because it
        can require a second full-size copy of the database.
        """
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        policies = {
            "markets_meta": ("received_at", 30),
            "ob_top": ("received_at", 14),
            "ob_snapshots": ("received_at", 14),
            "trades": ("received_at", 30),
            "maker_observations": ("received_at", 14),
            "maker_probe_orders": ("placed_at", 14),
            "structural_group_observations": ("received_at", 14),
            "weather_market_observations": ("received_at", 30),
            "weather_shadow_fairs": ("received_at", 30),
            "weather_shadow_edges": ("received_at", 30),
            "weather_settlements": ("observed_at", 90),
            "odds_fetches": ("received_at", 30),
            "odds_quotes": ("received_at", 30),
            "fair_snapshots": ("processed_at", 30),
            "intents": ("created_at", 30),
            "api_calls": ("received_at", 7),
            "data_quality_events": ("ts", 30),
            "audit_runs": ("completed_at", 30),
        }
        deleted = {}
        with self.connect() as con:
            for table, (column, days) in policies.items():
                cutoff = utc_iso(now_dt - timedelta(days=days))
                cur = con.execute(
                    f"DELETE FROM {table} WHERE {column} < ?",
                    (cutoff,),
                )
                deleted[table] = int(cur.rowcount if cur.rowcount is not None else 0)
            try:
                con.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
        return deleted

    def audit_point_in_time(self) -> dict:
        """Fail-closed research audit for the received_at/processed_at invariant."""
        started = utc_iso()
        checks = {}
        with self.connect() as con:
            checks["fair_input_after_processed"] = int(con.execute(
                """SELECT COUNT(*) FROM fair_snapshots
                   WHERE input_received_at_max > processed_at"""
            ).fetchone()[0])
            checks["intent_future_fair"] = int(con.execute(
                """SELECT COUNT(*) FROM intents i
                   JOIN fair_snapshots f ON f.fair_id=i.fair_id
                   WHERE i.fair_id IS NOT NULL AND f.processed_at > i.created_at"""
            ).fetchone()[0])
            checks["intent_future_book"] = int(con.execute(
                """SELECT COUNT(*) FROM intents
                   WHERE ob_received_at IS NOT NULL AND ob_received_at > created_at"""
            ).fetchone()[0])
            violations = sum(checks.values())
            completed = utc_iso()
            audit_id = hashlib.sha256(
                f"{started}|{completed}|{json.dumps(checks, sort_keys=True)}".encode("utf-8")
            ).hexdigest()[:24]
            con.execute(
                """INSERT INTO audit_runs(audit_id,started_at,completed_at,violations,detail)
                   VALUES(?,?,?,?,?)""",
                (audit_id, started, completed, violations, json.dumps(checks, sort_keys=True)),
            )
            if violations:
                con.execute(
                    """INSERT INTO data_quality_events(ts,type,market_ticker,detail)
                       VALUES(?,?,?,?)""",
                    (
                        completed,
                        "point_in_time_violation",
                        None,
                        json.dumps({"audit_id": audit_id, "checks": checks}, sort_keys=True),
                    ),
                )
        return {
            "audit_id": audit_id,
            "started_at": started,
            "completed_at": completed,
            "violations": violations,
            "checks": checks,
            "ok": violations == 0,
        }

    def counts(self) -> dict:
        names = ("markets_meta", "ob_top", "ob_snapshots", "trades", "odds_fetches",
                 "odds_quotes", "fair_snapshots", "intents", "data_quality_events",
                 "audit_runs", "maker_observations", "maker_probe_orders",
                 "weather_market_observations",
                 "structural_group_observations", "weather_shadow_fairs",
                 "weather_shadow_edges")
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
