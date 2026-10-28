import pytest

import odds_calc as oc


@pytest.mark.parametrize("txt,exp", [("+150", 150), ("150", 150), ("-200", -200), (" -110 ", -110), ("even", 100)])
def test_parse_ok(txt, exp):
    assert oc.parse_american(txt) == exp


@pytest.mark.parametrize("txt", ["", "abc", "50", "-99", "nan"])
def test_parse_bad(txt):
    assert oc.parse_american(txt) is None


def test_conversions_round_trip():
    assert oc.american_to_prob(150) == pytest.approx(0.40)
    assert oc.american_to_prob(-200) == pytest.approx(2 / 3)
    assert oc.american_to_prob(-110) == pytest.approx(0.5238, abs=1e-4)
    assert oc.fmt_american(oc.prob_to_american(0.40)) == "+150"
    assert oc.fmt_american(oc.prob_to_american(2 / 3)) == "-200"
    assert oc.fmt_american(oc.prob_to_american(0.5)) == "+100"
    for a in (-350, -200, -110, 100, 120, 450):
        assert oc.prob_to_american(oc.american_to_prob(a)) == pytest.approx(a)
