"""v2 lanes: a fix for every losing lane, tested on paper next to the original (Nov 12, 2026).

Cole asked to rework every lane that isn't profiting. A breakdown of every settled bet showed the
same few leaks over and over, so each v2 lane is its original lane with those leaks plugged:

  1. Too early. Plays logged long before the game lost badly across lanes (Prop Board 12h+ out:
     -28% on 79 plays; Hunter's Plays 12h+ out: -18% on 122; Today's Plays 24h+ out: -6% / -8%).
     Early Pinnacle prices are low-limit and still moving, so an "edge" against them often isn't one.
     Close to the game Pinnacle's price is at its sharpest, so an edge against it is more likely real.
  2. Shaky fair price. Prop Board plays on props Pinnacle takes under $500 on: -8.7% on 183.
  3. Over-trusting our own models. The NCAA football and NBA game models were worse than the market
     at picking winners (log loss: the market alone beat every blend with the model). A v2 model lane
     only bets when the CALIBRATED fair (fair_calibration: the model shrunk toward the market by how
     much its disagreements have actually held up) beats the logged price by 2-8% EV, at -200 to +150.
     A bigger "edge" than 8% from a shrunk model is almost always something the model is missing
     (NCAA: calibrated EV 6%+ went -37% on 36).
  4. Kalshi Sharp: spread markets (-14% ROI, -6.8c CLV on 290) and sub-25c long shots (long-shot bias).

How it's scored, so nothing here is curve-fit to the past: a v2 lane only counts bets its original
lane logs from START on. The rules above came from the old bets; whether they work is decided only by
new ones. Each v2 lane is graded on the same go-live bar as everything else, and once one clears it,
the Bet Queue and Cole's Plays pick its plays up on their own (see candidates()). Until then it's
paper only and sends no phone alerts. The original lanes keep running unchanged as the comparison.

Not reworked: Sharp Plays (its closing-line value is ~0 in every slice, i.e. the alert comes after the
move -- no filter fixes that; its line-steam plays are already tested inside Hunter's Plays v2), the
benched Weather / Crypto / Polymarket / Maker Bot lanes, BreakTheBank (winning) and lanes with too few
bets to judge.
"""
from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
START = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)   # only bets logged after this count

PROP_MAX_HOURS = 6.0       # props + steam: logged within 6h of the start
LINE_MAX_HOURS = 12.0      # Bet Queue plays
KALSHI_MAX_HOURS = 24.0    # Today's Plays (Kalshi game markets)
MIN_PIN_LIMIT = 500        # Prop Board: Pinnacle must take $500+ on the prop
MODEL_MIN_EV, MODEL_MAX_EV = 0.02, 0.08
MODEL_MIN_ODDS, MODEL_MAX_ODDS = -200, 150
SHARP_MIN_PRICE = 0.25


