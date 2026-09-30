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
from .recorder_store import RecorderStore, utc_iso
from .weather_shadow import OpenMeteoWeatherShadow, parse_weather_contract


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
        self.metadata_interval_seconds = max(self.interval_seconds, int(metadata_interval_seconds))
        self.snapshot_interval_seconds = max(self.interval_seconds, int(snapshot_interval_seconds))
        self.hot_markets = max(1, int(hot_markets))
        self.snapshot_markets = max(1, min(int(snapshot_markets), self.hot_markets))
        self.orderbook_depth = max(1, min(int(orderbook_depth), 100))
        self.max_markets = max(1000, int(max_markets))
        self.fee_schedule_id = str(fee_schedule_id)
        self.clock = clock or _utcnow
        self._last_metadata_at = None
        self._last_snapshot_at = None
        self._last_trade_poll_at = None
        self._last_audit_at = None
        self._last_prune_at = None
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

        raw_markets = self._api(
            "GET /markets?status=open",
            lambda: self.client.list_open_markets(max_markets=self.max_markets),
        )
        market_received = self.clock().astimezone(timezone.utc)

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

        metadata_due = (
            self._last_metadata_at is None
            or (cycle_started - self._last_metadata_at).total_seconds()
            >= self.metadata_interval_seconds
        )
        meta_changes = 0
        weather_markets = 0
        weather_samples = []
        targeted_weather_raw = []
        weather_series = []
        weather_discovery_errors = []
        if metadata_due:
            targeted_weather_raw, weather_series, weather_discovery_errors = (
                self._targeted_weather_markets()
            )
            for raw in raw_markets:
                try:
                    meta_changes += int(
                        self.store.record_market_meta(
                            raw,
                            received_at=market_received,
                            fee_schedule_id=self.fee_schedule_id,
                        )
                    )
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
                        "metadata_write_error",
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

        structural_groups = 0

        weather_shadow_rows = 0
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
                        weather_shadow_rows += self.store.record_weather_shadow_fairs(
                            priced, received_at=self.clock().astimezone(timezone.utc)
                        )
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

        top_rows = 0
        maker_observations = 0
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
        snapshot_due = (
            self._last_snapshot_at is None
            or (cycle_started - self._last_snapshot_at).total_seconds()
            >= self.snapshot_interval_seconds
        )
        if snapshot_due:
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
                    maker_observations += self.store.record_maker_from_orderbook(
                        normalize_market(raw), book, received_at=received_at
                    )
                    snapshots += 1
                except Exception as exc:
                    self.store.quality_event(
                        "orderbook_snapshot_error",
                        market_ticker=ticker,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
            self._last_snapshot_at = cycle_started

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
            rows = [
                row for row in (data.get("trades") or [])
                if str(row.get("ticker") or row.get("market_ticker") or "") in hot_tickers
            ]
            trades_inserted = self.store.record_trades(rows, received_at=trades_received)
            self._last_trade_poll_at = cycle_started
        except Exception as exc:
            self.store.quality_event(
                "trade_poll_error",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )

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

        result = {
            "recorder": "rest_v1",
            "observed_at": utc_iso(self.clock().astimezone(timezone.utc)),
            "open_markets": len(raw_markets),
            "hot_markets": len(hot_raw),
            "top_rows": top_rows,
            "maker_observations": maker_observations,
            "structural_groups_logged": structural_groups,
            "metadata_changes": meta_changes,
            "weather_markets_logged": weather_markets,
            "weather_series_found": len(weather_series),
            "weather_targeted_markets_found": len(targeted_weather_raw),
            "weather_samples": weather_samples,
            "weather_discovery_errors": weather_discovery_errors,
            "weather_shadow_rows": weather_shadow_rows,
            "weather_shadow_errors": weather_shadow_errors,
            "full_snapshots": snapshots,
            "trades_inserted": trades_inserted,
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
            try:
                result = self.cycle()
                print(json.dumps(result, separators=(",", ":")), flush=True)
            except Exception as exc:
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
            self._stop.wait(delay)


def start_recorder_thread(
    *,
    db_path=None,
    interval_seconds=None,
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

    def build():
        return RestRecorder(
            db_path=resolved_db_path,
            interval_seconds=interval_seconds or int(
                os.getenv("AUTOTRADER_RECORDER_INTERVAL_SECONDS", "60")
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
