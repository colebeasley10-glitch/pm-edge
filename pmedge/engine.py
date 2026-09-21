from __future__ import annotations

from .fair_value import FairValue, blend_fair_value, fair_from_event, load_manual_values, load_model_overlay
from .kalshi import KalshiPublicClient, normalize_market, tick_size_for_price
from .matcher import load_aliases, match_event
from .market_parse import parse_market
from .odds import OddsAPIClient, OddsAPIError
from .pricing import best_binary_trade
from .risk import size_trade
from .storage import Store

MARKET_KEYS = {
    "h2h": ["h2h"],
    "spread": ["spreads", "alternate_spreads"],
    "total": ["totals", "alternate_totals"],
    "team_total": ["team_totals", "alternate_team_totals"],
}

def _market_request_keys(market_type: str, fetch_alternates: bool) -> str:
    keys = MARKET_KEYS.get(market_type, [])
    if not fetch_alternates and len(keys) > 1:
        keys = keys[:1]
    return ",".join(keys)

class PMEdgeEngine:
    def __init__(self, settings):
        self.s = settings
        self.kalshi = KalshiPublicClient(settings.kalshi_base_url)
        self.store = Store(settings.db_path)
        self.aliases = load_aliases()
        self.overlay = load_model_overlay()
        self.manual = load_manual_values()
        self.odds = OddsAPIClient(settings.odds_api_key, settings.odds_regions) if settings.odds_api_key else None
        self.last_usage = []
        self.discovery_stats = {}

    def _selected_sports(self):
        if not self.odds:
            return []
        explicit = [x for x in self.s.odds_sports if x.upper() != "AUTO"]
        if explicit:
            return [{"key": x, "title": x, "group": "", "active": True, "has_outrights": False} for x in explicit]

        sports, usage = self.odds.active_sports()
        self.last_usage.append({"endpoint": "sports", **usage})
        if self.s.odds_skip_outright_only:
            sports = [s for s in sports if not bool(s.get("has_outrights"))]
        return sports

    def _load_external_events(self) -> list[dict]:
        if not self.odds:
            self.discovery_stats = {"sports": 0, "events": 0}
            return []

        events = []
        sports = self._selected_sports()
        for sport in sports:
            key = sport.get("key")
            if not key:
                continue
            try:
                rows, usage = self.odds.events(key)
                # The provider currently documents this endpoint as zero usage cost,
                # but retain headers so the UI can audit actual usage.
                self.last_usage.append({"endpoint": "events", "sport": key, **usage})
            except OddsAPIError:
                continue
            for e in rows:
                e = dict(e)
                e["_sport_key"] = key
                e["_sport_title"] = sport.get("title") or key
                e["_sport_group"] = sport.get("group") or ""
                e["_has_outrights"] = bool(sport.get("has_outrights"))
                events.append(e)

        self.discovery_stats = {"sports": len(sports), "events": len(events)}
        return events

    def _sportsbook_event_with_odds(self, spec, event):
        if not self.odds or not event:
            return None
        markets = _market_request_keys(spec.market_type, self.s.odds_fetch_alternates)
        if not markets:
            return None
        try:
            data, usage = self.odds.event_odds(event["_sport_key"], event["id"], markets)
            self.last_usage.append({
                "endpoint": "event_odds",
                "sport": event["_sport_key"],
                "event_id": event["id"],
                "markets": markets,
                **usage,
            })
            if isinstance(data, dict):
                data = dict(data)
                data["_sport_key"] = event["_sport_key"]
                data["_sport_title"] = event.get("_sport_title", event["_sport_key"])
                data["_sport_group"] = event.get("_sport_group", "")
            return data
        except OddsAPIError:
            return None

    def _fair_value_for(self, market: dict, spec, event_match) -> FairValue | None:
        manual = self.manual.get(market["ticker"])
        if manual:
            return FairValue(
                manual["fair_yes_prob"], manual["source"], manual["confidence"],
                0, 0.0, None, None, manual.get("notes", "manual override"),
            )

        if not event_match.event:
            ov = self.overlay.get(market["ticker"])
            if ov:
                return FairValue(
                    ov["model_prob"], ov["source"], 0.55, 0, 0.0,
                    None, ov["model_prob"], "model-only; no sportsbook match",
                )
            return None

        odds_event = self._sportsbook_event_with_odds(spec, event_match.event)
        consensus = None
        if odds_event:
            consensus = fair_from_event(
                spec, odds_event, self.aliases,
                min_books=self.s.odds_min_books,
                max_dispersion=self.s.odds_max_dispersion,
            )
        return blend_fair_value(market["ticker"], consensus, self.overlay)

    def scan(self, create_paper_orders: bool = True) -> list[dict]:
        raw_markets = self.kalshi.list_open_markets(self.s.max_markets)
        markets = [normalize_market(m) for m in raw_markets]
        external_events = self._load_external_events()
        rows = []

        for market in markets:
            if market["volume_24h"] < self.s.min_volume_24h:
                continue
            spec = parse_market(market)
            if spec.market_type == "unsupported":
                continue

            if external_events:
                event_match = match_event(
                    spec, external_events, self.aliases,
                    market.get("occurrence_datetime") or market.get("close_time"),
                )
            else:
                event_match = type("EM", (), {
                    "event": None, "confidence": 0.0, "reason": "no odds API"
                })()

            if (
                event_match.event is not None
                and event_match.confidence < self.s.min_match_confidence
                and market["ticker"] not in self.manual
                and market["ticker"] not in self.overlay
            ):
                continue

            fair = self._fair_value_for(market, spec, event_match)
            if not fair:
                continue

            tick = tick_size_for_price(market, market["yes_bid"] or market["yes_ask"] or 0.50)
            q = best_binary_trade(
                fair_yes_prob=fair.fair_yes_prob,
                yes_bid=market["yes_bid"], yes_ask=market["yes_ask"],
                no_bid=market["no_bid"], no_ask=market["no_ask"],
                min_net_edge=self.s.min_net_edge,
                nonfee_buffer=self.s.nonfee_buffer,
                maker_fee_rate=self.s.maker_fee_rate,
                taker_fee_rate=self.s.taker_fee_rate,
                tick_size=tick,
            )
            if not q.qualifies or q.recommended_limit is None:
                continue

            sizing = size_trade(
                fair_prob=q.fair_prob, price=q.recommended_limit,
                fee_per_contract=q.expected_fee_per_contract,
                bankroll=self.s.bankroll_dollars,
                kelly_fraction=self.s.kelly_fraction,
                max_trade_pct=self.s.max_trade_pct,
                max_contracts=self.s.max_contracts,
                min_contracts=self.s.min_contracts,
            )
            if sizing.contracts <= 0:
                continue

            if self.s.mode != "live" and self.store.has_active_paper_order(market["ticker"], q.side):
                continue
            if self.s.mode == "live" and self.store.has_recent_signal(market["ticker"], q.side, minutes=30):
                continue

            day_remaining = self.s.bankroll_dollars * self.s.max_daily_pct - self.store.risk_cost_today()
            event_remaining = self.s.bankroll_dollars * self.s.max_event_pct - self.store.risk_cost_event(market["event_ticker"])
            if sizing.cost_dollars > max(0.0, day_remaining) + 1e-9:
                continue
            if sizing.cost_dollars > max(0.0, event_remaining) + 1e-9:
                continue

            sport = "Manual"
            if event_match.event:
                sport = event_match.event.get("_sport_title") or event_match.event.get("_sport_key") or "Sports"

            row = {
                "ticker": market["ticker"],
                "event_ticker": market["event_ticker"],
                "title": market["title"],
                "sport": sport,
                "market_type": spec.market_type,
                "selection": spec.selection,
                "point": spec.point,
                "fair_yes_prob": fair.fair_yes_prob,
                "fair_source": fair.source,
                "fair_confidence": fair.confidence,
                "match_confidence": event_match.confidence,
                "side": q.side,
                "execution": q.execution,
                "entry_price": q.recommended_limit,
                "hard_max": q.hard_max,
                "net_edge": q.net_edge,
                "contracts": sizing.contracts,
                "est_cost": sizing.cost_dollars,
                "fee_per_contract": q.expected_fee_per_contract,
                "best_bid": q.best_bid,
                "best_ask": q.best_ask,
                "spread": q.spread,
                "volume_24h": market["volume_24h"],
                "n_books": fair.n_books,
                "book_dispersion": fair.dispersion,
                "raw_book_prob": fair.raw_book_prob,
                "model_prob": fair.model_prob,
                "match_reason": event_match.reason,
                "fair_detail": fair.detail,
                "parse_reason": spec.reason,
            }
            signal_id = self.store.add_signal({
                **row,
                "extra_json": {
                    "sport": sport,
                    "best_bid": q.best_bid, "best_ask": q.best_ask,
                    "spread": q.spread, "n_books": fair.n_books,
                    "book_dispersion": fair.dispersion,
                    "raw_book_prob": fair.raw_book_prob,
                    "model_prob": fair.model_prob,
                    "match_reason": event_match.reason,
                    "fair_detail": fair.detail,
                    "parse_reason": spec.reason,
                },
            })
            row["signal_id"] = signal_id

            if create_paper_orders and self.s.mode != "live":
                self.store.add_paper_order(signal_id, row)
            rows.append(row)

        # Best picks first: estimated net edge, then fair-value quality and liquidity.
        rows.sort(
            key=lambda r: (r["net_edge"], r["fair_confidence"], r["n_books"], r["volume_24h"]),
            reverse=True,
        )
        return rows
