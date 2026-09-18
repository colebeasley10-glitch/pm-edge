"""Live NCAA beta evaluator. Read-only; never submits wagers."""
from datetime import datetime, timezone
from pathlib import Path
import json, math, os, re, statistics, requests
import approval_links
from cfbd_client import CFBD
from ncaa_predictive import ratings_map, project
from watcher import fetch_open_kalshi_sports_markets, inferred_sport_rule, parse_market

PUSH="https://api.pushover.net/1/messages.json"
STATE=Path(".ncaa_beta_state.json")
MIN_TOTAL_EDGE=float(os.getenv("NCAA_BETA_MIN_TOTAL_EDGE","4.0"))
MIN_SPREAD_EDGE=float(os.getenv("NCAA_BETA_MIN_SPREAD_EDGE","3.0"))
MIN_PROB=float(os.getenv("NCAA_BETA_MIN_PROB","0.57"))
TOTAL_SD=float(os.getenv("NCAA_BETA_TOTAL_SD","14.0"))
MARGIN_SD=float(os.getenv("NCAA_BETA_MARGIN_SD","16.0"))
MAX_ALERTS=int(os.getenv("NCAA_BETA_MAX_ALERTS","3"))
COOLDOWN_HOURS=float(os.getenv("NCAA_BETA_COOLDOWN_HOURS","6"))
MAX_RISK=float(os.getenv("NCAA_BETA_MAX_RISK_DOLLARS","5.00"))
MAX_SHARES=int(os.getenv("NCAA_BETA_MAX_SHARES","20"))
APPROVAL_TTL_SECONDS=int(os.getenv("PM_EDGE_APPROVAL_TTL_SECONDS","1800"))
PRICE_BUFFER=float(os.getenv("NCAA_BETA_PRICE_BUFFER","0.055"))
MAX_FISH_GAP=float(os.getenv("NCAA_BETA_MAX_FISH_GAP","0.05"))
KALSHI_BASE=os.getenv("PM_EDGE_KALSHI_PUBLIC_BASE_URL","https://api.elections.kalshi.com/trade-api/v2")

def notify(title,msg,url=None,url_title="Open Kalshi Market"):
    u=os.getenv("PUSHOVER_USER_KEY"); t=os.getenv("PUSHOVER_APP_TOKEN")
    if not (u and t): return False
    payload={"token":t,"user":u,"title":title,"message":msg}
    if url:
        payload["url"]=url; payload["url_title"]=url_title
    r=requests.post(PUSH,data=payload,timeout=15)
    r.raise_for_status(); return True

def cdf(x): return .5*(1+math.erf(x/math.sqrt(2)))

def load_state():
    try:return json.loads(STATE.read_text())
    except Exception:return {"alerts":{}}

def save_state(s): STATE.write_text(json.dumps(s,indent=2,sort_keys=True))

def median(xs):
    vals=[]
    for x in xs:
        try:
            v=float(x)
            if math.isfinite(v): vals.append(v)
        except Exception: pass
    return statistics.median(vals) if vals else None

def consensus_line(game_line):
    rows=game_line.get("lines") or []
    return median([r.get("overUnder") for r in rows]), median([r.get("spread") for r in rows]), len(rows)

def alert_ok(state,key,line,now):
    prev=state.setdefault("alerts",{}).get(key)
    if not prev:return True
    try:
        sent=datetime.fromisoformat(prev["sent_at"].replace("Z","+00:00"))
        age=(now-sent).total_seconds()/3600
    except Exception: age=999
    old=prev.get("line")
    if old is not None and abs(float(line)-float(old))>=1.0:return True
    return age>=COOLDOWN_HOURS

def mark(state,key,line,prob,now,kalshi=None):
    state["alerts"][key]={"sent_at":now.isoformat(),"line":line,"prob":prob,"kalshi":kalshi or {}}

def plus(x): return f"+{x:g}" if x>0 else f"{x:g}"

def canon_team(x):
    x=str(x or "").lower().replace("&"," and ")
    x=re.sub(r"[^a-z0-9 ]+"," ",x)
    x=re.sub(r"\b(state|st)\b","st",x)
    return " ".join(x.split())

def team_in_blob(team,blob):
    t=canon_team(team); b=canon_team(blob)
    if not t or not b:return False
    if t in b:return True
    tt=t.split(); bt=set(b.split())
    return len(tt)>=2 and sum(1 for z in tt if z in bt)>=max(2,len(tt)-1)

def market_blob(m):
    return " ".join(str(m.get(k) or "") for k in ("title","_kalshi_event_title","_kalshi_milestone_title","subtitle"))

def kalshi_url(m,side):
    ticker=str(m.get("ticker") or "")
    event=str(m.get("event_ticker") or "")
    if not ticker or not event:return "https://kalshi.com/hubs/college-football"
    series=ticker.split("-",1)[0].lower()
    return (f"https://kalshi.com/markets/{series}/x/{event.lower()}"
            f"?op_market_ticker={ticker}&op_order_side={side.lower()}&op_order_type=dollars&op_side=buy")

