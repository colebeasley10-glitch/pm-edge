"""Process-wide pacing and 429 handling for PUBLIC, read-only Kalshi GETs.

Every Kalshi request in one process (Value Edge, every whale lane, the
research recorder) goes through ``shared_limiter()`` so independent threads
cannot burst together, and through ``get_with_retry()`` so a 429 is retried
with ``Retry-After`` / exponential backoff instead of failing the whole scan.

Pacing is OFF unless configured (``PM_EDGE_KALSHI_RATE_LIMIT_RPS`` > 0, or
``configure_shared_limiter`` called by a long-running worker), so short-lived
tools and CI keep their current behavior. 429 retries are always on and bounded.
This module performs no writes and never touches authenticated endpoints.
"""
from __future__ import annotations

import email.utils
import os
import random
import threading
import time
from datetime import datetime, timezone


class TokenBucket:
    """Thread-safe token bucket. ``rate`` tokens/second, ``burst`` capacity."""

    def __init__(self, rate: float, burst: float, *, clock=None, sleep=None):
        self.rate = float(rate)
        self.burst = max(1.0, float(burst))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._tokens = self.burst
        self._last = self._clock()
        self._lock = threading.Lock()
        self.waited_seconds = 0.0
        self.acquired = 0

    @property
    def enabled(self) -> bool:
        return self.rate > 0

    def acquire(self) -> float:
        """Block until one token is available. Returns seconds waited."""
        if not self.enabled:
            self.acquired += 1
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
                self._last = now
                # Tolerance: float refills can land at 0.9999999999999998, whose
                # "remaining wait" is below clock resolution and would never
                # advance time (livelock). Treat that as a full token.
                if self._tokens >= 1.0 - 1e-9:
                    self._tokens = max(0.0, self._tokens - 1.0)
                    self.acquired += 1
                    self.waited_seconds += waited
                    return waited
                need = max(1e-3, (1.0 - self._tokens) / self.rate)
            self._sleep(need)
            waited += need


_SHARED = None
_SHARED_LOCK = threading.Lock()


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def configure_shared_limiter(rate=None, burst=None) -> TokenBucket:
    """(Re)configure the process-wide limiter. Env vars take precedence."""
    global _SHARED
    rate = _env_float("PM_EDGE_KALSHI_RATE_LIMIT_RPS", 0 if rate is None else rate)
    burst = _env_float("PM_EDGE_KALSHI_RATE_LIMIT_BURST", (rate or 1) if burst is None else burst)
    with _SHARED_LOCK:
        _SHARED = TokenBucket(rate, burst)
        return _SHARED


def shared_limiter() -> TokenBucket:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            rate = _env_float("PM_EDGE_KALSHI_RATE_LIMIT_RPS", 0)
            burst = _env_float("PM_EDGE_KALSHI_RATE_LIMIT_BURST", rate or 1)
            _SHARED = TokenBucket(rate, burst)
        return _SHARED


class RetryStats:
    def __init__(self):
        self.retries = 0
        self.rate_limited = 0
        self.waited_seconds = 0.0


def retry_after_seconds(headers, *, now=None):
    """Parse Retry-After (delta-seconds or HTTP-date). None if absent/invalid."""
    if not headers:
        return None
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw in (None, ""):
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def backoff_seconds(attempt, *, base=1.0, cap=30.0, rand=None):
    """Exponential backoff with +/-25% jitter: ~1s, 2s, 4s ... capped."""
    rand = rand or random.random
    raw = min(float(cap), float(base) * (2 ** max(0, int(attempt))))
    return max(0.0, min(float(cap), raw * (0.75 + 0.5 * rand())))


def get_with_retry(do_get, *, limiter=None, max_retries=None, max_wait=None,
                   sleep=None, stats=None, rand=None):
    """Run ``do_get()`` (returns a requests-like Response) under the limiter.

    Retries ONLY HTTP 429, at most ``max_retries`` times, waiting Retry-After
    when given (bounded by ``max_wait``) else exponential backoff with jitter.
    Returns the final response; the caller still calls raise_for_status(), so
    an exhausted 429 surfaces exactly as before.
    """
    limiter = limiter or shared_limiter()
    sleep = sleep or time.sleep
    if max_retries is None:
        max_retries = int(_env_float("PM_EDGE_KALSHI_429_MAX_RETRIES", 3))
    if max_wait is None:
        max_wait = _env_float("PM_EDGE_KALSHI_429_MAX_WAIT_SECONDS", 30)
    attempt = 0
    while True:
        limiter.acquire()
        response = do_get()
        if getattr(response, "status_code", None) != 429:
            return response
        if stats is not None:
            stats.rate_limited += 1
        if attempt >= int(max_retries):
            return response
        wait = retry_after_seconds(getattr(response, "headers", None))
        if wait is None:
            wait = backoff_seconds(attempt, cap=max_wait, rand=rand)
        wait = min(float(max_wait), wait)
        if stats is not None:
            stats.retries += 1
            stats.waited_seconds += wait
        sleep(wait)
        attempt += 1
