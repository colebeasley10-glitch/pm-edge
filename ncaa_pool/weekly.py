"""Week-by-week NCAA pool cards for the SharpIQ app (Nov 1, 2026).

What the "NCAA Pools" page shows, and where it comes from:

  * This week's card for Clay's pool (12 picks, 3 double-point keys) and Chappy's (6 picks, 1 key),
    built by the CPM v1 model (ncaa_pool/model.py) from that pool's lines + the latest market line the
    NCAA grader logs hourly from ESPN.
  * Past weeks: every saved card, graded from the final scores, plus the season so far
    (CPM v1 replayed on Clay's real weeks 1-9 slates, and the v51 picks actually made where we have them).

A pool's lines live in ncaa_pool/data/slates/<pool>_<season>_w<week>.csv (away,home,home_spread[,total]).
Cole can paste them on the page (saved to the repo through the GitHub API) or send a screenshot to Claude.

snapshot() runs hourly from the NCAA grader workflow. Until the lock (Saturday noon ET, or a game's own
kickoff if earlier) it rebuilds the card with the newest market read and writes it to
ncaa_pool/data/cards/<pool>_<season>_w<week>.csv; once a pick's game has kicked off that pick is frozen,
and after the lock the whole card is frozen. It also grades each pick when its final score is in.
Paper only: nothing here bets.
"""
from __future__ import annotations

import csv
import json
import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ncaa_pool.model import Game, build_card
from ncaa_pool.run_card import POOLS, _aliases, _f, _norm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "ncaa_pool" / "data"
SLATES = DATA / "slates"
CARDS = DATA / "cards"
MINE = DATA / "my_picks"                # Nov 1, 2026: "Lock my picks" -- exactly what Cole submitted
GRADER = ROOT / "ncaa_grader" / "data" / "picks.csv"
ET = ZoneInfo("America/New_York")
STAKE_KEY, STAKE_OTHER = 10.0, 5.0     # Cole's staking: $10 on ranks 1-3, $5 on ranks 4-12
WIN_AT_110 = 100 / 110
LOCK_HOUR_ET = 12                       # picks lock Saturday noon ET
CARD_FIELDS = ["pool", "season", "week", "rank", "key", "pick", "kind", "side", "away", "home", "line",
               "prob", "value_pts", "market", "src", "kickoff", "made_at", "frozen", "home_pts", "away_pts", "result"]


def _csv(p: Path) -> list[dict]:
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _dt(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def stake(rank: int) -> float:
    return STAKE_KEY if rank <= 3 else STAKE_OTHER


def slate_path(pool: str, season: int, week: int) -> Path:
    return SLATES / f"{pool}_{season}_w{week}.csv"


def card_path(pool: str, season: int, week: int) -> Path:
    return CARDS / f"{pool}_{season}_w{week}.csv"


# ------------------------------------------------------------------ pasting a slate
_LINE = re.compile(r"^\s*(?P<away>.+?)\s+(?:at|@|vs\.?)\s+(?P<home>.+?)\s+(?P<spread>[+-]?\d+(?:\.\d+)?|pk|PK|Pk)"
                   r"(?:\s*(?:,|\s)\s*(?:o/u|ou|total|t)?\s*(?P<total>\d+(?:\.\d+)?))?\s*$")


_TB = re.compile(r"\s*[,(]?\s*(?:tiebreaker|tie breaker|tb)\s*\)?\s*$", re.I)


def parse_slate(text: str) -> tuple[list[dict], list[str]]:
    """Lines like 'Ohio at Akron -3.5' or 'Ohio @ Akron +7, 52.5' (spread = HOME team's line),
    or CSV 'away,home,home_spread[,total]'. End a line with 'tiebreaker' (or 'TB') to mark the
    tiebreaker game (Nov 5, 2026). Returns (rows, lines it couldn't read)."""
    rows, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("away,"):
            continue
        tb = bool(_TB.search(line))
        line = _TB.sub("", line).strip() if tb else line
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and _f(parts[2]) is not None and not _LINE.match(line):
            rows.append({"away": parts[0], "home": parts[1], "home_spread": _f(parts[2]),
                         "total": _f(parts[3]) if len(parts) > 3 else None, "tiebreaker": "Y" if tb else ""})
            continue
        m = _LINE.match(line)
        if not m:
            bad.append(line)
            continue
        rows.append({"away": m["away"].strip(), "home": m["home"].strip(), "home_spread": _f(m["spread"]),
                     "total": _f(m["total"]), "tiebreaker": "Y" if tb else ""})
    return rows, bad


def render_slate(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["away", "home", "home_spread", "total", "exclude", "tiebreaker"], extrasaction="ignore",
                       lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "total": "" if r.get("total") is None else r["total"]})
    return buf.getvalue()


