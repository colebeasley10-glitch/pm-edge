"""NCAA Basketball Model (PAPER ONLY) — daily value screen, $500 paper bankroll, honest grading.

Every run (see .github/workflows/ncaab-model.yml):

1. PROJECT. ncaab_model.ratings fits opponent-adjusted efficiency ratings on this season's D1
   finals (only games before today), pulled toward each team's preseason prior.
2. PRICE. For every game tipping in the next LOOKAHEAD hours with a line, the model's best side of
   the spread, total and moneyline is priced with the CALIBRATED probability from the model card
   (the backtest measured how much of the model-vs-market gap actually shows up in results), then
   EV, break-even, minimum acceptable odds and a capped quarter-Kelly stake are computed.
3. TIER.  BET   = EV >= the market's tested threshold AND that market is enabled on the card
          WATCH = positive EV but below the threshold, or a market the backtest didn't validate
          PASS  = no edge (still logged: it's the big-sample test of the model)
4. FREEZE + CLOSE + SETTLE. Line, price, stake, model version and timestamp are frozen at first
   log; the line keeps being re-read until tip (closing line -> CLV); finals are graded.

Separate from the NCAA football grader and every other lane. Nothing here can place a bet.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ncaa_grader.core import _parse_dt, clv, consensus, devig, fmt_line, fmt_odds, grade, implied, profit_per_unit

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LEDGER_PATH = DATA_DIR / "picks.csv"
HOME_TEST_PATH = DATA_DIR / "home_test.csv"      # Nov 3, 2026: home-court spread TEST lane (own ledger)
STATUS_PATH = DATA_DIR / "status.json"
REPORT_PATH = DATA_DIR / "daily_report.md"
CARD_PATH = ROOT.parent / "ncaab_model" / "data" / "model_card.json"

ET = timezone(timedelta(hours=-5))
LOOKAHEAD = timedelta(hours=float(os.getenv("NCAAB_LOOKAHEAD_HOURS", 30)))
MIN_LEAD = timedelta(minutes=10)
SETTLE_AFTER = timedelta(minutes=90)   # the feed's "completed" flag decides; was 3h (Nov 4, 2026)
DEFAULT_ODDS = -110.0
OUT_OF_SCOPE = "not a D1-vs-D1 game"

FIELDS = ["pick_id", "model", "logged_at", "season", "day", "game_id", "tipoff", "home", "away", "neutral",
          "market", "side", "pick_team", "direction", "line", "odds", "model_line", "market_line", "proj",
          "model_prob", "market_prob", "breakeven", "ev", "edge_pts", "min_odds", "tier", "stake", "stake_kelly",
          "bankroll_at_log", "flags", "home_games", "away_games",
          "close_line", "close_odds", "clv", "home_pts", "away_pts", "result", "units", "pnl", "pnl_kelly",
          "settled_at"]


# ---------------------------------------------------------------- model card
def load_card(path: Path | None = None) -> dict:
    return json.loads((path or CARD_PATH).read_text())


def cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def decimal(odds: float) -> float:
    return 1 + profit_per_unit(odds)


def american(dec: float) -> float:
    if dec <= 1:
        return float("nan")
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def _odds(x) -> float:
    try:
        v = float(x)
        return v if abs(v) >= 100 else DEFAULT_ODDS
    except (TypeError, ValueError):
        return DEFAULT_ODDS


# ---------------------------------------------------------------- pricing
def price_game(*, proj, market: dict, card: dict, neutral: bool = False) -> list[dict]:
    """Best side of each market with its calibrated probability. proj has home_margin/total/poss.

    Spread: E[(margin + spread)] = b_s * (model_margin + spread)  -> P(home covers) ~ Normal(sd_s)
    Total:  E[(total - line)]    = a_t + b_t * (model_total - line) -> P(over)     ~ Normal(sd_t)
    ML:     E[margin] = -spread + b_s * (model_margin + spread)     -> P(home wins) ~ Normal(sd_s)
    b < 1 is the backtest's measured shrink: the market is right about most of any disagreement.
    Spreads/ML use a separate shrink on neutral courts, where the backtest found the model more informative."""
    c = card["calibration"]
    b_s = c["b_s_neutral"] if neutral and "b_s_neutral" in c else c["b_s"]
    sd_s = c["sd_s_neutral"] if neutral and "sd_s_neutral" in c else c["sd_s"]
    out = []
    s = market.get("spread")
    if s is not None:
        e = proj.home_margin + s
        mu = b_s * e
        p_home = cdf(mu / sd_s)
        for team_side, p, odds, line in (("home", p_home, _odds(market.get("home_spread_odds")), s),
                                         ("away", 1 - p_home, _odds(market.get("away_spread_odds")), -s)):
            out.append({"market": "SPREAD", "team_side": team_side, "p": p, "odds": odds, "line": line,
                        "edge_pts": e if team_side == "home" else -e,
                        "model_line": -proj.home_margin if team_side == "home" else proj.home_margin})
    t = market.get("total")
    if t is not None and t > 90:
        e = proj.total - t
        mu = c["a_t"] + c["b_t"] * e
        p_over = cdf(mu / c["sd_t"])
        for d, p, odds in (("OVER", p_over, _odds(market.get("over_odds"))),
                           ("UNDER", 1 - p_over, _odds(market.get("under_odds")))):
            out.append({"market": "TOTAL", "direction": d, "p": p, "odds": odds, "line": t,
                        "edge_pts": e if d == "OVER" else -e, "model_line": proj.total})
    hm, am = market.get("home_ml"), market.get("away_ml")
    if hm is not None and am is not None and abs(hm) >= 100 and abs(am) >= 100 and s is not None:
        m_star = -s + b_s * (proj.home_margin + s)
        p_home = cdf(m_star / sd_s)
        fair_h, fair_a = devig(hm, am)
        for team_side, p, odds, fair in (("home", p_home, hm, fair_h), ("away", 1 - p_home, am, fair_a)):
            out.append({"market": "ML", "team_side": team_side, "p": p, "odds": float(odds), "line": "",
                        "edge_pts": "", "fair": fair,
                        "model_line": proj.home_margin if team_side == "home" else -proj.home_margin})
    best = {}
    for o in out:
        o["ev"] = o["p"] * decimal(o["odds"]) - 1
        o["breakeven"] = implied(o["odds"])
        if o["market"] not in best or o["ev"] > best[o["market"]]["ev"]:
            best[o["market"]] = o
    return list(best.values())


def tier_for(o: dict, card: dict, neutral: bool = False) -> str:
    rule = card["rules"][o["market"]]
    if o["market"] == "ML" and not (rule.get("min_odds", -1e9) <= o["odds"] <= rule.get("max_odds", 1e9)):
        return "PASS" if o["ev"] <= 0 else "WATCH"
    if o["ev"] >= rule["min_ev"]:
        ok = rule["enabled"] and (neutral or not rule.get("neutral_only"))
        # Huge model-vs-market gaps lost in the backtest (usually news the model can't see).
        if rule.get("max_edge_pts") and o.get("edge_pts") not in ("", None) and abs(o["edge_pts"]) > rule["max_edge_pts"]:
            ok = False
        return "BET" if ok else "WATCH"
    return "WATCH" if o["ev"] >= rule["min_ev"] / 2 and o["ev"] > 0 else "PASS"


def min_odds(p: float, min_ev: float) -> float:
    """Worst American price that still clears the EV threshold: decimal >= (1 + min_ev) / p."""
    return american((1 + min_ev) / p) if p > 0 else float("nan")


def kelly_stake(p: float, odds: float, bankroll: float, st: dict) -> float:
    b = decimal(odds) - 1
    k = max(0.0, (b * p - (1 - p)) / b)
    stake = bankroll * min(st["kelly_fraction"] * k, st["max_stake_pct"])
    return round(stake, 2) if stake >= st.get("min_stake", 1.0) else 0.0


# ---------------------------------------------------------------- bankroll
def bankroll(rows: list[dict], card: dict) -> dict:
    """Paper bankroll: start + settled P/L at the primary (flat) stake. The quarter-Kelly column is a
    side-by-side comparison only. At risk = stakes on unsettled BETs."""
    start = card["staking"]["start_bankroll"]
    pnl = sum(float(r.get("pnl") or 0) for r in rows if r.get("result"))
    pnl_k = sum(float(r.get("pnl_kelly") or 0) for r in rows if r.get("result"))
    at_risk = sum(float(r.get("stake") or 0) for r in rows if r.get("tier") == "BET" and not r.get("result"))
    return {"start": start, "pnl": round(pnl, 2), "balance": round(start + pnl, 2), "at_risk": round(at_risk, 2),
            "kelly_balance": round(start + pnl_k, 2), "pnl_kelly": round(pnl_k, 2)}


def _flags(g: dict, proj_info: dict, card: dict) -> list[str]:
    f = []
    n_min = card["flags"]["early_season_games"]
    if min(proj_info.get("home_games", 0), proj_info.get("away_games", 0)) < n_min:
        f.append(f"early season (<{n_min} games): mostly preseason prior")
    if g.get("neutralSite"):
        f.append("neutral site")
    if proj_info.get("home_new") or proj_info.get("away_new"):
        f.append("team new to D1 / thin history")
    if proj_info.get("no_poss_share", 0) > 0.2:
        f.append("possession data incomplete")
    f.append("injuries/lineups not modeled — check before betting")
    return f


# ---------------------------------------------------------------- the live round
def run_live(*, games: list[dict], lines: dict, project, card: dict, now: datetime | None = None,
             rows: list[dict] | None = None, suspended: str = "") -> dict:
    """project(game) -> (proj, info) or (None, reason). Logs, closes and settles; returns new rows + counts."""
    now = now or datetime.now(timezone.utc)
    rows = load_ledger() if rows is None else rows
    by_id = {r["pick_id"]: r for r in rows}
    games_by_id = {str(g.get("id")): g for g in games}
    lines = {str(k): v for k, v in lines.items()}
    stamp = now.isoformat(timespec="seconds")
    st = card["staking"]
    bank = bankroll(rows, card)
    today = now.astimezone(ET).date().isoformat()
    exposure_today = sum(float(r.get("stake") or 0) for r in rows if r.get("tier") == "BET" and r.get("day") == today)
    max_daily = bank["balance"] * st["max_daily_exposure_pct"]

    upcoming = []
    for g in games:
        ko = _parse_dt(g.get("startDate") or "")
        if not g.get("completed") and g.get("state") != "in" and ko and now + MIN_LEAD <= ko <= now + LOOKAHEAD:
            upcoming.append(g)

    new, insufficient, no_line, out_of_scope = [], [], [], []
    candidates = []
    for g in upcoming:
        gl = lines.get(str(g.get("id")))
        if not gl:
            no_line.append(f'{g["awayTeam"]} @ {g["homeTeam"]}')
            continue
        proj, info = project(g)
        if proj is None:
            (out_of_scope if info == OUT_OF_SCOPE else insufficient).append(f'{g["awayTeam"]} @ {g["homeTeam"]}: {info}')
            continue
        mkt = consensus(gl)
        for o in price_game(proj=proj, market=mkt, card=card, neutral=bool(g.get("neutralSite"))):
            pid = f"live:{card['version']}:{g['id']}:{o['market']}"
            if pid in by_id:
                continue                                  # frozen at first log
            candidates.append((g, proj, info, mkt, o, pid))

    # Stake the day's BETs best-EV first so the exposure cap keeps the strongest ones.
    candidates.sort(key=lambda x: -x[4]["ev"])
    for g, proj, info, mkt, o, pid in candidates:
        home, away = g["homeTeam"], g["awayTeam"]
        tier = tier_for(o, card, neutral=bool(g.get("neutralSite")))
        if suspended and tier == "BET":
            tier = "WATCH"
        stake = stake_k = 0.0
        if tier == "BET":
            # Primary stake is FLAT (backtest: flat beat quarter Kelly and had a smaller drawdown,
            # because Kelly leans hardest on the highest-EV plays, which were the least reliable).
            stake = min(st["flat_stake"], max(0.0, max_daily - exposure_today))
            stake = round(stake, 2) if stake >= st.get("min_stake", 1.0) else 0.0
            if stake <= 0:
                tier = "WATCH"
            else:
                stake_k = kelly_stake(o["p"], o["odds"], bank["start"], st)
            exposure_today += stake
        if o["market"] == "TOTAL":
            side, team, direction = f"{o['direction']} {o['line']:g}", "", o["direction"]
            mline = f"{o['model_line']:.1f}"
            mkt_line = f"{o['line']:g}"
        else:
            team = home if o["team_side"] == "home" else away
            direction = ""
            if o["market"] == "SPREAD":
                side = f"{team} {fmt_line(o['line'])}"
                mline = f"{team} {fmt_line(round(o['model_line'] * 2) / 2)}"
                mkt_line = fmt_line(o["line"])
            else:
                side = f"{team} ML {fmt_odds(o['odds'])}"
                mline = f"fair {fmt_odds(american(1 / o['p']))}" if 0 < o["p"] < 1 else ""
                mkt_line = f"no-vig {o['fair'] * 100:.1f}%"
        rule = card["rules"][o["market"]]
        row = {"pick_id": pid, "model": card["version"], "logged_at": stamp, "season": g.get("season"),
               "day": g.get("week"), "game_id": g["id"], "tipoff": g.get("startDate") or "", "home": home,
               "away": away, "neutral": bool(g.get("neutralSite")), "market": o["market"], "side": side,
               "pick_team": team, "direction": direction, "line": o["line"], "odds": o["odds"],
               "model_line": mline, "market_line": mkt_line,
               "proj": f"{away} {proj.away_pts:.0f} - {home} {proj.home_pts:.0f}",
               "model_prob": round(o["p"], 4), "breakeven": round(o["breakeven"], 4), "ev": round(o["ev"], 4),
               "edge_pts": round(o["edge_pts"], 1) if o["edge_pts"] != "" else "",
               "min_odds": min_odds(o["p"], rule["min_ev"]), "tier": tier, "stake": stake,
               "stake_kelly": stake_k, "bankroll_at_log": bank["balance"],
               "flags": "; ".join(_flags(g, info, card)), "home_games": info.get("home_games", ""),
               "away_games": info.get("away_games", ""), "market_prob": o.get("fair", "")}
        by_id[pid] = row
        new.append(row)

    closed, settled = close_and_settle(by_id, games_by_id, lines, now, stamp)

    return {"rows": list(by_id.values()), "new": new, "logged": len(new), "settled": settled,
            "close_updates": closed, "upcoming_games": len(upcoming),
            "games_with_lines": len(upcoming) - len(no_line) - len(out_of_scope), "insufficient": insufficient[:60],
            "no_line": no_line[:60], "out_of_scope": len(out_of_scope)}


def close_and_settle(by_id: dict, games_by_id: dict, lines: dict, now: datetime, stamp: str) -> tuple[int, int]:
    """Re-read the line until tip (CLV), then grade finals. Shared by the BET ledger and the home test."""
    closed = 0
    for r in by_id.values():
        if r.get("result"):
            continue
        g, gl = games_by_id.get(str(r.get("game_id"))), lines.get(str(r.get("game_id")))
        ko = _parse_dt(r.get("tipoff") or "")
        if g and gl and ko and now < ko and not g.get("completed"):
            cl, co, v = clv(_as_grader(r), consensus(gl))
            if v != "":
                r["close_line"], r["close_odds"], r["clv"] = cl, co, v
                closed += 1

    settled = 0
    for r in by_id.values():
        if r.get("result"):
            continue
        g = games_by_id.get(str(r.get("game_id")))
        ko = _parse_dt(r.get("tipoff") or "")
        if not (g and g.get("completed") and g.get("homePoints") is not None and ko and now >= ko + SETTLE_AFTER):
            continue
        hp, ap = float(g["homePoints"]), float(g["awayPoints"])
        res, units = grade(_as_grader(r), hp, ap)
        r["result"], r["units"] = res, units
        r["home_pts"], r["away_pts"] = int(hp), int(ap)
        r["pnl"] = round(float(r.get("stake") or 0) * units, 2)
        r["pnl_kelly"] = round(float(r.get("stake_kelly") or 0) * units, 2)
        r["settled_at"] = stamp
        settled += 1
    return closed, settled


def downgrade_bets(new_rows: list[dict]) -> int:
    """A guard that fires after pricing (e.g. too many unprojectable games) turns this run's BETs into WATCH."""
    n = 0
    for r in new_rows:
        if r.get("tier") == "BET":
            r["tier"], r["stake"], r["stake_kelly"] = "WATCH", 0.0, 0.0
            n += 1
    return n