def _f(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _rows(rel: str, root: Path | None = None) -> list[dict]:
    p = Path(root or ROOT) / rel
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def hours_before(logged, start) -> float | None:
    a, b = _dt(logged), _dt(start)
    return None if not a or not b else (b - a).total_seconds() / 3600


def since_start(logged) -> bool:
    d = _dt(logged)
    return bool(d and d >= START)


def _near(logged, start, max_h: float) -> bool:
    h = hours_before(logged, start)
    return h is not None and 0 <= h <= max_h


def dec_from_american(v) -> float | None:
    a = _f(v)
    if a is None or a == 0:
        return None
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


# ------------------------------------------------------------------ the rules, one per lane
def prop_board_ok(r: dict) -> bool:
    return _near(r.get("alert_time"), r.get("game_start"), PROP_MAX_HOURS) and (_f(r.get("limit")) or 0) >= MIN_PIN_LIMIT


def odds_fam_ok(r: dict) -> bool:
    return bool(r.get("kind")) and _near(r.get("sent_at"), r.get("game_start"), PROP_MAX_HOURS)


def ev_finder_ok(r: dict) -> bool:
    return _near(r.get("opened_at"), r.get("commence_time"), KALSHI_MAX_HOURS)


def bet_queue_ok(r: dict) -> bool:
    return _near(r.get("alert_time"), r.get("game_start"), LINE_MAX_HOURS)


def sharp_ok(r: dict) -> bool:
    import scorecard
    if r.get("strategy") != "sharp" or r.get("category") != "Sports":
        return False
    if scorecard.bet_type(r) == "Spread":
        return False
    return (_f(r.get("entry_price")) or 0) >= SHARP_MIN_PRICE


def model_ev(source: str, r: dict) -> float | None:
    """Calibrated fair x decimal price - 1 at the price the model logged."""
    import fair_calibration
    fair, dec = fair_calibration.calibrated(source, r), dec_from_american(r.get("odds"))
    return None if not fair or not dec else fair * dec - 1


def model_ok(source: str, r: dict) -> bool:
    odds = _f(r.get("odds"))
    if odds is None or not (MODEL_MIN_ODDS <= odds <= MODEL_MAX_ODDS):
        return False
    if "source" in r:                        # NCAA / NBA graders: live picks only, never backtests
        if (r.get("source") or "live") != "live":
            return False
    elif not str(r.get("pick_id", "")).startswith("live:"):     # NCAAB model: live picks are "live:..."
        return False
    ev = model_ev(source, r)
    return ev is not None and MODEL_MIN_EV <= ev <= MODEL_MAX_EV


# name, ledger, logged-at column, rule, scorecard grade args, what changed (shown on the scorecard)
LANES = [
    ("Prop Board steam v2", "research_lab/data/PROP_BOARD/plays.csv", "alert_time", prop_board_ok,
     {"risk": 10.0, "clv": "clv"}, "Prop Board fix: only within 6h of the game and props Pinnacle takes $500+ on"),
    ("Hunter's Plays v2", "research_lab/data/ODDS_FAM/plays.csv", "sent_at", odds_fam_ok,
     {"risk": 10.0, "clv": "clv"}, "Hunter's Plays fix: only plays sent within 6h of the game"),
    ("Today's Plays v4 (EV v2 near game)", "research_lab/data/EV_FINDER_v2/positions.csv", "opened_at", ev_finder_ok,
     {"risk": "committed", "clv": "clv_cents"}, "Today's Plays EV v2 fix: only bets opened within 24h of the game"),
    ("Today's Plays v5 (EV v3 near game)", "research_lab/data/EV_FINDER_v3/positions.csv", "opened_at", ev_finder_ok,
     {"risk": "committed", "clv": "clv_cents"}, "Today's Plays EV v3 fix: only bets opened within 24h of the game"),
    ("Paper Sharp v2", "league_trades.csv", "opened_at", sharp_ok,
     {"clv": "clv_cents"}, "Sharp fix: sports only, no spread markets, nothing under 25¢"),
    ("NCAA Grader v2", "ncaa_grader/data/picks.csv", "logged_at", lambda r: model_ok("ncaa_grader", r),
     {"pl": "units", "risk": 1.0, "clv": "clv", "clv_unit": "mixed"},
     "NCAA football fix: calibrated fair beats the price by 2-8%, odds -200 to +150 (any tier)"),
    ("NBA Model v2", "nba_grader/data/picks.csv", "logged_at", lambda r: model_ok("nba_grader", r),
     {"pl": "units", "risk": 1.0, "clv": "clv", "clv_unit": "mixed"},
     "NBA game model fix: calibrated fair beats the price by 2-8%, odds -200 to +150 (any tier)"),
    ("NCAAB Model v2", "ncaab_grader/data/picks.csv", "logged_at", lambda r: model_ok("ncaab_model", r),
     {"pl": "units", "risk": 1.0, "clv": "clv", "clv_unit": "mixed"},
     "NCAA basketball fix: calibrated fair beats the price by 2-8%, odds -200 to +150 (any game)"),
    ("Bet Queue v2", "research_lab/data/BET_QUEUE/queue.csv", "alert_time", bet_queue_ok,
     {"risk": 10.0, "clv": "clv"}, "Bet Queue fix: only plays queued within 12h of the game"),
]


def rows_for(name: str, root: Path | None = None) -> list[dict]:
    """The bets a v2 lane would have taken: its original lane's bets since START that pass its rule."""
    for n, rel, logged, ok, _, _ in LANES:
        if n == name:
            return [r for r in _rows(rel, root) if since_start(r.get(logged)) and ok(r)]
    raise KeyError(name)


def graded(root: Path | None = None) -> list[dict]:
    """Scorecard entries for every v2 lane (paper)."""
    import scorecard
    out = []
    for name, _, _, _, args, note in LANES:
        try:
            rows = rows_for(name, root)
        except Exception as exc:          # one bad ledger never breaks the scorecard
            rows, note = [], f"{note} (error: {type(exc).__name__})"
        out.append(scorecard.grade(name, rows, note=f"{note}; counts bets from Nov 12, 2026 on", **args))
    return out


# ------------------------------------------------------------------ feeding the Bet Queue / Cole's Plays
def candidates(root: Path | None = None, base: Path | None = None) -> list[dict]:
    """Open plays of each v2 lane, in the Bet Queue's candidate format. The queue (and Cole's Plays'
    best-of) only uses a lane once it clears the bar, so these sit unused until a v2 lane earns it.
    Same source/source_id as the original lane, so a play is never queued twice and settles the same way."""
    from research_lab import bet_queue as bq, config
    import fair_calibration
    root = Path(root or ROOT)
    lab = Path(base or config.DATA_DIR)
    out = []
    for r in bq._csv(lab / "PROP_BOARD" / "plays.csv"):
        if r.get("status") == "open" and since_start(r.get("alert_time")) and prop_board_ok(r):
            out.append(bq._cand("Prop Board steam v2", "prop_board", r["play_id"], r.get("league"), r.get("game"),
                                r["pick"], r.get("game_start"), _f(r.get("target_decimal")), _f(r.get("fair_prob")),
                                key=f"prop:{r['pick']}|{r.get('game')}"))
    for r in bq._csv(lab / "ODDS_FAM" / "plays.csv"):
        if r.get("status") == "open" and since_start(r.get("sent_at")) and odds_fam_ok(r):
            dec = _f(r.get("best_decimal")) or _f(r.get("bet_at_decimal"))
            key = f"prop:{r['pick']}|{r.get('game')}" if r.get("market") == "prop" else None
            out.append(bq._cand("Hunter's Plays v2", "odds_fam", r["of_id"], r.get("league"), r.get("game"),
                                r["pick"], r.get("game_start"), dec, _f(r.get("fair_prob")), key=key))
    for lane, rel, src, league, start_col in (
            ("NCAA Grader v2", "ncaa_grader/data/picks.csv", "ncaa_grader", "NCAAF", "kickoff"),
            ("NBA Model v2", "nba_grader/data/picks.csv", "nba_grader", "NBA", "kickoff"),
            ("NCAAB Model v2", "ncaab_grader/data/picks.csv", "ncaab_model", "NCAAB", "tipoff")):
        for r in bq._csv(root / rel):
            if r.get("result") or not since_start(r.get("logged_at")) or not model_ok(src, r):
                continue
            out.append(bq._cand(lane, src, r["pick_id"], league, f"{r['away']} at {r['home']}", r["side"],
                                r.get(start_col), dec_from_american(r.get("odds")),
                                fair_calibration.calibrated(src, r), "Model"))
    return [c for c in out if c]
