import sys, json
from backtest import *
from multiprocessing import Pool
SEASONS=list(range(2014,2027))
params=json.loads(sys.argv[2]) if len(sys.argv)>2 else {}
tag=sys.argv[1]
mode={'v1':('full',False),'noprior':('none',False),'lastyr':('lastyr',False),'points':('full',True)}[tag.split('_')[0]]
def job(S): return run_season(S,params,prior_mode=mode[0],points_only=mode[1])
if __name__=='__main__':
    with Pool(2) as p: dfs=p.map(job,SEASONS)
    df=pd.concat(dfs); df.to_parquet(DATA+f'preds_{tag}.parquet'); print(tag,len(df))
