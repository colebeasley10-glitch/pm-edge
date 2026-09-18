"""NCAA pregame projection engine. Uses only data available through prior completed games."""
from dataclasses import dataclass
import math

@dataclass
class Projection:
    home:str; away:str; home_points:float; away_points:float; total:float; home_margin:float
    home_win_prob:float; inputs:list; confidence:str

def _norm_cdf(x): return .5*(1+math.erf(x/math.sqrt(2)))

def project(home,away,ratings,*,neutral=False,national_total=55.0,home_field=2.2):
    """CORE-based v1 projection. CORE offense/defense are opponent-relative points/100 plays.
    We intentionally shrink them heavily because this is a first calibrated feature layer."""
    h=ratings.get(home); a=ratings.get(away)
    if not h or not a:return None
    # Convert per-100-play relative efficiencies to a conservative per-game adjustment.
    scale=.32
    h_adj=scale*(float(h["offense"])-float(a["defense"]))
    a_adj=scale*(float(a["offense"])-float(h["defense"]))
    hf=0 if neutral else home_field
    hp=national_total/2+h_adj+hf/2
    ap=national_total/2+a_adj-hf/2
    margin=hp-ap; total=hp+ap
    # CFB margin SD is intentionally broad; probability is not treated as certainty.
    win=_norm_cdf(margin/16.0)
    plays=min(int(h.get("offensePlays",0)),int(a.get("offensePlays",0)))
    conf="medium" if plays>=250 else "low"
    return Projection(home,away,round(hp,1),round(ap,1),round(total,1),round(margin,1),round(win,4),
                      ["CFBD CORE opponent-relative offense/defense","home-field adjustment","conservative efficiency shrinkage"],conf)

def ratings_map(rows,through_week=None):
    out={}
    for r in rows:
        if through_week is not None and int(r.get("throughWeek",999))>=through_week: continue
        old=out.get(r["team"])
        if old is None or int(r.get("throughWeek",0))>int(old.get("throughWeek",0)):out[r["team"]]=r
    return out
