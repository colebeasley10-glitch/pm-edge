"""Live status: score, clock and winning/losing for in-progress plays (ESPN faked)."""
from live_status import Live, describe_polymarket, evaluate

START = "2026-11-01T18:00:00Z"


def team(name, short, abbr):
    return {"displayName": name, "shortDisplayName": short, "name": short, "abbreviation": abbr}


SCOREBOARD = {"events": [{"id": "401", "competitions": [{
    "status": {"type": {"state": "in", "shortDetail": "3rd 4:21"}},
    "competitors": [
        {"homeAway": "home", "score": "17", "team": team("Pittsburgh Steelers", "Steelers", "PIT")},
        {"homeAway": "away", "score": "10", "team": team("Cleveland Browns", "Browns", "CLE")}]}]}]}
SUMMARY = {"boxscore": {"players": [{"statistics": [
    {"name": "receiving", "labels": ["REC", "YDS"], "athletes": [
        {"athlete": {"displayName": "Blake Whiteheart"}, "stats": ["1", "3"]}]}]}]}}


class Resp:
    def __init__(self, data):
        self.status_code, self._d = 200, data

    def json(self):
        return self._d


class Sess:
    def get(self, url, params=None, timeout=None):
        return Resp(SUMMARY if url.endswith("/summary") else SCOREBOARD)


def run(bet):
    return evaluate({"league": "NFL", "start": START, **bet}, Live(session=Sess()))


def test_spread_dog_covering_and_favorite_short():
    r = run(describe_polymarket("Spread: Steelers (-3.5)", "Browns"))
    assert r["score"] == "CLE 10 – PIT 17" and r["clock"] == "3rd 4:21"
    assert r["status"] == "losing" and "short by 3.5" in r["note"]
    r = run(describe_polymarket("Spread: Steelers (-3.5)", "Steelers"))
    assert r["status"] == "winning" and "covering by 3.5" in r["note"]


def test_moneyline_and_total():
    assert run(describe_polymarket("Browns vs. Steelers", "Steelers"))["status"] == "winning"
    r = run(describe_polymarket("Browns vs. Steelers: O/U 41.5", "Under"))
    assert r["status"] == "winning" and "27 of 41.5" in r["note"]


def test_prop_from_live_box_score():
    r = run({"kind": "prop", "player": "Blake Whiteheart", "stat": "Receiving Yards", "line": 4.5, "side": "under",
             "teams": ["Cleveland Browns", "Pittsburgh Steelers"]})
    assert r["status"] == "winning" and r["note"].startswith("3 Receiving Yards of 4.5")


def test_final_says_won_or_lost():
    SCOREBOARD["events"][0]["competitions"][0]["status"]["type"].update(state="post", shortDetail="Final")
    try:
        assert run(describe_polymarket("Browns vs. Steelers", "Browns"))["status"] == "lost"
    finally:
        SCOREBOARD["events"][0]["competitions"][0]["status"]["type"].update(state="in", shortDetail="3rd 4:21")


def test_unknown_game_is_none():
    assert run({"kind": "ml", "team": "Raiders", "teams": ["Raiders", "Jets"]}) is None
