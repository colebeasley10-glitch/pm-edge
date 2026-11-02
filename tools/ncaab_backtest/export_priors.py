from paths import DATA
"""Preseason priors for 2026-27 (season 2027): coefficients fit on every completed season 2014-2026."""
import pandas as pd, numpy as np, json
import priors as PR
D=DATA
F=PR.F; G=PR.G
s27=pd.read_parquet(D+'raw/s2027.parquet')
d1_26=set(F[F.season==2026].team)
cnt=pd.concat([s27.home_id,s27.away_id]).astype(int).value_counts()
d1=sorted(d1_26 | set(cnt[cnt>=20].index))   # + teams with a full D1 schedule this year (new D1 programs)
c=PR.fit(range(2014,2027))
P=PR.predict(2027,c,d1)
X=PR.features(2027,d1)
names={}
for side in ('home','away'):
    for r in s27[[f'{side}_id',f'{side}_display_name']].itertuples(index=False): names[int(r[0])]=r[1]
for r in G[G.season==2026][['home_id','home_display_name']].itertuples(index=False): names.setdefault(int(r[0]),r[1])
out=P.join(X[['conf','ret_min','tin_min','tin_net','new']])
out['team_name']=[names.get(int(t),str(t)) for t in out.index]
fs=F[F.season==2026].set_index('team')
out['net_2026']=(fs.O-fs.D).reindex(out.index)
out['prior_net']=out.O-out.D
out=out.reset_index().rename(columns={'team':'team_id','O':'prior_o','D':'prior_d','P':'prior_tempo'})
out=out[['team_id','team_name','conf','prior_o','prior_d','prior_tempo','prior_net','net_2026','ret_min','tin_min','tin_net','new']].sort_values('prior_net',ascending=False)
out.round(3).to_csv(str(__import__('pathlib').Path(__file__).resolve().parents[2])+'/ncaab_model/data/priors_2027.csv',index=False)
mu,tp=float(F[F.season==2026].mu.iloc[0]),float(F[F.season==2026].tempo.iloc[0])
json.dump({'season':2027,'league_eff':round(mu,3),'league_tempo':round(tp,3),'built_from':'hoopR/ESPN 2013-2026 results+box scores, 2027 ESPN rosters',
           'coefficients':{k:{n:round(float(v),4) for n,v in c[k].items()} for k in c}},open(str(__import__('pathlib').Path(__file__).resolve().parents[2])+'/ncaab_model/data/priors_2027_meta.json','w'),indent=1)
print(len(out)); print(out.head(25)[['team_name','prior_net','net_2026','ret_min','tin_min']].to_string()); print(out.tail(5)[['team_name','prior_net','new']].to_string())