# ------------------------------------------------------------------ grader feed (schedule, market, finals)
@dataclass
class GameInfo:
    kickoff: datetime | None = None
    home_pts: float | None = None
    away_pts: float | None = None
    spread: float | None = None     # latest market HOME spread
    total: float | None = None      # latest market total
    first_spread: float | None = None
    first_total: float | None = None
    pinn_spread: float | None = None    # Pinnacle main HOME spread (Nov 4, 2026)
    pinn_total: float | None = None
    pinn_at: datetime | None = None     # when Pinnacle generated that price


# ------------------------------------------------------------------ Pinnacle main lines (Nov 4, 2026)
# The research lab saves Pinnacle's NCAA main line every time it fetches football (no extra requests).
# Pinnacle is the sharpest book, so its line is the model's market when it's fresh; ESPN fills in otherwise.
PINN = ROOT / "research_lab" / "data" / "pinnacle_ncaaf_lines.json"
PINN_MAX_AGE = timedelta(hours=6)
MARKETS = ("pinnacle", "espn")


def _pinn_aliases() -> dict:
    from pmedge.matcher import load_aliases
    from pmedge.utils import normalize_text
    out = load_aliases(str(ROOT / "data" / "team_aliases.csv"))
    p = ROOT / "research_lab" / "pinnacle_aliases.csv"
    for r in _csv(p):
        a, c = normalize_text(r.get("alias", "")), normalize_text(r.get("canonical", ""))
        if a and c:
            out[a] = c
    return out


def attach_pinnacle(feed: dict, path: Path | None = None, aliases: dict | None = None) -> int:
    """Copy Pinnacle's main lines onto the grader feed's games (both teams must match, kickoff within 6h).
    A game that could match two Pinnacle events is left alone. Returns games matched."""
    import json
    path = path or PINN
    try:
        pinn = list(json.loads(path.read_text()).get("games", {}).values()) if path.exists() else []
    except ValueError:
        pinn = []
    if not pinn:
        return 0
    from pmedge.matcher import shadow_team_match
    aliases = aliases if aliases is not None else _pinn_aliases()
    n = 0
    for games in feed.values():
        for (away, home), g in games.items():
            hits = []
            for p in pinn:
                ks = _dt(p.get("starts"))
                if g.kickoff and ks and abs((ks - g.kickoff).total_seconds()) > 6 * 3600:
                    continue
                for flip, (pa, ph) in ((False, (p["away"], p["home"])), (True, (p["home"], p["away"]))):
                    sa, _ = shadow_team_match(away, pa, aliases)
                    sh, _ = shadow_team_match(home, ph, aliases)
                    if sa >= 0.90 and sh >= 0.90:
                        hits.append((sa + sh, flip, p))
            if not hits:
                continue
            hits.sort(key=lambda h: -h[0])
            if len(hits) > 1 and hits[1][0] == hits[0][0] and hits[1][2] is not hits[0][2]:
                continue                     # ambiguous (e.g. two Miamis): don't guess
            _, flip, p = hits[0]
            sp = _f(p.get("spread"))
            g.pinn_spread = (-sp if flip else sp) if sp is not None else None
            g.pinn_total = _f(p.get("total"))
            g.pinn_at = _dt(p.get("at"))
            n += 1
    return n