def price_plan(prob,ask):
    hard=max(.01,min(.99,math.floor((prob-PRICE_BUFFER)*100)/100))
    if hard<=0 or ask is None:return None
    ask=float(ask)
    if ask<=hard:
        return {"action":"BUY NOW","target":ask,"hard_max":hard,"gap":0.0}
    gap=ask-hard
    if gap<=MAX_FISH_GAP:
        return {"action":"PLACE LIMIT","target":hard,"hard_max":hard,"gap":gap}
    return None

def approval_booking(signal, kalshi):
    """Build the same signed PM Edge Review & Submit flow used by normal alerts."""
    base_url=os.getenv("STREAMLIT_APP_URL","").strip()
    if not base_url:
        return None
    try:
        price=float(kalshi["target"])
        if not (0 < price < 1):
            return None
        shares=min(max(1, int(math.floor(MAX_RISK / price))), max(1, MAX_SHARES))
        max_loss=round(shares * price, 2)
        execution="TAKE" if str(kalshi.get("action") or "").upper()=="BUY NOW" else "MAKE"
        token=approval_links.create_approval_token(
            ticker=str(kalshi.get("ticker") or ""),
            side=str(kalshi.get("side") or "").upper(),
            lane="primary",
            execution=execution,
            limit_price=price,
            shares=shares,
            max_loss=max_loss,
            fair_probability=float(signal["prob"]),
            net_edge=float(signal["prob"])-price,
            books=int(signal.get("providers") or 0),
            ttl_seconds=APPROVAL_TTL_SECONDS,
        )
        url=approval_links.build_approval_url(base_url, token)
        if len(url) > 512:
            return None
        return {"url":url,"shares":shares,"max_loss":max_loss,"execution":execution}
    except Exception as e:
        print("NCAA_APPROVAL_LINK_SKIPPED", kalshi.get("ticker"), type(e).__name__, str(e)[:160])
        return None


def match_kalshi(signal,markets,home,away,home_spread):
    desired_team=signal.get("team")
    for m in markets:
        rule=inferred_sport_rule(m)
        if rule != ("exact","americanfootball_ncaaf"): continue
        blob=market_blob(m)
        if not (team_in_blob(home,blob) and team_in_blob(away,blob)): continue
        try: spec=parse_market(m)
        except Exception: continue
        if signal["kind"]=="TOTAL":
            if spec.market_type!="total" or spec.point is None: continue
            if abs(float(spec.point)-float(signal["line"]))>.11: continue
            kside="YES" if signal["direction"]=="OVER" else "NO"
        else:
            if spec.market_type!="spread" or spec.point is None: continue
            if abs(abs(float(spec.point))-abs(float(home_spread)))>.11: continue
            selection=str(getattr(spec,"selection","") or "")
            # Kalshi spread contracts are typically "TEAM wins by over X".
            if not selection:
                selection=blob
            contract_team_home=team_in_blob(home,selection)
            contract_team_away=team_in_blob(away,selection)
            if desired_team==home:
                if contract_team_home: kside="YES"
                elif contract_team_away: kside="NO"
                else: continue
            else:
                if contract_team_away: kside="YES"
                elif contract_team_home: kside="NO"
                else: continue
        ask=m.get("yes_ask") if kside=="YES" else m.get("no_ask")
        plan=price_plan(signal["prob"],ask)
        if not plan: continue
        return {**plan,"side":kside,"ask":float(ask),"ticker":m.get("ticker"),
                "event_ticker":m.get("event_ticker"),"url":kalshi_url(m,kside),
                "market_title":m.get("title")}
    return None

