"""Lane detail split by sport / bet type (Nov 4, 2026)."""
import scorecard as sc


def test_categories_across_lane_formats():
    assert sc.bet_category({"ticker": "KXNFLSPREAD-26NOV09KCDEN-KC3"}) == "NFL"
    assert sc.bet_category({"ticker": "KXNCAAFGAME-26NOV08OSUMICH-OSU"}) == "NCAAF"
    assert sc.bet_category({"ticker": "KXATPMATCH-26NOV05AB-A"}) == "Tennis"
    assert sc.bet_category({"league": "NBA", "stat": "Points", "player": "X"}) == "NBA Props"
    assert sc.bet_category({"league": "NCAA", "stat": "Rushing Yards"}) == "NCAAF Props"
    assert sc.bet_category({"league": "NCAA", "market": "spread", "line": "-3"}) == "NCAAF"
    assert sc.bet_category({"league": "NCAA", "market": "total", "line": "145.5"}) == "NCAAB"
    assert sc.bet_category({"league": "NBA", "source": "nba_props", "lane": "NBA Props — Strong + Lean"}) == "NBA Props"
    assert sc.bet_category({"sport": "NFL"}) == "NFL"
    assert sc.bet_category({"market": "SPREAD"}, "NCAA Grader — STRONG") == "NCAAF"


def test_bet_type():
    assert sc.bet_type({"pick": "Akron +12.5"}) == "Spread"
    assert sc.bet_type({"pick": "Buffalo ML +190"}) == "Moneyline"
    assert sc.bet_type({"pick": "OVER 51.5"}) == "Total"
    assert sc.bet_type({"market": "ML"}) == "Moneyline"


def test_split_regrades_with_lane_rules():
    rows = [{"league": "NFL", "pl": "9.09", "status": "won", "clv": "0.02"},
            {"league": "NFL", "pl": "-10", "status": "lost", "clv": "-0.01"},
            {"league": "NBA", "pl": "9.09", "status": "won", "clv": "0.03"},
            {"league": "NBA", "pl": "", "status": "open"}]
    lane = sc.grade("Test", rows, risk=10.0, clv="clv")
    parts = {g["name"]: g for g in sc.split(lane)}
    assert parts["NFL"]["won"] == 1 and parts["NFL"]["lost"] == 1 and parts["NFL"]["risk"] == 20.0
    assert parts["NBA"]["n"] == 1 and len(parts["NBA"]["rows"]) == 2
    assert sum(g["pl"] for g in parts.values()) == lane["pl"]