def market_line(g: "GameInfo", market: str, now: datetime) -> tuple[float | None, float | None, str]:
    """(home spread, total, source) the model should use. Pinnacle when fresh, else ESPN."""
    if market == "pinnacle" and g.pinn_spread is not None and g.pinn_at and now - g.pinn_at <= PINN_MAX_AGE:
        return g.pinn_spread, g.pinn_total if g.pinn_total is not None else g.total, "pinnacle"
    return g.spread, g.total, "espn"


def game_feed(path: Path = GRADER, aliases: dict | None = None) -> dict[tuple, dict]:
    """(season, week) -> {(away, home): GameInfo} from the NCAA grader's log (newest read wins)."""
    aliases = aliases if aliases is not None else _aliases()
    rows = sorted(_csv(path), key=lambda r: r.get("logged_at") or "")
    out: dict[tuple, dict] = {}
    for r in rows:
        if r.get("source", "live") != "live":
            continue
        try:
            sw = (int(r["season"]), int(r["week"]))
        except (KeyError, ValueError):
            continue
        key = (_norm(r["away"], aliases), _norm(r["home"], aliases))
        g = out.setdefault(sw, {}).setdefault(key, GameInfo())
        g.kickoff = _dt(r.get("kickoff")) or g.kickoff
        if r.get("home_pts") not in (None, "") and r.get("away_pts") not in (None, ""):
            g.home_pts, g.away_pts = _f(r["home_pts"]), _f(r["away_pts"])
        line = _f(r.get("close_line")) if r.get("close_line") not in (None, "") else _f(r.get("line"))
        first = _f(r.get("line"))
        if r.get("market") == "SPREAD" and line is not None:
            home = r.get("pick_team") == r.get("home")
            g.spread = line if home else -line
            if g.first_spread is None and first is not None:
                g.first_spread = first if home else -first
        elif r.get("market") == "TOTAL" and line is not None:
            g.total = line
            if g.first_total is None and first is not None:
                g.first_total = first
    return out


def current_week(feed: dict, now: datetime) -> tuple[int, int] | None:
    """The week whose games are next (or still being played)."""
    best = None
    for sw, games in feed.items():
        ks = [g.kickoff for g in games.values() if g.kickoff]
        if not ks or max(ks) + timedelta(hours=5) < now:
            continue
        if best is None or sw < best:
            best = sw
    return best


def lock_time(games: dict) -> datetime | None:
    """Saturday noon ET of the week (the Saturday on or after the earliest kickoff)."""
    ks = sorted(g.kickoff for g in games.values() if g.kickoff)
    if not ks:
        return None
    d = ks[0].astimezone(ET).date()
    sat = d + timedelta(days=(5 - d.weekday()) % 7)
    return datetime(sat.year, sat.month, sat.day, LOCK_HOUR_ET, tzinfo=ET).astimezone(timezone.utc)


# ------------------------------------------------------------------ building a card
def _lookup(games: dict, away: str, home: str) -> tuple[GameInfo | None, bool]:
    if (away, home) in games:
        return games[(away, home)], False
    if (home, away) in games:
        return games[(home, away)], True
    return None, False


