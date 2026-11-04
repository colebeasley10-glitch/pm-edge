"""Nov 4, 2026: model fair values are shrunk toward the market and capped at 10 points of edge."""
import fair_calibration as fc


def test_shrinks_overconfident_model_toward_market():
    fc._cache["ncaa_grader"] = (1e18, {"SPREAD": 0.45})
    row = {"model_prob": "0.95", "odds": "-110", "market": "SPREAD"}
    fair = fc.calibrated("ncaa_grader", row)
    m = fc.market_prob(row)
    assert abs(m - 0.5) < 0.01
    assert fair <= m + fc.MAX_EDGE + 1e-9          # was 95% / +81% EV on Sacramento State +26.5


def test_uses_ledger_market_prob_when_present():
    fc._cache["nba_grader"] = (1e18, {"ML": 0.3})
    row = {"model_prob": "0.62", "market_prob": "0.55", "odds": "-138", "market": "ML"}
    fair = fc.calibrated("nba_grader", row)
    assert 0.55 < fair < 0.62


def test_other_sources_untouched():
    assert fc.calibrated("nba_props", {"model_prob": "0.58", "odds": "+104", "market": "PROP"}) == 0.58


def test_fit_weight_prior_and_cap():
    assert abs(fc.fit_weight([]) - fc.PRIOR_W) < 1e-9
    # a model that's exactly right a lot of the time can't earn more than MAX_W
    perfect = [(0.9, 0.5, True)] * 2000
    assert fc.fit_weight(perfect) <= fc.MAX_W
    # a model that's always wrong when it disagrees gets pulled to ~0 as the sample grows
    wrong = [(0.8, 0.5, False), (0.8, 0.5, True)] * 50 + [(0.8, 0.5, False)] * 400
    assert fc.fit_weight(wrong) < 0.1
