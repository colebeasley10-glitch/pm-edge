"""Always-on paper AutoTrader research recorder.

Starts collecting point-in-time Kalshi market metadata, top-of-book observations,
periodic full orderbook snapshots and public trades immediately.  This is a
read-only recorder: it has no order submission methods and requires no Kalshi
write credentials.

WebSocket capture will plug into the same append-only schema once a dedicated
read-only key is configured.  Until then, public REST capture prevents losing
all day-one market history.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from pmedge.kalshi import KalshiPublicClient, normalize_market
from .collector import PUBLIC_BASE
from .clv_capture import capture_clv_checkpoints
from .recorder_store import RecorderStore, utc_iso
from .weather_shadow import (
    OpenMeteoWeatherShadow,
    parse_weather_contract,
    settlement_source_hint,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _elapsed_ms(start, end):
    return int(max(0.0, (end - start).total_seconds()) * 1000)


def _volume(raw):
    for key in ("volume_24h_fp", "volume_24h"):
        try:
            return float(raw.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return 0.0


class RestRecorder:
    def __init__(
        self,
        *,
        db_path="/data/autotrader-recorder.sqlite3",
        client=None,
        interval_seconds=60,
        universe_interval_seconds=300,
        metadata_interval_seconds=900,
        snapshot_interval_seconds=300,
        hot_markets=40,
        snapshot_markets=20,
        orderbook_depth=20,
        max_markets=5000,
        fee_schedule_id="kalshi-predictions-current",
        clock=None,
    ):
        self.store = RecorderStore(db_path)
        self.client = client or KalshiPublicClient(PUBLIC_BASE)
        self.interval_seconds = max(30, int(interval_seconds))
        self.universe_interval_seconds = max(
            self.interval_seconds, int(universe_interval_seconds)
        )
        self.metadata_interval_seconds = max(self.interval_seconds, int(metadata_interval_seconds))
        self.snapshot_interval_seconds = max(self.interval_seconds, int(snapshot_interval_seconds))
        self.hot_markets = max(1, int(hot_markets))
        self.snapshot_markets = max(1, min(int(snapshot_markets), self.hot_markets))
        self.orderbook_depth = max(1, min(int(orderbook_depth), 100))
        self.max_markets = max(1000, int(max_markets))
        self.fee_schedule_id = str(fee_schedule_id)
        self.clock = clock or _utcnow
        self._clv_close_time_cache = {}
        self._last_market_refresh_at = None
        self._market_cache = []
        self._market_cache_received_at = None
        self._last_metadata_at = None
        self._last_snapshot_at = None
        self._last_trade_poll_at = None
        self._last_audit_at = None
        self._last_prune_at = None
        self._last_weather_settlement_at = None
        self._weather_settlement_cursor = 0
        self._weather_shadow = None
        self._stop = threading.Event()
        self._last_result = {}

    @property
    def last_result(self):
        return dict(self._last_result)

    def stop(self):
        self._stop.set()

    def _api(self, endpoint, fn):
        sent_at = self.clock().astimezone(timezone.utc)
        status = 200
        error = None
        result = None
        try:
            result = fn()
            return result
        except Exception as exc:
            status = None
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
            raise
        finally:
            received_at = self.clock().astimezone(timezone.utc)
            try:
                self.store.record_api_call(
                    service="autotrader-recorder",
                    endpoint=endpoint,
                    sent_at=sent_at,
                    received_at=received_at,
                    latency_ms=_elapsed_ms(sent_at, received_at),
                    status=status,
                    error=error,
                )
            except Exception:
                pass

    def _targeted_weather_markets(self):
        """Discover open temperature markets by Weather series, not global pagination."""
        # Verified current daily-temperature series provide a fast, no-cost
        # bootstrap. Dynamic category discovery can be enabled separately and
        # augments these; it is not required for the first shadow dataset.
        default_seeds = (
            "KXHIGHAUS,KXHIGHLAX,KXHIGHNY,KXHIGHTSFO,"
            "KXHIGHTSEA,KXHIGHTTTN,KXLOWTMIA"
        )
        seed_tickers = [
            x.strip().upper()
            for x in os.getenv("AUTOTRADER_WEATHER_SERIES_SEEDS", default_seeds).split(",")
            if x.strip()
        ]
        series_rows = [{"ticker": ticker, "title": "temperature seed"} for ticker in seed_tickers]
        series_errors = []
        seen_series = set(seed_tickers)

        dynamic = os.getenv(
            "AUTOTRADER_WEATHER_DYNAMIC_SERIES", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if dynamic:
            for category in ("Climate and Weather", "Climate And Weather", "Weather", "Climate"):
                try:
                    rows = self._api(
                        f"GET /series?category={category}",
                        lambda category=category: self.client.list_series(
                            category=category, include_volume=True
                        ),
                    )
                except Exception as exc:
                    if len(series_errors) < 4:
                        series_errors.append({
                            "stage": "series",
                            "category": category,
                            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                        })
                    continue
                for row in rows:
                    ticker = str(row.get("ticker") or "").strip()
                    if ticker and ticker not in seen_series:
                        seen_series.add(ticker)
                        series_rows.append(row)
        if not series_rows:
            return [], [], [{"stage": "series", "error": "no_temperature_series_configured"}]

        temperature_series = []
        for row in series_rows:
            blob = " ".join([
                str(row.get("ticker") or ""),
                str(row.get("title") or ""),
                str(row.get("category") or ""),
                " ".join(str(x) for x in (row.get("categories") or [])),
                " ".join(str(x) for x in (row.get("tags") or [])),
            ]).lower()
            if any(token in blob for token in (
                "temperature", "daily high", "daily low", "highest temperature",
                "lowest temperature", "high temperature", "low temperature",
            )):
                temperature_series.append(row)

        # Prefer active/used series by volume, then deterministic ticker order.
        def volume(row):
            try:
                return float(row.get("volume_fp") or 0)
            except (TypeError, ValueError):
                return 0.0
        temperature_series.sort(
            key=lambda row: (-volume(row), str(row.get("ticker") or ""))
        )
        max_series = max(1, int(os.getenv("AUTOTRADER_WEATHER_MAX_SERIES", "20")))
        temperature_series = temperature_series[:max_series]

        markets = []
        errors = list(series_errors)
        for series in temperature_series:
            ticker = str(series.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                events = self._api(
                    f"GET /events?series_ticker={ticker}",
                    lambda ticker=ticker: self.client.list_events(
                        series_ticker=ticker,
                        status="open",
                        with_nested_markets=True,
                        max_events=20,
                    ),
                )
            except Exception as exc:
                if len(errors) < 8:
                    errors.append({
                        "stage": "events",
                        "series_ticker": ticker,
                        "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                    })
                continue
            for event in events:
                event_title = str(event.get("title") or "")
                event_ticker = str(event.get("event_ticker") or "")
                for raw in event.get("markets") or []:
                    if not isinstance(raw, dict):
                        continue
                    merged = dict(raw)
                    merged["event_ticker"] = str(
                        merged.get("event_ticker") or event_ticker
                    )
                    merged["series_ticker"] = ticker
                    # Market titles can be generic bucket labels; preserve the
                    # event title separately for city/date parsing.
                    merged["_weather_event_title"] = event_title
                    merged["_weather_series"] = series
                    markets.append(merged)

        return markets, temperature_series, errors

    def _targeted_weather_settlements(self, series_tickers):
        """Fetch recent settled temperature markets for calibration only."""
        rows = []
        errors = []
        for ticker in list(series_tickers or []):
            ticker = str(ticker or "").strip()
            if not ticker:
                continue
            try:
                events = self._api(
                    f"GET /events?series_ticker={ticker}&status=settled",
                    lambda ticker=ticker: self.client.list_events(
                        series_ticker=ticker,
                        status="settled",
                        with_nested_markets=True,
                        max_events=10,
                    ),
                )
            except Exception as exc:
                if len(errors) < 8:
                    errors.append({
                        "series_ticker": ticker,
                        "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                    })
                continue
            for event in events:
                event_ticker = str(event.get("event_ticker") or "")
                event_title = str(event.get("title") or "")
                for raw in event.get("markets") or []:
                    if not isinstance(raw, dict):
                        continue
                    merged = dict(raw)
                    merged["event_ticker"] = str(
                        merged.get("event_ticker") or event_ticker
                    )
                    merged["_weather_event_title"] = event_title
                    merged["series_ticker"] = ticker
                    normalized = normalize_market(merged)
                    normalized["_weather_event_title"] = event_title
                    normalized["series_ticker"] = ticker
                    spec = parse_weather_contract(normalized)
                    if spec is None:
                        continue
                    settlement_value = (
                        raw.get("settlement_value_dollars")
                        or raw.get("settlement_value")
                        or raw.get("yes_settlement_value_dollars")
                    )
                    rows.append({
                        "ticker": spec.ticker,
                        "event_ticker": spec.event_ticker,
                        "status": raw.get("status"),
                        "result": raw.get("result"),
                        "yes_settlement_value": settlement_value,
                        "source_hint": settlement_source_hint(normalized),
                        "target_date": spec.target_date,
                        "bucket_label": spec.bucket_label,
                    })
        return rows, errors

    def cycle(self):
        cycle_started = self.clock().astimezone(timezone.utc)
        storage_guard = None
        try:
            storage_guard = self.store.enforce_soft_limit(
                max_mb=int(os.getenv("AUTOTRADER_RECORDER_SOFT_MAX_MB", "350")),
                now=cycle_started,
            )
        except Exception as exc:
            storage_guard = {
                "triggered": False,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }

        universe_refreshed = False
        universe_error = None
        universe_due = (
            self._last_market_refresh_at is None
            or not self._market_cache
            or (cycle_started - self._last_market_refresh_at).total_seconds()
            >= self.universe_interval_seconds
        )
        if universe_due:
            try:
                fresh_markets = self._api(
                    "GET /markets?status=open",
                    lambda: self.client.list_open_markets(max_markets=self.max_markets),
                )
                self._market_cache = list(fresh_markets)
                self._market_cache_received_at = self.clock().astimezone(timezone.utc)
                self._last_market_refresh_at = cycle_started
                universe_refreshed = True
            except Exception as exc:
                universe_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                # A transient Kalshi 429 should not stop trade-tape collection or
                # the rest of the worker if we have a previously observed universe.
                if not self._market_cache:
                    raise

        raw_markets = list(self._market_cache)
        market_received = (
            self._market_cache_received_at
            or self.clock().astimezone(timezone.utc)
        )

        ranked = sorted(
            [m for m in raw_markets if str(m.get("ticker") or "").strip()],
            key=lambda m: (-_volume(m), str(m.get("ticker") or "")),
        )
        hot_raw = ranked[: self.hot_markets]
        hot_tickers = {str(m.get("ticker")) for m in hot_raw}

        # Prefer markets with an actually two-sided list quote for maker
        # observation. Raw volume alone can otherwise select one-sided or MVE
        # books that teach us nothing about queue/adverse-selection behavior.
        maker_raw = []
        for raw in ranked:
            try:
                m = normalize_market(raw)
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                nb, na = m.get("no_bid"), m.get("no_ask")
                if (
                    yb is not None and ya is not None and nb is not None and na is not None
                    and 0 < float(yb) <= float(ya) < 1
                    and 0 < float(nb) <= float(na) < 1
                    and float(ya) - float(yb) <= 0.15
                ):
                    maker_raw.append(raw)
                    if len(maker_raw) >= self.snapshot_markets:
                        break
            except Exception:
                continue

        metadata_due = universe_refreshed and (
            self._last_metadata_at is None
            or (cycle_started - self._last_metadata_at).total_seconds()
            >= self.metadata_interval_seconds
        )
        meta_changes = 0
        meta_skipped = 0
        meta_capped = 0
        meta_distinct_after = None
        weather_markets = 0
        weather_samples = []
        targeted_weather_raw = []
        weather_series = []
        weather_discovery_errors = []
        if metadata_due:
            targeted_weather_raw, weather_series, weather_discovery_errors = (
                self._targeted_weather_markets()
            )
            batch_meta = self.store.record_market_meta_batch(
                raw_markets,
                received_at=market_received,
                fee_schedule_id=self.fee_schedule_id,
                max_distinct_tickers=int(
                    os.getenv("AUTOTRADER_RECORDER_METADATA_MAX_TICKERS", "7500")
                ),
            )
            meta_changes += int(batch_meta.get("changed") or 0)
            meta_skipped += int(batch_meta.get("skipped") or 0)
            meta_capped += int(batch_meta.get("capped") or 0)
            meta_distinct_after = batch_meta.get("distinct_after")
            for err in batch_meta.get("errors") or []:
                self.store.quality_event(
                    "metadata_write_error",
                    market_ticker=err.get("ticker"),
                    detail={"error": err.get("error")},
                )
            for raw in raw_markets:
                try:
                    normalized_weather = normalize_market(raw)
                    recorded_weather = self.store.record_weather_market(
                        normalized_weather, received_at=market_received
                    )
                    weather_markets += int(recorded_weather)
                    if recorded_weather and len(weather_samples) < 8:
                        weather_samples.append({
                            "ticker": normalized_weather.get("ticker"),
                            "event_ticker": normalized_weather.get("event_ticker"),
                            "title": normalized_weather.get("title"),
                        })
                except Exception as exc:
                    self.store.quality_event(
                        "weather_metadata_write_error",
                        market_ticker=str(raw.get("ticker") or "") or None,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
            # Targeted Weather-series discovery reaches markets that may not
            # appear within the first N globally paginated open markets.
            for raw in targeted_weather_raw:
                try:
                    normalized_weather = normalize_market(raw)
                    event_title = str(raw.get("_weather_event_title") or "")
                    if event_title:
                        normalized_weather["_weather_event_title"] = event_title
                    normalized_weather["series_ticker"] = str(raw.get("series_ticker") or "")
                    recorded_weather = self.store.record_weather_market(
                        normalized_weather, received_at=market_received
                    )
                    weather_markets += int(recorded_weather)
                    if recorded_weather and len(weather_samples) < 8:
                        weather_samples.append({
                            "ticker": normalized_weather.get("ticker"),
                            "event_ticker": normalized_weather.get("event_ticker"),
                            "series_ticker": normalized_weather.get("series_ticker"),
                            "title": event_title or normalized_weather.get("title"),
                        })
                except Exception as exc:
                    if len(weather_discovery_errors) < 8:
                        weather_discovery_errors.append({
                            "stage": "weather_write",
                            "ticker": str(raw.get("ticker") or ""),
                            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                        })
            self._last_metadata_at = cycle_started

        # Weather markets are often far outside the globally ranked top markets.
        # Add two-sided targeted weather contracts to the maker-observation pool.
        if targeted_weather_raw:
            seen_maker = {str(x.get("ticker") or "") for x in maker_raw}
            weather_maker_candidates = []
            for raw in targeted_weather_raw:
                ticker = str(raw.get("ticker") or "")
                if not ticker or ticker in seen_maker:
                    continue
                try:
                    m = normalize_market(raw)
                    yb, ya = m.get("yes_bid"), m.get("yes_ask")
                    nb, na = m.get("no_bid"), m.get("no_ask")
                    if (
                        yb is not None and ya is not None and nb is not None and na is not None
                        and 0 < float(yb) <= float(ya) < 1
                        and 0 < float(nb) <= float(na) < 1
                    ):
                        weather_maker_candidates.append(raw)
                except Exception:
                    continue
            weather_maker_candidates.sort(key=lambda raw: -_volume(raw))
            for raw in weather_maker_candidates:
                if len(maker_raw) >= self.snapshot_markets:
                    break
                maker_raw.append(raw)
                seen_maker.add(str(raw.get("ticker") or ""))

        structural_groups = 0

        weather_shadow_rows = 0
        weather_shadow_edge_rows = 0
        weather_shadow_intents = 0
        weather_shadow_top_edges = []
        weather_shadow_errors = []
        if (
            metadata_due
            and os.getenv("AUTOTRADER_WEATHER_SHADOW_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ):
            try:
                weather_groups = {}
                for raw in targeted_weather_raw:
                    m = normalize_market(raw)
                    if raw.get("_weather_event_title"):
                        m["_weather_event_title"] = raw.get("_weather_event_title")
                    m["series_ticker"] = str(raw.get("series_ticker") or "")
                    spec = parse_weather_contract(m)
                    if spec is not None:
                        weather_groups.setdefault(spec.event_ticker, []).append(m)
                if self._weather_shadow is None:
                    self._weather_shadow = OpenMeteoWeatherShadow()
                max_events = max(1, int(os.getenv("AUTOTRADER_WEATHER_SHADOW_MAX_EVENTS", "8")))
                for event_ticker, group in list(weather_groups.items())[:max_events]:
                    try:
                        priced = self._weather_shadow.price_group(group)
                        shadow_received = self.clock().astimezone(timezone.utc)
                        weather_shadow_rows += self.store.record_weather_shadow_fairs(
                            priced, received_at=shadow_received
                        )
                        weather_shadow_edge_rows += self.store.record_weather_shadow_edges(
                            priced, received_at=shadow_received
                        )
                        weather_shadow_intents += self.store.record_weather_shadow_intents(
                            priced, received_at=shadow_received
                        )
                        for row in priced:
                            edge = row.get("best_gross_edge")
                            if edge is None:
                                continue
                            weather_shadow_top_edges.append({
                                "ticker": row.get("ticker"),
                                "city": row.get("city"),
                                "bucket": row.get("bucket_label"),
                                "side": row.get("best_side"),
                                "gross_edge": round(float(edge), 4),
                                "p_shadow": round(float(row.get("p_shadow") or 0), 4),
                                "source_hint": row.get("settlement_source_hint"),
                            })
                    except Exception as exc:
                        if len(weather_shadow_errors) < 5:
                            weather_shadow_errors.append({
                                "event_ticker": event_ticker,
                                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                            })
            except Exception as exc:
                weather_shadow_errors.append({
                    "event_ticker": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                })

        weather_settlements_logged = 0
        weather_settlement_errors = []
        weather_calibration = None
        settlement_due = (
            self._last_weather_settlement_at is None
            or (cycle_started - self._last_weather_settlement_at).total_seconds()
            >= int(os.getenv("AUTOTRADER_WEATHER_SETTLEMENT_INTERVAL_SECONDS", "900"))
        )
        if settlement_due:
            seed_series = [
                x.strip().upper()
                for x in os.getenv(
                    "AUTOTRADER_WEATHER_SERIES_SEEDS",
                    "KXHIGHAUS,KXHIGHLAX,KXHIGHNY,KXHIGHTSFO,"
                    "KXHIGHTSEA,KXHIGHTTTN,KXLOWTMIA",
                ).split(",")
                if x.strip()
            ]
            batch_size = max(
                1, int(os.getenv("AUTOTRADER_WEATHER_SETTLEMENT_BATCH_SERIES", "2"))
            )
            if seed_series:
                start = self._weather_settlement_cursor % len(seed_series)
                batch = [
                    seed_series[(start + i) % len(seed_series)]
                    for i in range(min(batch_size, len(seed_series)))
                ]
                self._weather_settlement_cursor = (
                    start + len(batch)
                ) % len(seed_series)
            else:
                batch = []
            settlement_rows, weather_settlement_errors = (
                self._targeted_weather_settlements(batch)
            )
            try:
                weather_settlements_logged = self.store.record_weather_settlements(
                    settlement_rows,
                    observed_at=self.clock().astimezone(timezone.utc),
                )
                weather_calibration = self.store.weather_calibration_summary()
            except Exception as exc:
                weather_settlement_errors.append({
                    "series_ticker": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                })
            self._last_weather_settlement_at = cycle_started

        top_rows = 0
        maker_observations = 0
        if universe_refreshed:
            for raw in hot_raw:
                try:
                    normalized = normalize_market(raw)
                    self.store.record_top(
                        normalized,
                        received_at=market_received,
                        processed_at=self.clock().astimezone(timezone.utc),
                        origin="rest_market_list",
                    )
                    top_rows += 1
                    maker_observations += self.store.record_maker_observations(
                        normalized, received_at=market_received
                    )
                except Exception as exc:
                    self.store.quality_event(
                        "top_write_error",
                        market_ticker=str(raw.get("ticker") or "") or None,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )

        snapshots = 0
        maker_probes_created = 0
        snapshot_due = (
            self._last_snapshot_at is None
            or (cycle_started - self._last_snapshot_at).total_seconds()
            >= self.snapshot_interval_seconds
        )
        if snapshot_due:
            if universe_refreshed:
                try:
                    normalized_all = [normalize_market(raw) for raw in raw_markets]
                    structural_groups = self.store.record_structural_groups(
                        normalized_all,
                        received_at=market_received,
                        max_groups=int(os.getenv("AUTOTRADER_STRUCTURAL_MAX_GROUPS", "200")),
                    )
                except Exception as exc:
                    self.store.quality_event(
                        "structural_group_write_error",
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
            for raw in maker_raw:
                ticker = str(raw.get("ticker") or "").strip()
                if not ticker:
                    continue
                try:
                    book = self._api(
                        f"GET /markets/{ticker}/orderbook",
                        lambda ticker=ticker: self.client.get_orderbook(
                            ticker, depth=self.orderbook_depth
                        ),
                    )
                    received_at = self.clock().astimezone(timezone.utc)
                    self.store.record_snapshot(
                        ticker,
                        book,
                        received_at=received_at,
                        origin="rest",
                    )
                    normalized_for_book = normalize_market(raw)
                    maker_observations += self.store.record_maker_from_orderbook(
                        normalized_for_book, book, received_at=received_at
                    )
                    maker_probes_created += self.store.create_maker_probes_from_orderbook(
                        normalized_for_book,
                        book,
                        received_at=received_at,
                        ttl_seconds=int(os.getenv("AUTOTRADER_MAKER_PROBE_TTL_SECONDS", "300")),
                    )
                    snapshots += 1
                except Exception as exc:
                    self.store.quality_event(
                        "orderbook_snapshot_error",
                        market_ticker=ticker,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
            self._last_snapshot_at = cycle_started

        # CLV checkpoints for open positions in every paper lane (evaluation only).
        clv_summary = None
        if os.getenv("AUTOTRADER_CLV_CAPTURE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
            try:
                clv_summary = capture_clv_checkpoints(
                    self.store, self.client,
                    now=self.clock().astimezone(timezone.utc),
                    close_time_cache=self._clv_close_time_cache,
                )
            except Exception as exc:
                clv_summary = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

        trades_inserted = 0
        try:
            min_ts = None
            if self._last_trade_poll_at is not None:
                # One-second overlap; trade_id primary key handles duplicates.
                min_ts = int((self._last_trade_poll_at - timedelta(seconds=1)).timestamp())
            data = self._api(
                "GET /markets/trades",
                lambda: self.client.get_trades(
                    limit=1000,
                    min_ts=min_ts,
                    is_block_trade=False,
                ),
            )
            trades_received = self.clock().astimezone(timezone.utc)
            maker_tickers = {
                str(raw.get("ticker") or "") for raw in maker_raw
                if str(raw.get("ticker") or "")
            }
            tracked_tickers = hot_tickers | maker_tickers
            rows = [
                row for row in (data.get("trades") or [])
                if str(row.get("ticker") or row.get("market_ticker") or "") in tracked_tickers
            ]
            trades_inserted = self.store.record_trades(rows, received_at=trades_received)
            self._last_trade_poll_at = cycle_started
        except Exception as exc:
            self.store.quality_event(
                "trade_poll_error",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )

        maker_probe_evaluation = None
        try:
            maker_probe_evaluation = self.store.evaluate_maker_probes(
                now=self.clock().astimezone(timezone.utc)
            )
        except Exception as exc:
            maker_probe_evaluation = {
                "error": f"{type(exc).__name__}: {str(exc)[:240]}"
            }

        maker_probe_summary = None
        try:
            maker_probe_summary = self.store.maker_probe_summary()
        except Exception as exc:
            maker_probe_summary = {
                "error": f"{type(exc).__name__}: {str(exc)[:240]}"
            }

        retention_pruned = None
        if (
            self._last_prune_at is None
            or (cycle_started - self._last_prune_at).total_seconds() >= 3600
        ):
            try:
                retention_pruned = self.store.prune_retention(
                    now=self.clock().astimezone(timezone.utc)
                )
            except Exception as exc:
                retention_pruned = {
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}"
                }
            self._last_prune_at = cycle_started

        audit = None
        if (
            self._last_audit_at is None
            or (cycle_started - self._last_audit_at).total_seconds() >= 3600
        ):
            try:
                audit = self.store.audit_point_in_time()
            except Exception as exc:
                audit = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            self._last_audit_at = cycle_started

        weather_shadow_top_edges.sort(
            key=lambda row: row["gross_edge"], reverse=True
        )
        weather_shadow_top_edges = weather_shadow_top_edges[:10]

        result = {
            "recorder": "rest_v1",
            "observed_at": utc_iso(self.clock().astimezone(timezone.utc)),
            "open_markets": len(raw_markets),
            "universe_refreshed": universe_refreshed,
            "universe_error": universe_error,
            "universe_age_seconds": (
                max(0.0, (cycle_started - self._last_market_refresh_at).total_seconds())
                if self._last_market_refresh_at is not None else None
            ),
            "hot_markets": len(hot_raw),
            "top_rows": top_rows,
            "maker_observations": maker_observations,
            "structural_groups_logged": structural_groups,
            "metadata_changes": meta_changes,
            "metadata_skipped": meta_skipped,
            "metadata_capped": meta_capped,
            "metadata_distinct_after": meta_distinct_after,
            "weather_markets_logged": weather_markets,
            "weather_series_found": len(weather_series),
            "weather_targeted_markets_found": len(targeted_weather_raw),
            "weather_samples": weather_samples,
            "weather_discovery_errors": weather_discovery_errors,
            "weather_shadow_rows": weather_shadow_rows,
            "weather_shadow_edge_rows": weather_shadow_edge_rows,
            "weather_shadow_intents": weather_shadow_intents,
            "weather_shadow_top_edges": weather_shadow_top_edges,
            "weather_shadow_errors": weather_shadow_errors,
            "weather_settlements_logged": weather_settlements_logged,
            "weather_settlement_errors": weather_settlement_errors,
            "weather_calibration": weather_calibration,
            "full_snapshots": snapshots,
            "maker_probes_created": maker_probes_created,
            "maker_probe_evaluation": maker_probe_evaluation,
            "maker_probe_summary": maker_probe_summary,
            "trades_inserted": trades_inserted,
            "clv_checkpoints": clv_summary,
            "point_in_time_audit": audit,
            "retention_pruned": retention_pruned,
            "storage_guard": storage_guard,
            "storage_bytes": self.store.storage_bytes(),
            "db_path": self.store.path,
        }
        self._last_result = result
        return result

    def run_forever(self):
        print(json.dumps({"autotrader_recorder": "starting", "mode": "paper"}), flush=True)
        while not self._stop.is_set():
            started = time.monotonic()
            cycle_error = None
            try:
                result = self.cycle()
                print(json.dumps(result, separators=(",", ":")), flush=True)
            except Exception as exc:
                cycle_error = exc
                try:
                    self.store.quality_event(
                        "recorder_cycle_error",
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
                except Exception:
                    pass
                print(
                    json.dumps(
                        {
                            "autotrader_recorder": "error",
                            "error": type(exc).__name__,
                            "message": str(exc)[:500],
                        }
                    ),
                    flush=True,
                )
            delay = max(1.0, self.interval_seconds - (time.monotonic() - started))
            # If Kalshi rate-limits a cold start (before any cache exists), back
            # off instead of hammering the same paginated endpoint every minute.
            if cycle_error is not None and "429" in str(cycle_error):
                delay = max(delay, 180.0)
            self._stop.wait(delay)


def _reset_legacy_recorder_if_needed(db_path: str) -> dict:
    """One-time reset of the early uncompressed research DB.

    Only recorder telemetry is eligible. Paper portfolio/fair state files are
    separate and are never touched.
    """
    out = {"reset": False, "reason": None, "removed": []}
    try:
        size = os.path.getsize(db_path)
    except OSError:
        return out
    legacy_threshold = int(
        os.getenv("AUTOTRADER_RECORDER_LEGACY_RESET_MIN_MB", "100")
    ) * 1024 * 1024
    oversized_threshold = int(
        os.getenv("AUTOTRADER_RECORDER_OVERSIZE_RESET_MB", "300")
    ) * 1024 * 1024
    if size < min(legacy_threshold, oversized_threshold):
        return out

    marker_value = None
    legacy_plain_rows = False
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT value FROM schema_meta WHERE key='raw_payload_format_version'"
            ).fetchone()
            marker_value = row[0] if row else None
            # The format marker may have been written before the one-time reset
            # code was deployed. Inspect actual legacy payload rows as the source
            # of truth so an oversized mixed-format DB cannot evade migration.
            try:
                legacy_plain_rows = con.execute(
                    """SELECT 1 FROM markets_meta
                       WHERE raw_json NOT LIKE 'zlib64:%' LIMIT 1"""
                ).fetchone() is not None
            except sqlite3.Error:
                legacy_plain_rows = False
        finally:
            con.close()
    except Exception:
        marker_value = None
        legacy_plain_rows = True

    oversize_reset = size >= oversized_threshold
    legacy_reset = size >= legacy_threshold and legacy_plain_rows
    if not oversize_reset and not legacy_reset:
        return out

    for suffix in ("", "-wal", "-shm"):
        target = db_path + suffix
        try:
            if os.path.exists(target):
                os.remove(target)
                out["removed"].append(target)
        except OSError:
            pass
    out["reset"] = bool(out["removed"])
    out["reason"] = (
        "oversized_recorder_compaction"
        if oversize_reset else "legacy_uncompressed_recorder_db"
    )
    return out


def start_recorder_thread(
    *,
    db_path=None,
    interval_seconds=None,
    universe_interval_seconds=None,
    metadata_interval_seconds=None,
    snapshot_interval_seconds=None,
    hot_markets=None,
    snapshot_markets=None,
    orderbook_depth=None,
    fee_schedule_id=None,
):
    resolved_db_path = db_path or os.getenv(
        "AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"
    )
    legacy_reset = _reset_legacy_recorder_if_needed(resolved_db_path)
    if legacy_reset.get("reset"):
        print(
            json.dumps({
                "autotrader_recorder": "legacy_storage_reset",
                "reason": legacy_reset.get("reason"),
                "removed": legacy_reset.get("removed"),
                "preserved_other_data": True,
            }),
            flush=True,
        )

    def build():
        return RestRecorder(
            db_path=resolved_db_path,
            interval_seconds=interval_seconds or int(
                os.getenv("AUTOTRADER_RECORDER_INTERVAL_SECONDS", "60")
            ),
            universe_interval_seconds=universe_interval_seconds or int(
                os.getenv("AUTOTRADER_RECORDER_UNIVERSE_INTERVAL_SECONDS", "300")
            ),
            metadata_interval_seconds=metadata_interval_seconds or int(
                os.getenv("AUTOTRADER_RECORDER_METADATA_INTERVAL_SECONDS", "900")
            ),
            snapshot_interval_seconds=snapshot_interval_seconds or int(
                os.getenv("AUTOTRADER_RECORDER_SNAPSHOT_INTERVAL_SECONDS", "300")
            ),
            hot_markets=hot_markets or int(
                os.getenv("AUTOTRADER_RECORDER_HOT_MARKETS", "40")
            ),
            snapshot_markets=snapshot_markets or int(
                os.getenv("AUTOTRADER_RECORDER_SNAPSHOT_MARKETS", "20")
            ),
            orderbook_depth=orderbook_depth or int(
                os.getenv("AUTOTRADER_RECORDER_ORDERBOOK_DEPTH", "20")
            ),
            max_markets=int(os.getenv("AUTOTRADER_RECORDER_MAX_MARKETS", "5000")),
            fee_schedule_id=fee_schedule_id or os.getenv(
                "AUTOTRADER_FEE_SCHEDULE_ID", "kalshi-predictions-current"
            ),
        )

    try:
        recorder = build()
    except (sqlite3.OperationalError, OSError) as exc:
        full = (
            "full" in str(exc).lower()
            or "no space left" in str(exc).lower()
            or getattr(exc, "errno", None) == 28
        )
        allow_reset = os.getenv(
            "AUTOTRADER_RECORDER_RESET_ON_FULL", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        if not (full and allow_reset):
            raise
        # The recorder DB is research telemetry only. Delete ONLY this database
        # and its SQLite sidecars; never touch portfolio/fair state on /data.
        removed = []
        for suffix in ("", "-wal", "-shm"):
            target = resolved_db_path + suffix
            try:
                if os.path.exists(target):
                    os.remove(target)
                    removed.append(target)
            except OSError:
                pass
        print(
            json.dumps(
                {
                    "autotrader_recorder": "reset_after_full_volume",
                    "removed": removed,
                    "preserved_other_data": True,
                }
            ),
            flush=True,
        )
        recorder = build()

    thread = threading.Thread(
        target=recorder.run_forever,
        name="autotrader-recorder",
        daemon=True,
    )
    thread.start()
    return recorder, thread


def main():
    recorder = RestRecorder(
        db_path=os.getenv("AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"),
        interval_seconds=int(os.getenv("AUTOTRADER_RECORDER_INTERVAL_SECONDS", "60")),
        universe_interval_seconds=int(
            os.getenv("AUTOTRADER_RECORDER_UNIVERSE_INTERVAL_SECONDS", "300")
        ),
        metadata_interval_seconds=int(
            os.getenv("AUTOTRADER_RECORDER_METADATA_INTERVAL_SECONDS", "900")
        ),
        snapshot_interval_seconds=int(
            os.getenv("AUTOTRADER_RECORDER_SNAPSHOT_INTERVAL_SECONDS", "300")
        ),
        hot_markets=int(os.getenv("AUTOTRADER_RECORDER_HOT_MARKETS", "40")),
        snapshot_markets=int(
            os.getenv("AUTOTRADER_RECORDER_SNAPSHOT_MARKETS", "20")
        ),
        orderbook_depth=int(
            os.getenv("AUTOTRADER_RECORDER_ORDERBOOK_DEPTH", "20")
        ),
        max_markets=int(os.getenv("AUTOTRADER_RECORDER_MAX_MARKETS", "5000")),
    )
    recorder.run_forever()


if __name__ == "__main__":
    main()
