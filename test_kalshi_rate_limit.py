"""PR A: process-wide Kalshi pacing, bounded 429 retries, partial tape on error."""
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from ledger_isolation import CommittedLedgerGuard, isolate_runtime_ledgers  # noqa: E402

isolate_runtime_ledgers()

import whale_edge  # noqa: E402
from autotrader import whale_paper_runner  # noqa: E402
from pmedge import rate_limit  # noqa: E402
from pmedge.kalshi import KalshiError, KalshiPublicClient  # noqa: E402

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
MIN_TS = int((NOW - timedelta(minutes=2)).timestamp())


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} Client Error: Too Many Requests")

    def json(self):
        return self._payload


def no_limit():
    return rate_limit.TokenBucket(0, 1)


class TokenBucketTests(unittest.TestCase):
    def test_disabled_never_waits(self):
        b = rate_limit.TokenBucket(0, 1)
        self.assertEqual(sum(b.acquire() for _ in range(50)), 0.0)

    def test_burst_then_steady_rate(self):
        clock = FakeClock()
        b = rate_limit.TokenBucket(5, 5, clock=clock, sleep=clock.sleep)
        for _ in range(5):
            self.assertEqual(b.acquire(), 0.0)  # burst
        for _ in range(10):
            b.acquire()
        self.assertAlmostEqual(clock.t, 2.0, places=6)  # 10 more at 5/s

    def test_thread_safe_no_overissue(self):
        b = rate_limit.TokenBucket(1000, 1000)
        threads = [threading.Thread(target=lambda: [b.acquire() for _ in range(100)]) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(b.acquired, 800)

    def test_env_overrides_worker_defaults(self):
        with patch.dict("os.environ", {"PM_EDGE_KALSHI_RATE_LIMIT_RPS": "2", "PM_EDGE_KALSHI_RATE_LIMIT_BURST": "3"}):
            b = rate_limit.configure_shared_limiter(rate=5, burst=5)
        self.assertEqual((b.rate, b.burst), (2.0, 3.0))
        rate_limit.configure_shared_limiter(rate=0, burst=1)  # reset for other tests


class RetryTests(unittest.TestCase):
    def run_seq(self, statuses, headers=None, max_retries=3):
        seq = [Resp(s, headers=(headers or {}) if s == 429 else {}) for s in statuses]
        calls = []
        sleeps = []
        stats = rate_limit.RetryStats()

        def do_get():
            calls.append(1)
            return seq[len(calls) - 1]
        r = rate_limit.get_with_retry(do_get, limiter=no_limit(), max_retries=max_retries, max_wait=30,
                                      sleep=sleeps.append, stats=stats, rand=lambda: 0.5)
        return r, calls, sleeps, stats

    def test_success_no_retry(self):
        r, calls, sleeps, _ = self.run_seq([200])
        self.assertEqual((r.status_code, len(calls), sleeps), (200, 1, []))

    def test_retry_after_honored(self):
        r, calls, sleeps, stats = self.run_seq([429, 200], headers={"Retry-After": "4"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sleeps, [4.0])
        self.assertEqual((stats.retries, stats.rate_limited), (1, 1))

    def test_exponential_backoff_without_header(self):
        _, _, sleeps, _ = self.run_seq([429, 429, 429, 200])
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])  # jitter midpoint

    def test_retry_after_capped_by_max_wait(self):
        _, _, sleeps, _ = self.run_seq([429, 200], headers={"Retry-After": "600"})
        self.assertEqual(sleeps, [30.0])

    def test_exhausted_returns_last_429(self):
        r, calls, sleeps, _ = self.run_seq([429, 429, 429, 429], max_retries=3)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(calls), 4)

    def test_non_429_errors_are_not_retried(self):
        r, calls, _, _ = self.run_seq([500])
        self.assertEqual((r.status_code, len(calls)), (500, 1))

    def test_http_date_retry_after(self):
        now = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
        hdr = {"Retry-After": "Thu, 01 Oct 2026 18:00:07 GMT"}
        self.assertAlmostEqual(rate_limit.retry_after_seconds(hdr, now=now), 7.0)
        self.assertIsNone(rate_limit.retry_after_seconds({"Retry-After": "soon"}))


