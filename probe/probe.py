"""One-off: capture real API payload samples so parsers are built on truth."""
import json, os, time, requests
from pathlib import Path
OUT = Path("probe/out"); OUT.mkdir(parents=True, exist_ok=True)
K = "https://external-api.kalshi.com/trade-api/v2"
S = requests.Session(); S.headers["User-Agent"] = "pm-edge-probe"

def get(url, **params):
    try:
        r = S.get(url, params=params, timeout=25)
        return {"status": r.status_code, "json": r.json() if "json" in r.headers.get("content-type","") else r.text[:2000]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def save(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str)[:400000])

# Kalshi series by category
allseries = get(f"{K}/series")
cats = {}
for s in (allseries.get("json") or {}).get("series", []) or []:
    cats.setdefault(s.get("category"), []).append({"ticker": s.get("ticker"), "title": s.get("title"), "frequency": s.get("frequency")})
save("kalshi_series_by_category", {k: v for k, v in cats.items()})
save("kalshi_series_counts", {k: len(v) for k, v in cats.items()})

def events(series, n=2):
    e = get(f"{K}/events", series_ticker=series, status="open", with_nested_markets="true", limit=n)
    return e

for series in ["KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHAUS", "KXHIGHLAX", "KXHIGHDEN", "KXHIGHPHIL", "KXRAINNYC", "KXBTCD", "KXBTC", "KXETHD", "KXINX", "KXNASDAQ100"]:
    save(f"kalshi_events_{series}", events(series))
    time.sleep(0.3)

# weather series found in category
wx = [s["ticker"] for s in cats.get("Climate and Weather", [])]
save("kalshi_weather_series", wx)
# Open markets count overall (first 3 pages)
mk, cursor, pages = [], None, 0
while pages < 5:
    p = {"status": "open", "limit": 1000}
    if cursor: p["cursor"] = cursor
    r = get(f"{K}/markets", **p); j = r.get("json") or {}
    mk += j.get("markets", []) or []; cursor = j.get("cursor"); pages += 1
    if not cursor: break
save("kalshi_open_markets_sample", {"count": len(mk), "first": mk[:5],
     "prefix_counts": sorted(((pre, n) for pre, n in __import__("collections").Counter(m.get("ticker","").split("-")[0] for m in mk).items()), key=lambda x: -x[1])[:120]})

save("kalshi_trades", get(f"{K}/markets/trades", limit=50))

# Polymarket
G = "https://gamma-api.polymarket.com"
save("poly_sports", get(f"{G}/sports"))
save("poly_events_sports", get(f"{G}/events", closed="false", active="true", limit=40, tag_slug="sports", order="volume24hr", ascending="false"))
save("poly_events_top", get(f"{G}/events", closed="false", active="true", limit=40, order="volume24hr", ascending="false"))
save("poly_markets", get(f"{G}/markets", closed="false", limit=10, order="volume24hr", ascending="false"))
save("poly_tags", get(f"{G}/tags", limit=200))

# Weather sources
save("openmeteo_nyc", get("https://api.open-meteo.com/v1/forecast", latitude=40.7794, longitude=-73.9692,
     daily="temperature_2m_max", temperature_unit="fahrenheit", timezone="America/New_York", forecast_days=3,
     models="gfs_seamless,ecmwf_ifs025,icon_seamless,gem_seamless,ncep_hrrr_conus"))
save("nws_obs_knyc", get("https://api.weather.gov/stations/KNYC/observations/latest"))
save("coinbase_candles", get("https://api.exchange.coinbase.com/products/BTC-USD/candles", granularity=3600))
print("done", os.listdir(OUT))
