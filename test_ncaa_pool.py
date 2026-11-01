from ncaa_pool.model import Game, build_card, spread_cover, margin_pmf


def test_pmf_sums_to_one_and_no_ties():
    p = margin_pmf(-3.0)
    assert abs(sum(p.values()) - 1) < 1e-9 and 0 not in p


def test_fair_line_is_a_coin_flip_and_hook_on_7_matters():
    w, _ = spread_cover(7.0, -7.5, "home")
    w2, _ = spread_cover(7.0, -6.5, "home")
    assert 0.40 < w < 0.50 and w2 - w > 0.05          # 7 is a key number


def test_stale_line_side_is_picked():
    g = Game("A", "B", pool_spread=-3.5, market_spread=-7.0)   # market moved toward home
    card = build_card([g], n_picks=1, n_keys=1)
    assert card["picks"][0].side == "home" and card["picks"][0].prob > 0.55


def test_card_size_and_expected_points():
    games = [Game(f"A{i}", f"H{i}", -3.5, -3.5 - i * 0.5) for i in range(15)]
    card = build_card(games)
    assert len(card["picks"]) == 12 and card["max_points"] == 15 and 7 < card["expected_points"] < 15


def test_market_line_is_a_coin_flip_even_off_key_numbers():
    from ncaa_pool.model import center_for_line
    for line in (-8.5, -6.5, -3.5, 2.5, 13.5):
        mu = center_for_line(line)
        h, _ = spread_cover(mu, line, "home")
        assert abs(h - 0.5) < 0.003


def test_worse_than_market_is_below_half():
    g = Game("UNC", "UConn", pool_spread=7.5, market_spread=8.5)
    card = build_card([g], n_picks=1, n_keys=1)
    assert card["picks"][0].label.startswith("UNC -7.5") and card["picks"][0].prob > 0.5