class KalshiClientTests(unittest.TestCase):
    def test_client_retries_429_then_succeeds(self):
        client = KalshiPublicClient("https://example.invalid")
        seq = [Resp(429, headers={"Retry-After": "0"}), Resp(200, {"market": {"ticker": "X"}})]
        with patch.object(client.session, "get", side_effect=seq), patch("time.sleep"):
            self.assertEqual(client.get_market("X"), {"ticker": "X"})
        self.assertEqual(client.retry_stats.retries, 1)

    def test_client_exhausted_429_still_raises_kalshi_error_with_429(self):
        client = KalshiPublicClient("https://example.invalid")
        with patch.object(client.session, "get", return_value=Resp(429, headers={"Retry-After": "0"})), \
                patch("time.sleep"):
            with self.assertRaises(KalshiError) as ctx:
                client.get_market("X")
        self.assertIn("429", str(ctx.exception))  # lane backoff detection unchanged


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


class PartialTapeTests(unittest.TestCase):
    def pages(self, fail_on):
        calls = []

        def fetch(params):
            calls.append(params)
            n = len(calls)
            if n == fail_on:
                raise RuntimeError("Kalshi request failed: 429 Client Error: Too Many Requests")
            rows = [{"trade_id": f"p{n}-{i}", "created_time": iso(NOW - timedelta(seconds=n * 5 + i * 0.001))}
                    for i in range(1000)]
            return {"trades": rows, "cursor": str(n)}
        return fetch, calls

    def test_mid_pagination_429_keeps_pages_and_marks_truncated(self):
        fetch, _ = self.pages(fail_on=4)
        diag = {}
        rows = whale_edge._public_trades(min_ts=MIN_TS, fetch_page=fetch, diagnostics=diag, now=NOW,
                                         clock=lambda: NOW)
        self.assertEqual(len(rows), 3000)
        self.assertTrue(diag["trade_partial_after_error"])
        self.assertTrue(diag["trade_rate_limited"])
        self.assertFalse(diag["trade_horizon_covered"])
        self.assertFalse(diag["trade_page_cap_hit"])
        self.assertIn("429", diag["trade_fetch_error"])

    def test_first_page_failure_still_raises(self):
        fetch, _ = self.pages(fail_on=1)
        with self.assertRaises(RuntimeError):
            whale_edge._public_trades(min_ts=MIN_TS, fetch_page=fetch, now=NOW, clock=lambda: NOW)

    def test_partial_tape_does_not_put_lane_into_long_backoff(self):
        fetch, _ = self.pages(fail_on=3)

        class Client:
            def get_market(self, ticker):
                return {"ticker": ticker, "status": "open", "title": "t", "close_time": "2026-10-01T18:15:00Z"}

            def get_orderbook(self, ticker, depth=20):
                return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}

        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(whale_edge, "_fetch_trade_page", fetch), patch.object(whale_edge, "_now", lambda: NOW):
                run = whale_paper_runner.run_once(
                    client=Client(), state_path=root / "p.json", runner_state_path=root / "s.json",
                    runs_path=root / "r.jsonl", lookback_minutes=2, mode="quick_v2", now=NOW,
                )
        self.assertEqual(run["public_trades"], 2000)
        self.assertIsNone(run["api_error"])
        self.assertFalse(run["rate_limited"])
        self.assertTrue(run["trade_tape"]["trade_partial_after_error"])


class ProcessWideSharingTests(unittest.TestCase):
    def test_all_public_clients_share_one_limiter(self):
        limiter = rate_limit.configure_shared_limiter(rate=0, burst=1)
        a, b = KalshiPublicClient("https://x.invalid"), KalshiPublicClient("https://y.invalid")
        with patch.object(a.session, "get", return_value=Resp(200, {"market": {}})), \
                patch.object(b.session, "get", return_value=Resp(200, {"market": {}})):
            a.get_market("A")
            b.get_market("B")
        self.assertEqual(limiter.acquired, 2)

    def test_whale_tape_uses_shared_limiter(self):
        limiter = rate_limit.configure_shared_limiter(rate=0, burst=1)
        with patch("whale_edge.requests.get", return_value=Resp(200, {"trades": [], "cursor": ""})):
            whale_edge._fetch_trade_page({"limit": 1000, "min_ts": MIN_TS})
        self.assertEqual(limiter.acquired, 1)


if __name__ == "__main__":
    with CommittedLedgerGuard():
        unittest.main()
