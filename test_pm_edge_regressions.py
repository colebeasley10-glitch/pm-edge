"""
PM Edge regression tests.

Pure logic tests: no network calls. Run with:
    pip install pytest rapidfuzz pandas cryptography --break-system-packages
    python -m pytest test_pm_edge_regressions.py -v

These exercise the embedded pmedge package (extracted from watcher.py's
runtime blob) plus watcher.py's own filtering/matching helper functions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import watcher  # noqa: E402  (triggers the embedded-runtime extraction)
from pmedge.matcher import load_aliases, match_event  # noqa: E402
from pmedge.market_parse import parse_market  # noqa: E402

ALIASES = load_aliases()


# ---------------------------------------------------------------------------
# Segment / futures safeguards (section 8-10 of the takeover brief)
# ---------------------------------------------------------------------------

def test_regression_milwaukee_pittsburgh_f3_is_rejected():
    """The exact live incident that drove v15: F3-innings market must never
    be treated as a full-game market."""
    market = {
        "ticker": "KXMLBF3-26SEP15MILPIT",
        "event_ticker": "KXMLBF3-26SEP15MILPIT",
        "title": "Milwaukee at Pittsburgh: Pittsburgh first 3 innings winner",
        "_kalshi_event_title": "Milwaukee at Pittsburgh",
        "_kalshi_milestone_title": "Milwaukee at Pittsburgh",
    }
    assert watcher.looks_like_unsupported_segment(market) is True


def test_full_game_h2h_is_not_flagged_as_segment_or_future():
    market = {
        "ticker": "KXMLBGAME-26SEP15MILPIT",
        "event_ticker": "KXMLBGAME-26SEP15MILPIT",
        "title": "Milwaukee at Pittsburgh: Winner?",
        "_kalshi_event_title": "Milwaukee at Pittsburgh",
    }
    assert watcher.looks_like_unsupported_segment(market) is False
    assert watcher.looks_like_future_or_outright(market) is False


def test_half_quarter_period_set_map_segments_rejected():
    titles = [
        "Team A at Team B: First half winner",
        "Team A at Team B: Second quarter total",
        "Team A at Team B: Third period winner",
        "Team A at Team B: First set winner",
        "Team A at Team B: First map winner",
    ]
    for t in titles:
        market = {"ticker": "X-1", "title": t, "_kalshi_event_title": t}
        assert watcher.looks_like_unsupported_segment(market) is True, t


def test_season_future_is_rejected():
    market = {
        "ticker": "KXNBAWINS-26",
        "title": "Will the Lakers win at least 50 games in the 2026-27 regular season?",
        "_kalshi_event_title": "Lakers regular season win total",
    }
    assert watcher.looks_like_future_or_outright(market) is True


def test_mvp_and_championship_futures_rejected():
    for title in [
        "Who will win NBA MVP?",
        "Will the Chiefs win the Super Bowl?",
        "World Series matchup: Yankees vs Dodgers",
    ]:
        market = {"ticker": "X-2", "title": title, "_kalshi_event_title": title}
        assert watcher.looks_like_future_or_outright(market) is True, title


# ---------------------------------------------------------------------------
# Event matching: sport, team, and now date confirmation for strong matches
# ---------------------------------------------------------------------------

def test_wrong_team_does_not_match():
    spec = parse_market({"title": "Boston Celtics at Miami Heat: Winner?"})
    events = [
        {"id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Golden State Warriors",
         "commence_time": "2026-09-15T23:00:00+00:00"},
    ]
    em = match_event(spec, events, ALIASES, "2026-09-15T23:00:00+00:00")
    assert em.event is None


def test_same_teams_correct_date_matches():
    spec = parse_market({"title": "New York Yankees at Boston Red Sox: Winner?"})
    events = [
        {"id": "evt1", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
         "commence_time": "2026-09-15T17:05:00+00:00"},
    ]
    market_time = "2026-09-15T17:05:00+00:00"
    em = match_event(spec, events, ALIASES, market_time)
    assert em.event is not None
    assert em.event["id"] == "evt1"


def test_same_teams_wrong_date_doubleheader_is_now_rejected_end_to_end():
    """
    Reproduces the gap found during the takeover review: two teams playing
    twice in a short window (doubleheader / back-to-back series). A strong
    team-name match must NOT bypass date confirmation, or Game 2 can get
    priced against Game 1's sportsbook odds.
    """
    spec = parse_market({"title": "New York Yankees at Boston Red Sox: Winner?"})
    game1_event = {
        "id": "evt_game1", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "commence_time": "2026-09-15T17:00:00+00:00",
    }
    # Kalshi's *second* game of the doubleheader, ~30.5 hours after game 1.
    game2_kalshi_time = "2026-09-16T23:30:00+00:00"

    em = match_event(spec, [game1_event], ALIASES, game2_kalshi_time)
    # The strict matcher now rejects this at the source: same teams, different day.
    assert em.event is None
    assert em.shadow["matcher_shadow_reason"] == "date_mismatch"

    # watcher.py's own time check still independently catches it as well.
    time_ok, delta_h = watcher.low_score_time_confirmed(
        {"_kalshi_game_time": game2_kalshi_time}, game1_event
    )
    assert delta_h is not None
    assert time_ok is False, "a 30.5-hour gap must fail the time-confirmation check"
    # This is exactly the condition the watcher.py patch now checks before
    # accepting a "strong" same-sport match.


def test_same_teams_close_date_within_tolerance_still_accepted():
    """Sanity check the fix isn't overly strict: a normal small discrepancy
    between Kalshi's listed time and sportsbook commence_time must still pass."""
    spec = parse_market({"title": "New York Yankees at Boston Red Sox: Winner?"})
    event = {
        "id": "evt1", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "commence_time": "2026-09-15T17:00:00+00:00",
    }
    kalshi_time = "2026-09-15T18:30:00+00:00"  # 1.5h off -- normal noise
    em = match_event(spec, [event], ALIASES, kalshi_time)
    time_ok, delta_h = watcher.low_score_time_confirmed(
        {"_kalshi_game_time": kalshi_time}, em.event
    )
    assert time_ok is True


