"""Free weather shadow research using Open-Meteo ensemble guidance.

This module is intentionally RESEARCH ONLY. It never feeds the production
Value Edge engine and never creates a paper fill. Probabilities are empirical
ensemble-member frequencies under an explicit nearest-degree rounding
assumption and must be calibrated against settlement/CLV before promotion.
"""
from __future__ import annotations

import math
import os
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import requests

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<dd>\d{2})", re.I)
_RANGE_RE = re.compile(r"(-?\d+)\s*°?\s*(?:to|[-–])\s*(-?\d+)\s*°?", re.I)
_BELOW_RE = re.compile(r"(-?\d+)\s*°?\s*or\s*below", re.I)
_ABOVE_RE = re.compile(r"(-?\d+)\s*°?\s*or\s*above", re.I)


@dataclass(frozen=True)
class WeatherContract:
    ticker: str
    event_ticker: str
    city: str
    target_date: str
    variable: str
    bucket_label: str
    lower: int | None
    upper: int | None


def _city_from_title(title: str) -> str | None:
    title = str(title or "").strip()
    lower = title.lower()
    for phrase in ("highest temperature in ", "lowest temperature in ", "temperature in "):
        if phrase in lower:
            tail = title[lower.index(phrase) + len(phrase):]
            city = tail.split(" on ")[0].split(" today")[0].split("?")[0].strip()
            return city or None
    return None


def _date_from_event(event_ticker: str) -> str | None:
    m = _DATE_RE.search(str(event_ticker or "").upper())
    if not m:
        return None
    try:
        dt = datetime(
            2000 + int(m.group("yy")), _MONTHS[m.group("mon").upper()],
            int(m.group("dd")), tzinfo=timezone.utc
        )
        return dt.date().isoformat()
    except Exception:
        return None


def _bucket(label: str):
    label = str(label or "").strip()
    m = _RANGE_RE.search(label)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b)
    m = _BELOW_RE.search(label)
    if m:
        return None, int(m.group(1))
    m = _ABOVE_RE.search(label)
    if m:
        return int(m.group(1)), None
    return None


def parse_weather_contract(market: dict) -> WeatherContract | None:
    ticker = str(market.get("ticker") or "").strip()
    event = str(market.get("event_ticker") or "").strip()
    title = str(
        market.get("_weather_event_title")
        or market.get("title")
        or ""
    )
    city = _city_from_title(title)
    target_date = _date_from_event(event or ticker)
    label = str(market.get("yes_sub_title") or market.get("subtitle") or "").strip()
    bounds = _bucket(label)
    blob = f"{ticker} {title}".upper()
    if not ticker or not event or not city or not target_date or not bounds:
        return None
    if ticker.upper().startswith("KXHIGH") or "HIGHEST TEMPERATURE" in blob:
        variable = "temperature_2m_max"
    elif ticker.upper().startswith("KXLOW") or "LOWEST TEMPERATURE" in blob:
        variable = "temperature_2m_min"
    else:
        return None
    return WeatherContract(
        ticker=ticker, event_ticker=event, city=city, target_date=target_date,
        variable=variable, bucket_label=label, lower=bounds[0], upper=bounds[1],
    )


def _round_nearest_degree(value: float) -> int:
    # Explicit half-up assumption for research; settlement-source rounding may
    # differ and is a promotion blocker until validated per series.
    return math.floor(float(value) + 0.5)


def _matches(value: float, contract: WeatherContract) -> bool:
    rounded = _round_nearest_degree(value)
    if contract.lower is not None and rounded < contract.lower:
        return False
    if contract.upper is not None and rounded > contract.upper:
        return False
    return True


class OpenMeteoWeatherShadow:
    GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
    ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"

    def __init__(self, *, timeout=15, model=None):
        self.timeout = int(timeout)
        self.model = model or os.getenv("AUTOTRADER_WEATHER_ENSEMBLE_MODEL", "ncep_gefs_seamless")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PM-Edge-AutoTrader-Research/1.0"})
        self._geo_cache = {}

    def geocode(self, city: str):
        key = city.strip().lower()
        if key in self._geo_cache:
            return self._geo_cache[key]
        r = self.session.get(
            self.GEOCODE,
            params={"name": city, "count": 5, "language": "en", "format": "json", "countryCode": "US"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        results = [x for x in (r.json().get("results") or []) if isinstance(x, dict)]
        if not results:
            self._geo_cache[key] = None
            return None
        # Prefer exact case-insensitive city-name match, otherwise provider rank.
        exact = [x for x in results if str(x.get("name") or "").strip().lower() == key]
        row = (exact or results)[0]
        out = {
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "timezone": str(row.get("timezone") or "auto"),
            "resolved_name": str(row.get("name") or city),
            "admin1": str(row.get("admin1") or ""),
        }
        self._geo_cache[key] = out
        return out

    def ensemble_values(self, contract: WeatherContract, geo: dict):
        params = {
            "latitude": geo["latitude"],
            "longitude": geo["longitude"],
            "daily": contract.variable,
            "temperature_unit": "fahrenheit",
            "timezone": geo.get("timezone") or "auto",
            "start_date": contract.target_date,
            "end_date": contract.target_date,
        }
        if self.model:
            params["models"] = self.model
        r = self.session.get(self.ENSEMBLE, params=params, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        daily = payload.get("daily") or {}
        time_values = daily.get("time") or []
        if contract.target_date not in time_values:
            return [], payload
        idx = time_values.index(contract.target_date)
        values = []
        # Include control and all member columns if supplied.
        for key, arr in daily.items():
            if key == contract.variable or key.startswith(contract.variable + "_member"):
                if isinstance(arr, list) and len(arr) > idx and arr[idx] is not None:
                    try:
                        values.append(float(arr[idx]))
                    except (TypeError, ValueError):
                        pass
        return values, payload

    def price_group(self, markets: Iterable[dict]):
        contracts = [parse_weather_contract(m) for m in markets]
        contracts = [c for c in contracts if c is not None]
        if not contracts:
            return []
        geo = self.geocode(contracts[0].city)
        if not geo:
            return []
        values, payload = self.ensemble_values(contracts[0], geo)
        if len(values) < 5:
            return []
        mean = statistics.mean(values)
        stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
        out = []
        for c in contracts:
            hits = sum(1 for v in values if _matches(v, c))
            p = hits / len(values)
            # Sampling uncertainty only; model/calibration error is intentionally
            # NOT claimed solved and is recorded separately as a promotion blocker.
            sampling_sigma = math.sqrt(max(0.0, p * (1.0 - p) / len(values)))
            out.append({
                "ticker": c.ticker,
                "event_ticker": c.event_ticker,
                "city": c.city,
                "target_date": c.target_date,
                "bucket_label": c.bucket_label,
                "variable": c.variable,
                "p_shadow": p,
                "sampling_sigma": sampling_sigma,
                "n_members": len(values),
                "ensemble_mean_f": mean,
                "ensemble_stdev_f": stdev,
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "timezone": geo.get("timezone"),
                "model": self.model,
                "rounding_assumption": "nearest_integer_half_up",
                "promotion_blocker": "requires station/source mapping and empirical calibration",
            })
        return out
