"""Hyperparameter grid on TRAIN seasons 2014-2019 only. Scored on margin RMSE and total RMSE (no lines needed)."""
from backtest import *
import itertools, json, sys
from multiprocessing import Pool
TRAIN=[2014,2015,2016,2017,2018,2019]
def job(args):
    S,hl,pg,cap=args
    df=run_season(S,dict(half_life=hl,prior_games=pg,tempo_prior_games=pg,eff_cap=cap))
    e=df.margin-df.pred_margin; et=df.total-df.pred_total
    return dict(S=S,hl=hl,pg=pg,cap=cap,n=len(df),sse=float((e**2).sum()),sset=float((et**2).sum()),ae=float(e.abs().sum()))
if __name__=='__main__':
    grid=list(itertools.product(TRAIN,[90],[1.5,2.0,3.0],[40,60]))
    with Pool(int(sys.argv[1]) if len(sys.argv)>1 else 4) as p: res=p.map(job,grid)
    r=pd.DataFrame(res).groupby(['hl','pg','cap'])[['n','sse','sset','ae']].sum()
    r['rmse']=np.sqrt(r.sse/r.n); r['rmse_tot']=np.sqrt(r.sset/r.n); r['mae']=r.ae/r.n
    print(r[['n','rmse','mae','rmse_tot']].sort_values('rmse').round(3).to_string())
    r.to_csv(DATA+'tune_results2.csv')