def _as_grader(r: dict) -> dict:
    """The shared grade()/clv() helpers expect the football grader's column names."""
    return {**r, "kickoff": r.get("tipoff")}


# ---------------------------------------------------------------- home-court spread TEST lane
def home_test_bankroll(rows: list[dict], card: dict) -> dict:
    ht = card.get("home_test", {})
    start = float(ht.get("start_bankroll", 500.0))
    pnl = sum(float(r.get("pnl") or 0) for r in rows if r.get("result"))
    at_risk = sum(float(r.get("stake") or 0) for r in rows if not r.get("result"))
    return {"start": start, "pnl": round(pnl, 2), "balance": round(start + pnl, 2), "at_risk": round(at_risk, 2)}


def run_home_test(*, games: list[dict], lines: dict, project, card: dict, now: datetime | None = None,
                  rows: list[dict] | None = None) -> dict:
    """PAPER TEST: home-court (non-neutral) spreads priced with the NEUTRAL-court calibration.

    The main card shrinks home-game spreads almost all the way to the market (b_s 0.046), so they can
    never reach a bet. This lane asks: with home court fixed at the card's value, is the model as
    informative at home as on neutral floors? Flat stake, own $500 ledger (home_test.csv), logged only
    when EV >= min_ev and the gap is <= max_edge_pts. Never touches picks.csv or the BET bankroll."""
    ht = card.get("home_test") or {}
    now = now or datetime.now(timezone.utc)
    rows = load_ledger(HOME_TEST_PATH) if rows is None else rows
    by_id = {r["pick_id"]: r for r in rows}
    games_by_id = {str(g.get("id")): g for g in games}
    lines = {str(k): v for k, v in lines.items()}
    stamp = now.isoformat(timespec="seconds")
    bank = home_test_bankroll(rows, card)
    new = []
    if ht.get("enabled"):
        for g in games:
            ko = _parse_dt(g.get("startDate") or "")
            if g.get("completed") or g.get("state") == "in" or not ko or not (now + MIN_LEAD <= ko <= now + LOOKAHEAD):
                continue
            if g.get("neutralSite"):
                continue                                   # neutral floors are the main lane's job
            pid = f"home-test:{card['version']}:{g['id']}:SPREAD"
            gl = lines.get(str(g.get("id")))
            if pid in by_id or not gl:
                continue
            proj, info = project(g)
            if proj is None:
                continue
            mkt = consensus(gl)
            o = next((x for x in price_game(proj=proj, market=mkt, card=card, neutral=True)
                      if x["market"] == "SPREAD"), None)
            if not o or o["ev"] < ht.get("min_ev", 0.03):
                continue
            if ht.get("max_edge_pts") and abs(o["edge_pts"]) > ht["max_edge_pts"]:
                continue
            home, away = g["homeTeam"], g["awayTeam"]
            team = home if o["team_side"] == "home" else away
            row = {"pick_id": pid, "model": card["version"] + "+home_test", "logged_at": stamp,
                   "season": g.get("season"), "day": g.get("week"), "game_id": g["id"],
                   "tipoff": g.get("startDate") or "", "home": home, "away": away, "neutral": False,
                   "market": "SPREAD", "side": f"{team} {fmt_line(o['line'])}", "pick_team": team, "direction": "",
                   "line": o["line"], "odds": o["odds"],
                   "model_line": f"{team} {fmt_line(round(o['model_line'] * 2) / 2)}", "market_line": fmt_line(o["line"]),
                   "proj": f"{away} {proj.away_pts:.0f} - {home} {proj.home_pts:.0f}",
                   "model_prob": round(o["p"], 4), "breakeven": round(o["breakeven"], 4), "ev": round(o["ev"], 4),
                   "edge_pts": round(o["edge_pts"], 1), "min_odds": min_odds(o["p"], ht.get("min_ev", 0.03)),
                   "tier": "TEST", "stake": float(ht.get("flat_stake", 5.0)), "stake_kelly": 0.0,
                   "bankroll_at_log": bank["balance"],
                   "flags": "; ".join(["HOME-COURT TEST (paper): unproven"] + _flags(g, info, card)),
                   "home_games": info.get("home_games", ""), "away_games": info.get("away_games", "")}
            by_id[pid] = row
            new.append(row)
    closed, settled = close_and_settle(by_id, games_by_id, lines, now, stamp)
    return {"rows": list(by_id.values()), "new": new, "logged": len(new), "settled": settled, "close_updates": closed}


