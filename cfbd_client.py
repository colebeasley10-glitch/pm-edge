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
    def lines(self,year,week=None):
        p={"year":year,"seasonType":"regular"}
        if week is not None:p["week"]=week
        return self.get("/lines",**p)
    def team_box(self,year,week=None,team=None):
        p={"year":year,"seasonType":"regular"}
        if week is not None:p["week"]=week
        if team:p["team"]=team
        return self.get("/games/teams",**p)
    def advanced_box(self,game_id):
        return self.get("/game/box/advanced",id=game_id)


import json, time
from pathlib import Path


class CachedCFBD:
    """File-backed CFBD wrapper so a run every 30 minutes stays far under the free tier's
    monthly call limit. Each response is kept for `ttl` seconds; if a refresh fails
    (quota used up, bad key, outage) the last good copy is used instead, and only a
    missing key with nothing cached is fatal."""

    def __init__(self, path=".ncaa_beta_cfbd_cache.json", key=None, client=None, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.calls = 0
        self.errors = []
        try:
            self.cache = json.loads(self.path.read_text())
        except Exception:
            self.cache = {}
        try:
            self.client = client or CFBD(key)
        except RuntimeError as exc:
            self.client = None
            self.errors.append(str(exc))

    def _fetch(self, name, ttl, fn):
        hit = self.cache.get(name)
        if hit and self.clock() - hit.get("at", 0) < ttl:
            return hit["data"]
        if self.client is not None:
            try:
                data = fn(self.client)
                self.calls += 1
                self.cache[name] = {"at": self.clock(), "data": data}
                return data
            except Exception as exc:
                self.errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
        if hit:
            return hit["data"]
        raise RuntimeError("CFBD unavailable and nothing cached: " + "; ".join(self.errors))

    def games(self, year, ttl=12 * 3600):
        return self._fetch(f"games:{year}", ttl, lambda c: c.games(year))

    def core(self, year, ttl=12 * 3600):
        return self._fetch(f"core:{year}", ttl, lambda c: c.core(year))

    def lines(self, year, week, ttl=2 * 3600):
        return self._fetch(f"lines:{year}:{week}", ttl, lambda c: c.lines(year, week))

    def save(self):
        # keep the file small: drop entries older than two weeks
        cutoff = self.clock() - 14 * 86400
        self.cache = {k: v for k, v in self.cache.items() if v.get("at", 0) >= cutoff}
        self.path.write_text(json.dumps(self.cache))
