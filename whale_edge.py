"""Free Whale Flow lane using Kalshi's public trade tape.

This replaces the paid named-trader dependency with exchange-native public
trades. It detects unusually large directional taker flow, prioritizes
football, re-checks the live market, and records signals under lane="whale"
so performance remains separate from Primary/Standard/Fish.

No autonomous real-money order is placed here.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import threading

import requests

from pmedge.rate_limit import RetryStats, get_with_retry

import dashboard_data
import signal_tracker
import watcher

PUBLIC_BASE = os.getenv("PM_EDGE_KALSHI_PUBLIC_BASE_URL","https://external-api.kalshi.com/trade-api/v2").rstrip("/")
STATE_PATH = Path(os.getenv("PM_EDGE_WHALE_STATE_PATH", ".pm_edge_whale_state.json"))
LEDGER_PATH = Path(os.getenv("PM_EDGE_WHALE_LEDGER_PATH", "pm_edge_whale_flow_ledger.csv"))
POLL_MINUTES = max(10, int(os.getenv("PM_EDGE_WHALE_POLL_MINUTES", "30")))
LOOKBACK_MINUTES = max(10, int(os.getenv("PM_EDGE_WHALE_LOOKBACK_MINUTES", "45")))
SINGLE_MIN_CONTRACTS = max(1, int(os.getenv("PM_EDGE_WHALE_SINGLE_MIN_CONTRACTS", "250")))
SINGLE_MIN_NOTIONAL = max(1.0, float(os.getenv("PM_EDGE_WHALE_SINGLE_MIN_NOTIONAL", "100")))
AGG_MIN_CONTRACTS = max(1, int(os.getenv("PM_EDGE_WHALE_AGG_MIN_CONTRACTS", "600")))
AGG_MIN_NOTIONAL = max(1.0, float(os.getenv("PM_EDGE_WHALE_AGG_MIN_NOTIONAL", "250")))
AGG_MIN_TRADES = max(2, int(os.getenv("PM_EDGE_WHALE_AGG_MIN_TRADES", "2")))
MIN_REFERENCE_CENTS = max(1, int(os.getenv("PM_EDGE_WHALE_MIN_REFERENCE_CENTS", "15")))
MAX_REFERENCE_CENTS = min(99, int(os.getenv("PM_EDGE_WHALE_MAX_REFERENCE_CENTS", "85")))
MAX_REST_GAP_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_MAX_REST_GAP_CENTS", "4")))
BETTER_BY_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_BETTER_BY_CENTS", "1")))
MODEL_DISAGREE_TOLERANCE = max(0.0, float(os.getenv("PM_EDGE_WHALE_MODEL_DISAGREE_TOLERANCE", "0.03")))
SIGNAL_COOLDOWN_HOURS = max(1.0, float(os.getenv("PM_EDGE_WHALE_SIGNAL_COOLDOWN_HOURS", "6")))
MAX_ALERTS_PER_RUN = max(1, int(os.getenv("PM_EDGE_WHALE_MAX_ALERTS_PER_RUN", "5")))
MAX_NON_FOOTBALL_ALERTS_PER_RUN = max(0, int(os.getenv("PM_EDGE_WHALE_MAX_NON_FOOTBALL_ALERTS_PER_RUN", "2")))
SOURCE_VERSION = 30

LEDGER_FIELDS = [
    "observed_at","decision","reason","trade_id","ticker","side","question","football",
    "reference_price_cents","current_ask_cents","target_price_cents","price_improvement_cents",
    "execution","flow_contracts","flow_notional_usd","flow_trade_count","largest_trade_contracts",
    "block_trade","model_fair_side","model_edge","signal_id",
]

def _now():
    return datetime.now(timezone.utc)

def _iso(dt=None):
    return (dt or _now()).astimezone(timezone.utc).isoformat().replace("+00:00","Z")

def _safe_float(v):
    try: x=float(v)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None

def _parse_iso(v):
    try: dt=datetime.fromisoformat(str(v or "").replace("Z","+00:00"))
    except ValueError: return None
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _load_state(path=STATE_PATH):
    if not path.exists(): return {"version":2,"seen_trade_ids":[],"sent":{}}
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except Exception: data={}
    if not isinstance(data,dict): data={}
    data["version"]=2; data.setdefault("seen_trade_ids",[]); data.setdefault("sent",{})
    return data

def _save_state(state,path=STATE_PATH):
    state["seen_trade_ids"]=list(dict.fromkeys(state.get("seen_trade_ids") or []))[-5000:]
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(state,indent=2,sort_keys=True),encoding="utf-8")
    tmp.replace(path)

def _append_ledger(row,path=LEDGER_PATH):
    path.parent.mkdir(parents=True,exist_ok=True)
    header=not path.exists() or path.stat().st_size==0
    with path.open("a",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=LEDGER_FIELDS)
        if header: w.writeheader()
        w.writerow({k:row.get(k,"") for k in LEDGER_FIELDS})

def _poll_due(state,now):
    last=_parse_iso(state.get("last_poll_at"))
    return last is None or (now-last).total_seconds()>=POLL_MINUTES*60

def _is_football(ticker,question=""):
    text=f"{ticker} {question}".upper()
    return bool(re.search(r"(^|[^A-Z])(NFL|NCAAF|CFB|COLLEGE FOOTBALL|FOOTBALL)([^A-Z]|$)",text)
                or str(ticker).upper().startswith(("KXNFL","KXNCAAF","KXCFB")))

def _is_btc(ticker,question=""):
    """BTC/Bitcoin markets are disabled for Whale Flow alerts."""
    ticker_text=str(ticker or "").upper()
    text=f"{ticker_text} {question}".upper()
    return ticker_text.startswith(("KXBTC","KXBITCOIN")) or bool(
        re.search(r"(^|[^A-Z])(BTC|BITCOIN)([^A-Z]|$)",text)
    )

def _is_fast_financial_market(ticker,question=""):
    """Markets too fast for a human Whale Flow copy are disabled."""
    ticker_text=str(ticker or "").upper()
    text=f"{ticker_text} {question}".upper()
    if _is_btc(ticker,question):
        return True
    # Intraday precious-metal/commodity direction markets (for example
    # "Gold price up in next 15 mins?") routinely move before Review & Place.
    fast_window=bool(re.search(r"\b(5|10|15|30)\s*(MIN|MINS|MINUTE|MINUTES)\b",text))
    commodity=bool(re.search(r"\b(GOLD|SILVER|XAU|XAG|COMMODITY|OIL|WTI|BRENT)\b",text))
    return fast_window and commodity

TRADE_PAGE_CAP = max(1, int(os.getenv("PM_EDGE_WHALE_TRADE_PAGE_CAP", "10")))
TRADE_TIMEOUT_SECONDS = max(3, int(os.getenv("PM_EDGE_WHALE_TRADE_TIMEOUT_SECONDS", "20")))

_TRADE_STATS = threading.local()

def _trade_retry_stats():
    """Per-thread retry counters so each lane's diagnostics count only its own retries."""
    stats = getattr(_TRADE_STATS, "stats", None)
    if stats is None:
        stats = _TRADE_STATS.stats = RetryStats()
    return stats