# ---------------------------------------------------------------- ledger
def load_ledger(path: Path | None = None) -> list[dict]:
    try:
        with open(path or LEDGER_PATH, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def save_ledger(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (str(r.get("tipoff")), str(r.get("pick_id"))))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


# ---------------------------------------------------------------- reporting
def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def stats(rows: list[dict], pl: str = "units", risk: str | None = None) -> dict:
    done = [r for r in rows if r.get("result")]
    w = sum(r["result"] == "W" for r in done)
    l = sum(r["result"] == "L" for r in done)
    p = sum(r["result"] == "P" for r in done)
    profit = sum(_f(r.get(pl)) or 0 for r in done)
    risked = sum(_f(r.get(risk)) or 0 for r in done) if risk else len(done)
    clv_pts = [_f(r["clv"]) for r in done if _f(r.get("clv")) is not None and r["market"] != "ML"]
    clv_ml = [_f(r["clv"]) for r in done if _f(r.get("clv")) is not None and r["market"] == "ML"]
    allc = clv_pts + clv_ml
    return {"picks": len(done), "w": w, "l": l, "p": p, "profit": round(profit, 2),
            "roi": profit / risked if risked else None, "win_pct": w / (w + l) if (w + l) else None,
            "open": len(rows) - len(done), "clv_pts": statistics.mean(clv_pts) if clv_pts else None,
            "clv_ml": statistics.mean(clv_ml) if clv_ml else None,
            "beat_close": sum(c > 0 for c in allc) / len(allc) if allc else None,
            "avg_ev": statistics.mean([_f(r.get("ev")) or 0 for r in rows]) if rows else None}


def periods(rows: list[dict]) -> dict:
    """BET P/L ($, primary flat stake) by day, ISO week and month (ET day of the game)."""
    out = {"day": defaultdict(float), "week": defaultdict(float), "month": defaultdict(float)}
    for r in rows:
        if r.get("tier") != "BET" or not r.get("result"):
            continue
        d = str(r.get("day") or "")[:10]
        try:
            dt = datetime.fromisoformat(d)
        except ValueError:
            continue
        v = _f(r.get("pnl")) or 0.0
        out["day"][d] += v
        y, wk, _ = dt.isocalendar()
        out["week"][f"{y}-W{wk:02d}"] += v
        out["month"][d[:7]] += v
    return {k: dict(sorted((kk, round(vv, 2)) for kk, vv in v.items())) for k, v in out.items()}


def calibration_check(rows: list[dict], min_n: int = 150) -> dict:
    """Brier score of the model's probabilities vs a coin flip on settled, non-push picks.
    Used to SUSPEND BETs automatically if the live model is clearly mis-calibrated."""
    done = [r for r in rows if r.get("result") in ("W", "L") and _f(r.get("model_prob")) is not None
            and r.get("market") in ("SPREAD", "TOTAL")]
    if len(done) < min_n:
        return {"n": len(done), "brier": None, "brier_coin": 0.25, "suspend": False}
    b = statistics.mean((_f(r["model_prob"]) - (r["result"] == "W")) ** 2 for r in done)
    # A calibrated model with modest edges sits at ~0.249; >0.256 on 150+ picks is a ~2-sigma miss.
    return {"n": len(done), "brier": round(b, 4), "brier_coin": 0.25, "suspend": b > 0.256}


def write_status(summary: dict, path: Path | None = None) -> None:
    path = path or STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
