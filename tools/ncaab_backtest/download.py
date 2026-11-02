"""Step 1: download public hoopR/ESPN data (GitHub-hosted, free): schedules, team + player box
scores 2012-2026, ESPN rosters 2026-2027, and the 2027 schedule. Then run, in order:
  python download.py && python build_games.py && python final_ratings.py && python roster_features.py
  for y in 2014 ... 2023 2026; do python fetch_lines.py $y; done && python lines.py
  python tune.py 2 && python predict_all.py v1 '{"half_life":90,"prior_games":3,"tempo_prior_games":3,"eff_cap":40}'
  (also: noprior / lastyr / points for the comparison table)
  python report_bt.py && python era.py && python export_priors.py
"""
import urllib.request
from paths import DATA
REL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
def get(url, out):
    try:
        urllib.request.urlretrieve(url, DATA + "raw/" + out); print("ok", out)
    except Exception as e:
        print("MISSING", out, e)
for y in range(2012, 2028):
    get(f"{REL}espn_mens_college_basketball_schedules/mbb_schedule_{y}.parquet", f"s{y}.parquet")
for y in range(2012, 2027):
    get(f"{REL}espn_mens_college_basketball_team_boxscores/team_box_{y}.parquet", f"tbox{y}.parquet")
    get(f"{REL}espn_mens_college_basketball_player_boxscores/player_box_{y}.parquet", f"pbox{y}.parquet")
for y in (2026, 2027):
    get(f"{REL}espn_mens_college_basketball_rosters/rosters_{y}.parquet", f"ros{y}.parquet")
