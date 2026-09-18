"""CFBD credential/data smoke test. Safe: read-only and prints no secret."""
from datetime import datetime
from cfbd_client import CFBD
from ncaa_predictive import ratings_map,project

year=datetime.now().year
c=CFBD()
games=c.games(year)
core=c.core(year)
rm=ratings_map(core)
scheduled=[g for g in games if not g.get("completed") and g.get("homeTeam") in rm and g.get("awayTeam") in rm]
print({"cfbd_auth":"ok","year":year,"games":len(games),"core_ratings":len(core),"projectable_games":len(scheduled)})
for g in scheduled[:3]:
    p=project(g["homeTeam"],g["awayTeam"],rm,neutral=bool(g.get("neutralSite")))
    print({"game":f'{p.away} @ {p.home}',"projected_score":f'{p.away_points}-{p.home_points}',"total":p.total,"home_margin":p.home_margin,"confidence":p.confidence})
