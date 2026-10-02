"""Shared rolling Kalshi public trade tape for paper-only AutoTrader lanes.

One collector owns /markets/trades pagination. Whale Flow, Quick Whale lanes, and
the research recorder read the same in-process cache instead of independently
walking overlapping windows.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import whale_edge


VERSION = "shared_trade_tape_v1"


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dt(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return whale_edge._parse_iso(value)


class SharedTradeTape:
    def __init__(
        self,
        *,
        retention_minutes=22,
        page_cap=None,
        poll_seconds=None,
        overlap_seconds=2,
        fetch_page=None,
        clock=None,
    ):
        self.retention_seconds = max(120, int(float(retention_minutes) * 60))
        self.page_cap = max(
            1,
            int(page_cap if page_cap is not None else whale_edge.TRADE_PAGE_CAP),
        )
        self.poll_seconds = max(
            1.0,
            float(
                poll_seconds
                if poll_seconds is not None
                else os.environ.get("PM_EDGE_SHARED_TRADE_TAPE_POLL_SECONDS", "5")
            ),
        )
        self.overlap_seconds = max(1.0, float(overlap_seconds))
        self.fetch_page = fetch_page or whale_edge._fetch_trade_page
        self.clock = clock or _utcnow

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        # trade_id -> compact tuple:
        # (epoch, id, ticker, side, yes_price, no_price, count_fp, count,
        #  is_block_trade, created_time)
        self._rows = {}
        self._backfill_min_ts = None
        self._backfill_cursor = None
        self._backfill_complete = False
        self._coverage_floor_ts = None
        self._head_anchor = None
        self._head_healthy = True
        self._last_refresh_at = None
        self._last_success_at = None
        self._last_error = None
        self._last_rate_limited = False
        self._last_retries = 0
        self._pages_total = 0
        self._head_pages_total = 0
        self._backfill_pages_total = 0
        self._refreshes = 0

    @staticmethod
    def _compact(row):
        if not isinstance(row, dict):
            return None
        created = _dt(row.get("created_time"))
        if created is None:
            return None
        tid = str(row.get("trade_id") or "")
        ticker = str(row.get("ticker") or row.get("market_ticker") or "")
        if not tid or not ticker:
            return None
        side = str(row.get("taker_outcome_side") or row.get("taker_side") or "")
        return (
            created.timestamp(),
            tid,
            ticker,
            side,
            row.get("yes_price_dollars"),
            row.get("no_price_dollars"),
            row.get("count_fp"),
            row.get("count"),
            bool(row.get("is_block_trade")),
            _iso(created),
        )

    @staticmethod
    def _expand(item):
        return {
            "trade_id": item[1],
            "ticker": item[2],
            "taker_outcome_side": item[3],
            "yes_price_dollars": item[4],
            "no_price_dollars": item[5],
            "count_fp": item[6],
            "count": item[7],
            "is_block_trade": item[8],
            "created_time": item[9],
        }

    def _fetch_chunk(self, *, min_ts, cursor=None):
        rows = []
        pages = 0
        current = cursor
        exhausted = False
        partial_error = None
        stats = whale_edge._trade_retry_stats()
        retries_before = stats.retries
        limited_before = stats.rate_limited

        while pages < self.page_cap:
            params = {"limit": 1000, "min_ts": int(min_ts)}
            if current:
                params["cursor"] = current
            try:
                payload = self.fetch_page(params)
            except Exception as exc:
                partial_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                break
            pages += 1
            payload = payload if isinstance(payload, dict) else {}
            batch = payload.get("trades") or []
            batch = batch if isinstance(batch, list) else []
            rows.extend(batch)
            current = payload.get("cursor")
            if not batch or not current:
                exhausted = True
                current = None
                break

        return {
            "rows": rows,
            "pages": pages,
            "next_cursor": current,
            "exhausted": exhausted,
            "partial_error": partial_error,
            "rate_limited": bool(
                (partial_error and "429" in partial_error)
                or stats.rate_limited > limited_before
            ),
            "retries": stats.retries - retries_before,
        }

    def _merge(self, rows):
        oldest = None
        newest = None
        inserted = 0
        with self._lock:
            for row in rows:
                item = self._compact(row)
                if item is None:
                    continue
                epoch = item[0]
                if oldest is None or epoch < oldest:
                    oldest = epoch
                if newest is None or epoch > newest:
                    newest = epoch
                if item[1] not in self._rows:
                    inserted += 1
                self._rows[item[1]] = item
        return inserted, oldest, newest

    def _prune(self, now_epoch):
        cutoff = now_epoch - self.retention_seconds
        with self._lock:
            stale = [tid for tid, item in self._rows.items() if item[0] < cutoff]
            for tid in stale:
                self._rows.pop(tid, None)
            if self._backfill_complete:
                self._coverage_floor_ts = max(
                    cutoff,
                    float(self._coverage_floor_ts or cutoff),
                )
        return len(stale)

    def refresh(self, *, now=None):
        cycle_started = (now or self.clock()).astimezone(timezone.utc)
        started_epoch = cycle_started.timestamp()
        with self._lock:
            first = self._backfill_min_ts is None
            if first:
                self._backfill_min_ts = int(started_epoch - self.retention_seconds)
                backfill_min_ts = self._backfill_min_ts
                backfill_cursor = None
                head_anchor = None
            else:
                backfill_min_ts = int(self._backfill_min_ts)
                backfill_cursor = self._backfill_cursor
                head_anchor = self._head_anchor
            backfill_complete = self._backfill_complete

        total_pages = 0
        total_retries = 0
        rate_limited = False
        last_error = None

        # Once the first newest-first chunk has established an anchor, keep the
        # head current while older pages are being backfilled.
        if not first and head_anchor is not None:
            head_min_ts = max(
                backfill_min_ts,
                int(head_anchor.timestamp() - self.overlap_seconds),
            )
            head = self._fetch_chunk(min_ts=head_min_ts, cursor=None)
            total_pages += head["pages"]
            total_retries += head["retries"]
            rate_limited = rate_limited or head["rate_limited"]
            self._merge(head["rows"])
            head_ok = bool(head["exhausted"] and head["partial_error"] is None)
            if head["partial_error"]:
                last_error = head["partial_error"]
            with self._lock:
                self._head_pages_total += head["pages"]
                self._head_healthy = head_ok
                if head_ok:
                    # Anchor the next incremental query to the START of this
                    # successful refresh so rows arriving during pagination are
                    # covered by the overlap on the next cycle.
                    self._head_anchor = cycle_started

        # Backfill is cursor-resumable. Each cycle spends at most page_cap pages,
        # so the configured cap remains a per-batch safety bound rather than a
        # permanent horizon limit.
        if not backfill_complete:
            back = self._fetch_chunk(
                min_ts=backfill_min_ts,
                cursor=backfill_cursor,
            )
            total_pages += back["pages"]
            total_retries += back["retries"]
            rate_limited = rate_limited or back["rate_limited"]
            _, oldest, _ = self._merge(back["rows"])
            if back["partial_error"]:
                last_error = back["partial_error"]
            with self._lock:
                self._backfill_pages_total += back["pages"]
                if oldest is not None:
                    self._coverage_floor_ts = (
                        oldest
                        if self._coverage_floor_ts is None
                        else min(self._coverage_floor_ts, oldest)
                    )
                # Only advance the cursor after successfully consuming pages.
                # On a partial error, next_cursor is the failed page's cursor
                # and will be retried on the next refresh.
                self._backfill_cursor = back["next_cursor"]
                if back["exhausted"] and back["partial_error"] is None:
                    self._backfill_complete = True
                    self._backfill_cursor = None
                    self._coverage_floor_ts = float(backfill_min_ts)
                if first:
                    self._head_anchor = cycle_started
                    self._head_healthy = back["partial_error"] is None

        pruned = self._prune(started_epoch)
        with self._lock:
            self._refreshes += 1
            self._pages_total += total_pages
            self._last_refresh_at = cycle_started
            self._last_rate_limited = rate_limited
            self._last_retries = total_retries
            if last_error:
                self._last_error = last_error
            else:
                self._last_error = None
                if self._head_healthy:
                    self._last_success_at = cycle_started
            return {
                **self.status(now=cycle_started),
                "refresh_pages": total_pages,
                "refresh_retries": total_retries,
                "refresh_rate_limited": rate_limited,
                "pruned_rows": pruned,
            }

    def snapshot(self, *, min_ts, now=None):
        current = (now or self.clock()).astimezone(timezone.utc)
        now_epoch = current.timestamp()
        min_ts = int(min_ts)
        cutoff = now_epoch - self.retention_seconds
        with self._lock:
            items = [
                item
                for item in self._rows.values()
                if item[0] >= min_ts and item[0] <= now_epoch + 5
            ]
            items.sort(key=lambda item: item[0], reverse=True)
            floor = self._coverage_floor_ts
            head_healthy = self._head_healthy
            backfill_complete = self._backfill_complete
            last_error = self._last_error
            last_rate_limited = self._last_rate_limited
            last_retries = self._last_retries
            collector_rows = len(self._rows)
            refreshes = self._refreshes
            pages_total = self._pages_total
            oldest_all = min((x[0] for x in self._rows.values()), default=None)
            newest_all = max((x[0] for x in self._rows.values()), default=None)

        effective_floor = None if floor is None else max(float(floor), cutoff)
        covered = bool(
            head_healthy
            and effective_floor is not None
            and min_ts >= effective_floor - 1
        )
        oldest_in = min((x[0] for x in items), default=None)
        newest_in = max((x[0] for x in items), default=None)
        lookback = max(0.0, now_epoch - min_ts)
        if covered:
            fraction = 1.0
        elif oldest_in is not None and lookback > 0:
            fraction = max(0.0, min(1.0, (now_epoch - oldest_in) / lookback))
        else:
            fraction = 0.0

        diagnostics = {
            "trade_tape_version": VERSION,
            "trade_source": "shared_collector",
            "trade_pages_fetched": 0,
            "trade_rows_fetched": len(items),
            "trade_rows_deduped": 0,
            "trade_rows_in_window": len(items),
            "trade_rows_outside_window": 0,
            "trade_rows_missing_ts": 0,
            "trade_oldest_ts": (
                _iso(datetime.fromtimestamp(oldest_all, tz=timezone.utc))
                if oldest_all is not None else None
            ),
            "trade_oldest_in_window_ts": (
                _iso(datetime.fromtimestamp(oldest_in, tz=timezone.utc))
                if oldest_in is not None else None
            ),
            "trade_newest_ts": (
                _iso(datetime.fromtimestamp(newest_in, tz=timezone.utc))
                if newest_in is not None else None
            ),
            "trade_page_cap_hit": False,
            "trade_page_cap": self.page_cap,
            "trade_horizon_covered": covered,
            "trade_window_coverage_fraction": round(fraction, 4),
            "trade_requested_min_ts": _iso(
                datetime.fromtimestamp(min_ts, tz=timezone.utc)
            ),
            "trade_requested_lookback_seconds": round(lookback, 3),
            "trade_page_order": "newest_first",
            "trade_fetch_seconds": 0.0,
            "trade_partial_after_error": bool(last_error and not head_healthy),
            "trade_fetch_error": last_error,
            "trade_rate_limited": bool(last_rate_limited),
            "trade_rate_limit_retries": int(last_retries),
            "shared_collector_ready": covered,
            "shared_collector_backfill_complete": backfill_complete,
            "shared_collector_rows": collector_rows,
            "shared_collector_refreshes": refreshes,
            "shared_collector_pages_total": pages_total,
            "shared_collector_coverage_floor_ts": (
                _iso(datetime.fromtimestamp(effective_floor, tz=timezone.utc))
                if effective_floor is not None else None
            ),
        }
        return [self._expand(item) for item in items], diagnostics

    def status(self, *, now=None):
        current = (now or self.clock()).astimezone(timezone.utc)
        cutoff = current.timestamp() - self.retention_seconds
        with self._lock:
            floor = self._coverage_floor_ts
            effective_floor = None if floor is None else max(float(floor), cutoff)
            oldest = min((x[0] for x in self._rows.values()), default=None)
            newest = max((x[0] for x in self._rows.values()), default=None)
            return {
                "version": VERSION,
                "running": bool(self._thread and self._thread.is_alive()),
                "retention_seconds": self.retention_seconds,
                "page_cap_per_batch": self.page_cap,
                "poll_seconds": self.poll_seconds,
                "rows": len(self._rows),
                "backfill_complete": self._backfill_complete,
                "head_healthy": self._head_healthy,
                "coverage_floor_ts": (
                    _iso(datetime.fromtimestamp(effective_floor, tz=timezone.utc))
                    if effective_floor is not None else None
                ),
                "oldest_ts": (
                    _iso(datetime.fromtimestamp(oldest, tz=timezone.utc))
                    if oldest is not None else None
                ),
                "newest_ts": (
                    _iso(datetime.fromtimestamp(newest, tz=timezone.utc))
                    if newest is not None else None
                ),
                "last_refresh_at": _iso(self._last_refresh_at)
                if self._last_refresh_at is not None else None,
                "last_success_at": _iso(self._last_success_at)
                if self._last_success_at is not None else None,
                "last_error": self._last_error,
                "last_rate_limited": self._last_rate_limited,
                "last_retries": self._last_retries,
                "refreshes": self._refreshes,
                "pages_total": self._pages_total,
                "head_pages_total": self._head_pages_total,
                "backfill_pages_total": self._backfill_pages_total,
            }

    def run_forever(self):
        print(
            "SHARED_TRADE_TAPE "
            + __import__("json").dumps({
                "state": "starting",
                "version": VERSION,
                "retention_seconds": self.retention_seconds,
                "page_cap_per_batch": self.page_cap,
                "poll_seconds": self.poll_seconds,
            }),
            flush=True,
        )
        while not self._stop.is_set():
            try:
                result = self.refresh()
                print(
                    "SHARED_TRADE_TAPE "
                    + __import__("json").dumps({
                        "state": "ready" if result.get("backfill_complete") else "warming",
                        "rows": result.get("rows"),
                        "backfill_complete": result.get("backfill_complete"),
                        "head_healthy": result.get("head_healthy"),
                        "coverage_floor_ts": result.get("coverage_floor_ts"),
                        "refresh_pages": result.get("refresh_pages"),
                        "refresh_retries": result.get("refresh_retries"),
                        "refresh_rate_limited": result.get("refresh_rate_limited"),
                        "last_error": result.get("last_error"),
                    }),
                    flush=True,
                )
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:240]}"
                    self._head_healthy = False
                print(
                    "SHARED_TRADE_TAPE "
                    + __import__("json").dumps({
                        "state": "error",
                        "error": type(exc).__name__,
                        "message": str(exc)[:240],
                    }),
                    flush=True,
                )
            with self._lock:
                warming = not self._backfill_complete
            self._stop.wait(timeout=1.0 if warming else self.poll_seconds)

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._thread
            self._stop.clear()
            self._thread = threading.Thread(
                target=self.run_forever,
                name="shared-kalshi-trade-tape",
                daemon=True,
            )
            self._thread.start()
            return self._thread

    def stop(self):
        self._stop.set()
