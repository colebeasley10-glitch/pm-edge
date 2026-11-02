from paths import DATA
"""Betting backtest. Calibrate on TRAIN, choose rule on VALIDATION, report untouched TEST.
Markets: spread, total, moneyline at the ESPN/consensus line captured after the game (≈ closing line)."""
import pandas as pd, numpy as np, json, sys
from math import erf, sqrt
D=DATA
TRAIN=list(range(2014,2020)); VAL=[2020,2021,2022]; TEST=[2023,2026]
def Phi(x): return 0.5*(1+np.vectorize(erf)(np.asarray(x)/sqrt(2)))
def dec(o):
    o=np.asarray(o,float); return np.where(o>0,1+o/100,1+100/np.abs(o))
def odds_or(x): 
    x=pd.to_numeric(x,errors='coerce'); return x.where(x.abs()>=100,-110.0)
def load(tag):
    P=pd.read_parquet(D+f'preds_{tag}.parquet'); L=pd.read_parquet(D+'lines.parquet')
    df=P.merge(L,on='game_id',how='inner')
    df=df[df.spread.notna()&(df.spread.abs()<60)]
    # sanity: favorite by ML should be favorite by spread
    ok=df.home_ml.isna()|df.away_ml.isna()|((df.home_ml<df.away_ml)==(df.spread<0))|(df.spread==0)
    df=df[ok].copy()
    df['month']=pd.to_datetime(df.day).dt.month
    return df
def calibrate(df):
    """y = (actual - line) regressed on (model - line) with no intercept for spread; with intercept for totals."""
    e=df.pred_margin+df.spread; y=df.margin+df.spread
    b=float((e*y).sum()/(e*e).sum()); s=float(np.std(y-b*e))
    t=df[df.total_line.notna()&(df.total_line>90)&df.pred_total.notna()]
    et=t.pred_total-t.total_line; yt=t.total_act-t.total_line
    A=np.vstack([np.ones(len(et)),et]).T; (a_t,b_t),*_=np.linalg.lstsq(A,yt.values,rcond=None)
    st=float(np.std(yt-a_t-b_t*et))
    return dict(b_s=b,sd_s=s,a_t=float(a_t),b_t=float(b_t),sd_t=st,n=len(df))
def prep(df):
    df=df.rename(columns={'total_x':'total_act','total_y':'total_line'}) if 'total_x' in df else df
    return df
def bets(df,c,tau_s,tau_t,tau_m,ml_band=(-250,250)):
    out=[]
    # spreads
    e=df.pred_margin+df.spread; mu=c['b_s']*e            # expected (margin + spread)
    ph=1-Phi(-mu/c['sd_s'])                              # P(home covers)
    for side,p,o,res in (('home',ph,odds_or(df.home_spread_odds),df.margin+df.spread),('away',1-ph,odds_or(df.away_spread_odds),-(df.margin+df.spread))):
        ev=p*dec(o)-1
        m=ev>=tau_s
        out.append(pd.DataFrame(dict(game_id=df.game_id[m],season=df.season[m],month=df.month[m],market='SPREAD',side=side,p=p[m],odds=o[m],ev=ev[m],
              edge_pts=(e if side=='home' else -e)[m],res=res[m],line=(df.spread if side=='home' else -df.spread)[m],day=df.day[m],
              fav=((df.spread<0) if side=='home' else (df.spread>0))[m])))
    # totals
    t=df[df.total_line.notna()&(df.total_line>90)]
    et=t.pred_total-t.total_line; mu=c['a_t']+c['b_t']*et
    po=1-Phi(-mu/c['sd_t'])
    for side,p,o,res in (('over',po,odds_or(t.over_odds),t.total_act-t.total_line),('under',1-po,odds_or(t.under_odds),t.total_line-t.total_act)):
        ev=p*dec(o)-1; m=ev>=tau_t
        out.append(pd.DataFrame(dict(game_id=t.game_id[m],season=t.season[m],month=t.month[m],market='TOTAL',side=side,p=p[m],odds=o[m],ev=ev[m],
              edge_pts=(et if side=='over' else -et)[m],res=res[m],line=t.total_line[m],day=t.day[m],fav=False)))
    # moneyline: win prob from the market spread nudged by the calibrated model edge
    u=df[df.home_ml.notna()&df.away_ml.notna()&(df.home_ml.abs()>=100)&(df.away_ml.abs()>=100)]
    e=u.pred_margin+u.spread; m_star=-u.spread+c['b_s']*e
    phw=Phi(m_star/c['sd_s'])
    for side,p,o,res in (('home',phw,u.home_ml,u.margin),('away',1-phw,u.away_ml,-u.margin)):
        ev=p*dec(o)-1; m=(ev>=tau_m)&(o>=ml_band[0])&(o<=ml_band[1])
        out.append(pd.DataFrame(dict(game_id=u.game_id[m],season=u.season[m],month=u.month[m],market='ML',side=side,p=p[m],odds=o[m],ev=ev[m],
              edge_pts=np.nan,res=res[m],line=0.0,day=u.day[m],fav=(o<0)[m])))
    B=pd.concat(out,ignore_index=True)
    B=B.sort_values('ev',ascending=False).drop_duplicates(['game_id','market'])   # never both sides
    B['result']=np.where(B.res>0,'W',np.where(B.res<0,'L','P'))
    B['units']=np.where(B.res>0,dec(B.odds)-1,np.where(B.res<0,-1.0,0.0))
    return B.sort_values('day')
def summ(B,by=None):
    def f(g):
        n=len(g); w=(g.result=='W').sum(); l=(g.result=='L').sum(); u=g.units.sum()
        se=g.units.std(ddof=1)/np.sqrt(n) if n>1 else np.nan
        return pd.Series(dict(bets=n,W=w,L=l,P=n-w-l,units=round(u,2),roi=round(u/n*100,2) if n else np.nan,
                              roi_lo95=round((u/n-1.96*se)*100,2) if n>1 else np.nan,roi_hi95=round((u/n+1.96*se)*100,2) if n>1 else np.nan,
                              win_pct=round(w/max(w+l,1)*100,2),avg_ev=round(g.ev.mean()*100,2)))
    if by is None: return f(B).to_frame().T
    return B.groupby(by).apply(f,include_groups=False)
def max_dd(units):
    c=np.cumsum(units); peak=np.maximum.accumulate(np.concatenate([[0],c]))[1:]; return float((peak-c).max()) if len(c) else 0.0
