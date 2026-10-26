"""Odds Fam: feed priority and caps, price gaps, paper grading, and Cole's logged bets."""
from datetime import datetime, timedelta, timezone

from research_lab import odds_fam as of
from research_lab.ev_data import Offer, Pick

NOW = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)      # noon ET
START = NOW + timedelta(hours=7)


def prop(i, pick="Josh Jacobs Under 51.5 Rushing Yards"):
    return {"play_id": f"p{i}", "league": "NFL", "game": f"Game {i}", "pick": pick, "line": 51.5,
            "move": "Pinnacle line 56.5 → 51.5", "limit": "500", "fair_prob": 0.56, "fair_odds": "-127",
            "target_odds": "-120", "target_decimal": 1.8214, "game_start": START.isoformat()}


def line(i):
    return {"play_id": f"l{i}", "league": "NCAA", "game": f"Game L{i}", "pick": "Under 61", "market": "total",
            "line": 61.0, "move": "Pinnacle total 63.5 → 61", "fair_prob": 0.55, "fair_odds": "-122",
            "target_odds": "-116", "target_decimal": 1.86, "game_start": START.isoformat()}


def gap(i, fair=0.55, dec=1.92, venue="Kalshi"):
    o = Offer(venue, "home", dec, "52¢", NOW, f"KXT-{i}", "https://kalshi.com")
    return Pick(f"g{i}", f"Team{i} ML", f"Game G{i}", "NBA", "basketball", "moneyline", START, fair, [o],
                f"KXNBAGAME-{i}", "YES", f"e{i}")


def test_priority_props_then_lines_then_gaps_and_alert_text(tmp_path):
    pushes = []
    info = of.update(NOW, tmp_path, new_props=[prop(1)], new_lines=[line(1)], picks=[gap(1)],
                     push=lambda t, m, u: pushes.append((t, m)) or True)
    rows = of.load(tmp_path)
    assert info["sent"] == 3 and [r["kind"] for r in rows] == ["Prop steam", "Line steam", "Price gap"]
    assert "bet at -120 or better" in pushes[0][1] and "Shop it" in pushes[0][1]
    assert "Best we found: Kalshi" in pushes[2][1]


def test_daily_cap_and_no_repeats(tmp_path):
    of.update(NOW, tmp_path, new_props=[prop(i) for i in range(20)])
    assert len(of.load(tmp_path)) == of.MAX_PER_ROUND
    for k in range(5):
        of.update(NOW + timedelta(minutes=30 * (k + 1)), tmp_path, new_props=[prop(i) for i in range(20)])
    assert len(of.load(tmp_path)) == of.MAX_PER_DAY


def test_gap_needs_2pct_unless_late_and_short(tmp_path):
    small = gap(2, fair=0.53, dec=1.92)          # +1.8%
    assert of.update(NOW, tmp_path, picks=[small])["sent"] == 0
    late = NOW.replace(hour=20)                  # 4pm ET, nothing sent yet today
    assert of.update(late, tmp_path, picks=[small])["sent"] == 1


def test_gap_outside_odds_band_skipped(tmp_path):
    assert of.update(NOW, tmp_path, picks=[gap(3, fair=0.30, dec=3.6)])["sent"] == 0


def test_price_gap_grades_from_kalshi_and_logged_bet_settles(tmp_path):
    of.update(NOW, tmp_path, picks=[gap(4)])
    play = of.load(tmp_path)[0]
    (tmp_path / of.DIR / "my_bets.csv").write_text(of.render_bets([
        {"bet_id": "b1", "of_id": play["of_id"], "pick": play["pick"], "book": "Rebet", "odds": "-105",
         "decimal": 1.9524, "stake": 20, "line_taken": ""}]))
    mk = {"KXNBAGAME-4": {"status": "finalized", "result": "yes"}}
    info = of.update(START + timedelta(hours=4), tmp_path, markets=mk)
    assert of.load(tmp_path)[0]["status"] == "won" and info["bets_settled"] == 1
    res = of.load_results(tmp_path)["b1"]
    assert res["status"] == "won" and abs(res["pl"] - 19.05) < 0.01


def test_prop_bet_at_the_old_line_regrades(tmp_path):
    from research_lab import prop_board
    of.update(NOW, tmp_path, new_props=[prop(5)])
    play = of.load(tmp_path)[0]
    # the source prop finished: actual 53 yards. Play (Under 51.5) lost; his Under 56.5 at the old line won.
    prop_board._save([{**prop(5), "side": "under", "open_line": 56.5, "status": "lost", "actual": 53, "pl": -10}],
                     tmp_path)
    (tmp_path / of.DIR / "my_bets.csv").write_text(of.render_bets([
        {"bet_id": "b2", "of_id": play["of_id"], "pick": play["pick"], "book": "Betr", "odds": "-115",
         "decimal": 1.8696, "stake": 10, "line_taken": "56.5"}]))
    of.update(START + timedelta(hours=5), tmp_path)
    assert of.load(tmp_path)[0]["status"] == "lost"
    assert of.load_results(tmp_path)["b2"]["status"] == "won"
