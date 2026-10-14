"""Weather: price Kalshi daily high/low temperature brackets from free forecasts.

Kalshi settles these on the NWS climate report (CLIxxx) for one station. We
forecast that station's max/min with several free Open-Meteo models, use the
model spread plus a lead-time error floor as uncertainty, and, on the day
itself, fold in what the station has already observed (NWS observations): a
high can't finish below the max already recorded.

Bracket probability: integer readings, so bucket [a, b] covers (a-0.5, b+0.5).
"""
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

import requests

from league.book import Pick, f

NAME = "weather"
DIAG: dict = {}
MIN_EDGE = 0.06          # forecasts are noisy; demand more than other lanes
MAX_EDGE = 0.15          # bigger gaps mean the market knows something the models don't
MAX_SIGMA = 3.2          # skip days the models can't agree on
MAX_PICKS = 8
MAX_PER_EVENT = 1
# YES on a narrow "between" bracket (e.g. 64° to 65°) is off: the forecast's ±2-3°F spread
# is wider than the bracket, so these were cheap longshots that lost the full stake
# (0-4, -$42 through Oct 13, 2026). NO on brackets and both sides of the tails still run.
# Set LEAGUE_WEATHER_YES_BRACKETS=1 to turn them back on.


def yes_brackets_enabled() -> bool:
    return os.environ.get("LEAGUE_WEATHER_YES_BRACKETS", "0").strip().lower() in ("1", "true", "yes")


def closing_cutoff(event_ticker: str) -> datetime | None:
    """When the weather 'closing line' is taken: midnight Eastern standard time (05:00 UTC)
    on the event day, which is before any US station's climate day begins. Marks after that
    track the observed temperature, so they say who won, not whether the price was good."""
    day = event_date(event_ticker)
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, 5, tzinfo=timezone.utc)


def same_day_enabled() -> bool:
    """Same-day markets are off by default.

    NWS hourly observations understate the climate-report max/min that Kalshi
    settles on (Oct 12, 2026: KAUS obs showed 91°F while Kalshi had already
    priced the 92-93° bucket at $1.00), so same-day picks were built on a bad
    "already observed" reading. Set LEAGUE_WEATHER_SAME_DAY=1 to turn them back
    on once the observation source matches the CLI report.
    """
    return os.environ.get("LEAGUE_WEATHER_SAME_DAY", "0").strip().lower() in ("1", "true", "yes")

# CLI climate-report site -> (ICAO station, lat, lon, IANA tz)
STATIONS = {
    "CLINYC": ("KNYC", 40.7794, -73.9692, "America/New_York"),
    "CLIMDW": ("KMDW", 41.7868, -87.7522, "America/Chicago"),
    "CLIMIA": ("KMIA", 25.7959, -80.2870, "America/New_York"),
    "CLIAUS": ("KAUS", 30.1945, -97.6699, "America/Chicago"),
    "CLILAX": ("KLAX", 33.9382, -118.3866, "America/Los_Angeles"),
    "CLIDEN": ("KDEN", 39.8466, -104.6562, "America/Denver"),
    "CLIPHL": ("KPHL", 39.8733, -75.2268, "America/New_York"),
    "CLIATL": ("KATL", 33.6301, -84.4418, "America/New_York"),
    "CLIBOS": ("KBOS", 42.3606, -71.0097, "America/New_York"),
    "CLIDFW": ("KDFW", 32.8998, -97.0403, "America/Chicago"),
    "CLIDCA": ("KDCA", 38.8483, -77.0341, "America/New_York"),
    "CLISEA": ("KSEA", 47.4447, -122.3144, "America/Los_Angeles"),
    "CLISFO": ("KSFO", 37.6197, -122.3647, "America/Los_Angeles"),
    "CLIPHX": ("KPHX", 33.4342, -112.0116, "America/Phoenix"),
    "CLILAS": ("KLAS", 36.0719, -115.1634, "America/Los_Angeles"),
    "CLIMSP": ("KMSP", 44.8848, -93.2223, "America/Chicago"),
    "CLIMSY": ("KMSY", 29.9934, -90.2580, "America/Chicago"),
    "CLIOKC": ("KOKC", 35.3931, -97.6007, "America/Chicago"),
    "CLISAN": ("KSAN", 32.7336, -117.1831, "America/Los_Angeles"),
    "CLISAT": ("KSAT", 29.5443, -98.4839, "America/Chicago"),
    "CLIHOU": ("KHOU", 29.6375, -95.2825, "America/Chicago"),
    "CLIEWR": ("KEWR", 40.6825, -74.1694, "America/New_York"),
}
MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless,gem_seamless,ncep_hrrr_conus"
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")
_STATION_RE = re.compile(r"\((CLI[A-Z]{3})\)")


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def event_date(event_ticker: str):
    m = _DATE_RE.search(str(event_ticker or "").upper())
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))).date()
    except ValueError:
        return None


def variable_for(series: str) -> str | None:
    s = series.upper()
    if s.startswith("KXHIGH"):
        return "max"
    if s.startswith("KXLOW"):
        return "min"
    return None


