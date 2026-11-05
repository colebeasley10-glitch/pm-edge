"""Odds Fam: one daily feed of 3-15 sharp plays, OddsJam-style, built only from free data.

Three sources, best first:
  1. Prop steam  - Pinnacle moved a player prop hard (research_lab.prop_board)
  2. Line steam  - Pinnacle moved a side/total hard (research_lab.sharp_plays)
  3. Price gap   - Kalshi or Polymarket is already cheaper than Pinnacle's fair price (we line-shop
                   these two ourselves; the alert says where and at what price)
Every play gives the fair price and the worst odds still worth taking, so it can be shopped by
hand on Betr, Rebet or any book. Cole logs what he actually placed from the Odds Fam page; this
module grades both the paper record (1u at the target price) and his real bets.

Files (research_lab/data/ODDS_FAM):
  plays.csv        written here (the feed + paper results)
  game_board.json  written here (fair prices for every game market, for shopping)
  my_bets.csv      written ONLY by the app (one row per bet he logs; never edited here)
  my_results.json  written here (settlement of his logged bets)
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research_lab import config
from research_lab.ledger import _atomic_write_text, iso, parse_dt

DIR = "ODDS_FAM"
UNIT = 10.0
MAX_PER_DAY = 15
MIN_PER_DAY = 3
MAX_PER_ROUND = 6
GAP_EV = 0.02                # price gaps need +2% EV vs Pinnacle...
GAP_EV_FLOOR = 0.01          # ...or +1% late in the day if fewer than MIN_PER_DAY went out
FLOOR_HOUR_ET = 15
QUIET_START, QUIET_END = 23, 8   # ET: no phone pushes overnight; held plays go out in one 8am digest
SETTLE_AFTER = timedelta(minutes=90)   # ESPN's "completed" flag decides; was 4h (Nov 4, 2026)
GAP_MAX_AHEAD = timedelta(hours=30)   # price gaps only on games within ~a day (prices days out are noise)
MIN_DEC, MAX_DEC = 1.5, 2.5  # -200 to +150, the range Cole bets
ET = ZoneInfo("America/New_York")
KIND_RANK = {"Pick'em gap": 4, "Prop steam": 3, "Line steam": 2, "Price gap": 1}
FIELDS = ["of_id", "sent_at", "kind", "league", "game", "pick", "market", "line", "why", "fair_prob", "fair_odds",
          "bet_at", "bet_at_decimal", "best_found", "best_decimal", "game_start", "source_id", "settle_ticker",
          "settle_side", "pushed", "clv", "status", "pl", "settled_at", "player", "stat", "side", "actual"]
BET_FIELDS = ["bet_id", "logged_at", "of_id", "pick", "book", "odds", "decimal", "stake", "line_taken",
              # Nov 1, 2026 (v2): bets logged straight from the Target Price Board, any source
              "source", "board_id", "close_key", "fair_prob", "kind", "league", "game", "game_start", "market",
              "line", "player", "stat", "side", "settle_ticker", "settle_side"]
SOURCES = ["Odds Fam", "Hunter", "Me"]
SHOP = "Courtside, Betr, Rebet, Onyx"      # the slow books Hunter's plays come from
CLOSE_TTL = timedelta(days=4)


def _dir(base: Path | None) -> Path:
    return Path(base or config.DATA_DIR) / DIR


def american(dec: float) -> str:
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else f"{round(-100 / (dec - 1))}"


def american_to_decimal(text) -> float | None:
    t = str(text or "").strip().replace("−", "-").replace(" ", "")
    try:
        v = float(t)
    except ValueError:
        return None
    if v <= -100:
        return 1 + 100 / -v
    if v >= 100:
        return 1 + v / 100
    return None


def price_check(text, worst_decimal, fair_prob) -> dict | None:
    """Is a price you see good enough? Compares it to the play's worst price (fair + 2% edge).

    Returns None if the odds can't be read; otherwise ok (at or above the worst price), the EV
    at that price vs Pinnacle fair, and a one-line verdict.
    """
    dec = american_to_decimal(text)
    if dec is None:
        return None
    worst, fair = float(worst_decimal), float(fair_prob)
    ev = fair * dec - 1
    shown = american(dec)
    if dec >= worst - 1e-9:
        return {"ok": True, "ev": ev, "msg": f"Bet it: {shown} is at or above the worst price {american(worst)} ({ev * 100:+.1f}% EV)."}
    if ev > 0:
        why = f"only {ev * 100:+.1f}% EV, under the 2% cushion"
    else:
        why = f"{ev * 100:+.1f}% EV, worse than the fair price {american(1 / fair)}"
    return {"ok": False, "ev": ev, "msg": f"Pass: {shown} is below the worst price {american(worst)} ({why})."}


def et_day(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ storage
def _read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(p: Path, rows: list[dict], fields: list[str]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    _atomic_write_text(p, buf.getvalue())


def load(base: Path | None = None) -> list[dict]:
    return _read_csv(_dir(base) / "plays.csv")


def load_bets(base: Path | None = None) -> list[dict]:
    return _read_csv(_dir(base) / "my_bets.csv")


def load_results(base: Path | None = None) -> dict:
    p = _dir(base) / "my_results.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def load_game_board(base: Path | None = None) -> dict:
    p = _dir(base) / "game_board.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def render_bets(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=BET_FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in BET_FIELDS})
    return buf.getvalue()


def parse_bets(text: str) -> list[dict]:
    return [dict(r) for r in csv.DictReader(io.StringIO(text))] if text.strip() else []


# ------------------------------------------------------------------ candidates
def from_prop(p: dict) -> dict:
    return {"kind": "Prop steam", "league": p["league"], "game": p["game"], "pick": p["pick"], "market": "prop",
            "line": p["line"], "why": f"{p['move']} · P limit ${int(float(p['limit'])):,}",
            "fair_prob": p["fair_prob"], "fair_odds": p["fair_odds"], "bet_at": p["target_odds"],
            "bet_at_decimal": p["target_decimal"], "best_found": "", "best_decimal": "",
            "game_start": p["game_start"], "source_id": f"prop:{p['play_id']}", "settle_ticker": "", "settle_side": ""}


def from_pickem(e: dict) -> dict:
    """Sleeper multiplier beats Pinnacle fair (research_lab.sleeper.edges row)."""
    side = e["side"].title()
    pick = f"{e['player']} {side} {e['sleeper_line']:g} {e['stat']}"
    target = 1.02 / e["p"]
    line_note = "" if e["sleeper_line"] == e["pin_line"] else f" (Pinnacle line {e['pin_line']:g})"
    return {"kind": "Pick'em gap", "league": e["league"], "game": e["game"], "pick": pick, "market": "prop",
            "line": e["sleeper_line"],
            "why": f"Sleeper {e['mult']:.2f}x vs Pinnacle fair {american(1 / e['p'])}{line_note}: "
                   f"+{e['ev'] * 100:.1f}% EV",
            "fair_prob": round(e["p"], 4), "fair_odds": american(1 / e["p"]), "bet_at": american(target),
            "bet_at_decimal": round(target, 4), "best_found": f"Sleeper {e['mult']:.2f}x ({american(e['mult'])})",
            "best_decimal": round(e["mult"], 4), "game_start": e["starts"], "source_id": f"pickem:{e['key']}",
            "settle_ticker": "", "settle_side": "", "player": e["player"], "stat": e["stat"], "side": e["side"],
            "_ev": e["ev"]}


def from_line(p: dict) -> dict:
    return {"kind": "Line steam", "league": p["league"], "game": p["game"], "pick": p["pick"], "market": p["market"],
            "line": p["line"], "why": p["move"], "fair_prob": p["fair_prob"], "fair_odds": p["fair_odds"],
            "bet_at": p["target_odds"], "bet_at_decimal": p["target_decimal"], "best_found": "", "best_decimal": "",
            "game_start": p["game_start"], "source_id": f"line:{p['play_id']}", "settle_ticker": "", "settle_side": ""}


def best_offer(pick) -> tuple | None:
    offers = [o for o in (pick.offers or []) if o.venue in {"Kalshi", "Polymarket"} and o.decimal]
    return max(offers, key=lambda o: o.decimal) if offers else None


def from_gap(pick, now: datetime) -> dict | None:
    o = best_offer(pick)
    if (o is None or not pick.fair or not pick.starts or pick.starts <= now + timedelta(minutes=15)
            or pick.starts > now + GAP_MAX_AHEAD):
        return None
    ev = pick.fair * o.decimal - 1
    if not (MIN_DEC <= o.decimal <= MAX_DEC):
        return None
    target = 1.02 / pick.fair
    return {"kind": "Price gap", "league": pick.league, "game": pick.game, "pick": pick.label, "market": pick.market,
            "line": "", "why": f"{o.venue} {o.shown} ({american(o.decimal)}) beats Pinnacle fair by {ev * 100:.1f}%",
            "fair_prob": round(pick.fair, 4), "fair_odds": american(1 / pick.fair), "bet_at": american(target),
            "bet_at_decimal": round(target, 4), "best_found": f"{o.venue} {american(o.decimal)}",
            "best_decimal": round(o.decimal, 4), "game_start": iso(pick.starts), "source_id": f"gap:{pick.key}",
            "settle_ticker": pick.settle_ticker, "settle_side": pick.settle_side, "_ev": ev, "_url": o.url}


def game_board(picks, now: datetime) -> list[dict]:
    out = []
    for p in picks or []:
        if not p.starts or p.starts <= now or not p.fair:
            continue
        o = best_offer(p)
        out.append({"league": p.league, "game": p.game, "market": p.market, "pick": p.label,
                    "starts": iso(p.starts), "fair_odds": american(1 / p.fair), "bet_at": american(1.02 / p.fair),
                    "best_found": f"{o.venue} {american(o.decimal)}" if o else "",
                    "ev": round(p.fair * o.decimal - 1, 4) if o else None})
    return out


def alert_text(play: dict) -> tuple[str, str]:
    from trader_follow.core import _eastern
    start = parse_dt(play["game_start"])
    icon = {"Pick'em gap": "🔥", "Prop steam": "🎯", "Line steam": "📈", "Price gap": "💲"}[play["kind"]]
    lines = [f"{play['game']} · {play['league']} · {_eastern(start) if start else ''}",
             f"Why: {play['why']}",
             f"Fair {play['fair_odds']} · worst price {play['bet_at']}: bet at {play['bet_at']} or better, pass on anything lower"]
    if play.get("best_found"):
        lines.append(f"Best we found: {play['best_found']}")
    else:
        lines.append(f"Shop it: {SHOP}")
    lines.append("Size: 1u · log it in Odds Fam once placed")
    return f"{icon} Odds Fam: {play['pick']}", "\n".join(lines)


# ------------------------------------------------------------------ one round
def update(now: datetime, base: Path | None = None, *, new_props=(), new_lines=(), picks=(), markets=None,
           push=None, scores=None, pickem=(), box=None) -> dict:
    rows = load(base)
    info = {"sent": 0, "pushed": 0, "settled": 0, "bets_settled": 0}
    if picks:
        _atomic_write_text(_dir(base) / "game_board.json",
                           json.dumps({"at": iso(now), "rows": game_board(picks, now)}, indent=0))
    day = et_day(now)
    sent_today = [r for r in rows if et_day(parse_dt(r["sent_at"]) or now) == day]
    seen = {r["source_id"] for r in rows}
    # one game-line play per game, ever (Oct 26: Minnesota +17.5 and Indiana -20.5 both went out
    # for the same game across midnight)
    games_today = {(r["game"], r["market"] != "prop") for r in rows}
    hour = now.astimezone(ET).hour
    quiet = hour >= QUIET_START or hour < QUIET_END
    floor = FLOOR_HOUR_ET <= hour < QUIET_START and len(sent_today) < MIN_PER_DAY
    cands = ([from_pickem(e) for e in pickem] + [from_prop(p) for p in new_props]
             + [from_line(p) for p in new_lines])
    for pk in picks or []:
        g = from_gap(pk, now)
        if g and g["_ev"] >= (GAP_EV_FLOOR if floor else GAP_EV):
            cands.append(g)
    cands.sort(key=lambda c: (-KIND_RANK[c["kind"]], -c.get("_ev", 0)))
    sent_round = 0
    for c in cands:
        if len(sent_today) >= MAX_PER_DAY or sent_round >= MAX_PER_ROUND or c["source_id"] in seen:
            continue
        gk = (c["game"], c["market"] != "prop")
        if c["market"] != "prop" and gk in games_today:
            continue                       # one game-line play per game per day
        url = c.pop("_url", None)
        c.pop("_ev", None)
        play = {**c, "of_id": c["source_id"].replace(":", "-")[:80], "sent_at": iso(now), "pushed": False,
                "clv": "", "status": "open", "pl": "", "settled_at": ""}
        if push and not quiet:
            t, m = alert_text(play)
            try:
                play["pushed"] = bool(push(t, m, url))
            except Exception:
                play["pushed"] = False
        rows.append(play)
        sent_today.append(play)
        seen.add(play["source_id"])
        games_today.add(gk)
        sent_round += 1
        info["sent"] += 1
        info["pushed"] += play["pushed"] is True
    if push and not quiet:
        info["digest"] = morning_digest(rows, now, push)
    info["settled"] = grade(rows, now, base, markets or {}, box)
    _write_csv(_dir(base) / "plays.csv", rows, FIELDS)
    info["bets_settled"] = settle_bets(rows, now, base, scores)
    return info


def morning_digest(rows: list[dict], now: datetime, push) -> int:
    """One push listing plays found overnight (quiet hours) that are still bettable."""
    held = [r for r in rows if str(r.get("pushed")) in {"False", ""} and r["status"] == "open"
            and (parse_dt(r["game_start"]) or now) > now]
    if not held:
        return 0
    lines = [f"• {r['pick']} ({r['league']}) worst price {r['bet_at']}" for r in held[:15]]
    try:
        ok = push(f"☀️ Odds Fam: {len(held)} play{'s' if len(held) != 1 else ''} from overnight",
                  "\n".join(lines) + "\nDetails + log in Odds Fam", None)
    except Exception:
        ok = False
    if ok:
        for r in held:
            r["pushed"] = "digest"
    return len(held) if ok else 0


# ------------------------------------------------------------------ grading
def _source_rows(base: Path | None) -> dict:
    from research_lab import prop_board, sharp_plays
    out = {f"prop:{r['play_id']}": r for r in prop_board.load(base)}
    out.update({f"line:{r['play_id']}": r for r in sharp_plays.load(base)})
    return out


def _leg(market: dict | None) -> str | None:
    from research_lab.settle import leg_outcome
    return leg_outcome(market) if market else None


def grade(rows: list[dict], now: datetime, base: Path | None, markets: dict, box=None) -> int:
    src = _source_rows(base)
    n = 0
    for r in rows:
        if r["status"] != "open":
            continue
        if r["kind"] == "Pick'em gap":
            start = parse_dt(r["game_start"])
            if box is None or not start or now - start < SETTLE_AFTER:
                continue
            try:
                actual = box(r)
            except Exception:
                actual = None
            if actual is None:
                if now - start > timedelta(days=3):
                    r.update(status="ungraded", settled_at=iso(now))
                continue
            r["actual"] = actual
            line = float(r["line"])
            res = "void" if actual == line else ("won" if (actual > line) == (r["side"] == "over") else "lost")
        elif r["kind"] == "Price gap":
            out = _leg(markets.get(r.get("settle_ticker") or ""))
            if out is None:
                continue
            res = "void" if out == "void" else ("won" if (out == "yes") == (r["settle_side"] == "YES") else "lost")
        else:
            s = src.get(r["source_id"])
            if not s:
                continue
            if s.get("clv") not in ("", None):
                r["clv"] = s["clv"]
            res = s.get("status")
            if res not in {"won", "lost", "void", "ungraded"}:
                continue
        dec = float(r.get("best_decimal") or r["bet_at_decimal"])
        r["status"] = res
        r["pl"] = round(UNIT * (dec - 1), 2) if res == "won" else (-UNIT if res == "lost" else 0.0)
        r["settled_at"] = iso(now)
        n += 1
    return n


def open_tickers(base: Path | None = None) -> list[str]:
    return [r["settle_ticker"] for r in load(base) if r["status"] == "open" and r.get("settle_ticker")]


def _regrade(play: dict, line: float, base: Path | None, scores) -> str | None:
    """His bet at a different line than the play (e.g. the old number): grade it at his line."""
    if play["kind"] in {"Prop steam", "Pick'em gap"}:
        src = play if play["kind"] == "Pick'em gap" else (_source_rows(base).get(play["source_id"]) or {})
        actual = src.get("actual")
        if actual in ("", None):
            return None
        a = float(actual)
        if a == line:
            return "void"
        over = " Over " in f" {play['pick']} "
        return "won" if (a > line) == over else "lost"
    if play["kind"] == "Line steam" and scores is not None:
        from research_lab import sharp_plays
        src = _source_rows(base).get(play["source_id"])
        if not src:
            return None
        return scores(sharp_plays.espn_row({**src, "line": line if src["market"] == "total"
                                            else (line if src["side"] == "home" else -line)}))
    return None


def settle_bets(rows: list[dict], now: datetime, base: Path | None, scores=None) -> int:
    bets, results = load_bets(base), load_results(base)
    plays = {r["of_id"]: r for r in rows}
    n = 0
    for b in bets:
        bid = b.get("bet_id")
        if not bid or bid in results:
            continue
        play = plays.get(b.get("of_id") or "")
        if not play:
            continue
        res = None
        lt = b.get("line_taken")
        if lt not in ("", None) and play.get("line") not in ("", None) and float(lt) != float(play["line"]):
            res = _regrade(play, float(lt), base, scores)
        elif play["status"] in {"won", "lost", "void"}:
            res = play["status"]
        if res not in {"won", "lost", "void"}:
            continue
        stake, dec = float(b.get("stake") or 0), float(b.get("decimal") or 0)
        pl = round(stake * (dec - 1), 2) if res == "won" else (-stake if res == "lost" else 0.0)
        results[bid] = {"status": res, "pl": pl, "settled_at": iso(now)}
        n += 1
    if n:
        _atomic_write_text(_dir(base) / "my_results.json", json.dumps(results, indent=1, sort_keys=True))
    return n


# ------------------------------------------------------------------ summaries
def summary(rows: list[dict], kind: str | None = None) -> dict:
    rs = [r for r in rows if kind in (None, r["kind"])]
    done = [r for r in rs if r["status"] in {"won", "lost"}]
    pl = sum(float(r["pl"] or 0) for r in done)
    clvs = [float(r["clv"]) for r in rs if r.get("clv") not in ("", None)]
    return {"plays": len(rs), "won": sum(r["status"] == "won" for r in done),
            "lost": sum(r["status"] == "lost" for r in done), "open": sum(r["status"] == "open" for r in rs),
            "pl": round(pl, 2), "roi": pl / (UNIT * len(done)) if done else None,
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None}


def my_summary(bets: list[dict], results: dict) -> dict:
    won = lost = 0
    pl = risked = open_stake = 0.0
    for b in bets:
        res = results.get(b.get("bet_id") or "")
        stake = float(b.get("stake") or 0)
        if not res:
            open_stake += stake
            continue
        if res["status"] in {"won", "lost"}:
            won += res["status"] == "won"
            lost += res["status"] == "lost"
            pl += float(res["pl"])
            risked += stake
    return {"won": won, "lost": lost, "pl": round(pl, 2), "roi": pl / risked if risked else None,
            "open": len(bets) - won - lost - sum(1 for b in bets if (results.get(b.get("bet_id") or "") or {}).get("status") == "void"),
            "open_stake": round(open_stake, 2)}


# ================================================================== v2: Target Price Board (Nov 1, 2026)
# Hunter's edge is a slow book (Courtside, Betr, Rebet, Onyx) still showing a price that Pinnacle
# and the exchanges have moved past. We can't read those books for free, so the board gives the
# number to beat: fair price, "bet at or better", and, for game lines, what Kalshi/Polymarket's
# other side says (and the price where it becomes a straight arb). Every bet he logs from here is
# graded and scored by CLV: his price vs the fair price right before the game.
def pair_key(key: str) -> str:
    return key.rsplit(":", 1)[0]


def exchange_check(pick, opp) -> dict:
    """What the exchanges say about this side. Offer decimals are after fees, so this is conservative.

    floor: this side's win chance is at least 1 - (cost of the other side on an exchange);
    arb_at: a soft-book price at or above this locks a profit if you buy the other side there.
    """
    mine = best_offer(pick)
    other = best_offer(opp) if opp is not None else None
    floor = (1 - 1 / other.decimal) if other else None
    mid = ((1 / mine.decimal + floor) / 2) if (mine and floor) else None
    return {"floor": floor, "mid": mid, "venue": other.venue if other else "",
            "arb_at": (1 / floor) if floor and floor > 0.05 else None}


def blended(pin: float, mid: float | None) -> float:
    """Pinnacle counts twice, the exchange midpoint once (only when both exchange sides are quoted)."""
    if mid is None or not (0.02 < mid < 0.98) or abs(mid - pin) > 0.08:   # big gaps = bad match, trust Pinnacle
        return pin
    return (2 * pin + mid) / 3


def _game_rows(picks, now: datetime) -> list[dict]:
    by_pair: dict[str, list] = {}
    for p in picks or []:
        by_pair.setdefault(pair_key(p.key), []).append(p)
    out = []
    for p in picks or []:
        if not p.starts or p.starts <= now or not p.fair:
            continue
        opp = next((q for q in by_pair.get(pair_key(p.key), []) if q is not p), None)
        x = exchange_check(p, opp)
        fair = blended(p.fair, x["mid"])
        o = best_offer(p)
        out.append({"id": f"game:{p.key}", "kind": "game", "league": p.league, "game": p.game,
                    "starts": iso(p.starts), "pick": p.label, "market": p.market, "line": "",
                    "fair_prob": round(fair, 4), "fair_odds": american(1 / fair),
                    "bet_at": american(1.02 / fair), "bet_at_decimal": round(1.02 / fair, 4),
                    "arb_at": american(x["arb_at"]) if x["arb_at"] else "",
                    "exch": f"{x['venue']} other side → this side ≥ {american(1 / x['floor'])}" if x["floor"] else "",
                    "best_found": f"{o.venue} {american(o.decimal)}" if o else "",
                    "limit": "", "moved": "", "close_key": f"game:{p.key}",
                    "settle_ticker": p.settle_ticker, "settle_side": p.settle_side})
    return out


def _prop_rows(prop_board_rows, now: datetime) -> list[dict]:
    out = []
    for r in prop_board_rows or []:
        start = parse_dt(r.get("starts"))
        if not start or start <= now:
            continue
        line, po = float(r["line"]), float(r["p_over"])
        moved = ""
        if float(r.get("open_line", line)) != line:
            moved = f"{float(r['open_line']):g} → {line:g}"
        elif abs(po - float(r.get("open_p_over", po))) >= 0.02:
            d = po - float(r["open_p_over"])
            moved = f"{'Over' if d > 0 else 'Under'} +{abs(d) * 100:.0f}%"
        for side, prob in (("over", po), ("under", 1 - po)):
            out.append({"id": f"prop:{r['key']}:{line:g}:{side}", "kind": "prop", "league": r["league"],
                        "game": r["game"], "starts": r["starts"],
                        "pick": f"{r['player']} {side.title()} {line:g} {r['stat']}", "market": "prop",
                        "line": line, "player": r["player"], "stat": r["stat"], "side": side,
                        "fair_prob": round(prob, 4), "fair_odds": american(1 / prob),
                        "bet_at": american(1.02 / prob), "bet_at_decimal": round(1.02 / prob, 4),
                        "arb_at": "", "exch": "", "best_found": "", "limit": int(r.get("limit") or 0),
                        "moved": moved, "close_key": f"prop:{r['key']}:{line:g}:{side}",
                        "settle_ticker": "", "settle_side": ""})
    return out


def target_board(prop_board_rows, picks, now: datetime) -> list[dict]:
    rows = _prop_rows(prop_board_rows, now) + _game_rows(picks, now)
    rows.sort(key=lambda r: (r["starts"], r["game"], r["kind"] != "prop", r["pick"]))
    return rows


BOARD_KEYS = ("id", "kind", "league", "game", "starts", "pick", "market", "line", "player", "stat", "side",
              "fair_prob", "fair_odds", "bet_at", "bet_at_decimal", "arb_at", "exch", "best_found", "limit",
              "moved", "close_key", "settle_ticker", "settle_side")


def load_board(base: Path | None = None) -> dict:
    p = _dir(base) / "board.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}
    rows = []
    for r in data.get("rows") or []:
        full = {k: r.get(k, "") for k in BOARD_KEYS}
        full["close_key"] = r.get("close_key") or r.get("id", "")
        full["fair_odds"] = r.get("fair_odds") or (american(1 / float(r["fair_prob"])) if r.get("fair_prob") else "")
        rows.append(full)
    return {**data, "rows": rows}


def load_closes(base: Path | None = None) -> dict:
    p = _dir(base) / "closes.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def record_closes(rows: list[dict], now: datetime, base: Path | None = None) -> int:
    """Keep the latest pre-game fair price for every board row: the closing line for CLV."""
    closes = load_closes(base)
    for r in rows:
        start = parse_dt(r["starts"])
        if start and start > now:
            closes[r["close_key"]] = {"p": r["fair_prob"], "at": iso(now), "starts": r["starts"]}
    closes = {k: v for k, v in closes.items() if now - (parse_dt(v.get("starts")) or now) <= CLOSE_TTL}
    _atomic_write_text(_dir(base) / "closes.json", json.dumps(closes, separators=(",", ":")))
    return len(closes)


def update_board(now: datetime, base: Path | None = None, *, prop_board_rows=(), picks=()) -> dict:
    rows = target_board(prop_board_rows, picks, now)
    if rows:
        slim = [{k: v for k, v in r.items() if v not in ("", None) and k not in {"close_key", "fair_odds"}}
                for r in rows]                                  # restored by load_board (keeps the commit small)
        _atomic_write_text(_dir(base) / "board.json", json.dumps({"at": iso(now), "rows": slim}, separators=(",", ":")))
        record_closes(rows, now, base)
    return {"rows": len(rows), "props": sum(r["kind"] == "prop" for r in rows),
            "arbs_possible": sum(bool(r["arb_at"]) for r in rows)}


def bet_from_board(row: dict, *, source: str, book: str, odds: str, stake: float, line_taken: str = "") -> dict:
    dec = american_to_decimal(odds)
    if dec is None:
        raise ValueError("Enter odds like -110 or +135.")
    if source not in SOURCES:
        raise ValueError(f"Source must be one of {SOURCES}")
    lt = str(line_taken or "").strip()
    return {"of_id": "", "pick": row["pick"], "book": book, "odds": str(odds).strip().replace("−", "-"),
            "decimal": round(dec, 4), "stake": round(float(stake), 2), "line_taken": lt,
            "source": source, "board_id": row["id"],
            "close_key": row["close_key"] if (not lt or row.get("line") in ("", None) or float(lt) == float(row["line"])) else "",
            "fair_prob": row["fair_prob"], "kind": row["kind"], "league": row["league"], "game": row["game"],
            "game_start": row["starts"], "market": row["market"], "line": row.get("line", ""),
            "player": row.get("player", ""), "stat": row.get("stat", ""), "side": row.get("side", ""),
            "settle_ticker": row.get("settle_ticker", ""), "settle_side": row.get("settle_side", "")}


def _play_close_key(play: dict) -> str:
    sid = play.get("source_id") or ""
    if sid.startswith("gap:"):
        return f"game:{sid[4:]}"
    if play.get("kind") == "Prop steam" and play.get("line") not in ("", None):
        side = "over" if " Over " in f" {play['pick']} " else "under"
        return f"prop:{sid.split(':', 1)[-1]}:{float(play['line']):g}:{side}"
    return ""


def bet_clv(bet: dict, closes: dict, now: datetime, plays: dict | None = None) -> dict | None:
    """CLV = what his price was worth at the close: close fair prob x his decimal odds - 1."""
    key = bet.get("close_key") or ""
    start = parse_dt(bet.get("game_start"))
    if not key and plays is not None:
        play = plays.get(bet.get("of_id") or "")
        if play and (not bet.get("line_taken") or play.get("line") in ("", None)
                     or float(bet["line_taken"]) == float(play["line"])):
            key, start = _play_close_key(play), parse_dt(play.get("game_start"))
    c = closes.get(key) if key else None
    if not c or not start or start > now:
        return None
    try:
        return {"close_p": c["p"], "clv": round(float(c["p"]) * float(bet["decimal"]) - 1, 4)}
    except (TypeError, ValueError, KeyError):
        return None


def load_clv(base: Path | None = None) -> dict:
    p = _dir(base) / "my_clv.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def score_clv(now: datetime, base: Path | None = None) -> int:
    bets, closes, have = load_bets(base), load_closes(base), load_clv(base)
    plays = {r["of_id"]: r for r in load(base)}
    n = 0
    for b in bets:
        bid = b.get("bet_id")
        if not bid or bid in have:
            continue
        c = bet_clv(b, closes, now, plays)
        if c:
            have[bid] = c
            n += 1
    if n:
        _atomic_write_text(_dir(base) / "my_clv.json", json.dumps(have, indent=1, sort_keys=True))
    return n


def settle_board_bets(now: datetime, base: Path | None = None, *, markets=None, box=None) -> int:
    """Grade bets logged from the board (no Odds Fam play behind them)."""
    bets, results = load_bets(base), load_results(base)
    n = 0
    for b in bets:
        bid = b.get("bet_id")
        if not bid or bid in results or b.get("of_id") or not b.get("board_id"):
            continue
        start = parse_dt(b.get("game_start"))
        if not start or now - start < SETTLE_AFTER:
            continue
        res = None
        if b.get("kind") == "prop" and box is not None:
            line = float(b.get("line_taken") or b["line"])
            try:
                actual = box({"league": b["league"], "game": b["game"], "game_start": b["game_start"],
                              "player": b["player"], "stat": b["stat"]})
            except Exception:
                actual = None
            if actual is not None:
                res = "void" if actual == line else ("won" if (actual > line) == (b["side"] == "over") else "lost")
        elif b.get("kind") == "game" and not b.get("line_taken"):
            out = _leg((markets or {}).get(b.get("settle_ticker") or ""))
            if out is not None:
                res = "void" if out == "void" else ("won" if (out == "yes") == (b.get("settle_side") == "YES") else "lost")
        if res is None:
            if now - start > timedelta(days=3):
                results[bid] = {"status": "ungraded", "pl": 0.0, "settled_at": iso(now)}
                n += 1
            continue
        stake, dec = float(b.get("stake") or 0), float(b.get("decimal") or 0)
        pl = round(stake * (dec - 1), 2) if res == "won" else (-stake if res == "lost" else 0.0)
        results[bid] = {"status": res, "pl": pl, "settled_at": iso(now)}
        n += 1
    if n:
        _atomic_write_text(_dir(base) / "my_results.json", json.dumps(results, indent=1, sort_keys=True))
    return n


def bet_tickers(base: Path | None = None) -> list[str]:
    results = load_results(base)
    return [b["settle_ticker"] for b in load_bets(base)
            if b.get("settle_ticker") and b.get("bet_id") not in results]


def clv_report(bets: list[dict], results: dict, clvs: dict, by: str = "source") -> list[dict]:
    """One row per source (or book): bets, record, P/L, ROI, average CLV, % that beat the close."""
    groups: dict[str, list] = {}
    for b in bets:
        groups.setdefault(b.get(by) or ("Odds Fam" if by == "source" else "Other"), []).append(b)
    out = []
    for name, bs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        res = [results.get(b.get("bet_id") or "") or {} for b in bs]
        done = [(b, r) for b, r in zip(bs, res) if r.get("status") in {"won", "lost"}]
        pl = sum(float(r["pl"]) for _, r in done)
        risked = sum(float(b.get("stake") or 0) for b, _ in done)
        cs = [clvs[b["bet_id"]]["clv"] for b in bs if b.get("bet_id") in clvs]
        out.append({by: name, "bets": len(bs), "won": sum(r["status"] == "won" for _, r in done),
                    "lost": sum(r["status"] == "lost" for _, r in done), "pl": round(pl, 2),
                    "roi": pl / risked if risked else None, "clv_n": len(cs),
                    "clv_avg": sum(cs) / len(cs) if cs else None,
                    "beat_close": sum(c > 0 for c in cs) / len(cs) if cs else None})
    return out