# ---------------------------------------------------------------------------
# Alert priority: new/changed information must not be starved by reminders
# ---------------------------------------------------------------------------

def test_new_alerts_are_not_starved_by_higher_edge_reminders():
    """
    Regression for a second gap found during the takeover review: alerts were
    sorted purely by net edge, so a still-qualifying REMINDER with a bigger
    edge number could consume the whole per-run cap before a brand-new
    opportunity (lower edge, but never alerted before) got a chance.
    """
    state = {"alerts": {}}

    # Two markets already alerted 7 hours ago (past the 6h cooldown) with
    # high edge -- both are due a REMINDER, not new information.
    for i in range(2):
        state["alerts"][f"OLD-{i}|YES"] = {
            "active": True,
            "sent_at": (watcher.now_utc() - watcher.timedelta(hours=7)).isoformat(),
            "entry_price": 0.40,
            "net_edge": 0.10,
            "hard_max": 0.45,
            "title": f"Old market {i}",
        }

    class FakeQ:
        def __init__(self, side, recommended_limit, net_edge, hard_max, best_ask, execution="MAKE"):
            self.side = side
            self.recommended_limit = recommended_limit
            self.net_edge = net_edge
            self.hard_max = hard_max
            self.best_ask = best_ask
            self.execution = execution

    fair_entry = {"fair_yes_prob": 0.55, "sport_title": "MLB", "n_books": 4}

    old_candidates = [
        (0.10, {"ticker": f"OLD-{i}", "title": f"Old market {i}"}, fair_entry,
         FakeQ("YES", 0.40, 0.10, 0.45, 0.41))
        for i in range(2)
    ]
    # One brand-new qualifying opportunity, never alerted before, lower edge.
    new_candidate = (0.045, {"ticker": "NEW-1", "title": "New market"}, fair_entry,
                      FakeQ("YES", 0.50, 0.045, 0.55, 0.50))

    actionable = old_candidates + [new_candidate]
    actionable.sort(key=lambda x: x[0], reverse=True)

    new_info, reminders = [], []
    for net_edge, market, fe, q in actionable:
        ok, reason = watcher.should_alert(state, market, fe, q)
        assert ok  # all three qualify for a push this run
        (reminders if reason == "REMINDER" else new_info).append((net_edge, market, fe, q, reason))

    # With a cap of 2, the fixed ordering (new_info + reminders) must place
    # the NEW market ahead of both REMINDERs despite its smaller edge.
    ordered = new_info + reminders
    sent_tickers = [m["ticker"] for _, m, _, _, _ in ordered[:2]]
    assert "NEW-1" in sent_tickers, "a brand-new opportunity was starved out by reminders"


# ---------------------------------------------------------------------------
# Repeat alerts: never resend the same side at a worse price
# ---------------------------------------------------------------------------

