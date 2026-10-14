"""Live scores: matching Kalshi markets to ESPN games (ESPN-shaped fixtures, no network)."""
from datetime import date

from league import scores


def team(abbr, location, name, short=None):
    return {"abbreviation": abbr, "location": location, "name": name,
            "displayName": f"{location} {name}", "shortDisplayName": short or location}


def event(away, home, a_score, h_score, state="in", detail="Q4 - 12:35", start="2026-10-14T00:00Z"):
    return {"date": start, "name": f"{away['displayName']} at {home['displayName']}",
            "status": {"type": {"state": state, "shortDetail": detail, "completed": state == "post"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": str(h_score), "team": home},
                {"homeAway": "away", "score": str(a_score), "team": away}]}]}


CFB = [
    event(team("DEL", "Delaware", "Blue Hens"), team("MTU", "Middle Tennessee", "Blue Raiders"), 23, 26),
    event(team("FIU", "Florida International", "Panthers", "FIU"),
          team("JVST", "Jacksonville State", "Gamecocks", "Jax State"), 17, 17, detail="Halftime"),
]
NBA = [event(team("GS", "Golden State", "Warriors"), team("LAL", "Los Angeles", "Lakers", "Lakers"),
             101, 98, detail="4th - 2:10")]
UCL = [event(team("MUN", "Manchester United", "", "Man United"), team("ATM", "Atletico Madrid", "", "Atletico"),
             1, 2, state="post", detail="FT")]


def test_league_and_event_parsing():
    assert scores.league_path("KXNCAAFGAME-26OCT13DELMTU-MTU") == "football/college-football"
    assert scores.league_path("KXWNBAGAME-26OCT13X-Y") == "basketball/wnba"
    assert scores.league_path("KXUCL1H-26OCT13ATMMUN-ATM") == "soccer/uefa.champions"
    assert scores.league_path("KXATPCHALLENGERMATCH-26OCT13VILZAR-VIL") is None
    assert scores.parse_event("KXNCAAFGAME-26OCT13FIUJVST-JVST") == (date(2026, 10, 13), "FIUJVST")
    assert scores.parse_event("KXODIMATCH-26OCT131100NAMUSA-USA") == (date(2026, 10, 13), "NAMUSA")


def test_match_team_markets():
    g = scores.match("KXNCAAFGAME-26OCT13DELMTU-MTU", "Middle Tennessee wins", CFB)
    assert g and (g["away"], g["away_score"], g["home_score"], g["home"]) == ("DEL", "23", "26", "MTU")
    assert g["state"] == "in" and g["detail"] == "Q4 - 12:35" and g["mine"] == "home"
    g = scores.match("KXNCAAFGAME-26OCT13FIUJVST-JVST", "Jacksonville St. wins", CFB)
    assert g and g["detail"] == "Halftime" and g["mine"] == "home"


def test_match_totals_and_aliases():
    # no team in the title; Kalshi's GSW is ESPN's GS
    g = scores.match("KXNBATOTAL-26OCT13GSWLAL-230", "Full Game: Over 229.5 points scored", NBA)
    assert g and g["away_score"] == "101" and g["mine"] is None
    g = scores.match("KXUCL1H-26OCT13ATMMUN-ATM", "Atletico wins 1st Half", UCL)
    assert g and g["state"] == "post" and g["mine"] == "home"


def test_matchup_prefixed_title():
    ev = [event(team("MUG", "Mugla", "Spor"), team("BUR", "Bursa", "Spor"), 0, 1, detail="67'")]
    g = scores.match("KXEFLL1GAME-26OCT17MKDLUT-LUT", "Milton Keynes vs Luton: Luton wins", ev)
    assert g is None
    g = scores.match("KXTFF1LIGGAME-26OCT15MUGBUR-BUR", "Mugla vs Bursa: Bursa wins", ev)
    assert g and g["mine"] == "home"
    assert scores.league_path("KXEFLL1GAME-26OCT17MKDLUT-LUT") == "soccer/eng.3"


def test_no_match_returns_none():
    assert scores.match("KXNCAAFGAME-26OCT13ABCXYZ-ABC", "Nobody wins", CFB) is None


def test_for_bets_one_fetch_per_league_day():
    calls = []

    def fetch(path, day):
        calls.append((path, day))
        return CFB if "football" in path else []
    rows = [{"ticker": "KXNCAAFGAME-26OCT13DELMTU-MTU", "title": "Middle Tennessee wins"},
            {"ticker": "KXNCAAFGAME-26OCT13FIUJVST-JVST", "title": "Jacksonville St. wins"},
            {"ticker": "KXATPCHALLENGERMATCH-26OCT13VILZAR-VIL", "title": "Gonzalo Villanueva wins"}]
    out = scores.for_bets(rows, fetch)
    assert set(out) == {rows[0]["ticker"], rows[1]["ticker"]} and len(calls) == 1


def test_fetch_errors_are_quiet():
    def boom(*a, **k):
        raise OSError("offline")
    assert scores.fetch_events("football/nfl", date(2026, 10, 13), get=boom) == []


def test_team_colors_readable_on_dark():
    assert scores.team_color({"color": "CC0000"}) == "#CC0000"
    # near-black navy falls back to the alternate color
    assert scores.team_color({"color": "081E3F", "alternateColor": "B6862C"}) == "#B6862C"
    assert scores.team_color({"color": "000000", "alternateColor": "000000"}) == "#8796AD"
    assert scores.team_color({}) == "#8796AD"


def test_league_tags():
    assert scores.league_tag("KXNCAAFGAME-26OCT13DELMTU-MTU") == "NCAAF"
    assert scores.league_tag("KXATPCHALLENGERMATCH-26OCT13VILZAR-VIL") == "ATP"
    assert scores.league_tag("KXITFWMATCH-26OCT13BAYXUX-BAY") == "ITF"
    assert scores.league_tag("KXHIGHNY-26OCT14-B71.5") == "WEATHER"
    assert scores.league_tag("KXUCLGAME-26OCT14MCIPSG-MCI") == "UCL"
    assert scores.league_tag("KXSAUDIPLGAME-26OCT15AASALN-AAS") == "SOCCER"
