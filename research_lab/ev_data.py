"""+EV Finder data: every price we can get for a game's moneyline, next to Pinnacle's fair line.

Venues (all read-only, all free):
* Kalshi       - live asks from the public API (already fetched for the Pinnacle strategies)
* Polymarket   - live order-book asks from Polymarket's public Gamma API
* Sportsbooks  - DraftKings, Bovada, Fliff, Novig, ProphetX via The Odds API. Asking for named
                 books (not whole regions) makes one sport's moneylines cost 1 credit, so the
                 lab takes a few snapshots a day and never touches more than its own small
                 budget (and stops if the account is nearly empty, to leave credits for the scanner).

Moneylines only in v1: they can be settled from Kalshi's official game results.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests

from league.strategies.polymarket import _list, fetch_poly_events, halves
from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt
from research_lab.pinnacle import PinEvent, lab_aliases, two_way, which_team
from research_lab.portfolio import et_day

ODDS_BASE = "https://api.the-odds-api.com/v4"
# Odds API sport key -> PinnWire sport id, and Pinnacle leagues that sport covers
ODDS_SPORTS = {"americanfootball_ncaaf": (5, "NCAA"), "americanfootball_nfl": (5, "NFL"),
               "basketball_nba": (3, "NBA")}
BOOKS = {"draftkings": "DraftKings", "bovada": "Bovada", "fliff": "Fliff", "novig": "Novig",
         "prophetx": "ProphetX"}
DAILY_CREDITS = int(os.getenv("RESEARCH_LAB_ODDS_DAILY_CREDITS", "6"))
MIN_ACCOUNT_REMAINING = int(os.getenv("RESEARCH_LAB_ODDS_MIN_REMAINING", "60"))
SNAPSHOT_EVERY = timedelta(hours=float(os.getenv("RESEARCH_LAB_ODDS_EVERY_HOURS", "4")))


def american(decimal_odds: float) -> str:
    if decimal_odds >= 2.0:
        return f"+{round((decimal_odds - 1) * 100):d}"
    return f"{round(-100 / (decimal_odds - 1)):d}"


@dataclass
class Offer:
    venue: str
    side: str                  # "home" / "away" of the Pinnacle event
    decimal: float             # what $1 returns if it wins, after the venue's fees
    shown: str                 # the price as the venue shows it (e.g. "-110", "54¢")
    at: datetime | None
    kalshi_ticker: str = ""    # set for Kalshi offers


@dataclass
class Game:
    event: PinEvent
    sport: str
    fair: dict                 # {"home": p, "away": p} (power de-vig)
    offers: list = field(default_factory=list)
    settle: dict = field(default_factory=dict)   # side -> Kalshi game ticker (official result)

    @property
    def key(self) -> str:
        return self.event.id

    def title(self) -> str:
        return f"{self.event.away} at {self.event.home}"

    def team(self, side: str) -> str:
        return self.event.home if side == "home" else self.event.away


def games_from_pinnacle(events_by_sport: dict[int, list[PinEvent]], now: datetime, max_overround: float) -> list[Game]:
    out = []
    for sid, evs in events_by_sport.items():
        for ev in evs:
            if ev.starts is None or ev.starts <= now + timedelta(minutes=5):
                continue
            ml = ev.full.get("money_line") or {}
            if ml.get("draw") is not None:
                continue
            p = two_way(ml.get("home"), ml.get("away"), max_overround)
            if not p:
                continue
            out.append(Game(ev, "football" if sid == 5 else "basketball",
                            {"home": p["power"], "away": 1.0 - p["power"]}))
    return out


def _match(games: list[Game], a: str, b: str, start: datetime | None, aliases: dict,
           hours: float = 3.0) -> tuple[Game, dict] | None:
    """Find the game with both teams (either order) and start within `hours`. Returns (game, name->side)."""
    hits = []
    for g in games:
        if start is not None and g.event.starts is not None and abs((g.event.starts - start).total_seconds()) > hours * 3600:
            continue
        sa, sb = which_team(a, g.event, aliases), which_team(b, g.event, aliases)
        if sa and sb and sa != sb:
            hits.append((g, {a: sa, b: sb}))
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ Kalshi

def add_kalshi(games: list[Game], pinnacle_quotes: list, markets: dict, now: datetime, fee_fn) -> int:
    """Kalshi h2h asks (already priced against Pinnacle) as offers, plus settlement tickers."""
    by_id = {g.key: g for g in games}
    aliases, n = lab_aliases(), 0
    for q in pinnacle_quotes:
        if q.market_type != "h2h" or q.event_id not in by_id:
            continue
        g = by_id[q.event_id]
        side = which_team(q.selection, g.event, aliases)
        if side is None:
            continue
        g.settle.setdefault(side, q.ticker)
        m = markets.get(q.ticker) or {}
        ask = m.get("yes_ask")
        if ask is None or not (0.02 < float(ask) < 0.98):
            continue
        ask = float(ask)
        cost = ask + fee_fn(ask, 10) / 10
        g.offers.append(Offer("Kalshi", side, 1.0 / cost, f"{ask * 100:.0f}¢", now, q.ticker))
        n += 1
    return n


# ------------------------------------------------------------------ Polymarket

def add_polymarket(games: list[Game], raw_events: list[dict], now: datetime, fee_rate: float = 0.0) -> int:
    """Polymarket moneyline asks. outcome[0] ask = bestAsk; outcome[1] ask = 1 - bestBid."""
    aliases, n = lab_aliases(), 0
    for e in raw_events:
        for m in e.get("markets") or []:
            if m.get("sportsMarketType") != "moneyline":
                continue
            outs = [str(x) for x in _list(m.get("outcomes"))]
            if len(outs) != 2 or outs[0].lower() in {"yes", "no"}:
                continue
            try:
                ask0, bid0 = float(m.get("bestAsk")), float(m.get("bestBid"))
            except (TypeError, ValueError):
                continue
            if not (0 < bid0 <= ask0 < 1) or ask0 - bid0 > 0.05:
                continue
            start = parse_dt(str(m.get("gameStartTime") or "").replace(" ", "T").replace("+00", "+00:00"))
            hit = _match(games, outs[0], outs[1], start, aliases)
            if not hit:
                continue
            g, sides = hit
            for name, ask in ((outs[0], ask0), (outs[1], 1.0 - bid0)):
                cost = ask * (1 + fee_rate)
                if 0.02 < ask < 0.98:
                    g.offers.append(Offer("Polymarket", sides[name], 1.0 / cost, f"{ask * 100:.0f}¢", now))
                    n += 1
            break
    return n


def fetch_polymarket(now: datetime, session=None) -> list[dict]:
    return fetch_poly_events(session or requests.Session(), now)


# ------------------------------------------------------------------ sportsbooks (The Odds API)

def odds_usage_path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / "oddsapi_usage.json"


def _load_usage(now: datetime, base: Path | None) -> dict:
    p = odds_usage_path(base)
    try:
        u = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        u = {}
    if u.get("day") != et_day(now):
        # "remaining" is re-learned each day from the first response, so the lab notices when the
        # monthly credits reset (a call on an empty account is refused and costs nothing).
        u = {"day": et_day(now), "credits": 0, "last_snapshot": u.get("last_snapshot"),
             "remaining": None, "snapshot": u.get("snapshot")}
    return u


def book_snapshot(now: datetime, base: Path | None = None, session=None, key: str | None = None) -> tuple[dict, dict]:
    """Return ({sport_key: odds_events}, info). Uses the cached snapshot unless a new one is due and affordable."""
    key = key if key is not None else os.getenv("ODDS_API_KEY", "").strip()
    u = _load_usage(now, base)
    info = {"credits_today": u["credits"], "daily_credits": DAILY_CREDITS, "remaining": u.get("remaining"),
            "taken_at": u.get("last_snapshot"), "fresh": False, "errors": []}
    last = parse_dt(u.get("last_snapshot"))
    due = last is None or now - last >= SNAPSHOT_EVERY
    affordable = u["credits"] + len(ODDS_SPORTS) <= DAILY_CREDITS and \
        (u.get("remaining") is None or int(float(u["remaining"])) >= MIN_ACCOUNT_REMAINING)
    if key and due and affordable:
        s = session or requests.Session()
        snap = {}
        for sport in ODDS_SPORTS:
            try:
                r = s.get(f"{ODDS_BASE}/sports/{sport}/odds", timeout=20, params={
                    "apiKey": key, "bookmakers": ",".join(BOOKS), "markets": "h2h", "oddsFormat": "decimal",
                    "dateFormat": "iso"})
                u["credits"] += int(float(r.headers.get("x-requests-last") or 1))
                if r.headers.get("x-requests-remaining") is not None:
                    u["remaining"] = r.headers.get("x-requests-remaining")
                if r.status_code != 200:
                    info["errors"].append(f"{sport}: HTTP {r.status_code}")
                    continue
                snap[sport] = r.json()
            except (requests.RequestException, ValueError) as exc:
                info["errors"].append(f"{sport}: {type(exc).__name__}")
        if snap:
            u["snapshot"], u["last_snapshot"] = snap, iso(now)
            info["fresh"], info["taken_at"] = True, u["last_snapshot"]
        info.update({"credits_today": u["credits"], "remaining": u.get("remaining")})
    elif not key:
        info["errors"].append("ODDS_API_KEY not set")
    elif not affordable:
        info["skipped"] = "lab odds budget used or account nearly empty"
    _atomic_write_text(odds_usage_path(base), json.dumps(u, sort_keys=True))
    return u.get("snapshot") or {}, info


def add_books(games: list[Game], snapshot: dict, taken_at: datetime | None) -> int:
    aliases, n = lab_aliases(), 0
    for sport, events in snapshot.items():
        for e in events or []:
            hit = _match(games, str(e.get("home_team") or ""), str(e.get("away_team") or ""),
                         parse_dt(e.get("commence_time")), aliases)
            if not hit:
                continue
            g, sides = hit
            for bk in e.get("bookmakers") or []:
                name = BOOKS.get(bk.get("key"))
                if not name:
                    continue
                at = parse_dt(bk.get("last_update")) or taken_at
                for mk in bk.get("markets") or []:
                    if mk.get("key") != "h2h":
                        continue
                    for o in mk.get("outcomes") or []:
                        side = sides.get(o.get("name"))
                        try:
                            d = float(o.get("price"))
                        except (TypeError, ValueError):
                            continue
                        if side and d > 1.0:
                            g.offers.append(Offer(name, side, d, american(d), at))
                            n += 1
    return n


def board_rows(games: list[Game]) -> list[dict]:
    """Compact per-game, per-side view for the dashboard (fair vs best price everywhere)."""
    rows = []
    for g in games:
        for side in ("home", "away"):
            offers = sorted((o for o in g.offers if o.side == side), key=lambda o: -o.decimal)
            if not offers:
                continue
            fair = g.fair[side]
            rows.append({"game": g.title(), "sport": g.event.league, "starts": iso(g.event.starts), "team": g.team(side),
                         "fair": round(fair, 4), "fair_odds": american(1 / fair),
                         "kalshi_ticker": g.settle.get(side, ""),
                         "prices": [{"venue": o.venue, "shown": o.shown, "decimal": round(o.decimal, 4),
                                     "ev": round(fair * o.decimal - 1, 4), "at": iso(o.at)} for o in offers]})
    return rows