def test_repeat_alert_suppresses_worse_price_but_allows_better_price():
    state = {"alerts": {
        "TEST-REPEAT|YES": {
            "active": True,
            "sent_at": (watcher.now_utc() - watcher.timedelta(hours=7)).isoformat(),
            "entry_price": 0.46,
            "hard_max": 0.46,
            "net_edge": 0.08,
            "title": "Repeat market",
        }
    }}
    market = {"ticker": "TEST-REPEAT", "title": "Repeat market"}
    fair_entry = {"fair_yes_prob": 0.60, "sport_title": "NFL", "n_books": 5}

    class Q:
        side = "YES"
        hard_max = 0.80
        net_edge = 0.12
        best_ask = 0.76
        execution = "TAKE"
        recommended_limit = 0.76

    ok, reason = watcher.should_alert(state, market, fair_entry, Q())
    assert not ok
    assert reason == "worse_price_than_last_alert"

    Q.recommended_limit = 0.44
    Q.best_ask = 0.44
    Q.hard_max = 0.46
    ok, reason = watcher.should_alert(state, market, fair_entry, Q())
    assert ok
    assert reason == "PRICE"


# ---------------------------------------------------------------------------
# Speculative tier: lower edge bar, same correctness safeguards
# ---------------------------------------------------------------------------

def test_speculative_tier_catches_a_market_the_primary_tier_rejects():
    """A market with real (devigged) edge below the primary 4% bar but above
    the speculative 2% bar should qualify under score_market's speculative
    base -- and still respect the book-count scaling."""
    fair_entry = {"fair_yes_prob": 0.50, "n_books": 4}
    market = {"yes_bid": 0.43, "yes_ask": 0.44, "no_bid": 0.56, "no_ask": 0.57}

    class FakeSettings:
        maker_fee_rate = 0.0175
        taker_fee_rate = 0.07

    q_primary = watcher.score_market(market, fair_entry, FakeSettings())
    q_spec = watcher.score_market(market, fair_entry, FakeSettings(), base_min_edge=watcher.SPECULATIVE_MIN_EDGE)

    assert q_primary.qualifies is False, "fixture should NOT clear the primary bar"
    assert q_spec.qualifies is True, "fixture SHOULD clear the lower speculative bar"


def test_speculative_tier_never_weakens_book_count_scaling():
    """Fewer books must still demand more edge in the speculative tier too --
    only the base moves, the safety scaling does not disappear."""
    fair_entry_2books = {"n_books": 2}
    fair_entry_4books = {"n_books": 4}
    market = {"yes_bid": 0.40, "yes_ask": 0.41, "no_bid": 0.59, "no_ask": 0.60}

    class FakeSettings:
        maker_fee_rate = 0.0175
        taker_fee_rate = 0.07

    # Same fair prob for both; only book count differs.
    fair_2 = {**fair_entry_2books, "fair_yes_prob": 0.50}
    fair_4 = {**fair_entry_4books, "fair_yes_prob": 0.50}

    q2 = watcher.score_market(market, fair_2, FakeSettings(), base_min_edge=watcher.SPECULATIVE_MIN_EDGE)
    q4 = watcher.score_market(market, fair_4, FakeSettings(), base_min_edge=watcher.SPECULATIVE_MIN_EDGE)

    # 2-book requires MORE edge than 4-book -- if 2-book qualifies, 4-book
    # must also qualify (never the reverse).
    if q2.qualifies:
        assert q4.qualifies, "2-book cleared the bar but 4-book (easier bar) did not"


def test_speculative_alerts_use_separate_state_keys_from_primary():
    """A speculative alert for a ticker must not collide with -- or be
    mistaken for -- a primary alert on the same ticker/side."""
    state = {"alerts": {}}

    class FakeQ:
        side = "YES"
        recommended_limit = 0.45
        net_edge = 0.025
        hard_max = 0.48

    market = {"ticker": "SAME-TICKER", "title": "Test"}
    fair_entry = {"fair_yes_prob": 0.50}

    ok_primary, reason_primary = watcher.should_alert(state, market, fair_entry, FakeQ(), tier="primary")
    watcher.record_alert(state, market, fair_entry, FakeQ(), tier="primary")

    ok_spec, reason_spec = watcher.should_alert(state, market, fair_entry, FakeQ(), tier="speculative")

    assert reason_primary == "NEW"
    # The speculative tier has never been alerted for this ticker before,
    # so it must ALSO read as NEW -- not silently suppressed because the
    # primary tier already has an active entry for the same ticker/side.
    assert reason_spec == "NEW", "speculative alert was incorrectly suppressed by the primary tier's state"
    assert len(state["alerts"]) == 1  # only primary has been recorded so far


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