def main():
    now=datetime.now(timezone.utc); year=now.year
    c=CFBD(); games=c.games(year); core=c.core(year); rm=ratings_map(core)
    upcoming=[]; weeks=set()
    for g in games:
        if g.get("completed"): continue
        start=g.get("startDate") or g.get("startTime")
        if not start: continue
        try: dt=datetime.fromisoformat(start.replace("Z","+00:00"))
        except ValueError: continue
        hours=(dt-now).total_seconds()/3600
        if 0 <= hours <= 18:
            p=project(g["homeTeam"],g["awayTeam"],rm,neutral=bool(g.get("neutralSite")))
            if p:
                upcoming.append((dt,g,p))
                if g.get("week") is not None: weeks.add(int(g["week"]))
    line_rows=[]
    for w in sorted(weeks):
        try: line_rows.extend(c.lines(year,w))
        except Exception as e: print("CFBD lines fetch failed",w,e)
    by_id={str(x.get("id") or x.get("gameId")):x for x in line_rows}

    try:
        kalshi_markets,kalshi_stats=fetch_open_kalshi_sports_markets(KALSHI_BASE,max_events=3000)
    except Exception as e:
        print("Kalshi discovery failed",e); kalshi_markets=[]; kalshi_stats={}

    state=load_state(); signals=[]
    for dt,g,p in sorted(upcoming,key=lambda x:x[0]):
        gl=by_id.get(str(g.get("id")))
        if not gl: continue
        total_line, home_spread, nproviders=consensus_line(gl)
        if nproviders<1: continue
        if total_line is not None:
            diff=p.total-total_line
            if abs(diff)>=MIN_TOTAL_EDGE:
                if diff>0:
                    direction="OVER"; side=f"OVER {total_line:g}"; prob=1-cdf((total_line-p.total)/TOTAL_SD)
                    qualifier=f"Play only {total_line:g} or lower"
                else:
                    direction="UNDER"; side=f"UNDER {total_line:g}"; prob=cdf((total_line-p.total)/TOTAL_SD)
                    qualifier=f"Play only {total_line:g} or higher"
                if prob>=MIN_PROB:
                    signals.append({"game":f"{p.away} @ {p.home}","kind":"TOTAL","direction":direction,"side":side,
                                    "line":total_line,"prob":prob,"edge_pts":abs(diff),
                                    "proj":f"{p.away_points}-{p.home_points} ({p.total:.1f})","qualifier":qualifier,
                                    "kickoff":dt,"providers":nproviders,"confidence":p.confidence,
                                    "home":p.home,"away":p.away,"home_spread":home_spread})
        if home_spread is not None:
            home_cover=cdf((p.home_margin+home_spread)/MARGIN_SD)
            edge=p.home_margin-(-home_spread)
            if abs(edge)>=MIN_SPREAD_EDGE:
                if edge>0:
                    team=p.home; side=f"{p.home} {plus(home_spread)}"; prob=home_cover
                    qualifier=f"Play {plus(home_spread)} or better"
                else:
                    team=p.away; away_spread=-home_spread; side=f"{p.away} {plus(away_spread)}"; prob=1-home_cover
                    qualifier=f"Play {plus(away_spread)} or better"
                if prob>=MIN_PROB:
                    signals.append({"game":f"{p.away} @ {p.home}","kind":"SPREAD","team":team,"side":side,
                                    "line":home_spread,"prob":prob,"edge_pts":abs(edge),
                                    "proj":f"{p.away_points}-{p.home_points}; home margin {p.home_margin:+.1f}",
                                    "qualifier":qualifier,"kickoff":dt,"providers":nproviders,"confidence":p.confidence,
                                    "home":p.home,"away":p.away,"home_spread":home_spread})

    priced=[]
    for s in signals:
        km=match_kalshi(s,kalshi_markets,s["home"],s["away"],s["home_spread"])
        if km:
            s["kalshi"]=km
            # Conservative combined ranking: model probability first, then price cushion.
            s["price_edge"]=s["prob"]-km["target"]
            priced.append(s)
        else:
            print("MODEL_SIGNAL_NO_QUALIFIED_KALSHI_PRICE",s["game"],s["side"])
    priced.sort(key=lambda s:(s["prob"],s["price_edge"],s["edge_pts"]),reverse=True)

    print({"ncaa_beta":"ok","core_rows":len(core),"upcoming_projectable":len(upcoming),
           "line_games":len(line_rows),"model_qualified":len(signals),"kalshi_priced":len(priced),
           "kalshi_markets":len(kalshi_markets),"kalshi_game_events":kalshi_stats.get("kalshi_events_seen")})

    sent=0
    for s in priced:
        if sent>=MAX_ALERTS: break
        k=s["kalshi"]
        key=f'{s["game"]}|{s["kind"]}|{s["side"]}|{k["side"]}|booking-v1'
        if not alert_ok(state,key,s["line"],now): continue
        booking=approval_booking(s,k)
        booking_text=(
            f'AUTO-SIZED: {booking["shares"]} shares | max cost ${booking["max_loss"]:.2f}\n'
            if booking else ''
        )
        msg=(f'{s["game"]}\nTAKE: {s["side"]}\n'
             f'KALSHI: BUY {k["side"]}\n'
             f'{k["action"]}: {k["target"]*100:.0f}¢\n'
             f'DO NOT PAY OVER: {k["hard_max"]*100:.0f}¢\n'
             f'Current ask: {k["ask"]*100:.0f}¢\n'
             f'{booking_text}'
             f'Model probability: {s["prob"]:.1%}\n'
             f'Model projection: {s["proj"]}\n'
             f'Model-vs-market gap: {s["edge_pts"]:.1f} pts\n'
             f'Price cushion at target: {(s["prob"]-k["target"]):.1%}\n'
             f'Market consensus: {s["providers"]} provider(s)\n'
             f'{s["qualifier"]}\n\n'
             f'BETA: small stake only. '
             f'{("Tap below to review & submit; PM Edge re-checks the live price before sending." if booking else "Tap below to open the exact Kalshi market.")}')
        alert_url=booking["url"] if booking else k["url"]
        alert_title="Review & submit" if booking else "Open Kalshi Market"
        if notify("🧪 NCAA MODEL BETA — TAKE",msg,url=alert_url,url_title=alert_title):
            mark(state,key,s["line"],s["prob"],now,k); sent+=1
    save_state(state)
    if not priced: print("No NCAA beta bet cleared both model and Kalshi-price thresholds.")
if __name__=="__main__": main()
