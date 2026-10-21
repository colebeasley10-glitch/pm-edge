"""Trader Follow: settle from final scores when Polymarket hasn't resolved yet."""
from datetime import datetime, timezone

from trader_follow import core
from trader_follow.scores import ScoreResolver, decide, find_game, parse_events


def espn(*games):
    evs = []
    for date, (h, hs), (a, as_), final in games:
        evs.append({"date": date, "competitions": [{"status": {"type": {"completed": final}}, "competitors": [
            {"homeAway": "home", "score": str(hs), "team": {"displayName": h[0], "shortDisplayName": h[1], "location": h[2]}},
            {"homeAway": "away", "score": str(as_), "team": {"displayName": a[0], "shortDisplayName": a[1], "location": a[2]}}]}]})
    return {"events": evs}


KNICKS = (("New York Knicks", "Knicks", "New York"), 100)
SIXERS = (("Philadelphia 76ers", "76ers", "Philadelphia"), 106)
NBA = espn(("2026-10-20T23:00Z", KNICKS, SIXERS, True))
BAYERN = espn(("2026-10-21T19:00Z", (("Bayern Munich", "Bayern", "Bayern Munich"), 2),
               (("Arsenal", "Arsenal", "Arsenal"), 2), True))


def row(q, outcome, idx, mtype, slug, start, sport="NBA"):
    return {"question": q, "outcome": outcome, "outcome_index": str(idx), "market_type": mtype, "event_slug": slug,
            "event": slug, "sport": sport, "game_start": start, "status": "open", "condition_id": "c" + q,
            "his_price": "0.6", "copy_price": "0.62"}


ROWS = [row("Spread: Knicks (-4.5)", "Knicks", 0, "spreads", "nba-phi-nyk-2026-10-20", "2026-10-20T23:00:00Z"),
        row("76ers vs. Knicks", "Knicks", 1, "moneyline", "nba-phi-nyk-2026-10-20", "2026-10-20T23:00:00Z"),
        row("Spread: Knicks (-5.5)", "76ers", 1, "spreads", "nba-phi-nyk-2026-10-20", "2026-10-20T23:00:00Z")]


def resolver():
    return ScoreResolver(fetch=lambda path, day: NBA if "nba" in path else BAYERN)


def test_the_stuck_knicks_bets_settle():
    r = resolver()
    assert [r(x) for x in ROWS] == ["lost", "lost", "won"]   # 76ers won by 6


def test_soccer_will_x_win_and_draw():
    r = resolver()
    win = row("Will FC Bayern München win on 2026-10-21?", "Yes", 0, "moneyline", "ucl-bay-ars-2026-10-21",
              "2026-10-21T19:00:00Z", "UCL")
    draw = {**win, "question": "Will Bayern Munich vs. Arsenal end in a draw?"}
    assert r(win) == "lost"            # 2-2: Bayern didn't win
    assert r({**win, "outcome": "No"}) == "won"


def test_not_final_or_no_match_waits():
    live = ScoreResolver(fetch=lambda p, d: espn(("2026-10-20T23:00Z", KNICKS, SIXERS, False)))
    assert live(ROWS[1]) is None
    wrong_time = {**ROWS[1], "game_start": "2026-10-22T23:00:00Z"}
    assert resolver()(wrong_time) is None
    assert resolver()({**ROWS[1], "event_slug": "xyz-a-b", "sport": "XYZ"}) is None


def test_spread_push_is_void_and_totals():
    ev = parse_events(espn(("2026-10-20T23:00Z", (("A", "A", "A"), 105), (("B", "B", "B"), 100), True)))
    r = row("Spread: A (-5)", "A", 0, "spreads", "nba-a-b", "2026-10-20T23:00:00Z")
    assert decide(r, *find_game(r, ev)) == "void"
    t = row("B vs. A: O/U 204.5", "Over", 0, "totals", "nba-a-b", "2026-10-20T23:00:00Z")
    assert decide(t, *find_game(t, ev)) == "won"


def test_polymarket_result_wins_and_score_fills_gaps():
    now = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
    rows = [dict(x) for x in ROWS]
    closed = {"closed": True, "outcomePrices": '["0.0", "1.0"]'}
    n = core.settle(rows, now, lambda cid: closed if cid == rows[0]["condition_id"] else {"closed": False},
                    get_score=resolver())
    assert n == 3
    assert rows[0]["settled_by"] == "polymarket" and rows[0]["status"] == "lost"
    assert rows[1]["settled_by"] == "final score" and rows[1]["status"] == "lost" and rows[1]["pl_copy"] == -10.0
    assert rows[2]["status"] == "won" and rows[2]["pl_copy"] > 0