def _fetch_trade_page(params):
    # Shared process-wide pacing + bounded 429 retries (public GET only).
    r=get_with_retry(
        lambda: requests.get(f"{PUBLIC_BASE}/markets/trades",params=params,timeout=TRADE_TIMEOUT_SECONDS),
        stats=_trade_retry_stats(),
    )
    r.raise_for_status()
    return r.json()

TRADE_TAPE_VERSION = "trade_tape_horizon_v1"

def _public_trades(*,min_ts,limit=1000,page_cap=None,diagnostics=None,fetch_page=None,now=None,clock=None):
    """Public, read-only trade tape for the window [min_ts, now] via cursor pagination.

    Returns ONLY trades whose created_time is inside the requested window,
    de-duplicated by trade_id. Rows older than min_ts, and rows without a
    parseable created_time (their window membership is unknown), are dropped
    and counted.

    Paging stops when:
      * the server reports no further cursor   -> window complete
      * a page is empty                        -> window complete
      * a newest-first page reaches a row at/before min_ts -> window complete
      * the page cap is reached with a cursor still pending -> TRUNCATED
    min_ts is sent on every request, so when the server applies it (the
    original single-page design relied on that) no out-of-window row is ever
    returned and only the cursor or the cap can end paging. Full pages up to
    the cap therefore mean the window holds more trades than the cap allows:
    trade_horizon_covered=False reports that explicitly instead of hiding it.
    """
    fetch_page=fetch_page or _fetch_trade_page
    clock=clock or _now
    cap=max(1,int(page_cap if page_cap is not None else TRADE_PAGE_CAP))
    min_ts=int(min_ts)
    started=clock()
    out=[]; seen=set(); cursor=None
    pages=fetched=deduped=outside=missing_ts=0
    oldest_any=oldest_in=newest_in=None
    reached_boundary=exhausted=cap_hit=False
    orders=set()
    fetch_error=None
    retry_stats=_trade_retry_stats()
    retries_before=retry_stats.retries
    limited_before=retry_stats.rate_limited
    while True:
        if pages>=cap:
            cap_hit=bool(cursor) and not (reached_boundary and orders=={"newest_first"})
            break
        params={"limit":max(1,min(int(limit),1000)),"min_ts":min_ts}
        if cursor: params["cursor"]=cursor
        try:
            payload=fetch_page(params)
        except Exception as exc:
            # First page failing: nothing to keep, surface the error as before.
            if pages==0:
                raise
            # Later page failing (e.g. 429 after retries): KEEP what we have and
            # report the window as truncated instead of discarding every page.
            fetch_error=f"{type(exc).__name__}: {str(exc)[:200]}"
            break
        pages+=1
        rows=payload.get("trades",[]) if isinstance(payload,dict) else []
        rows=rows if isinstance(rows,list) else []
        if not rows:
            exhausted=True; break
        fetched+=len(rows)
        page_ts=[]
        for t in rows:
            if not isinstance(t,dict): continue
            tid=str(t.get("trade_id") or "")
            if tid:
                if tid in seen:
                    deduped+=1; continue
                seen.add(tid)
            ts=_parse_iso(t.get("created_time")) if t.get("created_time") else None
            if ts is None:
                missing_ts+=1; continue
            page_ts.append(ts)
            if oldest_any is None or ts<oldest_any: oldest_any=ts
            if ts.timestamp()<=min_ts: reached_boundary=True
            if ts.timestamp()<min_ts:
                outside+=1; continue
            out.append(t)
            if oldest_in is None or ts<oldest_in: oldest_in=ts
            if newest_in is None or ts>newest_in: newest_in=ts
        if len(page_ts)>=2:
            if page_ts[0]>page_ts[-1]: orders.add("newest_first")
            elif page_ts[0]<page_ts[-1]: orders.add("oldest_first")
        cursor=payload.get("cursor") if isinstance(payload,dict) else None
        if not cursor:
            exhausted=True; break
        # Only a newest-first walk proves everything older has been passed.
        if reached_boundary and orders=={"newest_first"}:
            break
    covered=bool(fetch_error is None and (exhausted or (reached_boundary and orders=={"newest_first"})))
    if diagnostics is not None:
        end=clock()
        ref=now or started
        lookback=max(0.0,ref.timestamp()-min_ts)
        if covered:
            fraction=1.0
        elif oldest_in is not None and lookback>0:
            fraction=max(0.0,min(1.0,(ref.timestamp()-oldest_in.timestamp())/lookback))
        else:
            fraction=0.0
        diagnostics.update({
            "trade_tape_version":TRADE_TAPE_VERSION,
            "trade_pages_fetched":pages,"trade_rows_fetched":fetched,"trade_rows_deduped":deduped,
            "trade_rows_in_window":len(out),"trade_rows_outside_window":outside,
            "trade_rows_missing_ts":missing_ts,
            "trade_oldest_ts":_iso(oldest_any) if oldest_any is not None else None,
            "trade_oldest_in_window_ts":_iso(oldest_in) if oldest_in is not None else None,
            "trade_newest_ts":_iso(newest_in) if newest_in is not None else None,
            "trade_page_cap_hit":cap_hit,"trade_page_cap":cap,
            "trade_horizon_covered":covered,
            "trade_window_coverage_fraction":round(fraction,4),
            "trade_requested_min_ts":_iso(datetime.fromtimestamp(min_ts,tz=timezone.utc)),
            "trade_requested_lookback_seconds":round(lookback,3),
            "trade_page_order":("unknown" if not orders else "mixed" if len(orders)>1 else next(iter(orders))),
            "trade_fetch_seconds":round(max(0.0,(end-started).total_seconds()),3),
            "trade_partial_after_error":fetch_error is not None,
            "trade_fetch_error":fetch_error,
            "trade_rate_limited":bool(
                (fetch_error and "429" in fetch_error)
                or retry_stats.rate_limited>limited_before
            ),
            "trade_rate_limit_retries":retry_stats.retries-retries_before,
        })
    return out

