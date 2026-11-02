from paths import DATA
"""Preseason prior regression: season-end rating ~ last season's rating x roster continuity + transfers + conference."""
import pandas as pd, numpy as np
D=DATA
F=pd.read_parquet(D+'final_ratings.parquet'); RF=pd.read_parquet(D+'roster_features.parquet')
G=pd.read_parquet(D+'games.parquet')
def conf_map(S):
    if S==2027:
        s=pd.read_parquet(D+'raw/s2027.parquet')
    else:
        s=G[G.season==S]
    c=pd.concat([s[['home_id','home_conference_id']].set_axis(['team','conf'],axis=1),s[['away_id','away_conference_id']].set_axis(['team','conf'],axis=1)])
    c=c.dropna(); c['team']=c.team.astype(int)
    return c.groupby('team').conf.agg(lambda x:x.mode().iloc[0])
def features(S, d1=None):
    prev=F[F.season==S-1].set_index('team')[['O','D','P']]
    teams=d1 if d1 is not None else F[F.season==S].team.tolist()
    X=pd.DataFrame(index=pd.Index(teams,name='team'))
    X['conf']=conf_map(S).reindex(X.index)
    X=X.join(prev.add_suffix('_prev'))
    X['new']=X.O_prev.isna().astype(float)
    for k in 'ODP':
        cm=X.groupby('conf')[f'{k}_prev'].transform('mean')
        X[f'{k}_conf']=cm.fillna(prev[k].quantile(0.1))       # unknown conference: weak-team level
        X[f'{k}_prev']=X[f'{k}_prev'].fillna(X[f'{k}_conf'] - (2.0 if k!='P' else 0) * (1 if k=='O' else -1 if k=='D' else 0))
    rf=RF[RF.season==S].set_index('team')
    X=X.join(rf[['ret_min','tin_min','tin_net']]).fillna({'ret_min':RF.ret_min.mean(),'tin_min':0,'tin_net':0})
    return X
def design(X,k):
    p=X[f'{k}_prev']; r=X.ret_min-0.45
    cols={'prev':p,'prev_x_ret':p*r,'conf':X[f'{k}_conf'],'ret':r,'new':X.new}
    if k!='P': cols.update({'tin':X.tin_min,'tin_x_net':X.tin_min*X.tin_net})
    M=pd.DataFrame(cols); M.insert(0,'const',1.0); return M
def fit(seasons):
    coefs={}
    for k in 'ODP':
        Xs,ys=[],[]
        for S in seasons:
            X=features(S); y=F[F.season==S].set_index('team')[k].reindex(X.index)
            Xs.append(design(X,k)); ys.append(y)
        A=pd.concat(Xs); y=pd.concat(ys)
        beta,*_=np.linalg.lstsq(A.values,y.values,rcond=None)
        coefs[k]=pd.Series(beta,index=A.columns)
    return coefs
def predict(S,coefs,d1=None):
    X=features(S,d1)
    return pd.DataFrame({k:design(X,k).values@coefs[k].values for k in 'ODP'},index=X.index)
def score(S,coefs):
    P=predict(S,coefs); y=F[F.season==S].set_index('team').reindex(P.index)
    net_p=P.O-P.D; net_y=y.O-y.D
    naive=(F[F.season==S-1].set_index('team').reindex(P.index))
    nn=(naive.O-naive.D).fillna(net_p)
    return dict(S=S,rmse_net=float(np.sqrt(((net_p-net_y)**2).mean())),rmse_lastyr=float(np.sqrt(((nn-net_y)**2).mean())),
                rmse_zero=float(np.sqrt((net_y**2).mean())),corr=float(np.corrcoef(net_p,net_y)[0,1]))
if __name__=='__main__':
    c=fit(range(2014,2020))
    for k in 'ODP': print(k, c[k].round(3).to_dict())
    for S in range(2020,2023): print(score(S,c))
