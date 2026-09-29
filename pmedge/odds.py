from __future__ import annotations
from datetime import datetime, timezone
import os
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
        sent_at = datetime.now(timezone.utc)
        payload = None
        usage = {}
        status = None
        error = None
        try:
            r = self.session.get(self.BASE + path, params=p, timeout=self.timeout)
            status = r.status_code
            r.raise_for_status()
            payload = r.json()
            usage = {
                "requests_remaining": r.headers.get("x-requests-remaining"),
                "requests_used": r.headers.get("x-requests-used"),
                "requests_last": r.headers.get("x-requests-last"),
            }
            return payload, usage
        except (requests.RequestException, ValueError) as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                status = getattr(response, "status_code", status)
            body = getattr(response, "text", "") if response is not None else ""
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            raise OddsAPIError(f"Odds API request failed: {exc}; {body[:500]}") from exc
        finally:
            recorder_db = os.getenv("AUTOTRADER_RECORDER_DB", "").strip()
            if recorder_db:
                try:
                    from autotrader.odds_capture import record_odds_response
                    clean = dict(params or {})
                    sport = None
                    parts = [x for x in path.split("/") if x]
                    if len(parts) >= 2 and parts[0] == "sports":
                        sport = parts[1]
                    record_odds_response(
                        db_path=recorder_db,
                        endpoint=path,
                        sport=sport,
                        regions=clean.get("regions") or self.regions,
                        markets=clean.get("markets"),
                        sent_at=sent_at,
                        received_at=datetime.now(timezone.utc),
                        payload=payload,
                        usage=usage,
                        http_status=status,
                        error=error,
                    )
                except Exception:
                    pass

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
