from paths import DATA
"""End-of-season ratings for every season (targets for the prior regression + inputs to next prior)."""
from common import *
out=[]
for S in range(2012,2027):
    g=season_games(S); last=max(g.day)
    mu,tp=league_level(S)
    r=Ratings(recs(g),as_of=last+timedelta(days=1),prior={},league=(mu,tp),half_life=10000,prior_games=1.0,tempo_prior_games=1.0)
    for t,i in r.idx.items():
        out.append(dict(season=S,team=t,O=r.O[i],D=r.D[i],P=r.P[i],games=r.n_games[t],mu=r.mu,hca=r.hca,tempo=r.tempo))
    print(S,'mu',round(r.mu,2),'hca',round(r.hca,2),'tempo',round(r.tempo,2),'teams',len(r.idx),flush=True)
pd.DataFrame(out).to_parquet(DATA+'final_ratings.parquet')