def build(pool: str, slate: list[dict], games: dict, *, now: datetime, preview: bool = False,
          pinned: list[dict] | None = None, aliases: dict | None = None, market: str = "pinnacle",
          fade: bool = False) -> dict:
    """Card rows for one pool. `slate` rows: away, home, home_spread, total, exclude.
    preview=True: no pool lines yet, so the grader's first logged line stands in for the pool line.
    pinned: picks from the last snapshot whose games have kicked off (kept as they were)."""
    aliases = aliases if aliases is not None else _aliases()
    n, k = POOLS[pool]
    pinned = pinned or []
    pinned_games = {(p["away"], p["home"]) for p in pinned}
    cand, missing, by_game = [], [], {}
    for r in slate:
        away, home = _norm(r["away"], aliases), _norm(r["home"], aliases)
        if r.get("exclude") or (away, home) in pinned_games:
            continue
        info, flipped = _lookup(games, away, home)
        spread, total, src = market_line(info, market, now) if info else (None, None, "")
        if spread is None:
            missing.append(f"{away} at {home}")
            continue
        if info.kickoff and info.kickoff <= now:
            continue                      # already started: can't be picked now
        mspread = -spread if flipped else spread
        pspread = _f(r.get("home_spread"))
        if pspread is None:
            continue
        g = Game(away, home, pspread, mspread, _f(r.get("total")), total)
        cand.append(g)
        by_game[(away, home)] = (info, src)
    n_left = max(0, n - len(pinned))
    k_left = max(0, k - sum(p["key"] == "Y" for p in pinned))
    card = build_card(cand, n_left, k_left, allow_totals=False, fade=fade)
    rows = [dict(p) for p in pinned]
    for i, o in enumerate(card["picks"]):
        g = o.game
        info, src = by_game.get((g.away, g.home), (None, ""))
        line = g.pool_spread if o.side == "home" else -g.pool_spread
        rows.append({"pool": pool, "rank": 0, "key": "Y" if i < k_left else "N", "pick": o.label, "kind": o.kind,
                     "side": o.side, "away": g.away, "home": g.home, "line": line, "prob": round(o.prob, 4),
                     "value_pts": round(o.gap, 2), "market": o.extra["market"], "src": src,
                     "kickoff": info.kickoff.isoformat().replace("+00:00", "Z") if info and info.kickoff else "",
                     "made_at": now.isoformat(timespec="minutes").replace("+00:00", "Z"), "frozen": "",
                     "home_pts": "", "away_pts": "", "result": ""})
    # keys first, then by chance to score
    rows.sort(key=lambda r: (r["key"] != "Y", -float(r["prob"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "missing": missing, "bench": [(o.label, o.prob) for o in card["bench"]],
            "preview": preview}


def preview_slate(games: dict) -> list[dict]:
    """No pool lines yet: use the grader's first logged line (what the market said early in the week)."""
    return [{"away": a, "home": h, "home_spread": g.first_spread, "total": g.first_total}
            for (a, h), g in games.items() if g.first_spread is not None]


# ------------------------------------------------------------------ grading
def grade(row: dict, home_pts: float | None, away_pts: float | None) -> str:
    if home_pts is None or away_pts is None:
        return ""
    if row.get("kind") != "spread":
        return ""
    line = float(row["line"])
    margin = (home_pts - away_pts) if row["side"] == "home" else (away_pts - home_pts)
    x = margin + line
    return "W" if x > 0 else "L" if x < 0 else "P"


def grade_rows(rows: list[dict], games: dict) -> None:
    for r in rows:
        if r.get("result"):
            continue
        info, flipped = _lookup(games, r["away"], r["home"])
        if not info or info.home_pts is None:
            continue
        hp, ap = (info.away_pts, info.home_pts) if flipped else (info.home_pts, info.away_pts)
        res = grade(r, hp, ap)
        if res:
            r["home_pts"], r["away_pts"], r["result"] = hp, ap, res


def points(rows: list[dict]) -> int:
    return sum((2 if r.get("key") in ("Y", "True", True) else 1) for r in rows if r.get("result") == "W")


def money(rows: list[dict]) -> float:
    pl = 0.0
    for r in rows:
        s = stake(int(r["rank"]))
        if r.get("result") == "W":
            pl += s * WIN_AT_110
        elif r.get("result") == "L":
            pl -= s
    return round(pl, 2)


def record(rows: list[dict]) -> tuple[int, int, int]:
    res = [r.get("result") for r in rows]
    return res.count("W"), res.count("L"), res.count("P")


# ------------------------------------------------------------------ files
def write_card(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CARD_FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    if not path.exists() or path.read_text(encoding="utf-8") != buf.getvalue():
        path.write_text(buf.getvalue(), encoding="utf-8")


def slates() -> list[tuple[str, int, int, Path]]:
    out = []
    for p in sorted(SLATES.glob("*.csv")):
        m = re.match(r"(clay|chappy)_(\d{4})_w(\d+)\.csv$", p.name)
        if m:
            out.append((m[1], int(m[2]), int(m[3]), p))
    return out


# ------------------------------------------------------------------ shadow variants (Nov 4, 2026)
# Cole: "track both in the background to see what would've worked better." Every week each pool gets four
# paper cards, built the same way and graded the same way, differing only in the market line the model reads
# and when the card is frozen:
#   espn_fri  -- ESPN line, frozen Friday 8pm ET (the old model at Cole's usual Friday-evening lock)
#   pinn_fri  -- Pinnacle line, frozen Friday 8pm ET
#   espn_sat  -- ESPN line, frozen at the Saturday noon lock (the old main card)
#   pinn_sat  -- Pinnacle line, frozen at the Saturday noon lock (the new main card)
VARIANTS = DATA / "variants"
#   pinn_sat_fade -- (Nov 5, 2026) the main card with its coin-flip filler swapped to the less-public side
#                    (model.FADE_*). Tests whether that wins more weeks outright; never touches the real card.
VARIANT_SPECS = {"espn_fri": ("espn", "fri"), "pinn_fri": ("pinnacle", "fri"),
                 "espn_sat": ("espn", "sat"), "pinn_sat": ("pinnacle", "sat"),
                 "pinn_sat_fade": ("pinnacle", "sat", "fade")}
VARIANT_NAMES = {"espn_fri": "ESPN · Fri 8pm", "pinn_fri": "Pinnacle · Fri 8pm",
                 "espn_sat": "ESPN · Sat lock", "pinn_sat": "Pinnacle · Sat lock",
                 "pinn_sat_fade": "Crowd fade · Sat lock"}
FRIDAY_LOCK_HOUR_ET = 20


def variant_path(pool: str, season: int, week: int, variant: str) -> Path:
    return VARIANTS / f"{pool}_{season}_w{week}__{variant}.csv"


def friday_lock(lock: datetime | None) -> datetime | None:
    if lock is None:
        return None
    d = lock.astimezone(ET).date() - timedelta(days=1)
    return datetime(d.year, d.month, d.day, FRIDAY_LOCK_HOUR_ET, tzinfo=ET).astimezone(timezone.utc)


def _refresh(cp: Path, pool: str, season: int, week: int, slate: list[dict], games: dict, now: datetime,
             freeze_at: datetime | None, market: str, info: dict, fade: bool = False) -> list[dict]:
    """Rebuild one card until freeze_at, then freeze it; grade finals. Writes the card and returns its rows."""
    old = _csv(cp)
    if freeze_at and now >= freeze_at and old:
        rows = old
        for r in rows:
            if not r.get("frozen"):
                r["frozen"] = "Y"
                info["frozen"] += 1
    else:
        pinned = [r for r in old if (_dt(r.get("kickoff")) or now + timedelta(days=1)) <= now]
        for r in pinned:
            r["frozen"] = "Y"
        rows = build(pool, slate, games, now=now, pinned=pinned, market=market, fade=fade)["rows"]
        info["rebuilt"] += 1
    for r in rows:
        r["season"], r["week"] = season, week
    before = sum(bool(r.get("result")) for r in rows)
    grade_rows(rows, games)
    info["graded"] += sum(bool(r.get("result")) for r in rows) - before
    write_card(cp, rows)
    return rows


# ------------------------------------------------------------------ tiebreaker (Nov 5, 2026)
# Clay's pool: guess the combined score of one game; closest guess wins ties. The model's guess is the
# market total (Pinnacle when fresh, else ESPN, else the pool's own total) rounded to a whole number -- the
# market total is the median outcome, which is the guess that lands closest on average. Like the card, it
# refreshes until the Saturday noon lock, then freezes.
def tiebreaker_path(pool: str, season: int, week: int) -> Path:
    return CARDS / f"{pool}_{season}_w{week}__tiebreaker.json"


def tiebreaker(slate: list[dict], games: dict, now: datetime, market: str = "pinnacle",
               aliases: dict | None = None) -> dict | None:
    row = next((r for r in slate if str(r.get("tiebreaker") or "").strip()), None)
    if row is None:
        return None
    aliases = aliases if aliases is not None else _aliases()
    away, home = _norm(row["away"], aliases), _norm(row["home"], aliases)
    info, _flipped = _lookup(games, away, home)
    _s, total, src = market_line(info, market, now) if info else (None, None, "")
    if total is None:
        total, src = _f(row.get("total")), "pool"
    if total is None:
        return None
    return {"away": away, "home": home, "guess": int(math.floor(total + 0.5)), "total": total, "src": src,
            "pool_total": _f(row.get("total")), "made_at": now.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "frozen": ""}


def load_tiebreaker(pool: str, season: int, week: int) -> dict | None:
    p = tiebreaker_path(pool, season, week)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except ValueError:
        return None


def refresh_tiebreaker(pool: str, season: int, week: int, slate: list[dict], games: dict, now: datetime,
                       lock: datetime | None) -> dict | None:
    old = load_tiebreaker(pool, season, week)
    if old and lock and now >= lock:
        if not old.get("frozen"):
            old["frozen"] = "Y"
            _write_json(tiebreaker_path(pool, season, week), old)
        return old
    tb = tiebreaker(slate, games, now)
    if tb is None:
        return old
    if old and {k: old.get(k) for k in ("away", "home", "guess", "total", "src")} == \
            {k: tb.get(k) for k in ("away", "home", "guess", "total", "src")}:
        return old                                     # unchanged: don't churn the file
    _write_json(tiebreaker_path(pool, season, week), tb)
    return tb


def _write_json(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def snapshot(now: datetime | None = None, feed: dict | None = None) -> dict:
    """Hourly: refresh unlocked cards with the newest market, freeze locked picks, grade finals."""
    now = now or datetime.now(timezone.utc)
    if feed is None:
        feed = game_feed()
        try:
            attach_pinnacle(feed)
        except Exception as exc:          # ESPN still works on its own
            print(f"pinnacle lines unavailable: {type(exc).__name__}: {exc}")
    info = {"rebuilt": 0, "frozen": 0, "graded": 0}
    shadow = {"rebuilt": 0, "frozen": 0, "graded": 0}
    for pool, season, week, path in slates():
        games = feed.get((season, week), {})
        slate = _csv(path)
        lock = lock_time(games)
        _refresh(card_path(pool, season, week), pool, season, week, slate, games, now, lock, "pinnacle", info)
        refresh_tiebreaker(pool, season, week, slate, games, now, lock)
        for v, (market, when, *opt) in VARIANT_SPECS.items():
            freeze = lock if when == "sat" else friday_lock(lock)
            _refresh(variant_path(pool, season, week, v), pool, season, week, slate, games, now, freeze, market,
                     shadow, fade="fade" in opt)
    info["shadow"] = shadow
    for p in sorted(MINE.glob("*.csv")):                    # your locked entries: grade only
        m = re.match(r"(clay|chappy)_(\d{4})_w(\d+)\.csv$", p.name)
        if not m:
            continue
        rows = _csv(p)
        before = sum(bool(r.get("result")) for r in rows)
        grade_rows(rows, feed.get((int(m[2]), int(m[3])), {}))
        n = sum(bool(r.get("result")) for r in rows) - before
        if n:
            info["graded"] += n
            p.write_text(render_card(rows), encoding="utf-8")
    return info


# ------------------------------------------------------------------ season history for the page
def history(feed: dict | None = None) -> list[dict]:
    """One entry per (week, pool, source) with its graded picks, newest week first.

    Sources: saved cards (live, from this page/snapshot), the CPM v1 replay on Clay's real slates
    (weeks 1-9, made before those cards were saved), and the v51 picks you actually made."""
    out = []
    saved_weeks = set()
    for p in sorted(CARDS.glob("*.csv")):
        m = re.match(r"(clay|chappy)_(\d{4})_w(\d+)\.csv$", p.name)
        if not m:
            continue
        rows = _csv(p)
        if rows:
            out.append({"season": int(m[2]), "week": int(m[3]), "pool": m[1], "source": "CPM card", "rows": rows})
            saved_weeks.add((m[1], int(m[3])))
    bt: dict[int, list[dict]] = {}
    for r in _csv(DATA / "cpm_v1_real_slate_backtest.csv"):
        bt.setdefault(int(r["week"]), []).append(
            {"rank": r["rank"], "key": "Y" if r["key"] == "True" else "N", "pick": r["pick"], "prob": r["chance"],
             "value_pts": r["value"], "result": r["result"], "final": r.get("final", "")})
    for w, rows in bt.items():
        if ("clay", w) not in saved_weeks:
            out.append({"season": 2026, "week": w, "pool": "clay", "source": "CPM v1 replay", "rows": rows})
    v51: dict[int, list[dict]] = {}
    for r in _csv(DATA / "history_verified.csv"):
        key = r.get("key") if r.get("key") in ("Y", "N") else ("Y" if int(r["rank"]) <= 3 else "N")
        v51.setdefault(int(r["week"]), []).append(
            {"rank": r["rank"], "key": key, "pick": r["pick"], "prob": "", "value_pts": r.get("edge", ""),
             "result": r["result"], "final": r.get("final", "")})
    for w, rows in v51.items():
        out.append({"season": 2026, "week": w, "pool": "clay", "source": "Your picks", "rows": rows})
    for p in sorted(MINE.glob("*.csv")):                    # weeks you locked in the app
        m = re.match(r"(clay|chappy)_(\d{4})_w(\d+)\.csv$", p.name)
        rows = _csv(p) if m else []
        if rows and not (m[1] == "clay" and int(m[2]) == 2026 and int(m[3]) in v51):
            out.append({"season": int(m[2]), "week": int(m[3]), "pool": m[1], "source": "Your picks", "rows": rows})
    order = {"CPM card": 0, "Your picks": 1, "CPM v1 replay": 2}
    out.sort(key=lambda e: (-e["season"], -e["week"], e["pool"], order.get(e["source"], 9)))
    return out


# ------------------------------------------------------------------ shadow comparison for the page
def variant_results() -> dict:
    """{'weeks': [{pool, season, week, variants: {v: {points, record, done}}}], 'totals': {pool: {v: {...}}}}.
    'best' counts weeks where a variant scored the most points of all versions (ties count for each)."""
    weeks: dict[tuple, dict] = {}
    for p in sorted(VARIANTS.glob("*.csv")):
        m = re.match(r"(clay|chappy)_(\d{4})_w(\d+)__(\w+)\.csv$", p.name)
        if not m or m[4] not in VARIANT_SPECS:
            continue
        rows = _csv(p)
        if not rows:
            continue
        w, l, pu = record(rows)
        weeks.setdefault((m[1], int(m[2]), int(m[3])), {})[m[4]] = {
            "points": points(rows), "record": (w, l, pu), "money": money(rows),
            "done": all(r.get("result") for r in rows), "frozen": all(r.get("frozen") for r in rows),
            "picks": [r["pick"] for r in rows], "keys": [r["pick"] for r in rows if r.get("key") == "Y"]}
    out, totals = [], {}
    for (pool, season, week), vs in sorted(weeks.items(), key=lambda kv: (-kv[0][1], -kv[0][2], kv[0][0])):
        out.append({"pool": pool, "season": season, "week": week, "variants": vs})
        if not all(v["done"] for v in vs.values()):
            continue
        top = max(v["points"] for v in vs.values())
        for name, v in vs.items():
            t = totals.setdefault(pool, {}).setdefault(name, {"points": 0, "w": 0, "l": 0, "p": 0, "weeks": 0,
                                                              "best": 0, "money": 0.0})
            t["points"] += v["points"]
            t["w"], t["l"], t["p"] = t["w"] + v["record"][0], t["l"] + v["record"][1], t["p"] + v["record"][2]
            t["weeks"] += 1
            t["best"] += v["points"] == top
            t["money"] += v["money"]
    return {"weeks": out, "totals": totals}


# ------------------------------------------------------------------ saving a slate from the app
def _github_write(rel: str, text: str | None, message: str, session=None) -> None:
    """Create/replace (text) or delete (text=None) a repo file via GitHub's contents API. Streamlit
    Cloud's disk is wiped on restart, so the app writes through the GITHUB_TOKEN in its secrets."""
    import base64

    import requests

    import bet_log
    if not bet_log.token():
        raise bet_log.BetLogError("GITHUB_TOKEN is not set in the app's secrets")
    s = session or requests
    api = f"https://api.github.com/repos/{bet_log.REPO}/contents/{rel}"
    for _ in range(3):
        r = s.get(api, headers=bet_log._headers(), params={"ref": "main"}, timeout=15)
        if r.status_code not in (200, 404):
            raise bet_log.BetLogError(f"GitHub read failed (HTTP {r.status_code})")
        sha = r.json().get("sha") if r.status_code == 200 else None
        body = {"message": f"{message} [skip ci]", "branch": "main"}
        if sha:
            body["sha"] = sha
        if text is None:
            if not sha:
                return
            w = s.delete(api, headers=bet_log._headers(), json=body, timeout=20)
        else:
            body["content"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
            w = s.put(api, headers=bet_log._headers(), json=body, timeout=20)
        if w.status_code in (200, 201):
            return
        if w.status_code not in (409, 422):
            raise bet_log.BetLogError(f"GitHub write failed (HTTP {w.status_code})")
    raise bet_log.BetLogError("GitHub write kept conflicting; try again")


def save_slate(pool: str, season: int, week: int, rows: list[dict], session=None) -> None:
    _github_write(f"ncaa_pool/data/slates/{pool}_{season}_w{week}.csv", render_slate(rows),
                  f"NCAA {pool} pool lines, week {week}", session)


# ------------------------------------------------------------------ "Lock my picks"
def mine_path(pool: str, season: int, week: int) -> Path:
    return MINE / f"{pool}_{season}_w{week}.csv"


def render_card(rows: list[dict], locked_at: str | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CARD_FIELDS + ["locked_at"], extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "locked_at": locked_at or r.get("locked_at", "")})
    return buf.getvalue()


def lock_picks(pool: str, season: int, week: int, rows: list[dict], now: datetime | None = None,
               session=None) -> list[dict]:
    """Save the card exactly as submitted. It's never rebuilt again, only graded."""
    at = (now or datetime.now(timezone.utc)).isoformat(timespec="minutes").replace("+00:00", "Z")
    locked = [{**r, "season": season, "week": week, "frozen": "Y", "locked_at": at} for r in rows]
    _github_write(f"ncaa_pool/data/my_picks/{pool}_{season}_w{week}.csv", render_card(locked, at),
                  f"NCAA {pool} pool: my picks locked, week {week}", session)
    return locked


def unlock_picks(pool: str, season: int, week: int, session=None) -> None:
    _github_write(f"ncaa_pool/data/my_picks/{pool}_{season}_w{week}.csv", None,
                  f"NCAA {pool} pool: unlocked my picks, week {week}", session)


def diff(mine: list[dict], model: list[dict]) -> dict:
    a, b = {r["pick"] for r in mine}, {r["pick"] for r in model}
    ka = {r["pick"] for r in mine if r.get("key") == "Y"}
    kb = {r["pick"] for r in model if r.get("key") == "Y"}
    return {"dropped": sorted(a - b), "added": sorted(b - a), "keys_changed": ka != kb, "model_keys": sorted(kb)}


def expected_points(rows: list[dict]) -> tuple[float, int]:
    from ncaa_pool.model import poisson_binomial
    probs = [float(r.get("prob") or 0) for r in rows]
    weights = [2 if r.get("key") == "Y" else 1 for r in rows]
    dist = poisson_binomial(probs, weights)
    return sum(k * q for k, q in dist.items()), sum(weights)


if __name__ == "__main__":
    print(snapshot())
