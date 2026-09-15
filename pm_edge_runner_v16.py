"""PM Edge v16 launcher.

Keeps the proven v15 pricing/matching engine intact while making Kalshi
market discovery rate-limit friendly:
  * only fetch event families the engine can price exactly;
  * cap Kalshi event-fetch concurrency at four workers;
  * retry HTTP 429 / transient 5xx responses with bounded backoff.

This launcher never places real-money orders; watcher.py remains paper-alert only.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor as _ThreadPoolExecutor

import requests
import watcher

V16_STATS = {
    "prefiltered_event_fetches": 0,
    "kalshi_429_retries": 0,
    "kalshi_5xx_retries": 0,
}


def supported_full_game_event_ticker(event_ticker: str) -> bool:
    """Return True only for event families PM Edge can price exactly."""
    family = str(event_ticker or "").upper().split("-", 1)[0]
    if not family:
        return False

    allowed = ("GAME", "MATCH", "FIGHT", "SPREAD", "TEAMTOTAL", "TOTAL")
    if not any(token in family for token in allowed):
        return False

    # Partial-game derivatives that still contain SPREAD/TOTAL.
    blocked = (
        "INNING", "F3", "F5", "F7",
        "1H", "2H", "1Q", "2Q", "3Q", "4Q",
        "1P", "2P", "3P", "HALF", "QUARTER", "PERIOD",
        "TOTALTD",
    )
    return not any(token in family for token in blocked)


def kalshi_get_json_with_backoff(session: requests.Session, url: str, params=None) -> dict:
    """GET Kalshi JSON with polite bounded retry/backoff."""
    max_attempts = 4
    last_error = None

    for attempt in range(max_attempts):
        try:
            response = session.get(url, params=params or {}, timeout=25)

            if response.status_code == 429:
                V16_STATS["kalshi_429_retries"] += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after is not None else 0.75 * (2 ** attempt)
                except (TypeError, ValueError):
                    wait = 0.75 * (2 ** attempt)
                last_error = requests.HTTPError(
                    f"429 Client Error: Too Many Requests for url: {response.url}",
                    response=response,
                )
                if attempt < max_attempts - 1:
                    time.sleep(min(max(wait, 0.5), 6.0))
                    continue
                raise last_error

            if 500 <= response.status_code < 600:
                V16_STATS["kalshi_5xx_retries"] += 1
                if attempt < max_attempts - 1:
                    time.sleep(min(0.5 * (2 ** attempt), 4.0))
                    continue

            response.raise_for_status()
            return response.json()

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                time.sleep(min(0.5 * (2 ** attempt), 4.0))
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Kalshi request failed without a response")


class RateFriendlyExecutor(_ThreadPoolExecutor):
    """Limit Kalshi event fan-out and skip unsupported event families."""

    def __init__(self, max_workers=None, *args, **kwargs):
        requested = max_workers if max_workers is not None else 4
        super().__init__(max_workers=min(int(requested), 4), *args, **kwargs)

    def submit(self, fn, /, *args, **kwargs):
        # watcher.py only uses ThreadPoolExecutor for fetch_event(event_ticker).
        if args and isinstance(args[0], str) and not supported_full_game_event_ticker(args[0]):
            V16_STATS["prefiltered_event_fetches"] += 1
            future = Future()
            future.set_result((args[0], {}, []))
            return future
        return super().submit(fn, *args, **kwargs)


def install_v16_patch() -> None:
    # Preserve the proven v15 engine and patch only Kalshi request behavior.
    watcher.WATCHER_VERSION = 16
    watcher._kalshi_get_json = kalshi_get_json_with_backoff
    watcher.ThreadPoolExecutor = RateFriendlyExecutor

    # watcher.py's heartbeat title is a literal; rewrite only that title so the
    # notification accurately reports the active semantics version.
    original_send_push = watcher.send_push

    def send_push_v16(title, message, *args, **kwargs):
        if title == "✅ PM Edge Watch v15 is live":
            title = "✅ PM Edge Watch v16 is live"
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v16


def self_test() -> None:
    keep = [
        "KXMLBGAME-26SEP151840MILPIT",
        "KXMLBSPREAD-26SEP151840MILPIT",
        "KXMLBTOTAL-26SEP151840MILPIT",
        "KXMLBTEAMTOTAL-26SEP151840MILPIT",
        "KXNCAAFGAME-26SEP17SYRPITT",
        "KXNCAAFSPREAD-26SEP17SYRPITT",
        "KXNCAAFTOTAL-26SEP17SYRPITT",
        "KXUFCFIGHT-26SEP15WILMON",
        "KXNCAAWVMATCH-26SEP151900NOVAPSU",
    ]
    drop = [
        "KXMLBINNINGTOTAL-26SEP151840MILPIT-1",
        "KXMLBF5SPREAD-26SEP151840MILPIT",
        "KXNCAAF1HSPREAD-26SEP17SYRPITT",
        "KXNCAAF1QTOTAL-26SEP17SYRPITT",
        "KXNFLTOTALTD-26SEP17DETBUF",
        "KXNFLPASSYDS-26SEP17DETBUF",
        "KXUFCROUNDS-26SEP15WILMON",
    ]
    assert all(supported_full_game_event_ticker(x) for x in keep)
    assert all(not supported_full_game_event_ticker(x) for x in drop)
    print("PM Edge v16 launcher self-test: PASS")


def main() -> int:
    install_v16_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(V16_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
