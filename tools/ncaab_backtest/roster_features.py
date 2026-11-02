from paths import DATA
"""Per team-season roster continuity features from player box scores (and 2026/2027 roster files).
ret_min  : share of last season's team minutes played by players on this season's roster
tin_min  : incoming D1 transfers' minutes share at their old team (sum; ~1.0 = a full lineup's worth)
tin_net  : minutes-weighted last-season net rating (O-D) of the teams those transfers came from
Roster for season S (historical): players who log minutes for the team in season S's first 30 days
(proxy for the preseason roster; documented approximation). 2026 and 2027 use ESPN roster files."""
import pandas as pd, numpy as np
R=DATA+'raw/'
F=pd.read_parquet(DATA+'final_ratings.parquet')
def pbox(yr):
    p=pd.read_parquet(R+f'pbox{yr}.parquet',columns=['game_id','game_date','athlete_id','team_id','minutes','did_not_play'])
    p=p[p.minutes.fillna(0)>0].copy(); p['aid']=pd.to_numeric(p.athlete_id,errors='coerce'); p['team_id']=p.team_id.astype(int)
    p['game_date']=pd.to_datetime(p.game_date); return p.dropna(subset=['aid'])
def roster(yr):
    try:
        if yr<2026: raise FileNotFoundError  # 2025 roster file is incomplete; proxy matches 2026 file (corr .96)
        r=pd.read_parquet(R+f'ros{yr}.parquet',columns=['team_id','athlete_id'])
        r['aid']=pd.to_numeric(r.athlete_id,errors='coerce'); r['team_id']=r.team_id.astype(int)
        return r.dropna(subset=['aid'])[['aid','team_id']].drop_duplicates()
    except FileNotFoundError:
        p=pbox(yr); start=p.game_date.min()
        p=p[p.game_date<start+pd.Timedelta(days=30)]
        return p[['aid','team_id']].drop_duplicates()
out=[]
for S in range(2013,2028):
    prev=pbox(S-1)
    m=prev.groupby(['aid','team_id']).minutes.sum().reset_index()
    tm=m.groupby('team_id').minutes.sum().rename('tmin')
    m=m.join(tm,on='team_id'); m['share']=m.minutes/m.tmin
    m=m.sort_values('share',ascending=False).drop_duplicates('aid')   # player's main team last season
    fr=F[F.season==S-1].set_index('team')
    m['src_net']=m.team_id.map(fr.O-fr.D)
    ros=roster(S)
    j=ros.merge(m[['aid','team_id','share','src_net']],on='aid',how='left',suffixes=('','_prev'))
    j['ret']=np.where(j.team_id==j.team_id_prev,j.share,0.0)
    tr=j[(j.team_id_prev.notna())&(j.team_id!=j.team_id_prev)&j.src_net.notna()]
    agg=j.groupby('team_id').ret.sum().rename('ret_min').to_frame()
    t2=tr.groupby('team_id').apply(lambda d: pd.Series({'tin_min':d.share.sum(),'tin_net':np.average(d.src_net,weights=d.share) if d.share.sum()>0 else 0.0}),include_groups=False)
    agg=agg.join(t2,how='left').fillna({'tin_min':0.0,'tin_net':0.0})
    agg['season']=S; out.append(agg.reset_index().rename(columns={'team_id':'team'}))
    print(S,len(agg),'ret_min mean',round(agg.ret_min.mean(),3),'tin_min mean',round(agg.tin_min.mean(),3),flush=True)
pd.concat(out).to_parquet(DATA+'roster_features.parquet')
