"""Entry point (PAPER ONLY).

  python -m ncaab_grader.run            # project, price, log, close, settle, report
  python -m ncaab_grader.run --report   # just print the daily report again

Data: ESPN's free D1 scoreboard + box scores (no key, no quota). Preseason priors come from
ncaab_model/data/priors_<season>.csv (built offline by tools/ncaab_backtest.py from public
hoopR/ESPN history). New BET-tier plays go to your phone if the Pushover secrets are set.
Nothing here can place a bet.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from espn_ncaab import ET, ESPNNCAAB, PossCache, season_start, season_year
from ncaab_grader import core, report
from ncaab_model.ratings import Ratings

MODEL_DIR = Path(__file__).resolve().parent.parent / "ncaab_model" / "data"


def load_priors(season: int) -> tuple[dict, dict, tuple[float, float]]:
    path = MODEL_DIR / f"priors_{season}.csv"
    meta = json.loads((MODEL_DIR / f"priors_{season}_meta.json").read_text())
    prior, info = {}, {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            t = str(r["team_id"])
            prior[t] = (float(r["prior_o"]), float(r["prior_d"]), float(r["prior_tempo"]))
            info[t] = r
    return prior, info, (float(meta["league_eff"]), float(meta["league_tempo"]))


def to_fit_rows(games: list[dict], d1: set, poss: dict) -> list[dict]:
    out = []
    for g in games:
        if not g.get("completed") or g.get("homePoints") is None:
            continue
        if g["homeId"] not in d1 or g["awayId"] not in d1:
            continue                        # D1 vs D1 only, like the training data
        try:
            day = date.fromisoformat(str(g["week"])[:10])
        except ValueError:
            continue
        out.append({"day": day, "home": g["homeId"], "away": g["awayId"], "home_pts": g["homePoints"],
                    "away_pts": g["awayPoints"], "poss": poss.get(str(g["id"])), "neutral": g.get("neutralSite")})
    return out


def make_projector(fit_rows: list[dict], prior: dict, pinfo: dict, league, params: dict):
    fits: dict = {}

    def project(g):
        if g["homeId"] not in prior or g["awayId"] not in prior:
            return None, core.OUT_OF_SCOPE
        try:
            d = date.fromisoformat(str(g["week"])[:10])
        except ValueError:
            return None, "bad date"
        if d not in fits:
            rows = [r for r in fit_rows if r["day"] < d]
            no_poss = sum(1 for r in rows if not r.get("poss"))
            fits[d] = (Ratings(rows, as_of=d, prior=prior, league=league, **params),
                       no_poss / len(rows) if rows else 0.0)
        r, no_poss_share = fits[d]
        p = r.project(g["homeId"], g["awayId"], bool(g.get("neutralSite")))
        if p is None:
            return None, "no rating"
        return p, {"home_games": r.n_games.get(g["homeId"], 0), "away_games": r.n_games.get(g["awayId"], 0),
                   "home_new": pinfo.get(g["homeId"], {}).get("new") == "1.0",
                   "away_new": pinfo.get(g["awayId"], {}).get("new") == "1.0",
                   "no_poss_share": no_poss_share}
    return project, fits


def _notify(new: list[dict]) -> int:
    token, user = os.getenv("PUSHOVER_APP_TOKEN", "").strip(), os.getenv("PUSHOVER_USER_KEY", "").strip()
    bets = [r for r in new if r.get("tier") == "BET"]
    if not (token and user and bets):
        return 0
    lines = []
    for r in sorted(bets, key=lambda r: -float(r["ev"]))[:8]:
        tip = report.tip(r["tipoff"])
        lines.append(f"{r['side']} ({core.fmt_odds(float(r['odds']))}) · {r['away']} @ {r['home']} {tip} · "
                     f"EV {float(r['ev']) * 100:+.1f}% · ${float(r['stake']):.2f} · min {core.fmt_odds(float(r['min_odds']))}")
    payload = {"token": token, "user": user,
               "title": f"🏀 NCAAB model: {len(bets)} paper bet{'s' if len(bets) != 1 else ''}",
               "message": ("\n".join(lines) + "\nPaper lane. Check injuries/lineups first.")[:1024]}
    url = os.getenv("STREAMLIT_APP_URL", "").strip().rstrip("/")
    if url:
        payload.update({"url": url + "/Research_Lab_NCAAB_Model", "url_title": "Open NCAAB Model"})
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)
    except Exception:
        return 0
    return len(bets)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    card = core.load_card()
    rows = core.load_ledger()
    if args.report:
        print(core.REPORT_PATH.read_text() if core.REPORT_PATH.exists() else "no report yet")
        return

    now = datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    season = season_year(today)
    prior, pinfo, league = load_priors(season)
    d1 = set(prior)

    espn = ESPNNCAAB()
    games, lines = espn.window(season_start(today), today + timedelta(days=1), today)
    espn.save()
    status = {"at": now.isoformat(timespec="seconds"), "season": season, "model": card["version"],
              "espn_calls": espn.calls, "espn_errors": espn.errors[-5:]}
    if not games:
        status["suspended"] = "ESPN returned no games — no recommendations this run"
        core.write_status(status)
        print(f"::warning title=NCAAB model skipped::ESPN returned no games. {'; '.join(espn.errors[-3:])}")
        return

    pc = PossCache()
    finals = [g for g in games if g.get("completed") and g["homeId"] in d1 and g["awayId"] in d1]
    for g in sorted(finals, key=lambda g: str(g.get("week")), reverse=True):
        ko = core._parse_dt(g.get("startDate") or "")
        if ko and now - ko > timedelta(hours=4):
            pc.get(g["id"])
    pc.save()
    poss = {k: v for k, v in pc.cache.items() if v}
    fit_rows = to_fit_rows(games, d1, poss)

    # Data-quality and calibration guards: BETs are downgraded to WATCH while any of these fire.
    reasons = []
    poss_cov = sum(1 for r in fit_rows if r.get("poss")) / len(fit_rows) if fit_rows else 1.0
    if fit_rows and len(fit_rows) >= 100 and poss_cov < 0.7:
        reasons.append(f"possession data for only {poss_cov:.0%} of finals")
    cal = core.calibration_check(rows)
    if cal["suspend"]:
        reasons.append(f"live calibration off (Brier {cal['brier']} on {cal['n']} picks)")
    if espn.errors and espn.calls == 0:
        reasons.append("ESPN feed errors — using cached data")

    project, fits = make_projector(fit_rows, prior, pinfo, league, card["params"])
    out = core.run_live(games=games, lines=lines, project=project, card=card, now=now, rows=rows,
                        suspended="; ".join(reasons))
    if out["games_with_lines"] >= 10 and len(out["insufficient"]) > 0.3 * out["games_with_lines"]:
        reasons.append(f"{len(out['insufficient'])} D1 games couldn't be projected")
        core.downgrade_bets(out["new"])
    core.save_ledger(out["rows"])
    # Home-court spread TEST lane (paper, own ledger). Paused, but still settled, while a guard fires.
    ht_card = card if not reasons else {**card, "home_test": {**card.get("home_test", {}), "enabled": False}}
    ht = core.run_home_test(games=games, lines=lines, project=project, card=ht_card, now=now)
    core.save_ledger(ht["rows"], core.HOME_TEST_PATH)
    pushed = _notify(out["new"]) if not reasons else 0

    latest = Ratings(fit_rows, as_of=today + timedelta(days=1), prior=prior, league=league, **card["params"])
    names = {}
    for g in games:
        names[g["homeId"]], names[g["awayId"]] = g["homeTeam"], g["awayTeam"]
    for t, r in pinfo.items():
        names.setdefault(t, r.get("team_name") or t)
    table = [{**r, "team": names.get(r["team"], r["team"]), "prior_net": round(float(pinfo[r["team"]]["prior_net"]), 1)}
             for r in latest.table() if r["team"] in d1]

    status.update({"games_this_season": len(games), "finals_d1": len(fit_rows),
                   "possession_coverage": round(poss_cov, 3), "box_calls": pc.calls, "box_errors": pc.errors[-3:],
                   "lines": len(lines), "logged": out["logged"], "settled": out["settled"],
                   "close_updates": out["close_updates"], "upcoming_games": out["upcoming_games"],
                   "games_with_lines": out["games_with_lines"], "insufficient": out["insufficient"],
                   "no_line": out["no_line"][:30], "out_of_scope_games": out["out_of_scope"], "bets_pushed": pushed, "suspended": "; ".join(reasons),
                   "calibration": cal, "bankroll": core.bankroll(out["rows"], card),
                   "home_test": {"logged": ht["logged"], "settled": ht["settled"],
                                 "bankroll": core.home_test_bankroll(ht["rows"], card),
                                 "record": core.stats(ht["rows"], pl="pnl", risk="stake")},
                   "home_court_pts": round(latest.hca / 100 * latest.tempo, 2), "league_eff": round(latest.mu, 1),
                   "league_tempo": round(latest.tempo, 1), "ratings": table})
    core.write_status(status)
    md = report.daily(out["rows"], status, card, now=now)
    core.REPORT_PATH.write_text(md)
    report.export_excel(out["rows"], status, card)
    print(md)


if __name__ == "__main__":
    main()
