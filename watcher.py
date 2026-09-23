# PM Edge background watcher
# Paper-alert only: this file never places a real-money order.

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from pmedge.settings import Settings
from pmedge.kalshi import KalshiPublicClient, normalize_market, tick_size_for_price
from pmedge.market_parse import parse_market
from pmedge.matcher import load_aliases, match_event
from pmedge.odds import OddsAPIClient, OddsAPIError
from pmedge.fair_value import fair_from_event, blend_fair_value, load_model_overlay, load_manual_values

from pmedge.pricing import best_binary_trade
import pmedge.matcher as _matcher_mod
from rapidfuzz.fuzz import ratio as _rf_ratio, token_set_ratio as _rf_token_set_ratio


_KALSHI_TICKER_START_RE = re.compile(
    r"-(?P<year>\d{2})(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})"
)
_KALSHI_MONTHS = {m: i for i, m in enumerate(
    ("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"), start=1
)}
_KALSHI_TICKER_TZ = ZoneInfo("America/New_York")


def kalshi_ticker_game_time(market: dict) -> datetime | None:
    """Parse YYMONDDHHMM embedded in sports contract tickers as Eastern time."""
    blob = str(market.get("ticker") or market.get("event_ticker") or "").upper()
    match = _KALSHI_TICKER_START_RE.search(blob)
    if not match:
        return None
    try:
        local = datetime(
            2000 + int(match.group("year")),
            _KALSHI_MONTHS[match.group("month")],
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=_KALSHI_TICKER_TZ,
        )
        return local.astimezone(timezone.utc)
    except (KeyError, ValueError):
        return None


def _abbr_matches(short_token: str, long_tokens: list[str]) -> bool:
    """
    Match common sports team codes against a full sportsbook team name.

    Examples:
      BUF -> Buffalo Sabres
      CHI -> Chicago Blackhawks
      KC  -> Kansas City Chiefs
      LAL -> Los Angeles Lakers
      NYG -> New York Giants
    """
    short_token = short_token.lower().strip()
    long_tokens = [t.lower().strip() for t in long_tokens if t]
    if not short_token or not long_tokens:
        return False

    # Prefix abbreviation of a city/team word: BUF -> Buffalo, DEN -> Denver.
    if len(short_token) >= 3 and any(t.startswith(short_token) for t in long_tokens):
        return True

    # Full-team initials: LAL -> Los Angeles Lakers; NYG -> New York Giants.
    full_acronym = "".join(t[0] for t in long_tokens)
    if len(short_token) >= 2 and short_token == full_acronym:
        return True

    # City/location initials: KC -> Kansas City (Chiefs), LA -> Los Angeles.
    # Try every leading prefix except the full team name.
    for n in range(2, len(long_tokens)):
        lead_acronym = "".join(t[0] for t in long_tokens[:n])
        if short_token == lead_acronym:
            return True

    return False

def _smart_team_similarity(a: str, b: str, aliases: dict[str, str]) -> float:
    """
    Strict abbreviation-aware matching.

    Important: mascot equality alone is NOT enough. That caused false matches
    such as NHL Panthers/Knights being paired with unrelated NCAA teams.
    """
    x = _matcher_mod.canon(a, aliases)
    y = _matcher_mod.canon(b, aliases)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    if x in y or y in x:
        return 0.97

    xt, yt = x.split(), y.split()

    # A Kalshi event can use only the team code while the sportsbook uses the
    # full franchise name. Sport-locking happens before this matcher, so a code
    # such as CHI is compared only inside the correct league.
    if len(xt) == 1 and _abbr_matches(xt[0], yt):
        return 0.95
    if len(yt) == 1 and _abbr_matches(yt[0], xt):
        return 0.95

    # Exact mascot phrase + credible city/team abbreviation.
    # Try one- and two-token mascot tails (e.g. Chiefs, Golden Knights).
    for tail_n in (2, 1):
        if len(xt) <= tail_n or len(yt) <= tail_n:
            continue
        if xt[-tail_n:] != yt[-tail_n:]:
            continue

        xlead = xt[:-tail_n]
        ylead = yt[:-tail_n]

        # Direct lead equality.
        if xlead == ylead:
            return 0.98

        # One side may be an abbreviation of the other's lead/full team tokens.
        if len(xlead) == 1 and _abbr_matches(xlead[0], ylead):
            return 0.96
        if len(ylead) == 1 and _abbr_matches(ylead[0], xlead):
            return 0.96

        # For VGK Golden Knights <-> Vegas Golden Knights, allow the
        # abbreviation to represent city + mascot initials.
        if len(xlead) == 1 and _abbr_matches(xlead[0], yt):
            return 0.95
        if len(ylead) == 1 and _abbr_matches(ylead[0], xt):
            return 0.95

    # Otherwise use ordinary fuzzy similarity without mascot-only boosting.
    return max(_rf_ratio(x, y), _rf_token_set_ratio(x, y)) / 100.0

_matcher_mod.team_similarity = _smart_team_similarity

STATE_PATH = Path(os.getenv("PM_EDGE_WATCH_STATE", ".pm_edge_watch_state.json"))
PERFORMANCE_HISTORY_PATH = Path(os.getenv("PM_EDGE_PERFORMANCE_HISTORY_PATH", ".pm_edge_performance_history.json"))
DAILY_RISK_BUDGET_DOLLARS = float(os.getenv("PM_EDGE_DAILY_RISK_BUDGET_DOLLARS", "40"))

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
STREAMLIT_APP_URL = os.getenv("STREAMLIT_APP_URL", "").strip()

