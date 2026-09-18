"""Read-only CollegeFootballData client for PM Edge NCAA modeling."""
import os, requests
BASE="https://api.collegefootballdata.com"

class CFBD:
    def __init__(self,key=None,timeout=20):
        self.key=key or os.getenv("CFBD_API_KEY")
        self.timeout=timeout
        if not self.key: raise RuntimeError("CFBD_API_KEY is not configured")
    def get(self,path,**params):
        r=requests.get(BASE+path,params=params,headers={"Authorization":f"Bearer {self.key}"},timeout=self.timeout)
        r.raise_for_status(); return r.json()
    def games(self,year,week=None,classification="fbs"):
        p={"year":year,"classification":classification,"seasonType":"regular"}
        if week is not None:p["week"]=week
        return self.get("/games",**p)
    def core(self,year):
        return self.get("/ratings/core",year=year)
    def team_box(self,year,week=None,team=None):
        p={"year":year,"seasonType":"regular"}
        if week is not None:p["week"]=week
        if team:p["team"]=team
        return self.get("/games/teams",**p)
    def advanced_box(self,game_id):
        return self.get("/game/box/advanced",id=game_id)