def bucket(market: dict) -> tuple[float | None, float | None] | None:
    """Return (lo, hi) bounds on the continuous temperature for YES."""
    st = str(market.get("strike_type") or "").lower()
    lo, hi = f(market.get("floor_strike")), f(market.get("cap_strike"))
    if st == "less" and hi is not None:          # reading <= hi-1
        return None, hi - 0.5
    if st == "greater" and lo is not None:       # reading >= lo+1
        return lo + 0.5, None
    if st == "between" and lo is not None and hi is not None:
        return lo - 0.5, hi + 0.5
    return None


def bucket_prob(lo, hi, mu: float, sigma: float, observed: float | None = None, kind: str = "max") -> float:
    """P(final reading in (lo, hi)).

    The day's final high is max(already observed, rest-of-day model value);
    the final low is min(observed, model). Without observations it's just the
    normal bracket probability around the forecast.
    """
    def cdf(x):
        return 0.0 if x is None else _phi((x - mu) / sigma)

    c_hi = 1.0 if hi is None else cdf(hi)
    c_lo = cdf(lo)
    if observed is None:
        return max(0.0, min(1.0, c_hi - c_lo))
    if kind == "max":
        if hi is not None and observed >= hi:
            return 0.0
        if lo is None or observed >= lo:      # already inside: stays unless it climbs past hi
            return c_hi
        return max(0.0, c_hi - c_lo)
    if lo is not None and observed <= lo:
        return 0.0
    if hi is None or observed < hi:           # already inside: stays unless it drops below lo
        return 1.0 - c_lo
    return max(0.0, c_hi - c_lo)


def lead_sigma(lead_days: int) -> float:
    return {0: 1.6, 1: 2.3, 2: 2.9}.get(lead_days, 3.5)


class Weather:
    def __init__(self, session: requests.Session | None = None, timeout: int = 20):
        self.s = session or requests.Session()
        self.s.headers.setdefault("User-Agent", "pm-edge-league (github.com/colebeasley10-glitch/pm-edge)")
        self.timeout = timeout
        self._fc: dict = {}

    def forecast(self, station: str, day, kind: str) -> tuple[float, float] | None:
        icao, lat, lon, tz = STATIONS[station]
        key = (station, kind)
        if key not in self._fc:
            var = "temperature_2m_max" if kind == "max" else "temperature_2m_min"
            r = self.s.get("https://api.open-meteo.com/v1/forecast", timeout=self.timeout, params={
                "latitude": lat, "longitude": lon, "daily": var, "temperature_unit": "fahrenheit",
                "timezone": tz, "forecast_days": 4, "models": MODELS})
            r.raise_for_status()
            self._fc[key] = (var, r.json().get("daily") or {})
        var, daily = self._fc[key]
        times = daily.get("time") or []
        if day.isoformat() not in times:
            return None
        i = times.index(day.isoformat())
        vals = [v[i] for k, v in daily.items() if k.startswith(var) and isinstance(v, list)
                and len(v) > i and v[i] is not None]
        if len(vals) < 2:
            return None
        return mean(vals), pstdev(vals)

    @staticmethod
    def climate_window(station: str, day, now: datetime) -> tuple[datetime, datetime]:
        """The NWS climate day runs midnight to midnight local STANDARD time."""
        zone = ZoneInfo(STATIONS[station][3])
        local_now = now.astimezone(zone)
        std_offset = local_now.utcoffset() - (local_now.dst() or timedelta(0))
        start = datetime(day.year, day.month, day.day, tzinfo=timezone(std_offset)).astimezone(timezone.utc)
        return start, start + timedelta(hours=24)

    def remaining_extreme(self, station: str, day, kind: str, now: datetime) -> tuple[float, float, int] | None:
        """Per-model max/min over the hours left in the climate day: (mean, spread, hours left)."""
        _, lat, lon, _ = STATIONS[station]
        key = (station, "hourly")
        if key not in self._fc:
            r = self.s.get("https://api.open-meteo.com/v1/forecast", timeout=self.timeout, params={
                "latitude": lat, "longitude": lon, "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
                "timezone": "GMT", "past_days": 1, "forecast_days": 2, "models": MODELS})
            r.raise_for_status()
            self._fc[key] = r.json().get("hourly") or {}
        hourly = self._fc[key]
        start, end = self.climate_window(station, day, now)
        times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly.get("time") or []]
        idx = [i for i, t in enumerate(times) if max(now, start) <= t < end]
        if not idx:
            return None
        per_model = []
        for k, arr in hourly.items():
            if not k.startswith("temperature_2m") or not isinstance(arr, list):
                continue
            vals = [arr[i] for i in idx if i < len(arr) and arr[i] is not None]
            if len(vals) >= max(1, len(idx) // 2):
                per_model.append(max(vals) if kind == "max" else min(vals))
        if len(per_model) < 2:
            return None
        return mean(per_model), pstdev(per_model), len(idx)

    def observed_extreme(self, station: str, day, kind: str, now: datetime) -> float | None:
        """Max (or min) observed so far on the climate day (local standard time)."""
        icao = STATIONS[station][0]
        start, _ = self.climate_window(station, day, now)
        if now < start:
            return None
        r = self.s.get(f"https://api.weather.gov/stations/{icao}/observations", timeout=self.timeout,
                       params={"start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")},
                       headers={"Accept": "application/geo+json"})
        r.raise_for_status()
        temps = []
        for feat in r.json().get("features") or []:
            t = ((feat.get("properties") or {}).get("temperature") or {}).get("value")
            if t is not None:
                temps.append(t * 9.0 / 5.0 + 32.0)
        if not temps:
            return None
        return max(temps) if kind == "max" else min(temps)

    def price_event(self, event: dict, now: datetime) -> list[tuple[dict, float, str]]:
        ms = event.get("markets") or []
        if not ms:
            return []
        rules = " ".join(str(m.get("rules_primary") or "") for m in ms[:1])
        st = _STATION_RE.search(rules)
        kind = variable_for(event.get("series") or "")
        day = event_date(event.get("event_ticker") or "")
        if not st or st.group(1) not in STATIONS or not kind or not day:
            return []
        station = st.group(1)
        tz = ZoneInfo(STATIONS[station][3])
        lead = (day - now.astimezone(tz).date()).days
        if lead < 0 or lead > 2:
            return []
        if lead == 0 and not same_day_enabled():
            DIAG["skipped_same_day"] = DIAG.get("skipped_same_day", 0) + 1
            return []
        fc = self.forecast(station, day, kind)
        if not fc:
            return []
        mu, spread = fc
        sigma = math.sqrt(spread ** 2 + lead_sigma(lead) ** 2)
        obs = None
        if lead == 0:
            # Same day: what's already happened + what the models say for the hours left.
            try:
                obs = self.observed_extreme(station, day, kind, now)
            except Exception:
                obs = None
            try:
                rem = self.remaining_extreme(station, day, kind, now)
            except Exception:
                rem = None
            if rem:
                mu, spread, hours_left = rem
                sigma = math.sqrt(spread ** 2 + 1.3 ** 2)
            elif obs is not None:
                mu, sigma = obs, 0.8   # day is essentially over
        if sigma > MAX_SIGMA:
            return []
        out = []
        for m in ms:
            b = bucket(m)
            if not b:
                continue
            p = bucket_prob(b[0], b[1], mu, sigma, obs, kind)
            p = min(0.97, max(0.03, p))  # never claim certainty from a forecast
            label = "high" if kind == "max" else "low"
            if lead == 0 and obs is not None:
                note = (f"{STATIONS[station][0]} {label} so far {obs:.0f}°F, rest of day "
                        f"forecast {mu:.1f}°F ±{sigma:.1f}")
            else:
                note = f"{STATIONS[station][0]} {label} forecast {mu:.1f}°F ±{sigma:.1f}"
            out.append((m, p, note))
        return out


