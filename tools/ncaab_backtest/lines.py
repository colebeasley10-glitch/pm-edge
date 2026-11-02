from paths import DATA
"""Parse fetched ESPN pickcenter rows -> one market line per game (home perspective)."""
import json, pandas as pd, glob, os
PRI=['consensus','teamrankings','Caesars Sportsbook (New Jersey)','Caesars Sportsbook','DraftKings','ESPN BET','William Hill (New Jersey)','numberfire']
def pick(pc):
    by={p.get('provider',{}).get('name'):p for p in pc}
    for n in PRI:
        if n in by and by[n].get('spread') is not None: return n,by[n]
    for n,p in by.items():
        if p.get('spread') is not None: return n,p
    return None,None
def f(x):
    try: return float(x)
    except: return None
rows=[]
for fn in sorted(glob.glob(DATA+'lines/*.jsonl')):
    for l in open(fn):
        d=json.loads(l)
        if d['status']!='ok' or not d['pickcenter']: continue
        n,p=pick(d['pickcenter'])
        if not p: continue
        h,a=p.get('homeTeamOdds') or {},p.get('awayTeamOdds') or {}
        tot=[q.get('overUnder') for q in d['pickcenter'] if q.get('overUnder')]
        rows.append(dict(game_id=d['id'],provider=n,spread=f(p.get('spread')),total=f(p.get('overUnder')) or (f(tot[0]) if tot else None),
             home_ml=f(h.get('moneyLine')),away_ml=f(a.get('moneyLine')),home_spread_odds=f(h.get('spreadOdds')),away_spread_odds=f(a.get('spreadOdds')),
             over_odds=f(p.get('overOdds')),under_odds=f(p.get('underOdds')),home_fav=h.get('favorite')))
L=pd.DataFrame(rows).drop_duplicates('game_id')
L.to_parquet(DATA+'lines.parquet'); print(L.shape); print(L.provider.value_counts().head(8))
