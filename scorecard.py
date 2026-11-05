"""Scorecard: every lane graded against one go-live bar (Oct 27, 2026; tightened Nov 1, 2026).

A lane earns real money only when ALL of these hold on its settled bets:
  * at least MIN_BETS settled bets,
  * average CLV above zero (beating the closing price on average) -- the main test, because CLV
    shows real edge in a few hundred bets while profit alone needs well over a thousand,
  * ROI not clearly negative (no worse than one standard error below zero).
Nov 1, 2026 tightening:
  * "reference" lanes (every pick a model logs, including the ones it rates no-edge) never clear
    the bar: only tiers you would actually bet can,
  * a real-money lane whose last DEMOTE_WINDOW bets lose to the close on average is flagged to
    go back to paper.
A hot week is not a reason to go live or size up; clearing the bar is.

Reads only the ledgers already committed to the repo, so it costs nothing to run.
Each lane's CLV is in its own unit (cents for Kalshi lanes, probability for Pinnacle lanes);
only its sign is used for the bar.
"""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path

MIN_BETS = 200
DEMOTE_WINDOW = 100
ROOT = Path(__file__).resolve().parent


def _f(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _rows(rel: str) -> list[dict]:
    p = ROOT / rel
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def grade(name: str, rows: list[dict], *, pl: str = "pl", risk=None, clv: str | None = None,
          money: str = "paper", note: str = "", clv_unit: str = "", reference: bool = False) -> dict:
    """rows -> {name, n, won, lost, pl, risk, roi, clv, verdict}. `risk` is a column name or a
    flat stake per bet; unsettled rows (blank P/L) are skipped."""
    if isinstance(risk, str):
        # A row with a $0 stake in this lane wasn't a bet here (e.g. quarter Kelly sizes a no-edge
        # copy at $0). Counting it showed real wins/losses as "+$0.00 push" (fixed Nov 2, 2026).
        rows = [r for r in rows if _f(r.get(risk)) != 0]
    n = won = lost = 0
    tot_pl = tot_risk = 0.0
    clvs: list[float] = []
    rets: list[float] = []
    for r in rows:
        p = _f(r.get(pl))
        if p is None or str(r.get("status", "")).lower() in {"open", "alert", "error", "no_fill", "void",
                                                            "expired", "rejected", "dry_run", "skipped"}:
            continue
        stake = risk if isinstance(risk, (int, float)) else _f(r.get(risk or "cost"))
        n += 1
        won += p > 0
        lost += p < 0
        tot_pl += p
        tot_risk += stake or 0.0
        if stake:
            rets.append(p / stake)
        if clv and (c := _f(r.get(clv))) is not None:
            clvs.append(c)
    roi = tot_pl / tot_risk if tot_risk else None
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    se = None
    if len(rets) >= 2:
        m = sum(rets) / len(rets)
        se = (sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 / math.sqrt(len(rets))
    roi_ok = roi is not None and (roi > 0 or (se is not None and roi > -se))
    checks = {"bets": n >= MIN_BETS, "roi": roi_ok, "clv": None if avg_clv is None else avg_clv > 0}
    recent = clvs[-DEMOTE_WINDOW:]
    if n == 0:
        verdict = "No settled bets"
    elif money == "real" and len(recent) >= DEMOTE_WINDOW and sum(recent) / len(recent) < 0:
        verdict = f"Demote to paper — losing to the close over its last {DEMOTE_WINDOW} bets"
    elif n >= MIN_BETS and roi is not None and se is not None and roi < -2 * se:
        verdict = "Proven loser — keep off"
    elif reference and n >= MIN_BETS:
        verdict = "Reference only — includes no-edge picks"
    elif checks["bets"] and checks["roi"] and checks["clv"]:
        verdict = "Clears the bar"
    elif checks["bets"] and checks["roi"] and checks["clv"] is None:
        verdict = "Needs CLV data — bets + ROI are there"
    elif checks["clv"] is False and n >= 50:
        verdict = "Losing to the close — not real yet"
    elif n >= MIN_BETS:
        verdict = "Not proven — ROI too far below zero"
    else:
        verdict = f"Too early — {max(0, MIN_BETS - n)} more bets"
    # Nov 1, 2026: units (P/L ÷ average stake) and the share of bets that beat the close, for the board
    units = tot_pl / (tot_risk / n) if n and tot_risk else None
    beat = sum(c > 0 for c in clvs) / len(clvs) if clvs else None
    if not clv_unit and clv:
        clv_unit = "cents" if clv == "clv_cents" else "prob"
    return {"name": name, "money": money, "n": n, "won": won, "lost": lost, "pl": round(tot_pl, 2),
            "units": None if units is None else round(units, 2), "beat_close": beat,
            "clv_unit": clv_unit if clvs else "",
            "risk": round(tot_risk, 2), "roi": None if roi is None else round(roi, 4),
            "clv": None if avg_clv is None else round(avg_clv, 4), "clv_n": len(clvs), "checks": checks, "verdict": verdict,
            "note": note, "rows": rows, "pl_col": pl, "reference": reference,
            "roi_se": None if se is None else round(se, 4),
            # Nov 4, 2026: kept so the app can re-grade a slice of this lane (by sport / bet type)
            "grade_args": {"pl": pl, "risk": risk, "clv": clv, "money": money, "clv_unit": clv_unit,
                           "reference": reference}}


# ------------------------------------------------------------------ categories (Nov 4, 2026)
# Every lane's detail view can split its bets by sport and bet type ("NFL", "NCAAF", "NBA", "NBA Props"...).
_TICKER_SPORTS = [("KXNCAAF", "NCAAF"), ("KXNCAAMB", "NCAAB"), ("KXNCAAB", "NCAAB"), ("KXNFL", "NFL"),
                  ("KXNBA", "NBA"), ("KXWNBA", "WNBA"), ("KXNHL", "NHL"), ("KXMLB", "MLB"), ("KXATP", "Tennis"),
                  ("KXWTA", "Tennis"), ("KXITF", "Tennis"), ("KXCHALLENGER", "Tennis"), ("KXEPL", "Soccer"),
                  ("KXUCL", "Soccer"), ("KXUEL", "Soccer"), ("KXLALIGA", "Soccer"), ("KXSERIEA", "Soccer"),
                  ("KXBUNDESLIGA", "Soccer"), ("KXLIGUE1", "Soccer"), ("KXMLS", "Soccer"), ("KXSAUDIPL", "Soccer"),
                  ("KXTFF1LIG", "Soccer"), ("KXLIGAMX", "Soccer"), ("KXAFCCL", "Soccer"), ("KXSUPERLIG", "Soccer"),
                  ("KXBRASILEIRO", "Soccer"), ("KXJLEAGUE", "Soccer"), ("KXJ2LEAGUE", "Soccer"), ("KXIDNSL", "Soccer"),
                  ("KXECULP", "Soccer"), ("KXLIGAPORTUG", "Soccer"), ("KXEFL", "Soccer"), ("KXARGPREMDIV", "Soccer"),
                  ("KXDIMAYOR", "Soccer"), ("KXALEAGUE", "Soccer"), ("KXCZEFNL", "Soccer"), ("KXKLEAGUE", "Soccer"),
                  ("KXCONMEBOL", "Soccer"), ("KXUECL", "Soccer"), ("KXVLEAGUE", "Soccer"), ("KXPERLIGA", "Soccer"),
                  ("KXSLGREECE", "Soccer"), ("KXT20", "Cricket"), ("KXWT20", "Cricket"), ("KXODI", "Cricket"),
                  ("KXTEST", "Cricket"), ("KXNBL", "Intl basketball"), ("KXKBL", "Intl basketball"),
                  ("KXKBO", "Intl baseball"), ("KXNPB", "Intl baseball"), ("KXBOXING", "Boxing"),
                  ("KXMVE", "Parlays"), ("KXHEISMAN", "Futures"), ("KXCS2", "Esports"), ("KXDOTA", "Esports"),
                  ("KXLOL", "Esports"), ("KXVALORANT", "Esports"), ("KXR6", "Esports"), ("KXHIGH", "Weather"),
                  ("KXLOW", "Weather"), ("KXRAIN", "Weather"), ("KXBTC", "Crypto"), ("KXETH", "Crypto"),
                  ("KXSOL", "Crypto"), ("KXXRP", "Crypto"), ("KXUFC", "MMA"), ("KXPGA", "Golf"), ("KXNASCAR", "NASCAR")]
_LEAGUES = {"NFL": "NFL", "NCAAF": "NCAAF", "CFB": "NCAAF", "NCAA FCS": "NCAAF", "NCAAB": "NCAAB", "CBB": "NCAAB",
            "NBA": "NBA", "WNBA": "WNBA", "NHL": "NHL", "MLB": "MLB", "EPL": "Soccer", "UCL": "Soccer",
            "UEL": "Soccer", "MLS": "Soccer", "LALIGA": "Soccer", "SERIEA": "Soccer", "BUNDESLIGA": "Soccer",
            "LIGUE1": "Soccer", "ATP": "Tennis", "WTA": "Tennis", "UFC": "MMA"}
_SPORT_WORDS = {"football": "Football", "basketball": "Basketball", "hockey": "NHL", "baseball": "MLB",
                "soccer": "Soccer", "tennis": "Tennis"}
_HOOPS_STATS = ("point", "rebound", "assist", "three", "steal", "block", "pts", "rebs", "asts")
_LANE_SPORTS = [("NCAA Grader", "NCAAF"), ("NCAAB", "NCAAB"), ("NBA Model", "NBA"), ("NBA Props", "NBA"),
                ("Weather", "Weather"), ("Crypto", "Crypto")]


def _ncaa(r: dict) -> str:
    """Pinnacle-fed lanes label both college sports "NCAA": tell them apart by stat or by total size."""
    stat = str(r.get("stat") or "").lower()
    if stat:
        return "NCAAB" if any(w in stat for w in _HOOPS_STATS) else "NCAAF"
    if str(r.get("market") or "").lower() == "total" and (_f(r.get("line")) or 0) >= 100:
        return "NCAAB"
    return "NCAAF"


def bet_sport(r: dict, lane_name: str = "") -> str:
    lg = str(r.get("league") or r.get("sport") or "").strip()
    up = lg.upper().replace(" ", "")
    if up == "NCAA":
        return _ncaa(r)
    if lg.upper() in _LEAGUES:
        return _LEAGUES[lg.upper()]
    if up in _LEAGUES:
        return _LEAGUES[up]
    ticker = str(r.get("ticker") or r.get("settle_ticker") or "").upper()
    for pre, sport in _TICKER_SPORTS:
        if ticker.startswith(pre):
            return sport
    word = _SPORT_WORDS.get(lg.lower())
    if word:
        return word
    for key, sport in _LANE_SPORTS:
        if key in lane_name or key in str(r.get("lane") or ""):
            return sport
    return lg or "Other"


def is_prop(r: dict) -> bool:
    src = f"{r.get('source') or ''} {r.get('lane') or ''}".lower()
    if "props" in src:                       # Bet Queue rows copied from a props lane
        return True
    return str(r.get("market") or "").lower() == "prop" or bool(str(r.get("stat") or "").strip()) or \
        bool(str(r.get("player") or "").strip())


def bet_category(r: dict, lane_name: str = "") -> str:
    """'NFL', 'NCAAF', 'NBA', 'NBA Props', 'Tennis'... for any lane's row."""
    sport = bet_sport(r, lane_name)
    return f"{sport} Props" if is_prop(r) else sport


def bet_type(r: dict) -> str:
    """'Spread' / 'Moneyline' / 'Total' / 'Prop' / '' for any lane's row."""
    if is_prop(r):
        return "Prop"
    m = str(r.get("market") or "").lower()
    t = str(r.get("ticker") or "").upper()
    if m in ("spread", "spreads") or "SPREAD" in t:
        return "Spread"
    if m in ("ml", "moneyline", "h2h") or "GAME" in t or "MATCH" in t:
        return "Moneyline"
    if m in ("total", "totals") or "TOTAL" in t:
        return "Total"
    pick = f" {r.get('pick') or ''} "
    if " ML " in pick:
        return "Moneyline"
    if any(w in pick for w in (" Over ", " Under ", " OVER ", " UNDER ")):
        return "Total"
    if re.search(r"[+\-−]\d", pick):
        return "Spread"
    return ""


def split(lane: dict, by: str = "category") -> list[dict]:
    """Re-grade a lane's rows per category (or bet type), same stake/CLV rules as the lane. Biggest first."""
    fn = bet_category if by == "category" else (lambda r, _n="": bet_type(r) or "Other")
    groups: dict[str, list[dict]] = {}
    for r in lane.get("rows") or []:
        groups.setdefault(fn(r, lane["name"]), []).append(r)
    args = lane.get("grade_args") or {"pl": lane.get("pl_col", "pl")}
    out = [grade(name, rows, **args) for name, rows in groups.items()]
    out.sort(key=lambda g: (-g["n"], -len(g["rows"]), g["name"]))
    return out


RANK_PRIOR = 50   # bets of "no edge" blended in before ranking, so 5 lucky bets can't top the board


def rank_score(lane: dict) -> float:
    """ROI shrunk toward zero by sample size: roi * n / (n + RANK_PRIOR). Used to rank lanes best to worst."""
    n, roi = lane.get("n") or 0, lane.get("roi")
    return 0.0 if not n or roi is None else roi * n / (n + RANK_PRIOR)


def parent_name(lane: dict, names: set[str]) -> str | None:
    """'Follow X — NFL' is a slice of 'Follow X'; 'NCAA Grader — ML' a slice of 'NCAA Grader — all'."""
    name = lane["name"]
    if " — " not in name or name.endswith(" — all"):
        return None
    pre = name.split(" — ")[0]
    return next((c for c in (pre, pre + " — all") if c in names), None)


def ranked(lanes_: list[dict], money: str = "paper") -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """(top-level lanes with settled bets, best first), (top-level lanes with none yet),
    {parent name: its slices, best first}. Slices are ranked inside their lane, not against it."""
    sel = [l for l in lanes_ if l["money"] == money]
    names = {l["name"] for l in sel}
    tops, kids = [], {}
    for l in sel:
        p = parent_name(l, names)
        (kids.setdefault(p, []) if p else tops).append(l)
    order = lambda l: (-rank_score(l), -(l["n"]))  # noqa: E731
    active = sorted([l for l in tops if l["n"]], key=order)
    for p in kids:
        kids[p].sort(key=order)
    return active, [l for l in tops if not l["n"]], kids


def lanes() -> list[dict]:
    out: list[dict] = []
    out.append(grade("Live Sharp", _rows("live_sharp_trades.csv"), clv="clv_cents", money="real",
                     note="Turned off Oct 27, 2026"))
    out.append(grade("Live Today's Plays", _rows("live_plays/data/bets.csv"), clv="clv_cents", money="real",
                     note="Paused Oct 27, 2026; open bets still settle"))
    out.append(grade("Maker Bot", _rows("maker_bot/data/orders.csv"), money="real",
                     note="Live off; dry runs keep logging what it would post"))

    league = _rows("league_trades.csv")
    sharp = [r for r in league if r.get("strategy") == "sharp"]
    out.append(grade("Paper Sharp (benchmark)", sharp, clv="clv_cents"))
    try:   # Heavy Flow test: Sharp picks backed by 60+ whale trades, scored from its start date
        from research_lab import heavy_flow as hf
        since = [r for r in sharp if (d := hf._dt(r.get("opened_at"))) and d >= hf.START]
        heavy = [r for r in since if (hf.flow_trades(r) or 0) >= hf.MIN_TRADES]
        out.append(grade(f"Heavy Flow Sharp ({hf.MIN_TRADES}+ trades)", heavy, clv="clv_cents"))
        out.append(grade("Light Flow Sharp (the rest)", [r for r in since if r not in heavy], clv="clv_cents"))
    except Exception:
        pass
    for strat, label in (("weather", "Weather"), ("crypto", "Crypto"), ("polymarket", "Polymarket league")):
        mine = [r for r in league if r.get("strategy") == strat]
        if mine:
            out.append(grade(label, mine, clv="clv_cents", note="Benched; shadow picks still logged"))

    for key, label in (("EV_FINDER_v2", "Today's Plays (EV v2)"), ("EV_FINDER_v3", "Today's Plays -200/+150 (EV v3)")):
        out.append(grade(label, _rows(f"research_lab/data/{key}/positions.csv"), risk="committed", clv="clv_cents"))

    tf = _rows("trader_follow/data/signals.csv")
    for trader in sorted({r.get("trader") for r in tf if r.get("trader")}):
        mine = [r for r in tf if r.get("trader") == trader]
        out.append(grade(f"Follow {trader}", mine, pl="pl_copy", risk=10.0, note="$10 paper per copy"))
        for sport in sorted({r.get("sport") for r in mine if r.get("sport")}):
            out.append(grade(f"Follow {trader} — {sport}", [r for r in mine if r.get("sport") == sport],
                             pl="pl_copy", risk=10.0))
    kf = _rows("trader_follow/data/kalshi_signals.csv")
    out.append(grade("Kalshi Follow", kf, pl="pl_copy", risk=10.0, note="No pre-game signals yet" if not kf else ""))

    out.append(grade("Prop Board steam", _rows("research_lab/data/PROP_BOARD/plays.csv"), risk=10.0, clv="clv"))
    of = _rows("research_lab/data/ODDS_FAM/plays.csv")
    for kind in sorted({r.get("kind") for r in of if r.get("kind")}):
        out.append(grade(f"Odds Fam — {kind}", [r for r in of if r.get("kind") == kind], risk=10.0, clv="clv"))
    out.append(grade("Sharp Plays (line steam)", _rows("research_lab/data/SHARP_PLAYS/plays.csv"), risk=10.0, clv="clv"))

    ncaa = _rows("ncaa_grader/data/picks.csv")   # open picks kept so the bet list shows them; grade() skips unsettled
    out.append(grade("NCAA Grader — all", ncaa, pl="units", risk=1.0, clv="clv", clv_unit="mixed", reference=True))
    out.append(grade("NCAA Grader — STRONG", [r for r in ncaa if r.get("tier") == "STRONG"], pl="units", risk=1.0,
                     clv="clv", clv_unit="mixed"))
    out.append(grade("NCAA Grader — ML", [r for r in ncaa if r.get("market") == "ML"], pl="units", risk=1.0, clv="clv",
                     reference=True))

    nba = _rows("nba_grader/data/picks.csv")
    out.append(grade("NBA Model — Strong + Lean", [r for r in nba if r.get("tier") in ("STRONG", "LEAN")],
                     pl="units", risk=1.0, clv="clv", clv_unit="mixed"))
    out.append(grade("NBA Model — all", nba, pl="units", risk=1.0, clv="clv", clv_unit="mixed", reference=True))
    nbp = [r for r in _rows("nba_grader/data/props.csv") if r.get("result") != "V"]
    out.append(grade("NBA Props — Strong + Lean", [r for r in nbp if r.get("tier") in ("STRONG", "LEAN")],
                     pl="units", risk=1.0, clv="clv"))
    out.append(grade("NBA Props — all", nbp, pl="units", risk=1.0, clv="clv", reference=True))

    ncb = _rows("ncaab_grader/data/picks.csv")   # Nov 2, 2026: NCAA basketball model, $500 paper bankroll
    out.append(grade("NCAAB Model — bets (flat $5)", [r for r in ncb if r.get("tier") == "BET"], pl="pnl",
                     risk="stake", clv="clv", clv_unit="mixed"))
    nht = _rows("ncaab_grader/data/home_test.csv")   # Nov 3, 2026: home-court spread test, own $500 paper
    out.append(grade("NCAAB Home-Court Test (flat $5)", nht, pl="pnl", risk="stake", clv="clv", clv_unit="mixed",
                     note="Home spreads at +3% EV with home court fixed at 3.1; paper, separate from the $5 bets"))
    out.append(grade("NCAAB Model — every game (1u)", ncb, pl="units", risk=1.0, clv="clv", clv_unit="mixed",
                     reference=True))

    bq = _rows("research_lab/data/BET_QUEUE/queue.csv")   # Nov 1, 2026: the daily short list, paper
    out.append(grade("Bet Queue", bq, risk=10.0, clv="clv",
                     note="Top ≤15 plays a day from lanes earning their place; $10 flat, paper"))
    out.append(grade("Bet Queue — ¼ Kelly on $1k", bq, pl="pl_kelly", risk="stake_kelly", clv="clv"))
    for lane in sorted({r.get("lane") for r in bq if r.get("lane")}):
        out.append(grade(f"Bet Queue — {lane}", [r for r in bq if r.get("lane") == lane], risk=10.0, clv="clv"))
    qb = _rows("research_lab/data/quick_bets.csv")
    for src in sorted({r.get("source") for r in qb if r.get("source")}):
        out.append(grade(f"My bets — {src}", [r for r in qb if r.get("source") == src], risk="stake",
                         money="real", note="Logged by hand"))
    return out


if __name__ == "__main__":
    for lane in lanes():
        roi = "—" if lane["roi"] is None else f"{lane['roi'] * 100:+.1f}%"
        clv = "—" if lane["clv"] is None else f"{lane['clv']:+.3f}"
        print(f"{lane['name']:<36} {lane['money']:<5} {lane['n']:>5} {lane['won']:>4}-{lane['lost']:<4} "
              f"{lane['pl']:>+9.2f} {roi:>7} {clv:>8}  {lane['verdict']}")
    print(json.dumps({"min_bets": MIN_BETS}))