MIN_EDGE = float(os.getenv("PM_EDGE_MIN_NET_EDGE", "0.04"))
MODEL_BUFFER = float(os.getenv("PM_EDGE_MODEL_ERROR_BUFFER", "0.01"))
SLIPPAGE_BUFFER = float(os.getenv("PM_EDGE_SLIPPAGE_BUFFER", "0.0025"))
TEAM_PAIR_MIN_SAME_SPORT = float(os.getenv("PM_EDGE_WATCH_TEAM_PAIR_MIN_SAME_SPORT", "0.55"))
TEAM_PAIR_STRONG_SAME_SPORT = float(os.getenv("PM_EDGE_WATCH_TEAM_PAIR_STRONG_SAME_SPORT", "0.70"))
LOW_SCORE_MAX_TIME_DELTA_HOURS = float(os.getenv("PM_EDGE_WATCH_LOW_SCORE_MAX_TIME_DELTA_HOURS", "18"))
MATCH_MIN_UNKNOWN_SPORT = float(os.getenv("PM_EDGE_WATCH_MATCH_CONFIDENCE_UNKNOWN_SPORT", "0.72"))
DAILY_ODDS_BUDGET = int(os.getenv("PM_EDGE_ODDS_DAILY_BUDGET", "15"))
MAX_REFRESH_GROUPS = int(os.getenv("PM_EDGE_MAX_ODDS_REFRESH_PER_RUN", "3"))
EVENT_CACHE_HOURS = float(os.getenv("PM_EDGE_EVENT_CACHE_HOURS", "8"))
BULK_FEATURED_ENABLED = os.getenv("PM_EDGE_BULK_FEATURED_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
MAX_BULK_SPORTS_PER_RUN = max(0, int(os.getenv("PM_EDGE_MAX_BULK_SPORTS_PER_RUN", "2")))
ALERT_COOLDOWN_HOURS = float(os.getenv("PM_EDGE_ALERT_COOLDOWN_HOURS", "6"))
MAX_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_MAX_ALERTS_PER_RUN", "3"))

# Speculative tier: same correctness safeguards (segment/futures/team/date
# matching, suspicious-edge ceiling) as the primary tier, just a lower edge
# bar. Sent as clearly-labeled separate alerts with their own budget, so they
# can never crowd out a primary alert's slot.
SPECULATIVE_MIN_EDGE = float(os.getenv("PM_EDGE_SPECULATIVE_MIN_EDGE", "0.02"))
MAX_SPECULATIVE_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_MAX_SPECULATIVE_ALERTS_PER_RUN", "2"))
MAX_MARKETS = int(os.getenv("PM_EDGE_MAX_MARKETS", "3000"))
MAX_EVENT_HOURS = float(os.getenv("PM_EDGE_MAX_EVENT_HOURS", "96"))
MAX_AUTO_EDGE = float(os.getenv("PM_EDGE_MAX_AUTO_EDGE", "0.15"))
# v25 may populate this before scanning so currently held markets get first
# priority for fresh fair-value refreshes and remain eligible slightly longer
# after kickoff/first pitch for position-management decisions.
HELD_POSITION_TICKERS: set[str] = set()
HELD_EVENT_LOOKBACK_HOURS = float(os.getenv("PM_EDGE_HELD_EVENT_LOOKBACK_HOURS", "6"))
WATCHER_VERSION = 16

PRIMARY_MARKET = {
    "h2h": "h2h",
    "spread": "spreads",
    "total": "totals",
    "team_total": "team_totals",
}

# The 500-credit/month Odds API plan makes bulk multi-market refreshes too
# expensive. Bulk refresh only moneylines; other market types keep the existing
# event-specific fallback inside the same hard daily credit budget.
BULK_FEATURED_KEY_FOR_TYPE = {"h2h": "h2h"}

ODDS_MARKETS_FOR_TYPE = {
    "h2h": "h2h",
    "spread": "spreads,alternate_spreads",
    "total": "totals,alternate_totals",
    "team_total": "team_totals,alternate_team_totals",
}

def now_utc():
    return datetime.now(timezone.utc)

def iso_now():
    return now_utc().isoformat()

def parse_dt(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return None

def age_hours(x):
    d = parse_dt(x)
    if not d:
        return 1e9
    return max(0.0, (now_utc() - d).total_seconds() / 3600.0)

def load_state():
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "events_cache": {},
            "fair_cache": {},
            "quotes": {},
            "alerts": {},
            "daily_usage": {"date": "", "credits": 0},
            "first_run_notified": False,
        }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("events_cache", {})
        data.setdefault("fair_cache", {})
        data.setdefault("quotes", {})
        data.setdefault("alerts", {})
        data.setdefault("daily_usage", {"date": "", "credits": 0})
        data.setdefault("first_run_notified", False)
        return data
    except Exception:
        return {
            "version": 1,
            "events_cache": {},
            "fair_cache": {},
            "quotes": {},
            "alerts": {},
            "daily_usage": {"date": "", "credits": 0},
            "first_run_notified": False,
        }

def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")

def reset_daily_usage(state):
    today = now_utc().date().isoformat()
    du = state.setdefault("daily_usage", {})
    if du.get("date") != today:
        du["date"] = today
        du["credits"] = 0
    return du

def usage_cost(usage):
    raw = (usage or {}).get("requests_last")
    try:
        return max(0, int(float(raw)))
    except Exception:
        return 1

def _region_count(regions) -> int:
    return max(1, len([x for x in str(regions or "us").split(",") if x.strip()]))

def estimated_featured_odds_cost(settings, markets) -> int:
    return max(0, len(list(markets or []))) * _region_count(getattr(settings, "odds_regions", "us"))

def fetch_featured_sport_odds(settings, sport_key: str, markets):
    """Fetch featured h2h/spreads/totals for every listed event in one sport."""
    markets = [m for m in dict.fromkeys(markets or []) if m in {"h2h", "spreads", "totals"}]
    if not sport_key or not markets:
        return [], {"requests_last": 0}

    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    response = requests.get(
        url,
        params={
            "apiKey": settings.odds_api_key,
            "regions": settings.odds_regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
        timeout=25,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Odds bulk refresh failed: {sport_key} {response.status_code} {response.text[:200]}"
        )

    usage = {
        "requests_last": response.headers.get("x-requests-last"),
        "requests_used": response.headers.get("x-requests-used"),
        "requests_remaining": response.headers.get("x-requests-remaining"),
    }
    payload = response.json()
    return (payload if isinstance(payload, list) else []), usage

def cents(x):
    return f"{float(x)*100:.1f}¢"

def pct(x):
    return f"{float(x)*100:.1f}%"

def send_push(title, message, *, priority=0, url=None, url_title="Open PM Edge"):
    if not PUSHOVER_USER_KEY or not PUSHOVER_APP_TOKEN:
        print("PUSH SKIPPED: missing Pushover secrets")
        print(title)
        print(message)
        return False
    payload = {
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title[:250],
        "message": message[:1024],
        "priority": str(priority),
    }
    target = url or STREAMLIT_APP_URL
    if target:
        payload["url"] = target
        payload["url_title"] = url_title
    r = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=20)
    if r.status_code >= 400:
        print("Pushover error:", r.status_code, r.text[:500])
        return False
    print("Pushover sent:", title)
    return True

def market_move(market, prev):
    if not prev:
        return 0.0
    vals = []
    for k in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
        a, b = market.get(k), prev.get(k)
        if a is not None and b is not None:
            vals.append(abs(float(a) - float(b)))
    return max(vals) if vals else 0.0

def event_hours(event):
    d = parse_dt((event or {}).get("commence_time"))
    if not d:
        return 9999.0
    return (d - now_utc()).total_seconds() / 3600.0

def fair_ttl_hours(event):
    h = event_hours(event)
    if h <= 3:
        return 1.5
    if h <= 12:
        return 3.0
    if h <= 36:
        return 6.0
    return 12.0

def priority_score(market, event, prev_quote, fair_entry):
    vol = max(0.0, float(market.get("volume_24h") or 0.0))
    score = min(40.0, math.log1p(vol) * 4.0)
    if str(market.get("ticker") or "") in HELD_POSITION_TICKERS:
        score += 5000.0
    h = event_hours(event)
    if 0 <= h <= 3:
        score += 45
    elif 0 <= h <= 12:
        score += 32
    elif 0 <= h <= 36:
        score += 18
    elif 0 <= h <= 96:
        score += 8
    move = market_move(market, prev_quote)
    if move >= 0.03:
        score += 30
    elif move >= 0.02:
        score += 20
    elif move >= 0.01:
        score += 8
    if not fair_entry:
        score += 12
    return score

def get_events(odds, state):
    cache = state.setdefault("events_cache", {})
    if cache.get("events") and age_hours(cache.get("fetched_at")) <= EVENT_CACHE_HOURS:
        return cache["events"], 0

    sports, usage = odds.active_sports()
    events = []
    for sport in sports:
        key = sport.get("key")
        if not key:
            continue
        try:
            rows, _ = odds.events(key)
        except OddsAPIError:
            continue
        for e in rows:
            x = dict(e)
            x["_sport_key"] = key
            x["_sport_title"] = sport.get("title") or key
            x["_sport_group"] = sport.get("group") or ""
            events.append(x)

    cache["fetched_at"] = iso_now()
    cache["events"] = events
    return events, 0



def _contains_matchup_separator(s: str) -> bool:
    x = f" {str(s or '').lower()} "
    return any(token in x for token in (" vs ", " vs. ", " at ", " @ ", " v "))


def _clean_matchup_label(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    # Kalshi event families sometimes append ": Spread", ": Total", etc.
    # Preserve the left side when that is the actual game label.
    left = s.split(":", 1)[0].strip()
    return left if _contains_matchup_separator(left) else s


def _is_game_milestone(m: dict) -> bool:
    category = str(m.get("category") or "").lower()
    typ = str(m.get("type") or "").lower()
    if category and category != "sports":
        return False
    # We want discrete contests, not season/tournament/award milestones.
    return ("game" in typ or "match" in typ) and "tournament" not in typ and "season" not in typ


def _milestone_is_near_term(m: dict) -> bool:
    d = parse_dt(m.get("start_date"))
    if not d:
        return False
    h = (d - now_utc()).total_seconds() / 3600.0
    return -1.0 <= h <= MAX_EVENT_HOURS


def _kalshi_get_json(session: requests.Session, url: str, params=None) -> dict:
    r = session.get(url, params=params or {}, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_open_kalshi_sports_markets(base_url: str, max_events: int = 2500):
    """
    Discover actual near-term sports contests from Kalshi milestones, then load
    only events/markets tied to those contests.

    Why this is safer:
      * Milestones represent real-world occurrences and expose start_date.
      * We only keep Sports milestones whose type is a discrete game/match.
      * Season-long wins/points, awards, futures and combo markets never enter
        the candidate pipeline.
    """
    base = base_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"User-Agent": "PM-Edge-Watch/13.0"})

    # 1) Pull upcoming Sports milestones. API has minimum_start_date but no max,
    # so filter the 96-hour upper bound locally.
    milestone_rows = []
    cursor = None
    pages = 0
    minimum_start = (now_utc() - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    while len(milestone_rows) < max_events and pages < 10:
        params = {
            "limit": min(500, max_events - len(milestone_rows)),
            "category": "Sports",
            "minimum_start_date": minimum_start,
        }
        if cursor:
            params["cursor"] = cursor

        data = _kalshi_get_json(session, base + "/milestones", params)
        batch = data.get("milestones", [])
        if not isinstance(batch, list) or not batch:
            break

        milestone_rows.extend(batch)
        pages += 1
        cursor = data.get("cursor")
        if not cursor:
            break

    game_milestones = [
        m for m in milestone_rows
        if _is_game_milestone(m) and _milestone_is_near_term(m)
    ]

    # 2) Map every related/primary event ticker to its milestone.
    ticker_to_milestone = {}
    for m in game_milestones:
        tickers = []
        tickers.extend(m.get("primary_event_tickers") or [])
        tickers.extend(m.get("related_event_tickers") or [])
        for ticker in tickers:
            t = str(ticker or "").strip()
            if t:
                ticker_to_milestone.setdefault(t, m)

    # 3) Fetch the small set of event objects concurrently.
    # Public Kalshi market-data endpoints do not need auth.
    def fetch_event(event_ticker):
        try:
            data = _kalshi_get_json(
                session,
                base + f"/events/{event_ticker}",
                {"with_nested_markets": "true"},
            )
            return event_ticker, data.get("event") or {}, data.get("markets") or []
        except Exception as exc:
            print("Kalshi event fetch failed:", event_ticker, exc)
            return event_ticker, {}, []

    event_results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(fetch_event, t) for t in ticker_to_milestone]
        for fut in as_completed(futs):
            event_results.append(fut.result())

    out = []
    event_count = 0

    for event_ticker, ev, top_level_markets in event_results:
        if not ev:
            continue
        event_count += 1
        milestone = ticker_to_milestone.get(event_ticker) or {}

        category = str(ev.get("category") or milestone.get("category") or "")
        ev_title = str(ev.get("title") or "").strip()
        ev_sub = str(ev.get("sub_title") or "").strip()
        milestone_title = str(milestone.get("title") or "").strip()
        milestone_start = milestone.get("start_date")

        # Prefer the milestone's contest label, then event title.
        matchup = ""
        for label in (milestone_title, ev_title):
            candidate = _clean_matchup_label(label)
            if _contains_matchup_separator(candidate):
                matchup = candidate
                break
        if not matchup:
            matchup = _clean_matchup_label(milestone_title or ev_title)

        ev_markets = ev.get("markets")
        if not isinstance(ev_markets, list):
            ev_markets = top_level_markets if isinstance(top_level_markets, list) else []

        for raw_market in ev_markets:
            if not isinstance(raw_market, dict):
                continue

            market_status = str(raw_market.get("status") or "").lower()
            if market_status not in ("active", "open"):
                continue

            ticker = str(raw_market.get("ticker") or "")
            if ticker.startswith("KXMVE") or raw_market.get("mve_collection_ticker"):
                continue

            enriched = dict(raw_market)
            original_title = str(raw_market.get("title") or "").strip()

            # Use the real game time from the Sports milestone for matching.
            if milestone_start:
                enriched["_match_datetime"] = milestone_start

            # Feed the parser a clean game prefix so it can parse participants.
            if matchup:
                enriched["title"] = f"{matchup}: {original_title}" if original_title else matchup

            nm = normalize_market(enriched)
            nm["_kalshi_event_title"] = ev_title
            nm["_kalshi_event_sub_title"] = ev_sub
            nm["_kalshi_category"] = category
            nm["_kalshi_milestone_title"] = milestone_title
            nm["_kalshi_milestone_type"] = milestone.get("type")
            nm["_kalshi_game_time"] = milestone_start
            nm["_match_datetime"] = milestone_start or raw_market.get("occurrence_datetime")
            out.append(nm)

    stats = {
        "milestones_scanned": len(milestone_rows),
        "game_milestones": len(game_milestones),
        "kalshi_events_seen": event_count,
        "event_tickers": len(ticker_to_milestone),
    }
    return out, stats



FUTURE_PHRASES = (
    "be the matchup in",
    "stanley cup final",
    "super bowl matchup",
    "world series matchup",
    "nba finals matchup",
    "championship matchup",
    "make the playoffs",
    "win the championship",
    "win the stanley cup",
    "win the super bowl",
    "win the world series",
    "season wins",
    "regular season wins",
    "total wins",
    "total points",
    "regular season",
    "season points",
    "most valuable player",
    "mvp",
    "rookie of the year",
    "coach of the year",
    "award",
)

FUTURE_TICKER_FRAGMENTS = (
    "WINS",
    "SEASONPTS",
    "SEASONPOINTS",
    "MVP",
    "CHAMP",
    "FINALS",
    "PLAYOFF",
    "AWARD",
    "ROY",
    "COTY",
)

def looks_like_future_or_outright(market: dict) -> bool:
    title = " ".join(
        str(market.get(k) or "")
        for k in ("title", "_kalshi_event_title", "_kalshi_event_sub_title")
    ).lower()

    ticker_blob = " ".join(
        str(market.get(k) or "")
        for k in ("ticker", "event_ticker")
    ).upper()

    if any(p in title for p in FUTURE_PHRASES):
        return True

    if any(fragment in ticker_blob for fragment in FUTURE_TICKER_FRAGMENTS):
        return True

    # Catch threshold-style season props even when phrased without "total wins".
    # Examples from live Kalshi data:
    # "win at least 60 games in the 2026-27 regular season"
    # "earn at least 70 points in the 2026-27 regular season"
    if "regular season" in title and any(
        phrase in title
        for phrase in ("at least", "more than", "fewer than", "less than", "games", "points")
    ):
        return True

    return False


UNSUPPORTED_SEGMENT_PHRASES = (
    # Baseball
    "first 3 innings",
    "1st 3 innings",
    "first three innings",
    "first 5 innings",
    "1st 5 innings",
    "first five innings",
    "first inning",
    "1st inning",
    "first 1 inning",
    # Halves
    "first half",
    "1st half",
    "second half",
    "2nd half",
    # Quarters
    "first quarter",
    "1st quarter",
    "second quarter",
    "2nd quarter",
    "third quarter",
    "3rd quarter",
    "fourth quarter",
    "4th quarter",
    # Periods
    "first period",
    "1st period",
    "second period",
    "2nd period",
    "third period",
    "3rd period",
    # Sets / maps / other partial contests
    "first set",
    "1st set",
    "second set",
    "2nd set",
    "first map",
    "1st map",
    "second map",
    "2nd map",
)

UNSUPPORTED_SEGMENT_TICKER_FRAGMENTS = (
    "1H",
    "2H",
    "1Q",
    "2Q",
    "3Q",
    "4Q",
    "1P",
    "2P",
    "3P",
    "F3",
    "F5",
    "1ST3",
    "1ST5",
)

def looks_like_unsupported_segment(market: dict) -> bool:
    """
    Exclude partial-game/period markets until PM Edge has exact matching
    sportsbook market support for that segment.

    This prevents a Kalshi market like "Pittsburgh first 3 innings winner"
    from being compared against a full-game moneyline.
    """
    title_blob = " ".join(
        str(market.get(k) or "")
        for k in (
            "title",
            "_kalshi_event_title",
            "_kalshi_event_sub_title",
            "_kalshi_milestone_title",
        )
    ).lower()

    if any(p in title_blob for p in UNSUPPORTED_SEGMENT_PHRASES):
        return True

    # Ticker fragments are only used as a secondary guard because title text
    # is more reliable. Surround with separators where possible to avoid broad
    # accidental substring matches.
    ticker_blob = " ".join(
        str(market.get(k) or "")
        for k in ("ticker", "event_ticker")
    ).upper()

    tokens = (
        ticker_blob.replace("-", " ")
        .replace("_", " ")
        .replace("/", " ")
        .split()
    )
    return any(fragment in tokens for fragment in UNSUPPORTED_SEGMENT_TICKER_FRAGMENTS)


def event_is_near_term(event: dict) -> bool:
    h = event_hours(event)
    # Require a real scheduled sportsbook event in the next few days.
    return -1.0 <= h <= MAX_EVENT_HOURS

def kalshi_market_is_near_term(market: dict) -> bool:
    kd = kalshi_ticker_game_time(market) or parse_dt(
        market.get("_kalshi_game_time")
        or market.get("_match_datetime")
        or market.get("occurrence_datetime")
    )
    if not kd:
        return False
    h = (kd - now_utc()).total_seconds() / 3600.0
    return -1.0 <= h <= MAX_EVENT_HOURS



def event_match_team_score(em) -> float:
    reason = str(getattr(em, "reason", "") or "")
    m = re.search(r"team=([0-9.]+)", reason)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except Exception:
        return 0.0


def low_score_time_confirmed(market: dict, event: dict) -> tuple[bool, float | None]:
    """
    For weaker but same-sport team-name matches, require independent time agreement.
    """
    kd = kalshi_ticker_game_time(market) or parse_dt(
        market.get("_kalshi_game_time")
        or market.get("_match_datetime")
        or market.get("occurrence_datetime")
    )
    sd = parse_dt((event or {}).get("commence_time"))
    if not kd or not sd:
        return False, None
    delta_h = abs((kd - sd).total_seconds()) / 3600.0
    return delta_h <= LOW_SCORE_MAX_TIME_DELTA_HOURS, delta_h

def inferred_sport_rule(market: dict):
    """
    Infer the sportsbook sport key from Kalshi's ticker/event ticker.
    Returns a predicate over The Odds API sport keys, or None if unknown.
    """
    blob = " ".join([
        str(market.get("ticker") or ""),
        str(market.get("event_ticker") or ""),
        str(market.get("_kalshi_event_title") or ""),
        str(market.get("_kalshi_milestone_title") or ""),
        str(market.get("_kalshi_milestone_type") or ""),
    ]).upper()

    exact = [
        (("NCAAF", "CFB"), "americanfootball_ncaaf"),
        (("NFL",), "americanfootball_nfl"),
        (("NCAAB", "CBB"), "basketball_ncaab"),
        (("WNBA",), "basketball_wnba"),
        (("NBA",), "basketball_nba"),
        (("MLB",), "baseball_mlb"),
        (("KBO",), "baseball_kbo"),
        (("NPB",), "baseball_npb"),
        (("NHL",), "icehockey_nhl"),
        (("MLS",), "soccer_usa_mls"),
        (("EPL", "PREMIERLEAGUE"), "soccer_epl"),
        (("UCL", "CHAMPIONSLEAGUE"), "soccer_uefa_champs_league"),
        (("UFC", "MMA"), "mma_mixed_martial_arts"),
        (("BOXING",), "boxing_boxing"),
    ]
    for needles, key in exact:
        if any(n in blob for n in needles):
            return ("exact", key)

    # Tennis is split into many ATP/WTA tournament sport keys.
    if any(n in blob for n in ("TENNIS", "ATP", "WTA")):
        return ("prefix", "tennis_")

    # Golf also uses event-specific keys, though most outright markets are
    # filtered before this stage.
    if "GOLF" in blob or "PGA" in blob:
        return ("prefix", "golf_")

    return None

def sport_rule_matches(event: dict, rule) -> bool:
    if not rule:
        return True
    key = str(event.get("_sport_key") or "")
    mode, value = rule
    if mode == "exact":
        return key == value
    if mode == "prefix":
        return key.startswith(value)
    return True

def fair_to_dict(fair, event, spec, usage=None, devig_diag=None):
    return {
        "fetched_at": iso_now(),
        "fair_yes_prob": float(fair.fair_yes_prob),
        "source": fair.source,
        "confidence": float(fair.confidence),
        "n_books": int(fair.n_books),
        "dispersion": float(fair.dispersion),
        "raw_book_prob": fair.raw_book_prob,
        "model_prob": fair.model_prob,
        "detail": fair.detail,
        "event_id": event.get("id"),
        "sport_key": event.get("_sport_key"),
        "sport_title": event.get("_sport_title") or event.get("_sport_key") or "Sports",
        "market_type": spec.market_type,
        "selection": spec.selection,
        "point": spec.point,
        "commence_time": event.get("commence_time"),
        "requests_remaining": (usage or {}).get("requests_remaining"),
        "devig_method": (devig_diag or {}).get("devig_method"),
        "fair_proportional": (devig_diag or {}).get("fair_proportional"),
        "fair_power": (devig_diag or {}).get("fair_power"),
        "fair_shin": (devig_diag or {}).get("fair_shin"),
        "books_available": (devig_diag or {}).get("books_available"),
        "books_matched": (devig_diag or {}).get("books_matched"),
        "book_drop_reasons": (devig_diag or {}).get("book_drop_reasons") or [],
    }

def score_market(market, fair_entry, settings, base_min_edge=None):
    tick = tick_size_for_price(market, market.get("yes_bid") or market.get("yes_ask") or 0.50)

    # Two-book alternate-line consensus is usable, but demand more edge.
    # When no base is given (the primary tier), this reproduces the original
    # behavior exactly: fixed absolute floors of 4.5%/6%, regardless of
    # MIN_EDGE. A custom base (the speculative tier) scales the same +0.5pt/
    # +2pt gaps relative to that lower base instead.
    n_books = int(fair_entry.get("n_books") or 0)
    if base_min_edge is None:
        base = MIN_EDGE
        floor_3, floor_2 = 0.045, 0.06
    else:
        base = base_min_edge
        floor_3, floor_2 = base + 0.005, base + 0.02

    if n_books >= 4:
        required_edge = base
    elif n_books == 3:
        required_edge = max(base, floor_3)
    else:
        required_edge = max(base, floor_2)

    q = best_binary_trade(
        fair_yes_prob=float(fair_entry["fair_yes_prob"]),
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        no_bid=market.get("no_bid"),
        no_ask=market.get("no_ask"),
        min_net_edge=required_edge,
        nonfee_buffer=MODEL_BUFFER + SLIPPAGE_BUFFER,
        maker_fee_rate=settings.maker_fee_rate,
        taker_fee_rate=settings.taker_fee_rate,
        tick_size=tick,
    )
    if q.net_edge is not None and float(q.net_edge) > 0.12:
        return replace(q, execution="PASS", recommended_limit=None, qualifies=False, reason="implausible_edge")
    if q.best_bid is None or q.best_ask is None:
        return replace(q, execution="PASS", recommended_limit=None, qualifies=False, reason="degenerate_book")
    if float(q.best_ask) - float(q.best_bid) > 0.10:
        return replace(q, execution="PASS", recommended_limit=None, qualifies=False, reason="wide_spread")
    return q


def drawdown_circuit_active(
    state: dict,
    *,
    history_path: Path = PERFORMANCE_HISTORY_PATH,
    daily_risk_budget: float = DAILY_RISK_BUDGET_DOLLARS,
) -> bool:
    """Latch alerts off after a severe trailing-20 drawdown; never auto-rearm."""
    circuit = state.setdefault("drawdown_circuit", {"active": False})
    if bool(circuit.get("active")):
        return True
    if not history_path.exists():
        return False
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        records = list(payload.get("records", [])) if isinstance(payload, dict) else []
    except Exception:
        return False

    graded = []
    for row in records:
        try:
            filled = float(row.get("filled_shares") or 0)
            pl = float(row.get("net_pl"))
        except (TypeError, ValueError):
            continue
        status = str(row.get("status") or "")
        if filled <= 0 or status == "unfilled":
            continue
        stamp = str(row.get("recorded_at") or row.get("settled_at") or row.get("trade_date") or "")
        graded.append((stamp, pl))
    graded.sort(key=lambda x: x[0])
    if len(graded) < 20:
        return False

    recent = graded[-20:]
    last_stamp = recent[-1][0]
    if circuit.get("last_trigger_record") == last_stamp:
        return False
    trailing_pl = sum(x[1] for x in recent)
    threshold = -3.0 * float(daily_risk_budget)
    if trailing_pl < threshold:
        circuit.update({
            "active": True,
            "triggered_at": iso_now(),
            "trailing_20_pl": round(trailing_pl, 6),
            "threshold": round(threshold, 6),
            "last_trigger_record": last_stamp,
        })
        return True
    return False


def should_alert(state, market, fair_entry, q, tier="primary"):
    if drawdown_circuit_active(state):
        return False, "drawdown_circuit"
    # Primary tier keeps the original key format for backward compatibility
    # with already-active alerts recorded before tiers existed.
    suffix = "" if tier == "primary" else f"|{tier.upper()}"
    key = f"{market['ticker']}|{q.side}{suffix}"
    prev = state.setdefault("alerts", {}).get(key)
    if not prev:
        return True, "NEW"
    if not prev.get("active", False):
        return True, "BACK"
    old_entry = float(prev.get("entry_price") or 0)
    old_edge = float(prev.get("net_edge") or 0)
    new_entry = float(q.recommended_limit) if q.recommended_limit is not None else None

    # Never re-alert the same ticker/side because the price moved against us.
    # A worse price is not new actionable information; it only creates a stale
    # notification that the approval page will reject as DO NOT CHASE.
    if new_entry is not None and old_entry > 0 and new_entry > old_entry + 1e-9:
        return False, "worse_price_than_last_alert"

    # Price-change re-alerts are one-way: only meaningfully BETTER prices.
    if new_entry is not None and old_entry > 0 and old_entry - new_entry >= 0.02:
        return True, "PRICE"

    # For TAKE orders, also suppress reminders when the current executable ask
    # is above the last alert's hard maximum.
    old_hard_max = float(prev.get("hard_max") or 0)
    current_ask = getattr(q, "best_ask", None)
    if (
        str(getattr(q, "execution", "") or "").upper() == "TAKE"
        and current_ask is not None
        and old_hard_max > 0
        and float(current_ask) > old_hard_max + 1e-9
    ):
        return False, "live_price_above_last_max"

    if q.net_edge is not None and float(q.net_edge) - old_edge >= 0.02:
        return True, "EDGE"
    if age_hours(prev.get("sent_at")) >= ALERT_COOLDOWN_HOURS:
        return True, "REMINDER"
    return False, "cooldown_active"


def record_suppression(state, market, fair_entry, q, tier, reason):
    """Instrumentation hook; v28 records cap/policy suppressions durably."""
    return None


def record_alert(state, market, fair_entry, q, tier="primary"):
    suffix = "" if tier == "primary" else f"|{tier.upper()}"
    key = f"{market['ticker']}|{q.side}{suffix}"
    state.setdefault("alerts", {})[key] = {
        "active": True,
        "sent_at": iso_now(),
        "entry_price": q.recommended_limit,
        "hard_max": q.hard_max,
        "net_edge": q.net_edge,
        "fair_yes_prob": fair_entry["fair_yes_prob"],
        "title": market.get("title"),
        "tier": tier,
    }

def maybe_send_pass_alerts(state, market_by_ticker, fair_cache, settings):
    sent = 0
    for key, alert in list(state.setdefault("alerts", {}).items()):
        if not alert.get("active"):
            continue
        # Speculative keys look like "TICKER|SIDE|SPECULATIVE"; primary keys
        # keep the original two-part "TICKER|SIDE" format.
        parts = key.split("|")
        ticker, side = parts[0], parts[1]
        tier = alert.get("tier", "primary")
        base_edge = SPECULATIVE_MIN_EDGE if tier == "speculative" else None
        market = market_by_ticker.get(ticker)
        fair_entry = fair_cache.get(ticker)
        if not market or not fair_entry:
            continue
        # Do not make a pass decision from very stale fair value.
        event = {"commence_time": fair_entry.get("commence_time")}
        if age_hours(fair_entry.get("fetched_at")) > max(8.0, fair_ttl_hours(event) * 2):
            continue
        q = score_market(market, fair_entry, settings, base_min_edge=base_edge)
        if q.qualifies:
            continue
        label = "" if tier == "primary" else " (speculative)"
        msg = (
            f"{side} — {market.get('title','')[:150]}\n"
            f"Current quote no longer clears PM Edge's {pct(base_edge if base_edge is not None else MIN_EDGE)} net-edge rule{label}.\n"
            f"Status: PASS / DO NOT CHASE."
        )
        if send_push("⚠️ PM EDGE — PASS NOW", msg, priority=0):
            alert["active"] = False
            alert["pass_sent_at"] = iso_now()
            sent += 1
    return sent

def self_test():
    # No network: validate state/format behavior and threshold mechanics.
    s = {
        "alerts": {},
        "daily_usage": {"date": "", "credits": 0},
    }
    reset_daily_usage(s)
    assert s["daily_usage"]["date"] == now_utc().date().isoformat()
    assert cents(0.57) == "57.0¢"
    assert pct(0.061) == "6.1%"
    ticker_dt = kalshi_ticker_game_time({"ticker": "KXNPBGAME-26SEP230500TOHHOK-HOK"})
    assert ticker_dt is not None and ticker_dt.isoformat() == "2026-09-23T09:00:00+00:00"
    ok, delta = low_score_time_confirmed(
        {"ticker": "KXNPBGAME-26SEP230500TOHHOK-HOK"},
        {"commence_time": "2026-09-22T09:00:00Z"},
    )
    assert not ok and delta == 24.0
    print("PM Edge watcher v15 self-test: PASS")
    return 0

def main():
    if "--self-test" in sys.argv:
        return self_test()

    if not os.getenv("ODDS_API_KEY", "").strip():
        raise SystemExit("Missing ODDS_API_KEY")
    settings = Settings()
    aliases = load_aliases()
    overlay = load_model_overlay()
    manual = load_manual_values()
    kalshi = KalshiPublicClient(settings.kalshi_base_url)
    odds = OddsAPIClient(settings.odds_api_key, settings.odds_regions)
    state = load_state()

    # v6 changes event-time semantics. Purge fair values and alerts produced by
    # older matcher versions so a stale future/false match can never alert again.
    if int(state.get("data_semantics_version", 0) or 0) != WATCHER_VERSION:
        state["fair_cache"] = {}
        state["alerts"] = {}
        state["quotes"] = {}
        state["data_semantics_version"] = WATCHER_VERSION

    daily = reset_daily_usage(state)

    markets, kalshi_stats = fetch_open_kalshi_sports_markets(
        settings.kalshi_base_url,
        max_events=MAX_MARKETS,
    )
    kalshi_events_seen = int(kalshi_stats.get("kalshi_events_seen", 0))
    market_by_ticker = {m["ticker"]: m for m in markets}
    events, _ = get_events(odds, state)

    candidates = []
    supported_markets = 0
    event_matches_any_conf = 0
    futures_filtered = 0
    segment_filtered = 0
    kalshi_horizon_filtered = 0
    sportsbook_horizon_filtered = 0

    sport_identified = 0
    same_sport_matches = 0
    accepted_same_sport = 0
    accepted_same_sport_strong = 0
    accepted_same_sport_time_confirmed = 0
    rejected_low_score_missing_time = 0
    rejected_low_score_bad_time = 0
    rejected_strong_bad_time = 0
    accepted_unknown_sport = 0
    top_same_sport_team_score = 0.0
    top_same_sport_confidence = 0.0
    best_confirmed_time_delta_hours = None

    for market in markets:
        # FIRST: eliminate season-long/futures markets before the generic text
        # parser can misread "win at least 60 games" as a head-to-head game.
        if looks_like_future_or_outright(market):
            futures_filtered += 1
            continue

        # Partial-game/period contracts require exact period-specific sportsbook
        # odds. Until explicitly supported, never price them as full-game markets.
        if looks_like_unsupported_segment(market):
            segment_filtered += 1
            continue

        # SECOND: require the Kalshi occurrence itself to be near-term before
        # attempting any sportsbook event match.
        if not kalshi_market_is_near_term(market):
            kalshi_horizon_filtered += 1
            continue

        spec = parse_market(market)
        if spec.market_type not in PRIMARY_MARKET:
            continue
        supported_markets += 1

        sport_rule = inferred_sport_rule(market)
        if sport_rule:
            sport_identified += 1
            event_pool = [e for e in events if sport_rule_matches(e, sport_rule)]
        else:
            event_pool = events

        if not event_pool:
            continue

        # Never use close_time as a surrogate for kickoff. If occurrence_datetime
        # is absent, match_event will rely on participant identity.
        em = match_event(
            spec, event_pool, aliases,
            (
                kalshi_ticker_game_time(market).isoformat()
                if kalshi_ticker_game_time(market)
                else market.get("_match_datetime") or market.get("occurrence_datetime")
            ),
        )

        team_score = event_match_team_score(em)
        if em.event:
            event_matches_any_conf += 1
            if sport_rule:
                same_sport_matches += 1
                top_same_sport_team_score = max(top_same_sport_team_score, team_score)
                top_same_sport_confidence = max(top_same_sport_confidence, float(em.confidence or 0))

        if not em.event:
            continue

        # Once the sport is identified, prefer direct participant-pair evidence
        # over the generic combined confidence score. match_event already rejects
        # ambiguous near-ties and only considers pair scores >= 0.55.
        if sport_rule:
            if team_score < TEAM_PAIR_MIN_SAME_SPORT:
                continue

            time_ok, delta_h = low_score_time_confirmed(market, em.event)

            if team_score >= TEAM_PAIR_STRONG_SAME_SPORT:
                # A strong team-name match does NOT guarantee this is the same
                # specific contest: two teams can play more than once in a
                # short window (doubleheaders, back-to-back series). When both
                # timestamps are available, require them to agree even for a
                # strong name match -- otherwise a correctly-named but wrong
                # game can be priced off the wrong sportsbook event.
                if delta_h is not None and not time_ok:
                    rejected_strong_bad_time += 1
                    continue
                accepted_same_sport_strong += 1
            else:
                if delta_h is None:
                    rejected_low_score_missing_time += 1
                    continue
                if not time_ok:
                    rejected_low_score_bad_time += 1
                    continue
                accepted_same_sport_time_confirmed += 1

            if delta_h is not None and (best_confirmed_time_delta_hours is None or delta_h < best_confirmed_time_delta_hours):
                best_confirmed_time_delta_hours = delta_h

            accepted_same_sport += 1
        else:
            if em.confidence < MATCH_MIN_UNKNOWN_SPORT:
                continue
            accepted_unknown_sport += 1

        if not event_is_near_term(em.event):
            held = str(market.get("ticker") or "") in HELD_POSITION_TICKERS
            h = event_hours(em.event)
            if not held or h < -HELD_EVENT_LOOKBACK_HOURS or h > MAX_EVENT_HOURS:
                sportsbook_horizon_filtered += 1
                continue

        cached = state.setdefault("fair_cache", {}).get(market["ticker"])
        prevq = state.setdefault("quotes", {}).get(market["ticker"])
        score = priority_score(market, em.event, prevq, cached)
        candidates.append((score, market, spec, em))

    candidates.sort(key=lambda x: x[0], reverse=True)

    # Update quote snapshot before scoring; cached fair values survive via Actions cache.
    for _, market, _, _ in candidates:
        state["quotes"][market["ticker"]] = {
            "seen_at": iso_now(),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "no_bid": market.get("no_bid"),
            "no_ask": market.get("no_ask"),
        }

    # Refresh fair values. Featured markets use the sport-wide Odds API endpoint
    # first so one paid request can price many events. Alternate lines and team
    # totals still fall back to the single-event endpoint below.
    refreshed_groups = 0
    bulk_refreshes = 0
    bulk_events_seen = 0
    bulk_fair_successes = 0
    run_event_odds_cache = {}
    credits_remaining_today = max(0, DAILY_ODDS_BUDGET - int(daily.get("credits", 0)))

    fair_attempts = 0
    fair_successes = 0
    odds_errors = 0

    featured_key_for_type = BULK_FEATURED_KEY_FOR_TYPE

    bulk_plan = {}
    if BULK_FEATURED_ENABLED and MAX_BULK_SPORTS_PER_RUN > 0:
        # candidates are already priority-sorted, so insertion order preserves
        # the sports whose stale fair values matter most right now.
        for score, market, spec, em in candidates:
            cached = state["fair_cache"].get(market["ticker"])
            ttl = fair_ttl_hours(em.event)
            if cached and age_hours(cached.get("fetched_at")) <= ttl:
                continue
            featured_key = featured_key_for_type.get(spec.market_type)
            sport_key = em.event.get("_sport_key")
            if not featured_key or not sport_key:
                continue
            row = bulk_plan.setdefault(sport_key, {"markets": [], "priority": score})
            if featured_key not in row["markets"]:
                row["markets"].append(featured_key)

    bulk_covered = set()
    for sport_key, plan in list(bulk_plan.items())[:MAX_BULK_SPORTS_PER_RUN]:
        if credits_remaining_today <= 0:
            break

        markets_to_fetch = list(plan["markets"])
        per_market_cost = _region_count(settings.odds_regions)
        affordable_markets = credits_remaining_today // per_market_cost
        if affordable_markets <= 0:
            break
        markets_to_fetch = markets_to_fetch[:affordable_markets]
        if not markets_to_fetch:
            continue

        estimated_cost = estimated_featured_odds_cost(settings, markets_to_fetch)
        if estimated_cost > credits_remaining_today:
            continue

        try:
            sport_rows, usage = fetch_featured_sport_odds(settings, sport_key, markets_to_fetch)
        except Exception as exc:
            odds_errors += 1
            print("Bulk odds refresh failed:", sport_key, markets_to_fetch, exc)
            continue

        cost = usage_cost(usage)
        # We estimate before calling and then account from the API's authoritative
        # x-requests-last header after the response.
        credits_remaining_today = max(0, credits_remaining_today - cost)
        daily["credits"] = int(daily.get("credits", 0)) + cost
        bulk_refreshes += 1
        bulk_events_seen += len(sport_rows)
        by_event_id = {
            str(row.get("id")): row
            for row in sport_rows
            if isinstance(row, dict) and row.get("id")
        }

        for market_key in markets_to_fetch:
            bulk_covered.add((sport_key, market_key))

        for _, market, spec, em in candidates:
            featured_key = featured_key_for_type.get(spec.market_type)
            if featured_key not in markets_to_fetch:
                continue
            if em.event.get("_sport_key") != sport_key:
                continue

            cached = state["fair_cache"].get(market["ticker"])
            ttl = fair_ttl_hours(em.event)
            if cached and age_hours(cached.get("fetched_at")) <= ttl:
                continue

            event_odds = by_event_id.get(str(em.event.get("id")))
            if not event_odds:
                continue
            event_odds = dict(event_odds)
            event_odds["_sport_key"] = em.event.get("_sport_key")
            event_odds["_sport_title"] = em.event.get("_sport_title")
            event_odds["_sport_group"] = em.event.get("_sport_group")
            fair_attempts += 1

            min_books_required = 3 if spec.market_type == "h2h" else 2
            consensus, devig_diag = fair_from_event(
                spec, event_odds, aliases,
                min_books=min_books_required,
                max_dispersion=settings.odds_max_dispersion,
                return_diagnostics=True,
            )
            fair = blend_fair_value(market["ticker"], consensus, overlay)
            if fair:
                fair_successes += 1
                bulk_fair_successes += 1
                state["fair_cache"][market["ticker"]] = fair_to_dict(
                    fair, em.event, spec, usage, devig_diag
                )

    # Event-specific fallback for alternate spreads/totals, team totals and
    # any featured market not covered by the bulk pass.
    for _, market, spec, em in candidates:
        cached = state["fair_cache"].get(market["ticker"])
        ttl = fair_ttl_hours(em.event)
        if cached and age_hours(cached.get("fetched_at")) <= ttl:
            continue

        requested_markets = ODDS_MARKETS_FOR_TYPE.get(spec.market_type)
        if not requested_markets:
            continue

        sport_key = em.event.get("_sport_key")
        featured_key = featured_key_for_type.get(spec.market_type)

        # A sport-wide h2h fetch is authoritative for the featured moneyline.
        # Avoid paying again event-by-event when that same h2h feed had no fair.
        if spec.market_type == "h2h" and (sport_key, "h2h") in bulk_covered:
            continue

        group = (sport_key, em.event.get("id"), requested_markets)

        if group not in run_event_odds_cache:
            if refreshed_groups >= MAX_REFRESH_GROUPS or credits_remaining_today <= 0:
                continue

            # The event endpoint can charge more than one credit when requesting
            # featured + alternate markets. Estimate from the requested keys and
            # regions before making the call so the local daily cap stays hard.
            estimated_cost = len([m for m in requested_markets.split(",") if m.strip()]) * _region_count(settings.odds_regions)
            if estimated_cost > credits_remaining_today:
                continue

            try:
                event_odds, usage = odds.event_odds(group[0], group[1], group[2])
            except OddsAPIError as exc:
                odds_errors += 1
                print("Odds refresh failed:", group, exc)
                continue

            cost = usage_cost(usage)
            credits_remaining_today = max(0, credits_remaining_today - cost)
            daily["credits"] = int(daily.get("credits", 0)) + cost
            refreshed_groups += 1

            if isinstance(event_odds, dict):
                event_odds = dict(event_odds)
                event_odds["_sport_key"] = em.event.get("_sport_key")
                event_odds["_sport_title"] = em.event.get("_sport_title")
                event_odds["_sport_group"] = em.event.get("_sport_group")

            run_event_odds_cache[group] = (event_odds, usage)

        event_odds, usage = run_event_odds_cache[group]
        fair_attempts += 1

        # Featured moneylines should have broader coverage. Exact alternate lines
        # can legitimately have fewer books, so allow two but demand more edge later.
        min_books_required = 3 if spec.market_type == "h2h" else 2

        consensus, devig_diag = fair_from_event(
            spec, event_odds, aliases,
            min_books=min_books_required,
            max_dispersion=settings.odds_max_dispersion,
            return_diagnostics=True,
        )
        fair = blend_fair_value(market["ticker"], consensus, overlay)
        if fair:
            fair_successes += 1
            state["fair_cache"][market["ticker"]] = fair_to_dict(fair, em.event, spec, usage, devig_diag)

    # Manual fair values override any cache.
    for ticker, x in manual.items():
        state["fair_cache"][ticker] = {
            "fetched_at": iso_now(),
            "fair_yes_prob": x["fair_yes_prob"],
            "source": x["source"],
            "confidence": x["confidence"],
            "n_books": 0,
            "dispersion": 0.0,
            "raw_book_prob": None,
            "model_prob": None,
            "detail": x.get("notes", "manual"),
            "event_id": None,
            "sport_key": None,
            "sport_title": "Manual",
            "market_type": None,
            "selection": None,
            "point": None,
            "commence_time": None,
            "devig_method": None,
            "fair_proportional": None,
            "fair_power": None,
            "fair_shin": None,
        }

    # Score cached, sufficiently fresh fair values.
    actionable = []
    speculative_actionable = []
    closest_miss_edge = None  # reporting only -- never affects any decision
    for _, market, spec, em in candidates:
        fair_entry = state["fair_cache"].get(market["ticker"])
        if not fair_entry:
            continue
        # Avoid alerts from stale fair values.
        if age_hours(fair_entry.get("fetched_at")) > max(8.0, fair_ttl_hours(em.event) * 2):
            continue
        try:
            q = score_market(market, fair_entry, settings)
        except Exception:
            continue

        # Same correctness safeguards apply to every tier -- only the edge
        # bar differs. Compute these once per market.
        is_future = looks_like_future_or_outright(market)
        is_segment = looks_like_unsupported_segment(market)
        is_near_term = kalshi_market_is_near_term(market)

        if q.qualifies and q.recommended_limit is not None:
            if is_future or is_segment or not is_near_term:
                continue
            net_edge = float(q.net_edge or 0)
            if net_edge > MAX_AUTO_EDGE:
                print(
                    "SUSPICIOUS_EDGE_SKIPPED", market.get("ticker"), market.get("title"),
                    q.side, q.recommended_limit, q.hard_max, net_edge,
                )
                continue
            actionable.append((net_edge, market, fair_entry, q))
            continue

        # Primary tier missed. Track how close, then try the speculative bar.
        if q.net_edge is not None:
            if closest_miss_edge is None or float(q.net_edge) > closest_miss_edge:
                closest_miss_edge = float(q.net_edge)

        if is_future or is_segment or not is_near_term:
            continue
        try:
            q_spec = score_market(market, fair_entry, settings, base_min_edge=SPECULATIVE_MIN_EDGE)
        except Exception:
            continue
        if not q_spec.qualifies or q_spec.recommended_limit is None:
            continue
        spec_net_edge = float(q_spec.net_edge or 0)
        if spec_net_edge > MAX_AUTO_EDGE:
            continue
        speculative_actionable.append((spec_net_edge, market, fair_entry, q_spec))

    actionable.sort(key=lambda x: x[0], reverse=True)
    speculative_actionable.sort(key=lambda x: x[0], reverse=True)

    # Decide each candidate's alert reason up front so genuinely new/changed
    # information (NEW, BACK, a moved PRICE, a bigger EDGE) is never crowded
    # out of the per-run cap by a still-qualifying REMINDER that simply has a
    # bigger edge number. The owner already knows about a REMINDER position;
    # they have not yet heard about a NEW one. New info always goes first.
    def _split_new_vs_reminder(rows, tier):
        new_info, reminders = [], []
        for net_edge, market, fair_entry, q in rows:
            ok, reason = should_alert(state, market, fair_entry, q, tier=tier)
            if not ok:
                continue
            (reminders if reason == "REMINDER" else new_info).append((net_edge, market, fair_entry, q, reason))
        return new_info, reminders

    new_info, reminders = _split_new_vs_reminder(actionable, "primary")
    spec_new_info, spec_reminders = _split_new_vs_reminder(speculative_actionable, "speculative")

    def _send(rows, cap, tier, title_prefix):
        n_sent = 0
        for net_edge, market, fair_entry, q, reason in rows:
            if n_sent >= cap:
                record_suppression(state, market, fair_entry, q, tier, "run_cap")
                continue
            yes_prob = float(fair_entry["fair_yes_prob"])
            side_prob = yes_prob if q.side == "YES" else 1.0 - yes_prob
            action = "BUY NOW" if q.execution == "TAKE" else "PLACE LIMIT"
            current_ask = q.best_ask
            sport = fair_entry.get("sport_title") or "Sports"
            spec_note = (
                "\nSpeculative pick: smaller edge, lower conviction. "
                "Only worth it as a passive limit order -- do not chase.\n"
                if tier == "speculative" else ""
            )
            msg = (
                f"{sport}\n"
                f"{q.side} — {market.get('title','')[:170]}\n"
                f"{spec_note}\n"
                f"{action}: {cents(q.recommended_limit)}\n"
                f"DO NOT PAY OVER: {cents(q.hard_max)}\n"
                f"Current ask: {cents(current_ask) if current_ask is not None else '—'}\n"
                f"Fair chance: {pct(side_prob)}\n"
                f"Est. net edge: +{pct(q.net_edge)}\n"
                f"Books: {int(fair_entry.get('n_books') or 0)}\n\n"
                f"Do not chase above MAX."
            )
            title = f"{title_prefix} +{pct(q.net_edge)} — {q.side}"
            if send_push(title, msg, priority=0):
                record_alert(state, market, fair_entry, q, tier=tier)
                n_sent += 1
        return n_sent

    sent = _send(new_info + reminders, MAX_ALERTS_PER_RUN, "primary", "🔥 PM EDGE")
    spec_sent = _send(
        spec_new_info + spec_reminders, MAX_SPECULATIVE_ALERTS_PER_RUN,
        "speculative", "🎲 PM EDGE SPECULATIVE",
    )

    pass_sent = maybe_send_pass_alerts(state, market_by_ticker, state["fair_cache"], settings)

    # Health heartbeat confirms notifications and verifies the event matcher.
    if (
        not state.get("first_run_notified")
        or int(state.get("watcher_version_notified", 0) or 0) != WATCHER_VERSION
    ):
        remaining = None
        for x in state["fair_cache"].values():
            if x.get("requests_remaining") is not None:
                remaining = x.get("requests_remaining")
                break
        msg = (
            f"PM Edge Watch is running.\n"
            f"Sports milestones scanned: {kalshi_stats.get('milestones_scanned', 0)}\n"
            f"Near-term game milestones: {kalshi_stats.get('game_milestones', 0)}\n"
            f"Kalshi game events loaded: {kalshi_events_seen}\n"
            f"Sports markets loaded: {len(markets)}\n"
            f"Supported game markets parsed: {supported_markets}\n"
            f"Partial-game markets filtered: {segment_filtered}\n"
            f"Sport identified: {sport_identified}\n"
            f"Event matches found: {event_matches_any_conf}\n"
            f"Same-sport matches: {same_sport_matches}\n"
            f"Accepted same-sport: {accepted_same_sport}\n"
            f"  strong-name: {accepted_same_sport_strong}\n"
            f"  time-confirmed: {accepted_same_sport_time_confirmed}\n"
            f"Top same-sport team score: {top_same_sport_team_score:.2f}\n"
            f"Top same-sport confidence: {top_same_sport_confidence:.2f}\n"
            f"Low-score missing-time rejects: {rejected_low_score_missing_time}\n"
            f"Low-score bad-time rejects: {rejected_low_score_bad_time}\n"
            f"Strong-name bad-time rejects: {rejected_strong_bad_time}\n"
            f"Kalshi horizon filtered: {kalshi_horizon_filtered}\n"
            f"Sportsbook horizon filtered: {sportsbook_horizon_filtered}\n"
            f"Near-term candidates: {len(candidates)}\n"
            f"Fair-value attempts: {fair_attempts}\n"
            f"Fair values created: {fair_successes}\n"
            f"Fair values cached: {len(state['fair_cache'])}\n"
            f"Odds request errors: {odds_errors}\n"
            f"Odds credits used today: {daily.get('credits',0)}/{DAILY_ODDS_BUDGET}"
        )
        if remaining is not None:
            msg += f"\nOdds API account credits remaining: {remaining}"
        if send_push("✅ PM Edge Watch v15 is live", msg, priority=0):
            state["first_run_notified"] = True
            state["watcher_version_notified"] = WATCHER_VERSION

    # Daily digest: once per calendar day, send a low-priority summary even
    # when zero alerts fired, so silence is distinguishable from a broken
    # scanner. This is a snapshot of the run that happens to trigger it (the
    # first run after the date rolls over), not an aggregate of every run
    # that day. It never affects matching, pricing, or alert decisions.
    today_str = now_utc().date().isoformat()
    digest = state.setdefault("digest", {"date": ""})
    if digest.get("date") != today_str:
        miss_line = (
            f"Closest miss below {pct(MIN_EDGE)} threshold: +{pct(closest_miss_edge)}"
            if closest_miss_edge is not None
            else "No sub-threshold candidates seen this run."
        )
        digest_msg = (
            f"Daily health check -- this reflects one run, not the full day.\n\n"
            f"Markets scanned: {len(markets)}\n"
            f"Supported game markets: {supported_markets}\n"
            f"Near-term candidates: {len(candidates)}\n"
            f"Actionable this run: {len(actionable)}\n"
            f"Alerts sent this run: {sent}\n"
            f"Speculative actionable: {len(speculative_actionable)}\n"
            f"Speculative alerts sent: {spec_sent}\n"
            f"{miss_line}\n"
            f"Odds credits used today: {daily.get('credits', 0)}/{DAILY_ODDS_BUDGET}\n\n"
            f"Green Actions runs + these numbers moving = scanner is healthy, "
            f"even with zero alerts."
        )
        if send_push("📋 PM Edge daily digest", digest_msg, priority=-1):
            digest["date"] = today_str

    # Prune old state.
    live_tickers = set(market_by_ticker)
    state["quotes"] = {k:v for k,v in state["quotes"].items() if k in live_tickers}
    state["fair_cache"] = {
        k:v for k,v in state["fair_cache"].items()
        if k in live_tickers and age_hours(v.get("fetched_at")) <= 72
    }

    save_state(state)
    print(json.dumps({
        "milestones_scanned": kalshi_stats.get("milestones_scanned", 0),
        "game_milestones": kalshi_stats.get("game_milestones", 0),
        "kalshi_events_seen": kalshi_events_seen,
        "markets": len(markets),
        "sportsbook_events": len(events),
        "supported_markets": supported_markets,
        "futures_filtered": futures_filtered,
        "segment_filtered": segment_filtered,
        "sport_identified": sport_identified,
        "event_matches_any_conf": event_matches_any_conf,
        "same_sport_matches": same_sport_matches,
        "accepted_same_sport": accepted_same_sport,
        "accepted_same_sport_strong": accepted_same_sport_strong,
        "accepted_same_sport_time_confirmed": accepted_same_sport_time_confirmed,
        "rejected_low_score_missing_time": rejected_low_score_missing_time,
        "rejected_low_score_bad_time": rejected_low_score_bad_time,
        "rejected_strong_bad_time": rejected_strong_bad_time,
        "accepted_unknown_sport": accepted_unknown_sport,
        "top_same_sport_team_score": round(top_same_sport_team_score, 4),
        "top_same_sport_confidence": round(top_same_sport_confidence, 4),
        "best_confirmed_time_delta_hours": None if best_confirmed_time_delta_hours is None else round(best_confirmed_time_delta_hours, 3),
        "kalshi_horizon_filtered": kalshi_horizon_filtered,
        "sportsbook_horizon_filtered": sportsbook_horizon_filtered,
        "candidates": len(candidates),
        "bulk_refreshes": bulk_refreshes,
        "bulk_events_seen": bulk_events_seen,
        "bulk_fair_successes": bulk_fair_successes,
        "event_refreshes": refreshed_groups,
        "fair_attempts": fair_attempts,
        "fair_successes": fair_successes,
        "odds_errors": odds_errors,
        "fair_cache": len(state["fair_cache"]),
        "actionable": len(actionable),
        "alerts_sent": sent,
        "speculative_actionable": len(speculative_actionable),
        "speculative_alerts_sent": spec_sent,
        "pass_alerts_sent": pass_sent,
        "odds_credits_today": daily.get("credits", 0),
        "daily_budget": DAILY_ODDS_BUDGET,
    }, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
