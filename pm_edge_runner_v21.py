"""PM Edge v21 launcher.

Builds on v20 and makes overnight/overseas opportunities useful to an Eastern
Time owner who is normally asleep overnight.

Behavior:
* During the pre-bed window, events scheduled to start during quiet hours get a
  large refresh-priority bonus so their sportsbook fair values are refreshed
  before late U.S. games can consume the limited Odds API budget.
* Part of the existing daily Odds API allowance is reserved until the pre-bed
  window, then unlocked. The hard daily credit ceiling is not increased.
* Pre-bed runs may refresh a few more event groups, but PM Edge's pricing/edge
  thresholds and the normal hard daily Odds API budget remain unchanged.
* During quiet hours, new actionable BET / PRICE FISH pushes are deferred.
  If the event will still be pregame after wake-up, the alert remains NEW and
  can fire after wake-up if it still qualifies.
* If a genuinely new opportunity appears while the owner is asleep and its game
  starts before wake-up, it is logged as a missed overnight opportunity rather
  than sending a useless middle-of-the-night actionable push.
* A low-priority morning digest summarizes those missed opportunities for model
  tracking. It explicitly says not to chase after the event has started.
* PASS / CANCEL safety alerts are not suppressed. If an already-resting order
  stops qualifying overnight, PM Edge can still tell the owner to cancel it.

This launcher is alert-only. It never places a real-money order.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import watcher
import pm_edge_runner_v20 as v20

LOCAL_TIMEZONE_NAME = os.getenv("PM_EDGE_LOCAL_TIMEZONE", "America/New_York").strip() or "America/New_York"
try:
    LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)
except Exception:
    LOCAL_TIMEZONE = timezone.utc
    LOCAL_TIMEZONE_NAME = "UTC"

PREBED_START_HOUR = float(os.getenv("PM_EDGE_PREBED_START_HOUR", "20"))
SLEEP_START_HOUR = float(os.getenv("PM_EDGE_SLEEP_START_HOUR", "23"))
WAKE_HOUR = float(os.getenv("PM_EDGE_WAKE_HOUR", "7"))
PREBED_LOOKAHEAD_HOURS = float(os.getenv("PM_EDGE_PREBED_LOOKAHEAD_HOURS", "14"))
PREBED_PRIORITY_BONUS = float(os.getenv("PM_EDGE_PREBED_PRIORITY_BONUS", "100"))
MORNING_PRIORITY_BONUS = float(os.getenv("PM_EDGE_MORNING_PRIORITY_BONUS", "35"))
PREBED_FAIR_TTL_HOURS = float(os.getenv("PM_EDGE_PREBED_FAIR_TTL_HOURS", "1.5"))
MORNING_FAIR_TTL_HOURS = float(os.getenv("PM_EDGE_MORNING_FAIR_TTL_HOURS", "2.0"))
PREBED_MAX_REFRESH_GROUPS = int(os.getenv("PM_EDGE_PREBED_MAX_ODDS_REFRESH_PER_RUN", "5"))
PREBED_RESERVED_ODDS_CREDITS = int(os.getenv("PM_EDGE_PREBED_RESERVED_ODDS_CREDITS", "4"))
MORNING_RESERVED_ODDS_CREDITS = int(os.getenv("PM_EDGE_MORNING_RESERVED_ODDS_CREDITS", "2"))
POST_WAKE_REVALIDATION_HOURS = float(os.getenv("PM_EDGE_POST_WAKE_REVALIDATION_HOURS", "2"))
POST_WAKE_PRIORITY_BONUS = float(os.getenv("PM_EDGE_POST_WAKE_PRIORITY_BONUS", "250"))
POST_WAKE_MAX_REFRESH_GROUPS = int(os.getenv("PM_EDGE_POST_WAKE_MAX_ODDS_REFRESH_PER_RUN", "5"))
MORNING_LOOKAHEAD_AFTER_WAKE_HOURS = float(
    os.getenv("PM_EDGE_MORNING_LOOKAHEAD_AFTER_WAKE_HOURS", "4")
)
OVERNIGHT_MISSED_RETENTION_DAYS = int(os.getenv("PM_EDGE_OVERNIGHT_MISSED_RETENTION_DAYS", "14"))

V21_STATS = {
    "prebed_priority_boosts": 0,
    "morning_priority_boosts": 0,
    "post_wake_priority_boosts": 0,
    "quiet_alerts_deferred": 0,
    "quiet_revalidation_queued": 0,
    "missed_overnight_queued": 0,
    "overnight_digests_sent": 0,
    "odds_paced_budget": 0,
    "v20_state_migrations": 0,
}
_DEFERRED_TICKERS: set[str] = set()


def _safe_float(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


def _hour_value(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


def _hour_in_window(hour: float, start: float, end: float) -> bool:
    """Return True for a half-open clock window, including midnight wrap."""
    hour = float(hour) % 24.0
    start = float(start) % 24.0
    end = float(end) % 24.0
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _as_utc(dt: datetime | None = None) -> datetime:
    dt = dt or watcher.now_utc()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_now(dt: datetime | None = None) -> datetime:
    return _as_utc(dt).astimezone(LOCAL_TIMEZONE)


def is_prebed(dt: datetime | None = None) -> bool:
    return _hour_in_window(_hour_value(local_now(dt)), PREBED_START_HOUR, SLEEP_START_HOUR)


def is_quiet(dt: datetime | None = None) -> bool:
    return _hour_in_window(_hour_value(local_now(dt)), SLEEP_START_HOUR, WAKE_HOUR)


def is_post_wake_revalidation(dt: datetime | None = None) -> bool:
    end = (WAKE_HOUR + max(0.25, POST_WAKE_REVALIDATION_HOURS)) % 24.0
    return _hour_in_window(_hour_value(local_now(dt)), WAKE_HOUR, end)


def overnight_aware_odds_budget(now: datetime | None = None) -> int:
    """Reserve both pre-bed capacity and a small post-wake revalidation pool."""
    v16 = v20.v19.v18.v17.v16
    hard = max(0, int(v16.HARD_DAILY_ODDS_BUDGET))
    local = local_now(now)
    hour = _hour_value(local)
    base = max(0, int(v16.paced_odds_budget(local)))
    prebed_reserve = min(hard, max(0, int(PREBED_RESERVED_ODDS_CREDITS)))
    morning_reserve = min(hard, max(0, int(MORNING_RESERVED_ODDS_CREDITS)))

    if is_prebed(now):
        return hard

    # After midnight but before wake-up, cap consumption so a known amount of
    # the budget unlocked at wake remains available to revalidate deferred bets.
    if is_quiet(now) and hour < WAKE_HOUR:
        wake_local = local.replace(
            hour=int(WAKE_HOUR) % 24,
            minute=int((WAKE_HOUR % 1) * 60),
            second=0,
            microsecond=0,
        )
        wake_budget = max(0, int(v16.paced_odds_budget(wake_local)))
        quiet_cap = max(0, wake_budget - morning_reserve)
        return min(base, quiet_cap, max(0, hard - prebed_reserve))

    # Before the pre-bed look-ahead window, preserve the evening reserve.
    if hour < PREBED_START_HOUR:
        return min(base, max(0, hard - prebed_reserve))
    return min(base, hard)

def _event_start_utc(event_or_fair: dict | None) -> datetime | None:
    d = watcher.parse_dt((event_or_fair or {}).get("commence_time"))
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _event_start_local(event_or_fair: dict | None) -> datetime | None:
    d = _event_start_utc(event_or_fair)
    return d.astimezone(LOCAL_TIMEZONE) if d is not None else None


def _hours_until_event(event_or_fair: dict | None, now: datetime | None = None) -> float | None:
    start = _event_start_utc(event_or_fair)
    if start is None:
        return None
    return (start - _as_utc(now)).total_seconds() / 3600.0


def event_starts_during_quiet(event_or_fair: dict | None) -> bool:
    start = _event_start_local(event_or_fair)
    if start is None:
        return False
    return _hour_in_window(_hour_value(start), SLEEP_START_HOUR, WAKE_HOUR)


def _wake_boundary_local(now: datetime | None = None) -> datetime:
    ln = local_now(now)
    wake_hour_int = int(math.floor(WAKE_HOUR)) % 24
    wake_minute = int(round((WAKE_HOUR - math.floor(WAKE_HOUR)) * 60.0))
    if wake_minute >= 60:
        wake_hour_int = (wake_hour_int + 1) % 24
        wake_minute = 0

    candidate = ln.replace(hour=wake_hour_int, minute=wake_minute, second=0, microsecond=0)
    if candidate <= ln:
        candidate += timedelta(days=1)
    return candidate


def prebed_priority_bonus(event: dict, now: datetime | None = None) -> float:
    """Large bonus for games that will start while the owner is asleep."""
    if not is_prebed(now):
        return 0.0
    h = _hours_until_event(event, now)
    if h is None or h < 0.0 or h > PREBED_LOOKAHEAD_HOURS:
        return 0.0
    if event_starts_during_quiet(event):
        return PREBED_PRIORITY_BONUS
    return 0.0


def morning_priority_bonus(event: dict, now: datetime | None = None) -> float:
    """While asleep, keep some attention on games starting shortly after wake."""
    if not is_quiet(now):
        return 0.0
    start = _event_start_local(event)
    if start is None:
        return 0.0
    wake = _wake_boundary_local(now)
    if wake <= start <= wake + timedelta(hours=MORNING_LOOKAHEAD_AFTER_WAKE_HOURS):
        return MORNING_PRIORITY_BONUS
    return 0.0


def _queue_missed_overnight(state: dict, market: dict, fair_entry: dict, q, tier: str, reason: str) -> None:
    key = f"{market.get('ticker','')}|{getattr(q, 'side', '')}|{tier}"
    bucket = state.setdefault("overnight_missed", {})
    prev = bucket.get(key) or {}
    first_seen = prev.get("first_seen_at") or watcher.iso_now()
    bucket[key] = {
        "ticker": market.get("ticker"),
        "title": market.get("title"),
        "sport_title": fair_entry.get("sport_title") or "Sports",
        "side": getattr(q, "side", None),
        "tier": tier,
        "reason": reason,
        "recommended_limit": getattr(q, "recommended_limit", None),
        "hard_max": getattr(q, "hard_max", None),
        "net_edge": getattr(q, "net_edge", None),
        "fair_yes_prob": fair_entry.get("fair_yes_prob"),
        "commence_time": fair_entry.get("commence_time"),
        "first_seen_at": first_seen,
        "last_seen_at": watcher.iso_now(),
        "recap_sent_at": prev.get("recap_sent_at"),
    }
    if not prev:
        V21_STATS["missed_overnight_queued"] += 1


def _prune_missed(state: dict) -> None:
    cutoff = watcher.now_utc() - timedelta(days=max(1, OVERNIGHT_MISSED_RETENTION_DAYS))
    kept = {}
    for key, row in (state.get("overnight_missed") or {}).items():
        seen = watcher.parse_dt((row or {}).get("last_seen_at") or (row or {}).get("first_seen_at"))
        if seen is None or seen >= cutoff:
            kept[key] = row
    state["overnight_missed"] = kept


def _format_local_start(value) -> str:
    d = watcher.parse_dt(value)
    if d is None:
        return "time unknown"
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    ld = d.astimezone(LOCAL_TIMEZONE)
    hour = ld.strftime("%I").lstrip("0") or "0"
    return f"{hour}:{ld.strftime('%M %p')} ET"


def maybe_send_overnight_digest(state: dict) -> bool:
    """Send one low-priority recap in the first two hours after wake."""
    ln = local_now()
    hour = _hour_value(ln)
    digest_end = (WAKE_HOUR + 2.0) % 24.0
    if not _hour_in_window(hour, WAKE_HOUR, digest_end):
        return False

    local_date = ln.date().isoformat()
    if state.get("overnight_digest_date") == local_date:
        return False

    rows = []
    for key, row in (state.get("overnight_missed") or {}).items():
        if (row or {}).get("recap_sent_at"):
            continue
        seen = watcher.parse_dt((row or {}).get("last_seen_at") or (row or {}).get("first_seen_at"))
        if seen is None:
            continue
        age_h = (watcher.now_utc() - seen).total_seconds() / 3600.0
        if age_h < -0.1 or age_h > 16.0:
            continue
        rows.append((key, row))

    if not rows:
        return False

    rows.sort(key=lambda item: _safe_float((item[1] or {}).get("net_edge"), 0.0), reverse=True)
    lines = [
        f"Missed while quiet hours were on: {len(rows)}.",
        "Logged for tracking only — do not chase an event that has already started.",
        "",
    ]
    for _, row in rows[:5]:
        side = str(row.get("side") or "?").upper()
        price = _safe_float(row.get("recommended_limit"))
        edge = _safe_float(row.get("net_edge"))
        price_text = f"{price * 100:.0f}¢" if price is not None else "—"
        edge_text = f"+{edge * 100:.1f}%" if edge is not None else "—"
        start_text = _format_local_start(row.get("commence_time"))
        title = str(row.get("title") or row.get("ticker") or "market")
        lines.append(f"{side} {price_text} | {edge_text} | {start_text} | {title[:70]}")
    if len(rows) > 5:
        lines.append(f"+{len(rows) - 5} more logged")

    if watcher.send_push("🌙 PM Edge overnight DIGEST", "\n".join(lines), priority=-1):
        stamp = watcher.iso_now()
        for key, _ in rows:
            state["overnight_missed"][key]["recap_sent_at"] = stamp
        state["overnight_digest_date"] = local_date
        V21_STATS["overnight_digests_sent"] += 1
        return True
    return False


def install_v21_patch() -> None:
    """Install scheduling/defer behavior after all v16-v20 patches."""
    watcher.WATCHER_VERSION = 21

    # Preserve v20 alert/fair-value state because v21 changes scheduling only,
    # not market semantics. Older states still go through watcher's normal purge.
    original_load_state = watcher.load_state

    def load_state_v21():
        global _DEFERRED_TICKERS
        state = original_load_state()
        if int(state.get("data_semantics_version", 0) or 0) == 20:
            state["data_semantics_version"] = 21
            V21_STATS["v20_state_migrations"] += 1
        state.setdefault("overnight_missed", {})

        cutoff = watcher.now_utc() - timedelta(hours=24)
        kept = {}
        for ticker, row in (state.setdefault("overnight_deferred", {}) or {}).items():
            seen = watcher.parse_dt((row or {}).get("last_seen_at"))
            if seen is not None and seen >= cutoff:
                kept[str(ticker)] = row
        state["overnight_deferred"] = kept
        _DEFERRED_TICKERS = set(kept)
        return state

    watcher.load_state = load_state_v21

    original_priority_score = watcher.priority_score

    def priority_score_v21(market, event, prev_quote, fair_entry):
        score = original_priority_score(market, event, prev_quote, fair_entry)
        prebed_bonus = prebed_priority_bonus(event)
        morning_bonus = morning_priority_bonus(event)
        if prebed_bonus > 0:
            V21_STATS["prebed_priority_boosts"] += 1
            score += prebed_bonus
        if morning_bonus > 0:
            V21_STATS["morning_priority_boosts"] += 1
            score += morning_bonus
        if is_post_wake_revalidation() and str(market.get("ticker") or "") in _DEFERRED_TICKERS:
            V21_STATS["post_wake_priority_boosts"] += 1
            score += POST_WAKE_PRIORITY_BONUS
        return score

    watcher.priority_score = priority_score_v21

    original_fair_ttl_hours = watcher.fair_ttl_hours

    def fair_ttl_hours_v21(event):
        ttl = original_fair_ttl_hours(event)
        if prebed_priority_bonus(event) > 0:
            return min(ttl, max(0.25, PREBED_FAIR_TTL_HOURS))
        if morning_priority_bonus(event) > 0:
            return min(ttl, max(0.25, MORNING_FAIR_TTL_HOURS))
        return ttl

    watcher.fair_ttl_hours = fair_ttl_hours_v21

    # v16 already established the hard daily ceiling and Eastern-day reset.
    # v21 only changes how much of that same ceiling is unlocked right now.
    watcher.DAILY_ODDS_BUDGET = overnight_aware_odds_budget()
    V21_STATS["odds_paced_budget"] = watcher.DAILY_ODDS_BUDGET

    # Give the pre-bed pass a little more breadth. The daily Odds API ceiling
    # is NOT raised, so this only changes when the existing budget is spent.
    if is_prebed():
        watcher.MAX_REFRESH_GROUPS = max(watcher.MAX_REFRESH_GROUPS, PREBED_MAX_REFRESH_GROUPS)
    if is_post_wake_revalidation():
        watcher.MAX_REFRESH_GROUPS = max(watcher.MAX_REFRESH_GROUPS, POST_WAKE_MAX_REFRESH_GROUPS)

    original_should_alert = watcher.should_alert

    def should_alert_v21(state, market, fair_entry, q, tier="primary"):
        ok, reason = original_should_alert(state, market, fair_entry, q, tier=tier)
        if not ok:
            return False, ""

        if not is_quiet():
            ticker = str(market.get("ticker") or "")
            state.setdefault("overnight_deferred", {}).pop(ticker, None)
            _DEFERRED_TICKERS.discard(ticker)
            return ok, reason

        # Actionable pushes are deferred during sleep. PASS/CANCEL messages do
        # not use should_alert and remain available as a safety override.
        V21_STATS["quiet_alerts_deferred"] += 1
        start_local = _event_start_local(fair_entry)
        wake_local = _wake_boundary_local()

        # Persist genuinely new/back opportunities so the first post-wake scan
        # prioritizes revalidation before ordinary daytime candidates.
        if reason in {"NEW", "BACK"}:
            ticker = str(market.get("ticker") or "")
            if ticker:
                state.setdefault("overnight_deferred", {})[ticker] = {
                    "last_seen_at": watcher.iso_now(),
                    "commence_time": fair_entry.get("commence_time"),
                    "side": str(getattr(q, "side", "") or "").upper(),
                    "tier": tier,
                }
                _DEFERRED_TICKERS.add(ticker)
                V21_STATS["quiet_revalidation_queued"] += 1

        # Only genuinely new/back opportunities count as missed. PRICE/EDGE
        # updates and REMINDERs refer to an opportunity the owner already saw.
        if (
            reason in {"NEW", "BACK"}
            and start_local is not None
            and start_local <= wake_local
        ):
            _queue_missed_overnight(state, market, fair_entry, q, tier, reason)

        # Do not record the ordinary alert. If the event starts after wake-up,
        # it therefore remains NEW and can notify then if it still qualifies.
        return False, ""

    watcher.should_alert = should_alert_v21

    original_save_state = watcher.save_state

    def save_state_v21(state):
        _prune_missed(state)
        maybe_send_overnight_digest(state)
        return original_save_state(state)

    watcher.save_state = save_state_v21

    original_send_push = watcher.send_push

    def send_push_v21(title, message, *args, **kwargs):
        if title == "✅ PM Edge Watch v20 Auto-Sized is live":
            title = "✅ PM Edge Watch v21 Overnight Look-Ahead is live"
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v21


def self_test() -> None:
    # Eastern daylight-time fixtures for September.
    prebed_utc = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)  # 9:00 PM ET Sep 17
    overnight_event = {"commence_time": "2026-09-18T09:00:00+00:00"}  # 5:00 AM ET
    daytime_event = {"commence_time": "2026-09-18T19:00:00+00:00"}  # 3:00 PM ET

    assert is_prebed(prebed_utc) is True
    assert is_quiet(prebed_utc) is False
    assert event_starts_during_quiet(overnight_event) is True
    assert prebed_priority_bonus(overnight_event, prebed_utc) == PREBED_PRIORITY_BONUS
    assert prebed_priority_bonus(daytime_event, prebed_utc) == 0.0

    quiet_utc = datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)  # 2:00 AM ET
    morning_event = {"commence_time": "2026-09-18T13:00:00+00:00"}  # 9:00 AM ET
    assert is_quiet(quiet_utc) is True
    assert morning_priority_bonus(morning_event, quiet_utc) == MORNING_PRIORITY_BONUS

    post_wake_utc = datetime(2026, 9, 18, 11, 30, tzinfo=timezone.utc)  # 7:30 AM ET
    assert is_post_wake_revalidation(post_wake_utc) is True
    assert overnight_aware_odds_budget(quiet_utc) <= overnight_aware_odds_budget(post_wake_utc)
    assert (
        overnight_aware_odds_budget(post_wake_utc) - overnight_aware_odds_budget(quiet_utc)
        >= min(MORNING_RESERVED_ODDS_CREDITS, 2)
    )

    # Existing 14-credit hard budget stays intact, but the configured reserve
    # cannot be spent before pre-bed and is fully unlocked during pre-bed.
    hard = max(0, int(v20.v19.v18.v17.v16.HARD_DAILY_ODDS_BUDGET))
    before_prebed = datetime(2026, 9, 17, 19, 0, tzinfo=LOCAL_TIMEZONE)
    at_prebed = datetime(2026, 9, 17, 21, 0, tzinfo=LOCAL_TIMEZONE)
    assert overnight_aware_odds_budget(before_prebed) <= max(0, hard - min(hard, PREBED_RESERVED_ODDS_CREDITS))
    assert overnight_aware_odds_budget(at_prebed) == hard

    # Missed queue is deduplicated by ticker/side/tier and preserves first seen.
    class FakeQ:
        side = "YES"
        recommended_limit = 0.42
        hard_max = 0.42
        net_edge = 0.061

    state = {}
    market = {"ticker": "TEST-OVERNIGHT", "title": "Overseas match winner"}
    fair = {
        "sport_title": "Tennis",
        "fair_yes_prob": 0.50,
        "commence_time": overnight_event["commence_time"],
    }
    _queue_missed_overnight(state, market, fair, FakeQ(), "primary", "NEW")
    first = state["overnight_missed"]["TEST-OVERNIGHT|YES|primary"]["first_seen_at"]
    _queue_missed_overnight(state, market, fair, FakeQ(), "primary", "NEW")
    assert len(state["overnight_missed"]) == 1
    assert state["overnight_missed"]["TEST-OVERNIGHT|YES|primary"]["first_seen_at"] == first

    print("PM Edge v21 overnight look-ahead self-test: PASS")


def main() -> int:
    v20.v19.v18.v17.v16.install_v16_patch()
    v20.v19.v18.v17.install_v17_patch()
    v20.v19.v18.install_v18_patch()
    v20.v19.install_v19_patch()
    v20.install_v20_patch()
    install_v21_patch()
    rc = watcher.main()
    print("PM_EDGE_V16_RATE_LIMIT " + json.dumps(v20.v19.v18.v17.v16.V16_STATS, sort_keys=True))
    print("PM_EDGE_V17_SAFETY " + json.dumps(v20.v19.v18.v17.V17_STATS, sort_keys=True))
    print("PM_EDGE_V18_PRICE_FISH " + json.dumps(v20.v19.v18.V18_STATS, sort_keys=True))
    print("PM_EDGE_V19_REALISTIC_FISH " + json.dumps(v20.v19.V19_STATS, sort_keys=True))
    print("PM_EDGE_V20_AUTO_SIZE " + json.dumps(v20.V20_STATS, sort_keys=True))
    print("PM_EDGE_V21_OVERNIGHT " + json.dumps(V21_STATS, sort_keys=True))
    return int(rc or 0)


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        v20.v19.v18.v17.v16.self_test()
        v20.v19.v18.v17.self_test()
        v20.v19.v18.self_test()
        v20.v19.self_test()
        v20.self_test()
        self_test()
        raise SystemExit(0)

    raise SystemExit(main())
