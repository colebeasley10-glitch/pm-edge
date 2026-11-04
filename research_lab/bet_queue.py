"""Bet Queue (PAPER lane, Nov 1, 2026): the one short daily list you would actually bet.

Every other lane logs everything it finds -- often 100+ plays a day across lanes, far more than
anyone can place by hand. The Bet Queue tests the plan for real money instead: each round it
looks at every lane's open, upcoming plays, keeps only lanes that are currently earning their
place on the scorecard, ranks the plays, and takes at most MAX_PER_DAY a day (Eastern). Its
record is exactly what you'd have if you bet only the queue, so that is the evidence for going live.

Ranking (no hand-picked favorites, so it adjusts by itself as lanes prove or fail):
  score = the lane's sample-size-shrunk ROI (scorecard.rank_score) + half the play's own EV,
          with the play EV capped at 10% (model probabilities run hot).
Eligible lanes: positive shrunk ROI, not "Proven loser", "Losing to the close" or "Demote", and not
a reference lane (every pick including no-edge). Nov 1, 2026: also positive CLV -- the lane must beat
the closing price on average over at least MIN_CLV_BETS bets. ROI on a small sample is mostly luck;
beating the close is the faster sign of a real edge. Lanes that don't track CLV (Trader Follow) can't
get in; BreakTheBank's NFL bets still alert straight from Trader Follow.
NBA props are checked against ESPN's injury report first (nba_injuries.py): Out/Doubtful players are
never queued, Questionable ones are queued on paper but never alerted, and no report = no alert. Steam plays from the Prop Board and Odds Fam are
the same props, so each prop is queued once.

Each queued play records the price, the fair price and EV at queue time, and two paper stakes:
  * flat $10 (the lane's main record, like every other lane),
  * quarter-Kelly on a $1,000 paper bankroll, capped at 2% a bet -- how it would look sized up.
Results, CLV and P/L come from the source lane once it settles the play. No orders.

Phone alerts (Nov 1, 2026): a queued play with a HUGE edge pushes to Cole's phone -- these are the
only Bet Queue plays he'd put real money on. Huge = EV of at least HUGE_EV_PINNACLE against a
Pinnacle fair price, or HUGE_EV_MODEL against our own model's price (model probabilities run hot,
so that bar is higher). At most MAX_ALERTS_PER_DAY a day. Sent through alert_gate as
"bet_queue_huge"; every queued play is still tracked on paper whether it alerts or not.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt

DIR = "BET_QUEUE"
ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
MAX_PER_DAY = 15
MAX_PER_ROUND = 5
MAX_PER_LANE_DAY = 6     # no single lane can fill the day
MIN_LEAD = timedelta(minutes=5)          # a play must still be bettable when it's queued
MAX_AHEAD = timedelta(hours=36)
FLAT = 10.0
BANKROLL = 1000.0
KELLY_FRACTION = 0.25
KELLY_CAP = 0.02
EV_CAP = 0.10
BAD_VERDICTS = ("Proven loser", "Losing to the close", "Demote")
HUGE_EV_PINNACLE = 0.06  # 6%+ vs Pinnacle's no-vig price
HUGE_EV_MODEL = 0.12     # 12%+ vs our own model's probability (model and copy-price fairs)
MAX_ALERTS_PER_DAY = 6
MIN_CLV_BETS = 20       # CLV readings a lane needs before its average counts
MIN_LANE_BETS = 30       # a lane (or slice) needs this many settled bets before it can feed the queue
FIELDS = ["queue_id", "alert_time", "day", "lane", "source", "source_id", "league", "game", "pick", "game_start",
          "bet_at", "decimal", "fair_prob", "fair_odds", "fair_src", "ev", "lane_score", "score", "kelly_pct", "stake_kelly",
          "status", "settled_time", "clv", "pl", "pl_kelly", "alerted", "injury"]


def _dir(base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / DIR


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _csv(p: Path) -> list[dict]:
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def american(dec: float) -> str:
    return f"+{(dec - 1) * 100:.0f}" if dec >= 2 else f"{-100 / (dec - 1):.0f}"


def dec_from_american(v) -> float | None:
    x = _f(v)
    if x is None or -100 < x < 100:
        return None
    return 1 + (x / 100 if x > 0 else 100 / -x)


def et_day(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%Y-%m-%d")


def load(base: Path | None = None) -> list[dict]:
    return _csv(_dir(base) / "queue.csv")


def _save(rows: list[dict], base: Path | None) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    _atomic_write_text(_dir(base) / "queue.csv", buf.getvalue())


# ------------------------------------------------------------------ candidates from every lane
_GAME_MODEL_SOURCES = ("ncaa_grader", "nba_grader")


def _game_key(source: str, sid: str, game) -> str | None:
    """One play per game per market (Nov 4, 2026). The NCAA grader runs two model versions
    (core_v1, scores_v1) that each log their own pick, so the same Akron +12.5 was queued twice,
    and Ball State ML and UMass ML (both sides of one game) were queued together."""
    if source not in _GAME_MODEL_SOURCES:
        return None
    market = str(sid).rsplit(":", 1)[-1].upper()
    if market not in ("SPREAD", "ML", "TOTAL") or not game:
        return None
    return f"game:{source}|{str(game).strip().lower()}|{market}"


def _cand(lane, source, sid, league, game, pick, start, dec, fair, fair_src="Pinnacle", key=None) -> dict | None:
    if not dec or dec <= 1 or not fair or not 0 < fair < 1 or not start:
        return None
    return {"lane": lane, "source": source, "source_id": sid, "league": league, "game": game, "pick": pick,
            "game_start": start, "decimal": round(dec, 4), "fair_prob": round(fair, 4), "fair_src": fair_src,
            "ev": round(fair * dec - 1, 4), "_key": key or _game_key(source, sid, game) or f"{source}:{sid}"}


def candidates(root: Path = ROOT, base: Path | None = None) -> list[dict]:
    lab = Path(base or config.DATA_DIR)
    out = []
    for r in _csv(lab / "PROP_BOARD" / "plays.csv"):
        if r.get("status") == "open":
            out.append(_cand("Prop Board steam", "prop_board", r["play_id"], r.get("league"), r.get("game"), r["pick"],
                             r.get("game_start"), _f(r.get("target_decimal")), _f(r.get("fair_prob")),
                             key=f"prop:{r['pick']}|{r.get('game')}"))
    for r in _csv(lab / "ODDS_FAM" / "plays.csv"):
        if r.get("status") == "open" and r.get("kind"):
            dec = _f(r.get("best_decimal")) or _f(r.get("bet_at_decimal"))
            key = f"prop:{r['pick']}|{r.get('game')}" if r.get("market") == "prop" else None
            out.append(_cand(f"Odds Fam — {r['kind']}", "odds_fam", r["of_id"], r.get("league"), r.get("game"),
                             r["pick"], r.get("game_start"), dec, _f(r.get("fair_prob")), key=key))
    for r in _csv(root / "ncaa_grader" / "data" / "picks.csv"):
        if not r.get("result") and r.get("tier") == "STRONG" and r.get("source", "live") == "live":
            out.append(_cand("NCAA Grader — STRONG", "ncaa_grader", r["pick_id"], "NCAAF", f"{r['away']} at {r['home']}",
                             r["side"], r.get("kickoff"), dec_from_american(r.get("odds")), _f(r.get("model_prob")),
                             "Model"))
    for r in _csv(root / "nba_grader" / "data" / "picks.csv"):
        if not r.get("result") and r.get("tier") in ("STRONG", "LEAN") and r.get("source", "live") == "live":
            out.append(_cand("NBA Model — Strong + Lean", "nba_grader", r["pick_id"], "NBA", f"{r['away']} at {r['home']}",
                             r["side"], r.get("kickoff"), dec_from_american(r.get("odds")), _f(r.get("model_prob")),
                             "Model"))
    for r in _csv(root / "nba_grader" / "data" / "props.csv"):
        if not r.get("result") and r.get("tier") in ("STRONG", "LEAN") and r.get("source", "live") == "live":
            out.append(_cand("NBA Props — Strong + Lean", "nba_props", r["pick_id"], "NBA", f"{r['away']} at {r['home']}",
                             r["side"], r.get("kickoff"), dec_from_american(r.get("odds")), _f(r.get("model_prob")),
                             "Model", key=f"prop:{r['side']}|{r['away']} at {r['home']}"))
    for r in _csv(root / "trader_follow" / "data" / "signals.csv"):
        if r.get("status") == "open" and r.get("in_band") == "True":
            cp = _f(r.get("copy_price"))
            fair = _f(r.get("fair_prob")) or (cp if cp else None)     # no Pinnacle match: treat as fair (0 EV)
            out.append(_cand(f"Follow {r['trader']} — {r.get('sport') or '?'}", "trader_follow", r["signal_id"],
                             r.get("sport"), r.get("event"), f"{r['outcome']} — {r['question']}", r.get("game_start"),
                             (1 / cp) if cp else None, fair, "Pinnacle" if _f(r.get("fair_prob")) else "Copy price"))
    return [c for c in out if c]


def lane_scores(lanes: list[dict]) -> dict[str, float]:
    """Lane name -> shrunk ROI, for lanes allowed into the queue.

    A slice ("Follow X — NFL") with fewer than MIN_LANE_BETS settled bets is judged by its parent
    lane ("Follow X") instead; once it has enough, its own record decides (good or bad)."""
    import scorecard
    by = {l["name"]: l for l in lanes}

    def good(l):
        if l.get("reference") or l["n"] < MIN_LANE_BETS or any(l["verdict"].startswith(b) for b in BAD_VERDICTS):
            return None
        if l.get("clv") is None or (l.get("clv_n") or 0) < MIN_CLV_BETS or l["clv"] <= 0:
            return None      # must be beating the close (Nov 1, 2026)
        s = scorecard.rank_score(l)
        return s if s > 0 else None

    ok = {}
    for name, l in by.items():
        judge = l
        if l["n"] < MIN_LANE_BETS and " — " in name:
            judge = by.get(name.split(" — ")[0]) or l
        s = good(judge)
        if s is not None:
            ok[name] = s
    return ok


def kelly(fair: float, dec: float) -> float:
    b = dec - 1
    full = (b * fair - (1 - fair)) / b if b > 0 else 0.0
    return max(0.0, min(KELLY_CAP, KELLY_FRACTION * full))


# ------------------------------------------------------------------ settle from the source lane
def _source_result(q: dict, root: Path, lab: Path) -> tuple[str, float | None] | None:
    """(won/lost/void, clv in probability points or None) once the source has settled the play."""
    src, sid = q["source"], q["source_id"]
    if src in ("prop_board", "odds_fam"):
        f = lab / ("PROP_BOARD/plays.csv" if src == "prop_board" else "ODDS_FAM/plays.csv")
        col = "play_id" if src == "prop_board" else "of_id"
        r = next((x for x in _csv(f) if x.get(col) == sid), None)
        if not r or r.get("status") not in ("won", "lost", "void", "ungraded"):
            return None
        return ("void" if r["status"] == "ungraded" else r["status"]), _f(r.get("clv"))
    if src in ("ncaa_grader", "nba_grader", "nba_props"):
        f = {"ncaa_grader": "ncaa_grader/data/picks.csv", "nba_grader": "nba_grader/data/picks.csv",
             "nba_props": "nba_grader/data/props.csv"}[src]
        r = next((x for x in _csv(root / f) if x.get("pick_id") == sid), None)
        if not r or r.get("result") not in ("W", "L", "P", "V"):
            return None
        res = {"W": "won", "L": "lost"}.get(r["result"], "void")
        prob_clv = src == "nba_props" or r.get("market") == "ML"      # spreads/totals CLV is in points
        return res, (_f(r.get("clv")) if prob_clv else None)
    if src == "trader_follow":
        r = next((x for x in _csv(root / "trader_follow/data/signals.csv") if x.get("signal_id") == sid), None)
        if not r or r.get("status") not in ("won", "lost", "void"):
            return None
        return r["status"], None
    return None


def settle(rows: list[dict], now: datetime, root: Path, lab: Path) -> int:
    n = 0
    for q in rows:
        if q["status"] != "open":
            continue
        got = _source_result(q, root, lab)
        if not got:
            start = parse_dt(q.get("game_start"))
            if start and now - start > timedelta(days=4):
                q.update(status="void", pl=0.0, pl_kelly=0.0, settled_time=iso(now))
                n += 1
            continue
        res, clv = got
        dec, stake = float(q["decimal"]), float(q.get("stake_kelly") or 0)
        q["status"], q["settled_time"] = res, iso(now)
        q["clv"] = "" if clv is None else round(clv, 4)
        q["pl"] = round(FLAT * (dec - 1), 2) if res == "won" else (-FLAT if res == "lost" else 0.0)
        q["pl_kelly"] = round(stake * (dec - 1), 2) if res == "won" else (-stake if res == "lost" else 0.0)
        n += 1
    return n


_STAT_WORDS = (" Over ", " Under ")


def _player_key(q: dict) -> str:
    """'Brandon Williams Under 14.5 Points' -> 'brandon williams|game': one prop per player per day."""
    pick = f" {q.get('pick') or ''} "
    for w in _STAT_WORDS:
        if w in pick and " — " not in pick:
            return pick.split(w)[0].strip().lower() + "|" + str(q.get("game") or "")
    return ""


# ------------------------------------------------------------------ huge-EV phone alerts
def is_huge(q: dict) -> bool:
    ev = _f(q.get("ev")) or 0.0
    return ev >= (HUGE_EV_PINNACLE if q.get("fair_src") == "Pinnacle" else HUGE_EV_MODEL)


def alert_text(q: dict) -> tuple[str, str]:
    start = parse_dt(q.get("game_start"))
    when = start.astimezone(ET).strftime("%a %-I:%M %p ET") if start else "?"
    ev = float(q["ev"])
    title = f"🔥 Bet Queue {ev * 100:+.1f}% EV: {q['pick']}"[:250]
    lines = [
        f"{q['pick']}",
        f"{q.get('league') or ''} · {q.get('game') or ''}".strip(" ·"),
        f"Bet at {q['bet_at']} or better (fair {q['fair_odds']}, {q.get('fair_src') or 'fair'})",
        f"Edge: {ev * 100:+.1f}% EV · quarter-Kelly {float(q.get('kelly_pct') or 0) * 100:.1f}% of bankroll",
        f"From: {q['lane']}",
        f"Starts {when}",
    ]
    return title, "\n".join(lines)


def _default_push(title: str, message: str) -> bool:
    import alert_gate
    return alert_gate.send("bet_queue_huge", title, message, os.getenv("STREAMLIT_APP_URL") or None,
                           url_title="Open SharpIQ")


# ------------------------------------------------------------------ one round
_UNSET = object()


def _prop_player(pick: str) -> str:
    p = f" {pick or ''} "
    for w in _STAT_WORDS:
        if w in p:
            return p.split(w)[0].strip()
    return ""


def update(now: datetime, base: Path | None = None, *, root: Path = ROOT, lanes: list[dict] | None = None,
           push=None, injury_report=_UNSET) -> dict:
    lab = Path(base or config.DATA_DIR)
    rows = load(base)
    info = {"queued": 0, "settled": settle(rows, now, root, lab), "eligible_lanes": 0, "candidates": 0}
    if lanes is None:
        import scorecard
        lanes = scorecard.lanes()
    ok = lane_scores(lanes)
    info["eligible_lanes"] = len(ok)
    day = et_day(now)
    today = [q for q in rows if q.get("day") == day]
    seen = {q["source"] + ":" + q["source_id"] for q in rows}
    seen_props = {f"prop:{q['pick']}|{q.get('game')}" for q in rows}
    seen_props |= {k for q in rows if (k := _game_key(q.get("source", ""), q.get("source_id", ""), q.get("game")))}
    pool = []
    for c in candidates(root, base):
        start = parse_dt(c["game_start"])
        if c["lane"] not in ok or not start or not (now + MIN_LEAD <= start <= now + MAX_AHEAD):
            continue
        if f"{c['source']}:{c['source_id']}" in seen or c["_key"] in seen_props:
            continue
        c["lane_score"] = round(ok[c["lane"]], 4)
        c["score"] = round(ok[c["lane"]] + 0.5 * min(c["ev"], EV_CAP), 4)
        pool.append(c)
    if any(c["source"] == "nba_props" for c in pool):
        import nba_injuries
        report = nba_injuries.fetch() if injury_report is _UNSET else injury_report
        info["injury_report"] = "ok" if report is not None else "unavailable"
        kept = []
        for c in pool:
            if c["source"] == "nba_props":
                player = _prop_player(c["pick"])
                verdict = nba_injuries.check(player, report)
                if verdict == "block":
                    info["injury_blocked"] = info.get("injury_blocked", 0) + 1
                    continue
                if verdict == "warn":
                    c["injury"] = report.get(nba_injuries.norm(player), "listed")
                elif verdict == "unknown":
                    c["injury"] = "no injury report"
            kept.append(c)
        pool = kept
    info["candidates"] = len(pool)
    pool.sort(key=lambda c: (-c["score"], -c["ev"], c["game_start"]))
    taken_keys: set[str] = set()
    per_lane = {}
    players = set()
    for q in today:
        per_lane[q["lane"]] = per_lane.get(q["lane"], 0) + 1
        players.add(_player_key(q))
    for c in pool:
        if len(today) >= MAX_PER_DAY or info["queued"] >= MAX_PER_ROUND:
            break
        pk = _player_key(c)
        if c["_key"] in taken_keys or per_lane.get(c["lane"], 0) >= MAX_PER_LANE_DAY or (pk and pk in players):
            continue
        k = kelly(c["fair_prob"], c["decimal"])
        q = {**{k_: v for k_, v in c.items() if not k_.startswith("_")},
             "queue_id": f"q-{c['source']}-{c['source_id']}"[:90], "alert_time": iso(now), "day": day,
             "bet_at": american(c["decimal"]), "fair_odds": american(1 / c["fair_prob"]), "kelly_pct": round(k, 4), "stake_kelly": round(BANKROLL * k, 2),
             "status": "open", "settled_time": "", "clv": "", "pl": "", "pl_kelly": "", "alerted": "",
             "injury": c.get("injury", "")}
        rows.append(q)
        today.append(q)
        taken_keys.add(c["_key"])
        per_lane[c["lane"]] = per_lane.get(c["lane"], 0) + 1
        if pk:
            players.add(pk)
        info["queued"] += 1
    info["alerted"] = _alert_new(today, now, push or _default_push)
    _save(rows, base)
    return info


def _alert_new(today: list[dict], now: datetime, push) -> int:
    """Push today's not-yet-alerted huge-EV plays (oldest first), up to MAX_ALERTS_PER_DAY.
    A failed push is retried next round; a muted/skipped one isn't marked, so it's never stuck."""
    sent_today = sum(q.get("alerted") == "Y" for q in today)
    n = 0
    for q in today:
        if sent_today >= MAX_ALERTS_PER_DAY:
            break
        start = parse_dt(q.get("game_start"))
        if q.get("status") != "open" or q.get("alerted") or not is_huge(q) or not start or start <= now:
            continue
        if q.get("injury"):
            continue         # questionable player or no injury report: paper only, no buzz
        title, msg = alert_text(q)
        try:
            ok = bool(push(title, msg))
        except Exception as exc:   # an alert problem must never break the queue
            print("Bet Queue alert error:", exc)
            ok = False
        if ok:
            q["alerted"] = "Y"
            sent_today += 1
            n += 1
    return n
