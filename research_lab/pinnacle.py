"""Pinnacle fair values from PinnWire's free REST feed (GET only, paper research).

Free plan: 100 requests/day. One request returns every upcoming game in a sport with
full-game moneyline, spread and total prices (decimal), so the lab spends at most one
request per sport per ~30 minutes and stops at DAILY_BUDGET. The key comes from the
PINNWIRE_KEY environment variable (a GitHub secret); it is a data key, not a trading key.

Pinnacle is a single, sharp book. Its de-vigged price is the fair value; there is no
multi-book consensus, so v2 strategies replace "3+ books agree" with "Pinnacle's own
margin is normal" (overround check) and require fresh prices.
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from pmedge.devig import devig_power, devig_proportional, devig_shin
from pmedge.market_parse import parse_market
from pmedge.matcher import load_aliases, match_event, shadow_team_match
from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt
from research_lab.portfolio import et_day
from research_lab.snapshot import FairQuote

BASE = "https://pinnwire.com"
DAILY_BUDGET = int(os.getenv("RESEARCH_LAB_PINNACLE_DAILY_BUDGET", "90"))   # of 100; leaves room for checks
MIN_REFETCH = timedelta(minutes=25)
MAX_FETCHES_PER_RUN = 3
# sport_id -> (name, Kalshi series to price, Pinnacle leagues to keep (empty = all), refresh minutes).
# The refresh schedule keeps every sport inside the free 100 requests/day (budget 90):
# football ~32/day, basketball ~24, hockey/baseball ~12 each, soccer ~8, tennis ~4.
SOCCER_SERIES = ["KXEPLGAME", "KXUCLGAME", "KXUELGAME", "KXLALIGAGAME", "KXSERIEAGAME", "KXBUNDESLIGAGAME",
                 "KXLIGUE1GAME", "KXMLSGAME"]
SPORTS = {
    5: ("football", ["KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"],
        {"NCAA", "NFL"}, 45),
    3: ("basketball", ["KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL"], {"NBA"}, 60),
    4: ("hockey", ["KXNHLGAME", "KXNHLSPREAD", "KXNHLTOTAL"], {"NHL"}, 120),
    6: ("baseball", ["KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL"], {"MLB"}, 120),
    1: ("soccer", SOCCER_SERIES, set(), 180),
    2: ("tennis", ["KXATPMATCH", "KXWTAMATCH"], set(), 360),
}
# Strategies frozen before more sports were added keep their original universe.
ORIGINAL_SPORTS = ("football", "basketball")
LAB_ALIASES = Path(__file__).parent / "pinnacle_aliases.csv"


class PinnacleError(RuntimeError):
    pass


def usage_path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / "pinnacle_usage.json"


def load_usage(now: datetime, base: Path | None = None) -> dict:
    p = usage_path(base)
    try:
        u = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        u = {}
    if u.get("day") != et_day(now):
        u = {"day": et_day(now), "requests": 0, "last_fetch": u.get("last_fetch", {})}
    u.setdefault("last_fetch", {})
    return u


def save_usage(u: dict, base: Path | None = None) -> None:
    _atomic_write_text(usage_path(base), json.dumps(u, indent=1, sort_keys=True))


class PinnWire:
    """GET-only client for PinnWire's prematch fixtures."""

    def __init__(self, key: str | None = None, session=None, timeout: int = 20):
        self.key = key if key is not None else os.getenv("PINNWIRE_KEY", "").strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    def fixtures(self, sport_id: int) -> dict:
        if not self.key:
            raise PinnacleError("PINNWIRE_KEY is not set")
        for attempt in range(2):
            r = self.session.get(f"{BASE}/kit/v1/prematch/fixtures", params={"sport_id": sport_id},
                                 headers={"x-api-key": self.key}, timeout=self.timeout)
            if r.status_code == 429 and attempt == 0:
                time.sleep(min(30, int(r.headers.get("Retry-After") or 5)))
                continue
            if r.status_code != 200:
                raise PinnacleError(f"PinnWire HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as exc:
                raise PinnacleError("PinnWire returned invalid JSON") from exc
        raise PinnacleError("PinnWire rate limited")


def fetch_due(client, now: datetime, base: Path | None = None) -> tuple[dict[int, dict], dict]:
    """Fetch the sports whose refresh time has come (at most MAX_FETCHES_PER_RUN), within the daily budget."""
    u = load_usage(now, base)
    out, info = {}, {"requests_today": u["requests"], "budget": DAILY_BUDGET, "fetched": [], "skipped": [],
                     "errors": []}
    if not getattr(client, "key", "x"):
        info["errors"].append("PINNWIRE_KEY is not set")
        return out, info
    for sid, spec in SPORTS.items():
        name, every = spec[0], timedelta(minutes=spec[3])
        last = parse_dt(u["last_fetch"].get(str(sid)))
        if last is not None and now - last < every:
            info["skipped"].append(f"{name}: next in {int((every - (now - last)).total_seconds() // 60)} min")
            continue
        if u["requests"] >= DAILY_BUDGET:
            info["skipped"].append(f"{name}: daily budget used")
            continue
        if len(info["fetched"]) + len(info["errors"]) >= MAX_FETCHES_PER_RUN:
            info["skipped"].append(f"{name}: next round")
            continue
        u["requests"] += 1
        try:
            out[sid] = client.fixtures(sid)
            u["last_fetch"][str(sid)] = iso(now)
            info["fetched"].append(name)
        except (PinnacleError, requests.RequestException) as exc:
            info["errors"].append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")
    info["requests_today"] = u["requests"]
    save_usage(u, base)
    return out, info


@dataclass
class PinEvent:
    id: str
    home: str
    away: str
    starts: datetime | None
    league: str
    full: dict          # periods.num_0
    generated_at: datetime | None

    def as_match_event(self) -> dict:
        return {"id": self.id, "home_team": self.home, "away_team": self.away, "commence_time": iso(self.starts)}


def events_from(payload: dict, leagues: set[str], now: datetime) -> list[PinEvent]:
    gen = parse_dt(payload.get("generated_at")) or now
    out = []
    for e in payload.get("events") or []:
        league = str(e.get("league_name") or "")
        if leagues and not any(league == x or league.startswith(x + " ") for x in leagues):
            continue
        periods = e.get("periods") or {}
        full = periods.get("num_0") or {}
        if not full or not e.get("home") or not e.get("away"):
            continue
        out.append(PinEvent(str(e.get("event_id") or ""), str(e["home"]), str(e["away"]),
                            parse_dt(e.get("starts") or e.get("start_ts")), league, full, gen))
    return out


def _f(v):
    try:
        x = float(v)
        return x if x > 1.0 else None
    except (TypeError, ValueError):
        return None


def two_way(a_price, b_price, max_overround: float) -> dict | None:
    """De-vig a two-way Pinnacle market three ways. Returns {method: P(first)} or None."""
    a, b = _f(a_price), _f(b_price)
    if a is None or b is None:
        return None
    if 1 / a + 1 / b > max_overround:
        return None
    outs = [{"name": "a", "price": a}, {"name": "b", "price": b}]
    return {"power": devig_power(outs)["a"], "proportional": devig_proportional(outs)["a"],
            "shin": devig_shin(outs)["a"]}


def three_way(ml: dict, side: str, max_overround: float) -> dict | None:
    """De-vig a home/draw/away moneyline; P(side wins in regulation) by method."""
    prices = {k: _f(ml.get(k)) for k in ("home", "draw", "away")}
    if any(v is None for v in prices.values()) or sum(1 / v for v in prices.values()) > max_overround:
        return None
    outs = [{"name": k, "price": v} for k, v in prices.items()]
    return {"power": devig_power(outs)[side], "proportional": devig_proportional(outs)[side],
            "shin": devig_shin(outs)[side]}


def _keyed(entries: dict, value: float, field: str) -> dict | None:
    for k, v in (entries or {}).items():
        try:
            num = float(v.get(field, k)) if isinstance(v, dict) else float(k)
        except (TypeError, ValueError):
            continue
        if abs(num - value) < 1e-6:
            return v
    return None


def which_team(selection: str, ev: PinEvent, aliases: dict) -> str | None:
    """'home' / 'away' when the selection matches exactly one team (strict token match)."""
    h, _ = shadow_team_match(selection, ev.home, aliases)
    a, _ = shadow_team_match(selection, ev.away, aliases)
    if h >= 0.90 and a < 0.90:
        return "home"
    if a >= 0.90 and h < 0.90:
        return "away"
    return None


def fair_for(spec, ev: PinEvent, aliases: dict, max_overround: float) -> tuple[dict | None, str]:
    """P(YES) by method for a parsed Kalshi market against a Pinnacle event, or (None, reason)."""
    full = ev.full
    if spec.market_type == "h2h":
        ml = full.get("money_line") or {}
        side = which_team(spec.selection or "", ev, aliases)
        if side is None:
            return None, "selection_not_matched"
        if ml.get("draw") is not None:
            p = three_way(ml, side, max_overround + 0.02)
            return (p, "ok") if p else (None, "no_moneyline")
        other = "away" if side == "home" else "home"
        p = two_way(ml.get(side), ml.get(other), max_overround)
        return (p, "ok") if p else (None, "no_moneyline")
    if spec.market_type == "spread":
        if spec.point is None:
            return None, "line_missing"
        side = which_team(spec.selection or "", ev, aliases)
        if side is None:
            return None, "selection_not_matched"
        home_hdp = spec.point if side == "home" else -spec.point
        entry = _keyed(full.get("spreads") or {}, home_hdp, "hdp")
        if not entry:
            return None, "pinnacle_line_not_offered"
        other = "away" if side == "home" else "home"
        p = two_way(entry.get(side), entry.get(other), max_overround)
        return (p, "ok") if p else (None, "no_spread_price")
    if spec.market_type == "total":
        if spec.point is None:
            return None, "line_missing"
        entry = _keyed(full.get("totals") or {}, spec.point, "points")
        if not entry:
            return None, "pinnacle_line_not_offered"
        p = two_way(entry.get("over"), entry.get("under"), max_overround)
        return (p, "ok") if p else (None, "no_total_price")
    return None, "unsupported_market_type"


def lab_aliases() -> dict:
    aliases = load_aliases()
    if LAB_ALIASES.exists():
        from pmedge.utils import normalize_text
        with LAB_ALIASES.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                a, c = normalize_text(row.get("alias", "")), normalize_text(row.get("canonical", ""))
                if a and c:
                    aliases[a] = c
    return aliases


def build_quotes(payloads: dict[int, dict], kalshi_events: dict[int, list[dict]], now: datetime,
                 max_overround: float = 1.10) -> tuple[list[FairQuote], dict[str, dict], dict]:
    """Match Kalshi markets to Pinnacle games. Returns (quotes, markets_by_ticker, info)."""
    aliases = lab_aliases()
    quotes, markets, reasons, samples = [], {}, {}, []

    def miss(reason: str, m: dict) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1
        if len(samples) < 8 and reason != "unsupported_market_type":
            samples.append(f"{reason}: {str(m.get('title'))[:70]}")
    for sid, payload in payloads.items():
        evs = events_from(payload, SPORTS[sid][2], now)
        pool = [e.as_match_event() for e in evs]
        by_id = {e.id: e for e in evs}
        for kev in kalshi_events.get(sid, []):
            for m in kev.get("markets") or []:
                if str(m.get("status") or "").lower() not in {"open", "active"}:
                    continue
                markets[m["ticker"]] = m
                spec = parse_market(m)
                if spec.market_type not in {"h2h", "spread", "total"}:
                    miss("unsupported_market_type", m)
                    continue
                em = match_event(spec, pool, aliases, m.get("occurrence_datetime"))
                if em.event is None:
                    miss("no_pinnacle_match", m)
                    continue
                ev = by_id[str(em.event["id"])]
                probs, why = fair_for(spec, ev, aliases, max_overround)
                if probs is None:
                    miss(why, m)
                    continue
                quotes.append(FairQuote(
                    ticker=m["ticker"], title=m.get("title") or m["ticker"], event_id=ev.id,
                    commence_time=ev.starts, sport_key=f"pinnacle_{SPORTS[sid][0]}", sport_title=ev.league,
                    market_type=spec.market_type, selection=spec.selection or "", point=spec.point,
                    fair_yes_power=probs["power"], fair_yes_prop=probs["proportional"], fair_yes_shin=probs["shin"],
                    books=1, dispersion=None, fair_at=ev.generated_at, evaluated_at=now, matcher_pass=True,
                    matcher_reason=em.reason))
    return quotes, markets, {"matched": len(quotes), "kalshi_markets": len(markets), "unmatched": reasons,
                             "pinnacle_games": sum(len(events_from(p, SPORTS[s][2], now)) for s, p in payloads.items()),
                             "samples": samples}
