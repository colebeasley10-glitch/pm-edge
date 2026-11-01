"""Odds Fam v2: Target Price Board, exchange check, closes, board-bet grading, CLV report."""
import json
from datetime import datetime, timedelta, timezone

from research_lab import odds_fam as of
from research_lab.ev_data import Offer, Pick

NOW = datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=3)


def prop_row(p_over=0.55, line=25.5, open_line=25.5, open_p=0.50):
    return {"key": "ev1", "league": "NBA", "player": "Ajay Mitchell", "stat": "Pts+Rebs+Asts",
            "game": "Houston Rockets at Oklahoma City Thunder", "starts": START.isoformat().replace("+00:00", "Z"),
            "line": line, "p_over": p_over, "limit": 1500, "open_line": open_line, "open_p_over": open_p}


def total_picks(over_ask_dec=None, under_ask_dec=None):
    def offers(side, dec):
        return [Offer("Kalshi", side, dec, "x", NOW)] if dec else []
    over = Pick("KXNFLTOTAL-NEMIA-42:YES", "Over 42.5", "Miami at New England", "NFL", "football", "total",
                START, 0.46, offers("YES", over_ask_dec), "KXNFLTOTAL-NEMIA-42", "YES", "e1")
    under = Pick("KXNFLTOTAL-NEMIA-42:NO", "Under 42.5", "Miami at New England", "NFL", "football", "total",
                 START, 0.54, offers("NO", under_ask_dec), "KXNFLTOTAL-NEMIA-42", "NO", "e1")
    return [over, under]


def test_board_has_both_prop_sides_and_moves():
    rows = of.target_board([prop_row()], [], NOW)
    assert [r["side"] for r in rows] == ["over", "under"]
    over = rows[0]
    assert over["fair_odds"] == "-122" and over["moved"] == "Over +5%" and over["limit"] == 1500
    assert over["bet_at_decimal"] > 1 / 0.55


def test_exchange_check_gives_arb_price_like_hunters_cards():
    # Over 42.5 costs +115 on the exchange (after fees) -> Under is worth at least -115;
    # Hunter's card: Under -115 at Betr vs Over +115 on the exchange is a straight arb.
    rows = of.target_board([], total_picks(over_ask_dec=2.15, under_ask_dec=1.80), NOW)
    under = next(r for r in rows if r["pick"] == "Under 42.5")
    assert under["arb_at"] == "-115"
    assert "≥ -115" in under["exch"]
    # midpoint blended in (Pinnacle twice, exchange once)
    assert abs(under["fair_prob"] - (2 * 0.54 + (1 / 1.80 + (1 - 1 / 2.15)) / 2) / 3) < 1e-3


def test_exchange_blend_ignores_bad_matches():
    assert of.blended(0.54, 0.70) == 0.54
    assert of.blended(0.54, None) == 0.54


def test_closes_keep_last_pregame_price(tmp_path):
    of.update_board(NOW, tmp_path, prop_board_rows=[prop_row(0.55)])
    of.update_board(NOW + timedelta(hours=2), tmp_path, prop_board_rows=[prop_row(0.60)])
    of.update_board(START + timedelta(minutes=5), tmp_path, prop_board_rows=[prop_row(0.70)])   # after start: ignored
    c = of.load_closes(tmp_path)["prop:ev1:25.5:over"]
    assert c["p"] == 0.6


def _log(tmp_path, bets):
    d = tmp_path / of.DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "my_bets.csv").write_text(of.render_bets(bets))


def test_board_bet_gets_clv_and_grade(tmp_path):
    of.update_board(NOW, tmp_path, prop_board_rows=[prop_row(0.55)])
    row = of.load_board(tmp_path)["rows"][0]
    bet = {**of.bet_from_board(row, source="Hunter", book="Betr", odds="-112", stake=50), "bet_id": "b1",
           "logged_at": NOW.isoformat()}
    _log(tmp_path, [bet])
    of.update_board(NOW + timedelta(hours=2), tmp_path, prop_board_rows=[prop_row(0.60)])   # moved our way
    assert of.score_clv(NOW + timedelta(hours=1), tmp_path) == 0          # game not started yet
    assert of.score_clv(START + timedelta(minutes=1), tmp_path) == 1
    clv = of.load_clv(tmp_path)["b1"]["clv"]
    assert abs(clv - (0.60 * (1 + 100 / 112) - 1)) < 1e-3 and clv > 0.13
    n = of.settle_board_bets(START + timedelta(hours=5), tmp_path, box=lambda r: 31.0)
    assert n == 1 and of.load_results(tmp_path)["b1"]["status"] == "won"
    rep = of.clv_report(of.load_bets(tmp_path), of.load_results(tmp_path), of.load_clv(tmp_path))
    assert rep[0]["source"] == "Hunter" and rep[0]["beat_close"] == 1.0 and rep[0]["won"] == 1


def test_game_bet_grades_from_kalshi_and_line_change_drops_clv(tmp_path):
    of.update_board(NOW, tmp_path, picks=total_picks(2.15, 1.80))
    under = next(r for r in of.load_board(tmp_path)["rows"] if r["pick"] == "Under 42.5")
    b1 = {**of.bet_from_board(under, source="Odds Fam", book="Courtside", odds="-115", stake=20), "bet_id": "g1"}
    _log(tmp_path, [b1])
    assert of.bet_tickers(tmp_path) == ["KXNFLTOTAL-NEMIA-42"]
    mk = {"KXNFLTOTAL-NEMIA-42": {"ticker": "KXNFLTOTAL-NEMIA-42", "status": "finalized", "result": "no"}}
    of.settle_board_bets(START + timedelta(hours=5), tmp_path, markets=mk)
    assert of.load_results(tmp_path)["g1"]["status"] == "won"
    prop = of.target_board([prop_row()], [], NOW)[0]
    moved = of.bet_from_board(prop, source="Me", book="Onyx", odds="-110", stake=10, line_taken="24.5")
    assert moved["close_key"] == ""            # different number than the board: no apples-to-apples close


def test_bad_inputs_rejected():
    row = of.target_board([prop_row()], [], NOW)[0]
    for kw in ({"odds": "abc"}, {"odds": "-110", "source": "Bob"}):
        args = {"source": "Me", "book": "Betr", "odds": "-110", "stake": 10, **kw}
        try:
            of.bet_from_board(row, **args)
            assert False
        except ValueError:
            pass