def _trade_side(t):
    side=str(t.get("taker_outcome_side") or t.get("taker_side") or "").upper()
    return side if side in {"YES","NO"} else None

def _trade_price(t,side):
    key="yes_price_dollars" if side=="YES" else "no_price_dollars"
    p=_safe_float(t.get(key))
    return p if p is not None and 0<p<1 else None

def _trade_count(t):
    return max(0.0,_safe_float(t.get("count_fp")) or _safe_float(t.get("count")) or 0.0)

def _market_fair_context(ticker,side):
    try:
        fair=(watcher.load_state().get("fair_cache") or {}).get(str(ticker))
    except Exception:
        return None, None
    if not isinstance(fair,dict):
        return None, None
    yes=_safe_float(fair.get("fair_yes_prob"))
    fair_side=None if yes is None else (yes if side=="YES" else 1.0-yes)
    commence=_parse_iso(fair.get("commence_time"))
    return fair_side, commence

def _market_fair_side(ticker,side):
    fair_side,_=_market_fair_context(ticker,side)
    return fair_side

_TICKER_DATE_RE=re.compile(
    r"-(?P<year>\d{2})(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?P<day>\d{2})"
)
_MONTHS={m:i for i,m in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"),start=1)}

def _ticker_event_date(ticker):
    m=_TICKER_DATE_RE.search(str(ticker or "").upper())
    if not m:
        return None
    try:
        return datetime(2000+int(m.group("year")),_MONTHS[m.group("month")],int(m.group("day")),
                        tzinfo=watcher._KALSHI_TICKER_TZ).date()
    except Exception:
        return None

def _trusted_start(ticker,side):
    _,fair_start=_market_fair_context(ticker,side)
    if fair_start is not None:
        return fair_start
    return watcher.kalshi_ticker_game_time({"ticker":ticker})

def _late_entry_reason(ticker,side,now):
    """Whale Flow is pregame-only until we have score-aware live fair value."""
    start=_trusted_start(ticker,side)
    if start is not None:
        if now >= start-timedelta(minutes=5):
            return "live_or_within_5m_of_start"
        return None
    event_date=_ticker_event_date(ticker)
    if event_date is not None:
        local_date=now.astimezone(watcher._KALSHI_TICKER_TZ).date()
        if event_date <= local_date:
            return "same_day_start_time_unverified"
    return None


def _aggregate(trades):
    groups=defaultdict(list)
    for t in trades:
        ticker=str(t.get("ticker") or "").strip(); side=_trade_side(t)
        price=_trade_price(t,side or ""); count=_trade_count(t)
        if ticker and side and price is not None and count>0: groups[(ticker,side)].append(t)
    out=[]
    for (ticker,side),rows in groups.items():
        total=notional=weighted=largest=0.0; block=False; latest=None; latest_id=""
        for t in rows:
            count=_trade_count(t); price=_trade_price(t,side) or 0.0
            total+=count; notional+=count*price; weighted+=count*price; largest=max(largest,count)
            block=block or bool(t.get("is_block_trade")); ts=_parse_iso(t.get("created_time"))
            if ts and (latest is None or ts>latest): latest=ts; latest_id=str(t.get("trade_id") or "")
        ref=weighted/total if total else 0.0
        single=largest>=SINGLE_MIN_CONTRACTS and notional>=SINGLE_MIN_NOTIONAL
        agg=len(rows)>=AGG_MIN_TRADES and total>=AGG_MIN_CONTRACTS and notional>=AGG_MIN_NOTIONAL
        if single or agg or block:
            out.append({"ticker":ticker,"side":side,"reference_price":ref,"reference_price_cents":int(round(ref*100)),
                        "flow_contracts":int(round(total)),"flow_notional_usd":round(notional,2),
                        "flow_trade_count":len(rows),"largest_trade_contracts":int(round(largest)),
                        "block_trade":block,"trade_id":latest_id,"latest_time":latest})
    return out

def _evaluate_price(*,reference_cents,current_ask,model_fair_side):
    if reference_cents<MIN_REFERENCE_CENTS or reference_cents>MAX_REFERENCE_CENTS:
        return {"eligible":False,"reason":"reference_price_outside_band"}
    if current_ask is None or not (0<current_ask<1):
        return {"eligible":False,"reason":"missing_live_ask"}
    ref=reference_cents/100; ask_cents=int(round(current_ask*100))
    if current_ask<=ref+1e-9:
        target=current_ask; execution="TAKE"
    else:
        if ask_cents-reference_cents>MAX_REST_GAP_CENTS:
            return {"eligible":False,"reason":"wait_for_better_price","current_ask_cents":ask_cents}
        target=max(MIN_REFERENCE_CENTS,reference_cents-BETTER_BY_CENTS)/100; execution="MAKE"
    if model_fair_side is not None and model_fair_side+MODEL_DISAGREE_TOLERANCE<target:
        return {"eligible":False,"reason":"pm_edge_model_disagrees","current_ask_cents":ask_cents,
                "target_price_cents":int(round(target*100))}
    return {"eligible":True,"reason":"flow_price_guard_passed","execution":execution,"target":round(target,4),
            "hard_max":round(ref,4),"current_ask_cents":ask_cents,"target_price_cents":int(round(target*100)),
            "price_improvement_cents":reference_cents-int(round(target*100)),
            "model_edge":None if model_fair_side is None else model_fair_side-target}

def _cooldown_active(state,ticker,side,now):
    row=(state.get("sent") or {}).get(f"{ticker}|{side}")
    ts=_parse_iso(row.get("sent_at")) if isinstance(row,dict) else None
    return bool(ts and (now-ts).total_seconds()<SIGNAL_COOLDOWN_HOURS*3600)

def _repeat_price_suppressed(state,ticker,side,ev):
    """Suppress repeat Whale alerts when the new actionable price is worse."""
    row=(state.get("sent") or {}).get(f"{ticker}|{side}")
    if not isinstance(row,dict):
        return False
    prev=_safe_float(row.get("target_price"))
    current=_safe_float(ev.get("target"))
    if prev is None or current is None:
        return False
    return current > prev + 1e-9

def _record_signal(flow,snapshot,ev,model_fair_side):
    side=flow["side"]; ticker=flow["ticker"]; question=str(snapshot.get("title") or ticker)
    football=_is_football(ticker,question)
    fair_yes=None if model_fair_side is None else (model_fair_side if side=="YES" else 1-model_fair_side)
    fair_entry={"event_id":str(flow.get("trade_id") or f"whale-flow-{ticker}"),
                "sport_key":"whale_flow_football" if football else "whale_flow_sports",
                "sport_title":"Football" if football else "Sports","market_type":"whale_flow",
                "fair_yes_prob":fair_yes,"n_books":None,"dispersion":None,"fetched_at":_iso(),
                "commence_time":str(snapshot.get("close_time") or "")}
    q=SimpleNamespace(side=side,execution=ev["execution"],recommended_limit=ev["target"],hard_max=ev["hard_max"],
                      net_edge=ev.get("model_edge"),pmedge_risk_multiplier=0.5,pmedge_audit_bucket="whale_flow")
    return signal_tracker.record_signal({"ticker":ticker,"title":question},fair_entry,q,lane="whale",source_version=SOURCE_VERSION)

def _message(flow,snapshot,ev,model_fair_side):
    side=flow["side"]; ref=flow["reference_price_cents"]; target=ev["target_price_cents"]; live=ev["current_ask_cents"]
    lines=[f"{flow['flow_contracts']:,} contracts of aggressive {side} flow",
           f"{flow['flow_trade_count']} trade(s) · ~${flow['flow_notional_usd']:,.0f} at risk",
           str(snapshot.get("title") or flow["ticker"])[:180],f"Flow VWAP: {ref}¢ · Current ask: {live}¢"]
    if ev["execution"]=="TAKE": lines.append(f"BUY {side} @ {target}¢ OR LOWER")
    else: lines.append(f"REST BUY {side} @ {target}¢ · DO NOT CHASE ABOVE {ref}¢")
    if flow.get("block_trade"): lines.append("Includes reported block trade")
    if model_fair_side is not None: lines.append(f"PM Edge fair check: {model_fair_side*100:.1f}%")
    else: lines.append("PM Edge fair check unavailable — flow tracked separately at 0.5×")
    return "\n".join(lines)

def run_once(*,now=None):
    now=(now or _now()).astimezone(timezone.utc); state=_load_state()
    if not _poll_due(state,now): return {"enabled":True,"polled":False,"signals":0,"reason":"poll_interval"}
    try: trades=_public_trades(min_ts=int((now-timedelta(minutes=LOOKBACK_MINUTES)).timestamp()),now=now)
    except Exception as exc:
        print("WHALE_FLOW_API_ERROR",type(exc).__name__,str(exc)[:240])
        return {"enabled":True,"polled":False,"signals":0,"reason":"public_api_error"}
    seen=set(state.get("seen_trade_ids") or []); fresh=[t for t in trades if str(t.get("trade_id") or "") not in seen]
    flows=_aggregate(fresh); enriched=[]
    for flow in flows:
        # BTC/Bitcoin moves too quickly for Whale Flow's observed-entry price
        # to remain reproducible by the time an alert reaches the user.
        if _is_fast_financial_market(flow["ticker"]):
            continue
        try: snap=dashboard_data.market_snapshot(flow["ticker"])
        except Exception: continue
        question=str(snap.get("title") or flow["ticker"])
        if _is_fast_financial_market(flow["ticker"],question):
            continue
        enriched.append((not _is_football(flow["ticker"],question),-flow["flow_notional_usd"],flow,snap))
    enriched.sort(key=lambda x:(x[0],x[1]))
    sent=nonfootball=evaluated=0
    for _,__,flow,snap in enriched:
        if sent>=MAX_ALERTS_PER_RUN: break
        football=_is_football(flow["ticker"],str(snap.get("title") or ""))
        if not football and nonfootball>=MAX_NON_FOOTBALL_ALERTS_PER_RUN: continue
        if _cooldown_active(state,flow["ticker"],flow["side"],now): continue
        if str(snap.get("status") or "").lower() not in {"open","active"}: continue
        side=flow["side"]
        late_reason=_late_entry_reason(flow["ticker"],side,now)
        if late_reason:
            _append_ledger({"observed_at":_iso(now),"decision":"SKIP","reason":late_reason,
                "trade_id":flow.get("trade_id"),"ticker":flow["ticker"],"side":side,
                "question":str(snap.get("title") or ""),"football":football,
                "reference_price_cents":flow.get("reference_price_cents"),
                "current_ask_cents":None,"target_price_cents":None,"price_improvement_cents":None,
                "execution":None,"flow_contracts":flow.get("flow_contracts"),
                "flow_notional_usd":flow.get("flow_notional_usd"),
                "flow_trade_count":flow.get("flow_trade_count"),
                "largest_trade_contracts":flow.get("largest_trade_contracts"),
                "block_trade":flow.get("block_trade"),"model_fair_side":None,
                "model_edge":None,"signal_id":""})
            continue
        ask=snap.get("yes_ask") if side=="YES" else snap.get("no_ask")
        model_fair_side=_market_fair_side(flow["ticker"],side)
        ev=_evaluate_price(reference_cents=flow["reference_price_cents"],current_ask=_safe_float(ask),model_fair_side=model_fair_side)
        evaluated+=1
        row={"observed_at":_iso(now),"trade_id":flow.get("trade_id"),"ticker":flow["ticker"],"side":side,
             "question":str(snap.get("title") or ""),"football":football,"reference_price_cents":flow["reference_price_cents"],
             "current_ask_cents":ev.get("current_ask_cents"),"target_price_cents":ev.get("target_price_cents"),
             "price_improvement_cents":ev.get("price_improvement_cents"),"execution":ev.get("execution"),
             "flow_contracts":flow["flow_contracts"],"flow_notional_usd":flow["flow_notional_usd"],
             "flow_trade_count":flow["flow_trade_count"],"largest_trade_contracts":flow["largest_trade_contracts"],
             "block_trade":flow["block_trade"],"model_fair_side":model_fair_side,"model_edge":ev.get("model_edge")}
        if not ev.get("eligible"):
            row.update({"decision":"WAIT" if ev.get("reason") in {"wait_for_better_price","pm_edge_model_disagrees"} else "SKIP",
                        "reason":ev.get("reason")}); _append_ledger(row); continue
        if _repeat_price_suppressed(state,flow["ticker"],side,ev):
            row.update({"decision":"SKIP","reason":"worse_price_than_last_whale_alert"}); _append_ledger(row); continue
        sig=_record_signal(flow,snap,ev,model_fair_side)
        if not sig.get("recorded") and sig.get("reason")!="duplicate":
            row.update({"decision":"ERROR","reason":sig.get("reason")}); _append_ledger(row); continue
        signal_id=str(sig.get("signal_id") or "")
        row.update({"decision":"SIGNAL","reason":"whale_flow_price_guard_passed","signal_id":signal_id}); _append_ledger(row)
        watcher.send_push("🐋 PM EDGE WHALE FLOW — FOOTBALL" if football else "🐋 PM EDGE WHALE FLOW",
                          _message(flow,snap,ev,model_fair_side),priority=0)
        state["sent"][f"{flow['ticker']}|{side}"]={"sent_at":_iso(now),"signal_id":signal_id,
            "target_price":ev.get("target"),"hard_max":ev.get("hard_max"),"current_ask_cents":ev.get("current_ask_cents")}
        sent+=1
        if not football: nonfootball+=1
    for t in trades:
        tid=str(t.get("trade_id") or "")
        if tid: state["seen_trade_ids"].append(tid)
    state["last_poll_at"]=_iso(now); _save_state(state)
    result={"enabled":True,"polled":True,"public_trades":len(trades),"fresh_trades":len(fresh),
            "qualified_flows":len(flows),"evaluated":evaluated,"signals":sent}
    print("PM_EDGE_WHALE_FLOW "+json.dumps(result,sort_keys=True)); return result

def main():
    run_once(); return 0

if __name__=="__main__":
    raise SystemExit(main())
