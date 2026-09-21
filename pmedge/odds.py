from __future__ import annotations
import requests

class OddsAPIError(RuntimeError):
    pass

class OddsAPIClient:
    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str, regions: str = "us", timeout: int = 20):
        if not api_key:
            raise OddsAPIError("ODDS_API_KEY is required for automatic sportsbook fair values")
        self.api_key = api_key
        self.regions = regions
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PM-Edge-Easy/3.0"})
        self._event_cache = {}
        self._events_cache = {}
        self._sports_cache = None

    def _get(self, path: str, params: dict | None = None):
        p = dict(params or {})
        p["apiKey"] = self.api_key
        try:
            r = self.session.get(self.BASE + path, params=p, timeout=self.timeout)
            r.raise_for_status()
            return r.json(), {
                "requests_remaining": r.headers.get("x-requests-remaining"),
                "requests_used": r.headers.get("x-requests-used"),
                "requests_last": r.headers.get("x-requests-last"),
            }
        except (requests.RequestException, ValueError) as exc:
            response = getattr(exc, "response", None)
            body = getattr(response, "text", "") if response is not None else ""
            raise OddsAPIError(f"Odds API request failed: {exc}; {body[:500]}") from exc

    def active_sports(self):
        if self._sports_cache is not None:
            return self._sports_cache
        data, usage = self._get("/sports")
        sports = [s for s in data if bool(s.get("active", True))]
        self._sports_cache = (sports, usage)
        return self._sports_cache

    def events(self, sport: str):
        if sport in self._events_cache:
            return self._events_cache[sport]
        data, usage = self._get(
            f"/sports/{sport}/events",
            {"dateFormat": "iso"},
        )
        self._events_cache[sport] = (data, usage)
        return data, usage

    def event_odds(self, sport: str, event_id: str, markets: str):
        # Sort market keys so equivalent requests share cache.
        mk = ",".join(sorted(x.strip() for x in markets.split(",") if x.strip()))
        cache_key = (sport, event_id, mk)
        if cache_key in self._event_cache:
            return self._event_cache[cache_key]
        data, usage = self._get(
            f"/sports/{sport}/events/{event_id}/odds",
            {
                "regions": self.regions,
                "markets": mk,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )
        self._event_cache[cache_key] = (data, usage)
        return data, usage