def run(kalshi, now: datetime, session=None) -> list[Pick]:
    DIAG.clear()
    DIAG.update({"events": 0, "priced": 0, "skipped_far_from_market": 0, "skipped_same_day": 0, "samples": []})
    wx = Weather(session)
    series = [s.get("ticker") for s in kalshi.series("Climate and Weather")]
    series = [s for s in series if s and variable_for(s) and re.match(r"^KX(HIGH|LOW)", s)]
    picks: list[Pick] = []
    for s in series:
        for ev in kalshi.open_events(s, limit=3):
            try:
                priced = wx.price_event(ev, now)
            except Exception as exc:  # one bad station never stops the lane
                print("WEATHER_EVENT_ERROR", ev.get("event_ticker"), type(exc).__name__, str(exc)[:160])
                continue
            DIAG["events"] += 1
            DIAG["priced"] += bool(priced)
            if priced and len(DIAG["samples"]) < 12:
                DIAG["samples"].append({"event": ev.get("event_ticker"), "note": priced[0][2],
                                        "model_vs_ask": [(m.get("yes_sub_title"), round(p, 2), m.get("yes_ask"))
                                                         for m, p, _ in priced]})
            best = []
            for m, p, note in priced:
                narrow = str(m.get("strike_type") or "").lower() == "between"
                for side, fair in (("YES", p), ("NO", 1.0 - p)):
                    if side == "YES" and narrow and not yes_brackets_enabled():
                        DIAG["skipped_yes_bracket"] = DIAG.get("skipped_yes_bracket", 0) + 1
                        continue
                    pk = Pick(NAME, m, side, fair, note, category="Weather", min_edge=MIN_EDGE,
                              extra={"title": f"{ev.get('title')} — {m.get('yes_sub_title')}"})
                    e = pk.edge()
                    if e is None or e < MIN_EDGE:
                        continue
                    if e > MAX_EDGE:  # model far from a liquid market: trust the market, log it
                        DIAG["skipped_far_from_market"] += 1
                        continue
                    best.append((e, pk))
            best.sort(key=lambda x: -x[0])
            picks += [pk for _, pk in best[:MAX_PER_EVENT]]
    picks.sort(key=lambda p: -(p.edge() or 0))
    return picks[:MAX_PICKS]
