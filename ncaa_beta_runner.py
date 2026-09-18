"""Live NCAA beta evaluator. Read-only; never submits wagers."""
from datetime import datetime, timezone
import os, requests
from cfbd_client import CFBD
from ncaa_predictive import ratings_map, project

PUSH="https://api.pushover.net/1/messages.json"

def notify(title,msg):
    u=os.getenv("PUSHOVER_USER_KEY"); t=os.getenv("PUSHOVER_APP_TOKEN")
    if not (u and t): return
    r=requests.post(PUSH,data={"token":t,"user":u,"title":title,"message":msg},timeout=15); r.raise_for_status()

def main():
    now=datetime.now(timezone.utc); year=now.year
    c=CFBD(); games=c.games(year); core=c.core(year); rm=ratings_map(core)
    upcoming=[]
    for g in games:
        if g.get("completed"): continue
        start=g.get("startDate") or g.get("startTime")
        if not start: continue
        try: dt=datetime.fromisoformat(start.replace("Z","+00:00"))
        except ValueError: continue
        hours=(dt-now).total_seconds()/3600
        if not 0 <= hours <= 18: continue
        p=project(g["homeTeam"],g["awayTeam"],rm,neutral=bool(g.get("neutralSite")))
        if p: upcoming.append((dt,p))
    upcoming.sort(key=lambda x:x[0])
    print({"ncaa_beta":"ok","core_rows":len(core),"upcoming_projectable":len(upcoming)})
    for dt,p in upcoming:
        print({"kickoff":dt.isoformat(),"game":f"{p.away} @ {p.home}","score":[p.away_points,p.home_points],"total":p.total,"home_margin":p.home_margin,"home_win_prob":p.home_win_prob,"confidence":p.confidence})
    # One status message per run only when games are in the near window. This is
    # deliberately NOT a bet alert: market-line/Kalshi qualification comes next.
    if upcoming:
        lines=[f"{p.away} @ {p.home}: {p.away_points}-{p.home_points}, total {p.total:.1f}" for _,p in upcoming[:4]]
        notify("🧪 NCAA MODEL BETA — projections", "\n".join(lines)+"\n\nProjection only — NOT yet a qualified bet.")
if __name__=="__main__": main()
