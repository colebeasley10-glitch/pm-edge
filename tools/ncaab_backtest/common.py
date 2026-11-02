from paths import DATA
import sys, pandas as pd, numpy as np
from datetime import timedelta
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from ncaab_model.ratings import Ratings
G=pd.read_parquet(DATA+'games.parquet')
G=G[G.home_d1&G.away_d1].copy()
G['home']=G.home_id.astype(int); G['away']=G.away_id.astype(int)
def recs(df):
    return [dict(day=r.day,home=r.home,away=r.away,home_pts=float(r.home_score),away_pts=float(r.away_score),
                 poss=(None if pd.isna(r.poss) else float(r.poss)),neutral=bool(r.neutral_site)) for r in df.itertuples()]
def season_games(S): return G[G.season==S]
def league_level(S):
    g=G[(G.season==S)&G.poss.notna()]
    eff=100*(g.home_score+g.away_score).sum()/(2*g.poss.sum()); return float(eff), float(g.poss.mean())
