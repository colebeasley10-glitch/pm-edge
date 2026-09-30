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
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from pmedge.kalshi import KalshiPublicClient, normalize_market
from .collector import PUBLIC_BASE
from .recorder_store import RecorderStore, utc_iso


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
        self.fee_schedule_id = str(fee_schedule_id)
        self.clock = clock or _utcnow
        self._last_metadata_at = None
        self._last_snapshot_at = None
        self._last_trade_poll_at = None
        self._last_audit_at = None
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

    def cycle(self):
        cycle_started = self.clock().astimezone(timezone.utc)
        raw_markets = self._api(
            "GET /markets?status=open",
            lambda: self.client.list_open_markets(max_markets=3000),
        )
        market_received = self.clock().astimezone(timezone.utc)

        ranked = sorted(
            [m for m in raw_markets if str(m.get("ticker") or "").strip()],
            key=lambda m: (-_volume(m), str(m.get("ticker") or "")),
        )
        hot_raw = ranked[: self.hot_markets]
        hot_tickers = {str(m.get("ticker")) for m in hot_raw}

        metadata_due = (
            self._last_metadata_at is None
            or (cycle_started - self._last_metadata_at).total_seconds()
            >= self.metadata_interval_seconds
        )
        meta_changes = 0
        if metadata_due:
            for raw in raw_markets:
                try:
                    meta_changes += int(
                        self.store.record_market_meta(
                            raw,
                            received_at=market_received,
                            fee_schedule_id=self.fee_schedule_id,
                        )
                    )
                except Exception as exc:
                    self.store.quality_event(
                        "metadata_write_error",
                        market_ticker=str(raw.get("ticker") or "") or None,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
            self._last_metadata_at = cycle_started

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
            for raw in hot_raw[: self.snapshot_markets]:
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
            "metadata_changes": meta_changes,
            "full_snapshots": snapshots,
            "trades_inserted": trades_inserted,
            "point_in_time_audit": audit,
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
                self.store.quality_event(
                    "recorder_cycle_error",
                    detail={"error": f"{type(exc).__name__}: {exc}"},
                )
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
    recorder = RestRecorder(
        db_path=db_path or os.getenv(
            "AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"
        ),
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
        fee_schedule_id=fee_schedule_id or os.getenv(
            "AUTOTRADER_FEE_SCHEDULE_ID", "kalshi-predictions-current"
        ),
    )
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
    )
    recorder.run_forever()


if __name__ == "__main__":
    main()
