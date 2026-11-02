"""Walk-forward daily predictions. For each game day d of season S, ratings are fit only on
season-S games before d, pulled toward a preseason prior built from seasons < S."""
from common import *
import priors as PR
F=pd.read_parquet(DATA+'final_ratings.parquet')
_coef_cache={}
def prior_for(S, mode='full'):
    if mode=='none': return {}
    if mode=='lastyr':   # last season's final rating shrunk 50%, no roster info
        f=F[F.season==S-1]; return {int(r.team):(0.5*r.O,0.5*r.D,0.5*r.P) for r in f.itertuples()}
    train=tuple(range(2014,max(S,2020)))     # walk-forward: only seasons before S (>=2014-2019)
    if train not in _coef_cache: _coef_cache[train]=PR.fit(train)
    P=PR.predict(S,_coef_cache[train])
    return {int(t):(r.O,r.D,r.P) for t,r in P.iterrows()}
def run_season(S, params=None, prior_mode='full', points_only=False):
    params=params or {}
    g=season_games(S).copy()
    if points_only: g['poss']=100.0
    R_=recs(g); days=sorted(set(g.day))
    prev=F[F.season==S-1]
    mu0,tp0=(100.0,100.0) if points_only else league_level(S-1)
    pr=prior_for(S,prior_mode)
    if points_only: pr={t:(v[0]*0.69,v[1]*0.69,0.0) for t,v in pr.items()}
    out=[]
    for d in days:
        r=Ratings(R_,as_of=d,prior=pr,league=(mu0,tp0),**params)
        for x in g[g.day==d].itertuples():
            p=r.project(x.home,x.away,bool(x.neutral_site))
            if p is None: continue
            out.append(dict(game_id=x.game_id,season=S,day=d,home=x.home,away=x.away,neutral=bool(x.neutral_site),
                pred_margin=p.home_margin,pred_total=p.total,pred_poss=p.poss,hg=r.n_games.get(x.home,0),ag=r.n_games.get(x.away,0),
                margin=x.home_score-x.away_score,total=x.home_score+x.away_score))
    return pd.DataFrame(out)
def elo_season(S, k=0.0, carry=None):
    pass
if __name__=='__main__':
    import time; t=time.time(); df=run_season(2019); print(time.time()-t, len(df))
    e=df.margin-df.pred_margin; print('MAE',e.abs().mean(),'RMSE',np.sqrt((e**2).mean()),'total MAE',(df.total-df.pred_total).abs().mean())

def elo_run(seasons, K=0.06, hfa=3.3, carry=0.7):
    """Margin-based Elo (in points): pred = Rh - Ra + hfa*site; R += K*(actual - pred). Pregame only."""
    R={}; out=[]
    for S in seasons:
        R={t:carry*v for t,v in R.items()}
        g=season_games(S)
        for x in g.itertuples():
            s=0 if x.neutral_site else 1
            p=R.get(x.home,0)-R.get(x.away,0)+hfa*s
            act=x.home_score-x.away_score
            out.append(dict(game_id=x.game_id,season=S,elo_margin=p))
            err=max(-25,min(25,act-p))
            R[x.home]=R.get(x.home,0)+K*err; R[x.away]=R.get(x.away,0)-K*err
    return pd.DataFrame(out)
