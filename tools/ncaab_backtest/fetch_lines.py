from paths import DATA
"""Fetch ESPN pickcenter (betting lines) for every completed game in a season by reading only
the tail of hoopR-mbb-raw's per-game JSON (HTTP Range). Resumable: appends to lines/{yr}.jsonl."""
import json, sys, re, time, urllib.request, pandas as pd
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
URL='https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-raw/main/mbb/json/final/{}.json'
def extract(s):
    i=s.find('"pickcenter"')
    if i<0: return None
    j=s.find('[',i); depth=0
    for k in range(j,len(s)):
        c=s[k]
        if c=='[': depth+=1
        elif c==']':
            depth-=1
            if depth==0: return json.loads(s[j:k+1])
    return None
def get(gid):
    for n in (30000,90000,300000):
        for attempt in range(4):
            try:
                req=urllib.request.Request(URL.format(gid),headers={'Range':f'bytes=-{n}'})
                s=urllib.request.urlopen(req,timeout=40).read().decode('utf8','replace'); break
            except urllib.error.HTTPError as e:
                if e.code==404: return {'id':gid,'status':'missing'}
                time.sleep(2+attempt*3)
            except Exception: time.sleep(2+attempt*3)
        else: return {'id':gid,'status':'error'}
        try: pc=extract(s)
        except Exception: pc=None
        if pc is not None: return {'id':gid,'status':'ok','pickcenter':pc}
    return {'id':gid,'status':'notfound'}
yr=int(sys.argv[1]); out=Path(DATA+f'lines/{yr}.jsonl')
done=set()
if out.exists():
    for l in out.open():
        try: d=json.loads(l)
        except: continue
        if d['status'] in ('ok','missing','notfound'): done.add(d['id'])
s=pd.read_parquet(DATA+f'raw/s{yr}.parquet'); s=s[s.status_type_completed==True]
ids=[int(g) for g in s.id if int(g) not in done]
print(yr,'todo',len(ids),flush=True)
with out.open('a') as f, ThreadPoolExecutor(24) as ex:
    for n,r in enumerate(ex.map(get,ids)):
        f.write(json.dumps(r)+'\n')
        if n%1000==0: print(yr,n,flush=True); f.flush()
print(yr,'done',flush=True)
