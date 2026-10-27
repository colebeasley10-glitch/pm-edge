"""Scorecard: every lane graded against one go-live bar (Oct 27, 2026).

A lane earns real money only when ALL of these hold on its settled bets:
  * at least MIN_BETS settled bets,
  * ROI above zero,
  * average CLV at or above zero (beating the closing price on average).
A hot week is not a reason to go live or size up; clearing the bar is.

Reads only the ledgers already committed to the repo, so it costs nothing to run.
Each lane's CLV is in its own unit (cents for Kalshi lanes, probability for Pinnacle lanes);
only its sign is used for the bar.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

MIN_BETS = 150
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
          money: str = "paper", note: str = "") -> dict:
    """rows -> {name, n, won, lost, pl, risk, roi, clv, verdict}. `risk` is a column name or a
    flat stake per bet; unsettled rows (blank P/L) are skipped."""
    n = won = lost = 0
    tot_pl = tot_risk = 0.0
    clvs: list[float] = []
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
        if clv and (c := _f(r.get(clv))) is not None:
            clvs.append(c)
    roi = tot_pl / tot_risk if tot_risk else None
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    checks = {"bets": n >= MIN_BETS, "roi": roi is not None and roi > 0,
              "clv": None if avg_clv is None else avg_clv >= 0}
    if n == 0:
        verdict = "No settled bets"
    elif checks["bets"] and checks["roi"] and checks["clv"] is not False:
        verdict = "Clears the bar" if checks["clv"] else "Clears bets + ROI (no CLV data)"
    elif n >= MIN_BETS and not checks["roi"]:
        verdict = "Proven loser — keep off"
    elif checks["clv"] is False and n >= 50:
        verdict = "Losing to the close — not real yet"
    else:
        verdict = f"Too early — {max(0, MIN_BETS - n)} more bets"
    return {"name": name, "money": money, "n": n, "won": won, "lost": lost, "pl": round(tot_pl, 2),
            "risk": round(tot_risk, 2), "roi": None if roi is None else round(roi, 4),
            "clv": None if avg_clv is None else round(avg_clv, 4), "checks": checks, "verdict": verdict,
            "note": note}


def lanes() -> list[dict]:
    out: list[dict] = []
    out.append(grade("Live Sharp", _rows("live_sharp_trades.csv"), clv="clv_cents", money="real",
                     note="Turned off Oct 27, 2026"))
    out.append(grade("Live Today's Plays", _rows("live_plays/data/bets.csv"), clv="clv_cents", money="real",
                     note="Paused Oct 27, 2026; open bets still settle"))
    out.append(grade("Maker Bot", _rows("maker_bot/data/orders.csv"), money="real", note="Off"))

    league = _rows("league_trades.csv")
    out.append(grade("Paper Sharp (benchmark)", [r for r in league if r.get("strategy") == "sharp"], clv="clv_cents"))

    for key, label in (("EV_FINDER_v2", "Today's Plays (EV v2)"), ("EV_FINDER_v3", "Today's Plays -200/+150 (EV v3)")):
        out.append(grade(label, _rows(f"research_lab/data/{key}/positions.csv"), risk="committed", clv="clv_cents"))

    tf = _rows("trader_follow/data/signals.csv")
    for trader in sorted({r.get("trader") for r in tf if r.get("trader")}):
        mine = [r for r in tf if r.get("trader") == trader]
        out.append(grade(f"Follow {trader}", mine, pl="pl_copy", risk=10.0, note="$10 paper per copy"))
        nfl = [r for r in mine if r.get("sport") == "NFL"]
        if nfl:
            out.append(grade(f"Follow {trader} — NFL only", nfl, pl="pl_copy", risk=10.0))

    out.append(grade("Prop Board steam", _rows("research_lab/data/PROP_BOARD/plays.csv"), risk=10.0, clv="clv"))
    of = _rows("research_lab/data/ODDS_FAM/plays.csv")
    for kind in sorted({r.get("kind") for r in of if r.get("kind")}):
        out.append(grade(f"Odds Fam — {kind}", [r for r in of if r.get("kind") == kind], risk=10.0, clv="clv"))
    out.append(grade("Sharp Plays (line steam)", _rows("research_lab/data/SHARP_PLAYS/plays.csv"), risk=10.0, clv="clv"))

    ncaa = [r for r in _rows("ncaa_grader/data/picks.csv") if r.get("units") not in (None, "")]
    out.append(grade("NCAA Grader — all", ncaa, pl="units", risk=1.0, clv="clv"))
    out.append(grade("NCAA Grader — STRONG", [r for r in ncaa if r.get("tier") == "STRONG"], pl="units", risk=1.0, clv="clv"))
    out.append(grade("NCAA Grader — ML", [r for r in ncaa if r.get("market") == "ML"], pl="units", risk=1.0, clv="clv"))

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
