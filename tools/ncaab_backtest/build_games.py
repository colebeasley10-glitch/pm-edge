from paths import DATA
"""Build one clean games table (2012-2026): result, location, possessions, four factors, D1 flags."""
import pandas as pd, numpy as np
R=DATA+'raw/'
rows=[]
for yr in range(2012,2027):
    s=pd.read_parquet(R+f's{yr}.parquet')
    s=s[(s.status_type_completed==True)&s.home_score.notna()&s.away_score.notna()]
    s=s[(s.home_score+s.away_score)>0]
    t=pd.read_parquet(R+f'tbox{yr}.parquet')
    tov='total_turnovers' if 'total_turnovers' in t else 'turnovers'
    t=t.assign(tov=t[tov].fillna(t.get('turnovers')))
    t['poss']=t.field_goals_attempted-t.offensive_rebounds+t.tov+0.475*t.free_throws_attempted
    keep=['game_id','team_id','poss','field_goals_made','field_goals_attempted','three_point_field_goals_made',
          'three_point_field_goals_attempted','free_throws_made','free_throws_attempted','offensive_rebounds',
          'defensive_rebounds','tov']
    t=t[keep].drop_duplicates(['game_id','team_id'])
    g=s[['id','season','season_type','date','neutral_site','conference_competition','home_id','away_id',
         'home_display_name','away_display_name','home_short_display_name','away_short_display_name',
         'home_score','away_score','home_conference_id','away_conference_id','notes_headline','venue_address_state']].copy()
    g=g.rename(columns={'id':'game_id'}).drop_duplicates('game_id')
    h=t.add_prefix('h_').rename(columns={'h_game_id':'game_id','h_team_id':'home_id'})
    a=t.add_prefix('a_').rename(columns={'a_game_id':'game_id','a_team_id':'away_id'})
    g=g.merge(h,on=['game_id','home_id'],how='left').merge(a,on=['game_id','away_id'],how='left')
    # D1 = teams with >=12 games in the season file (non-D1 opponents play only a handful)
    cnt=pd.concat([g.home_id,g.away_id]).value_counts()
    d1=set(cnt[cnt>=12].index)
    g['home_d1']=g.home_id.isin(d1); g['away_d1']=g.away_id.isin(d1)
    rows.append(g)
G=pd.concat(rows,ignore_index=True)
G['dt']=pd.to_datetime(G.date,utc=True)
G['day']=G.dt.dt.tz_convert('America/New_York').dt.date
G['poss']=(G.h_poss+G.a_poss)/2
bad=(G.poss<45)|(G.poss>100)
G.loc[bad,'poss']=np.nan
G=G.sort_values('dt').reset_index(drop=True)
G.to_parquet(DATA+'games.parquet')
print(G.shape); print(G.groupby('season').agg(n=('game_id','size'),d1=('home_d1','sum'),poss=('poss','mean'),poss_na=('poss',lambda x:x.isna().mean())))
