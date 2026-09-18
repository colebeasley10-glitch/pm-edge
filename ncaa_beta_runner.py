"""Live NCAA beta evaluator. Read-only; never submits wagers."""
from datetime import datetime, timezone
from pathlib import Path
import json, math, os, statistics, requests
from cfbd_client import CFBD
from ncaa_predictive import ratings_map, project

PUSH="https://api.pushover.net/1/messages.json"
STATE=Path(".ncaa_beta_state.json")
MIN_TOTAL_EDGE=float(os.getenv("NCAA_BETA_MIN_TOTAL_EDGE","4.0"))
MIN_SPREAD_EDGE=float(os.getenv("NCAA_BETA_MIN_SPREAD_EDGE","3.0"))
MIN_PROB=float(os.getenv("NCAA_BETA_MIN_PROB","0.57"))
TOTAL_SD=float(os.getenv("NCAA_BETA_TOTAL_SD","14.0"))
MARGIN_SD=float(os.getenv("NCAA_BETA_MARGIN_SD","16.0"))
MAX_ALERTS=int(os.getenv("NCAA_BETA_MAX_ALERTS","3"))
COOLDOWN_HOURS=float(os.getenv("NCAA_BETA_COOLDOWN_HOURS","6"))

def notify(title,msg):
    u=os.getenv("PUSHOVER_USER_KEY"); t=os.getenv("PUSHOVER_APP_TOKEN")
    if not (u and t): return False
    r=requests.post(PUSH,data={"token":t,"user":u,"title":title,"message":msg},timeout=15)
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

def mark(state,key,line,prob,now):
    state["alerts"][key]={"sent_at":now.isoformat(),"line":line,"prob":prob}

def plus(x):
    return f"+{x:g}" if x>0 else f"{x:g}"

def main():
    now=datetime.now(timezone.utc); year=now.year
    c=CFBD(); games=c.games(year); core=c.core(year); rm=ratings_map(core)
    upcoming=[]
    weeks=set()
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
    state=load_state(); signals=[]
    for dt,g,p in sorted(upcoming,key=lambda x:x[0]):
        gl=by_id.get(str(g.get("id")))
        if not gl: continue
        total_line, home_spread, nproviders=consensus_line(gl)
        if nproviders<1: continue
        # Totals
        if total_line is not None:
            diff=p.total-total_line
            if abs(diff)>=MIN_TOTAL_EDGE:
                if diff>0:
                    side=f"OVER {total_line:g}"; prob=1-cdf((total_line-p.total)/TOTAL_SD)
                    qualifier=f"Play only {total_line:g} or lower"
                else:
                    side=f"UNDER {total_line:g}"; prob=cdf((total_line-p.total)/TOTAL_SD)
                    qualifier=f"Play only {total_line:g} or higher"
                if prob>=MIN_PROB:
                    signals.append({"game":f"{p.away} @ {p.home}","kind":"TOTAL","side":side,"line":total_line,
                                    "prob":prob,"edge_pts":abs(diff),"proj":f"{p.away_points}-{p.home_points} ({p.total:.1f})",
                                    "qualifier":qualifier,"kickoff":dt,"providers":nproviders,"confidence":p.confidence})
        # Spreads. CFBD spread is home-team spread.
        if home_spread is not None:
            home_cover=cdf((p.home_margin+home_spread)/MARGIN_SD)
            market_implied_margin=-home_spread
            edge=p.home_margin-market_implied_margin
            if abs(edge)>=MIN_SPREAD_EDGE:
                if edge>0:
                    side=f"{p.home} {plus(home_spread)}"; prob=home_cover
                    qualifier=f"Play {plus(home_spread)} or better"
                else:
                    away_spread=-home_spread; side=f"{p.away} {plus(away_spread)}"; prob=1-home_cover
                    qualifier=f"Play {plus(away_spread)} or better"
                if prob>=MIN_PROB:
                    signals.append({"game":f"{p.away} @ {p.home}","kind":"SPREAD","side":side,"line":home_spread,
                                    "prob":prob,"edge_pts":abs(edge),"proj":f"{p.away_points}-{p.home_points}; home margin {p.home_margin:+.1f}",
                                    "qualifier":qualifier,"kickoff":dt,"providers":nproviders,"confidence":p.confidence})
    signals.sort(key=lambda s:(s["prob"],s["edge_pts"]),reverse=True)
    print({"ncaa_beta":"ok","core_rows":len(core),"upcoming_projectable":len(upcoming),"line_games":len(line_rows),"qualified":len(signals)})
    sent=0
    for s in signals:
        if sent>=MAX_ALERTS: break
        key=f'{s["game"]}|{s["kind"]}|{s["side"]}'
        if not alert_ok(state,key,s["line"],now): continue
        msg=(f'{s["game"]}\nTAKE: {s["side"]}\n'
             f'Model probability: {s["prob"]:.1%}\n'
             f'Model projection: {s["proj"]}\n'
             f'Model-vs-market gap: {s["edge_pts"]:.1f} pts\n'
             f'Market consensus: {s["providers"]} provider(s)\n'
             f'{s["qualifier"]}\n\n'
             f'BETA: small stake only. Verify the live line before placing.')
        if notify("🧪 NCAA MODEL BETA — QUALIFIED BET",msg):
            mark(state,key,s["line"],s["prob"],now); sent+=1
    save_state(state)
    if not signals: print("No NCAA beta bet cleared the current thresholds.")
if __name__=="__main__": main()
