"""Probe 2: summarized payloads (no truncation)."""
import json, time, collections, requests
from pathlib import Path
OUT = Path("probe/out"); OUT.mkdir(parents=True, exist_ok=True)
K = "https://external-api.kalshi.com/trade-api/v2"; G = "https://gamma-api.polymarket.com"
S = requests.Session(); S.headers["User-Agent"] = "pm-edge-probe"
def get(url, **p):
    for i in range(3):
        try:
            r = S.get(url, params=p, timeout=25)
            if r.status_code == 429: time.sleep(2); continue
            return r.json()
        except Exception as e:
            err = str(e)
    return {"error": err}
def save(n, o): (OUT / f"{n}.json").write_text(json.dumps(o, indent=1, default=str))

PM = ["question","outcomes","outcomePrices","bestBid","bestAsk","lastTradePrice","liquidityNum","volume24hr","endDate","gameStartTime","sportsMarketType","slug","clobTokenIds","line","groupItemTitle","acceptingOrders","enableOrderBook"]
def poly_events(**p):
    ev = get(f"{G}/events", **p)
    if not isinstance(ev, list): return ev
    return [{"title": e.get("title"), "slug": e.get("slug"), "endDate": e.get("endDate"), "startDate": e.get("startDate"),
             "volume24hr": e.get("volume24hr"), "liquidity": e.get("liquidity"),
             "tags": [t.get("slug") for t in (e.get("tags") or [])],
             "markets": [{k: m.get(k) for k in PM} for m in (e.get("markets") or [])[:6]]} for e in ev]
save("p2_poly_sports", poly_events(closed="false", active="true", limit=100, tag_slug="sports", order="volume24hr", ascending="false"))
save("p2_poly_top", poly_events(closed="false", active="true", limit=100, order="volume24hr", ascending="false"))
save("p2_poly_sports_list", get(f"{G}/sports"))

# Kalshi events overview
evs, cur = [], None
for _ in range(25):
    p = {"status": "open", "limit": 200}
    if cur: p["cursor"] = cur
    j = get(f"{K}/events", **p); b = j.get("events") or []; evs += b; cur = j.get("cursor")
    if not cur or not b: break
    time.sleep(0.15)
save("p2_kalshi_events_overview", {"n": len(evs),
    "by_category": collections.Counter(e.get("category") for e in evs),
    "by_series": collections.Counter(e.get("series_ticker") for e in evs).most_common(250)})
mk, cur, pages = 0, None, 0
while pages < 15:
    p = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
    if cur: p["cursor"] = cur
    j = get(f"{K}/markets", **p); b = j.get("markets") or []; mk += len(b); cur = j.get("cursor"); pages += 1
    if pages == 1: save("p2_kalshi_markets_nomve_first", [{k: m.get(k) for k in ["ticker","event_ticker","title","yes_sub_title","yes_ask_dollars","volume_24h_fp","close_time"]} for m in b[:40]])
    if not cur or not b: break
save("p2_kalshi_markets_nomve_count", {"count_upto": mk, "pages": pages, "more": bool(cur)})

# weather rules
wx = {}
for s in ["KXHIGHNY","KXHIGHCHI","KXHIGHMIA","KXHIGHAUS","KXHIGHLAX","KXHIGHDEN","KXHIGHPHIL","KXHIGHHOU","KXHIGHTATL","KXHIGHTBOS","KXHIGHTDAL","KXHIGHTDC","KXHIGHTSEA","KXHIGHTSFO","KXHIGHTPHX","KXHIGHTLV","KXHIGHTMIN","KXHIGHTNOLA","KXHIGHTOKC","KXHIGHTSAN","KXHIGHTSATX","KXHIGHTHOU","KXHIGHTEWR","KXHIGHTKSAN","KXLOWNY","KXLOWTNYC","KXLOWCHI","KXLOWAUS","KXHIGHTEGLL","KXHIGHTRJTT"]:
    j = get(f"{K}/events", series_ticker=s, status="open", with_nested_markets="true", limit=1)
    e = (j.get("events") or [{}])[0]; ms = e.get("markets") or []
    wx[s] = {"event": e.get("event_ticker"), "title": e.get("title"), "n": len(ms),
             "rules": (ms[0].get("rules_primary") if ms else None),
             "markets": [{k: m.get(k) for k in ["ticker","yes_sub_title","strike_type","floor_strike","cap_strike","yes_bid_dollars","yes_ask_dollars","close_time","volume_24h_fp"]} for m in ms]}
    time.sleep(0.2)
save("p2_kalshi_weather", wx)

# sports game series + crypto
sp = {}
for s in ["KXNFLGAME","KXMLBGAME","KXNCAAFGAME","KXNBAGAME","KXNHLGAME","KXEPLGAME","KXUCLGAME","KXWNBAGAME","KXATPMATCH","KXUFCFIGHT","KXBTCD","KXETHD","KXBTC","KXFEDDECISION","KXCPI"]:
    j = get(f"{K}/events", series_ticker=s, status="open", with_nested_markets="true", limit=3)
    sp[s] = [{"event": e.get("event_ticker"), "title": e.get("title"), "sub": e.get("sub_title"),
              "markets": [{k: m.get(k) for k in ["ticker","title","yes_sub_title","strike_type","floor_strike","cap_strike","yes_bid_dollars","yes_ask_dollars","close_time","expected_expiration_time","volume_24h_fp","rules_primary"]} for m in (e.get("markets") or [])[:4]]}
             for e in (j.get("events") or [])]
    time.sleep(0.2)
save("p2_kalshi_sports_crypto", sp)
print("ok")
