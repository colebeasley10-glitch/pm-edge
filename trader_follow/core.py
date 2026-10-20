"""Trader Follow: copy-alerts for proven Polymarket sports bettors (PAPER + ALERTS ONLY).

Every run (about every 2 minutes, see .github/workflows/trader-follow.yml):

1. Read each followed wallet's newest Polymarket trades (free public data API).
2. Keep only BUYs on sports games placed BEFORE the game starts. Live in-game buys
   can't be copied in time, so they never count.
3. Add up each wallet's pre-game buys per market side. Once a side reaches
   MIN_USDC, that's a signal: log it, and if the price is inside the
   +150 to -200 band, send a Pushover alert with his price and a "don't chase
   past" price so you can copy it at about the same number.
4. Settle logged signals from Polymarket's final prices and track paper P/L as
   if you'd bet $10 at the alert-time price (what you could actually get).

Nothing here can place an order. No keys besides Pushover are used.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import requests

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_PATH = DATA_DIR / "state.json"
LEDGER_PATH = DATA_DIR / "signals.csv"

TRADES_URL = "https://data-api.polymarket.com/trades"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
PUSH_URL = "https://api.pushover.net/1/messages.json"


@dataclass(frozen=True)
class Trader:
    name: str
    wallet: str
    note: str = ""


# Picked Oct 20, 2026 from Polymarket's all-time sports leaderboard: big volume with a
# steady 2-4% return (not one lucky World Cup hit, not a ~1% market maker).
TRADERS: tuple[Trader, ...] = (
    Trader("BreakTheBank", "0xf0318c32136c2db7fec88b84869aee6a1106c80c",
           "Mostly NFL sides and unders; ~$277M volume, 2-3% ROI"),
    Trader("kch123", "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee",
           "~$292M volume, ~4% ROI (sports mix not yet checked)"),
)

MIN_USDC = 20_000.0          # his pre-game stake on one side before it counts as a signal
BAND_LOW = 0.40              # +150
BAND_HIGH = 2.0 / 3.0        # -200
CHASE_CENTS = 0.02           # alert says "don't pay more than his price + 2¢"
PAPER_STAKE = 10.0           # paper dollars per signal
MAX_PUSHES_PER_RUN = 6
SETTLE_AFTER = timedelta(hours=2)   # start checking results 2h after kickoff
KEEP_POSITIONS = timedelta(days=7)
KEEP_SEEN = 4000

LEDGER_FIELDS = [
    "signal_id", "trader", "wallet", "sport", "market_type", "event", "question", "outcome",
    "outcome_index", "condition_id", "event_slug", "game_start", "first_buy", "alert_time",
    "his_usdc", "his_price", "copy_price", "in_band", "pushed", "status", "settled_time",
    "pl_copy", "pl_his",
]


# ------------------------------------------------------------------ helpers

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value) -> datetime | None:
    """Gamma uses '2026-10-18 20:25:00+00' and ISO 'Z' strings; trades use unix seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    text = text.replace("Z", "+00:00").replace(" ", "T", 1)
    if text.endswith("+00"):
        text += ":00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def american(p: float | None) -> str:
    if p is None or not 0 < p < 1:
        return "—"
    if p >= 0.5:
        return f"-{round(100 * p / (1 - p))}"
    return f"+{round(100 * (1 - p) / p)}"


def in_band(p: float | None) -> bool:
    return p is not None and BAND_LOW <= p <= BAND_HIGH + 1e-9


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ public data

def fetch_trades(wallet: str, session=requests, limit: int = 100) -> list[dict]:
    """Newest trades for a wallet. takerOnly=false so his resting limit orders count too."""
    r = session.get(TRADES_URL, params={"user": wallet, "limit": limit, "takerOnly": "false"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def fetch_market(condition_id: str, session=requests) -> dict | None:
    r = session.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data:
        return data[0]
    return None


def game_start(market: dict) -> datetime | None:
    start = parse_ts(market.get("gameStartTime"))
    if start:
        return start
    for ev in market.get("events") or []:
        start = parse_ts((ev or {}).get("startTime"))
        if start:
            return start
    return None


def is_sports_game(market: dict) -> bool:
    """A single game with a kickoff time. Futures (season winners) and politics have none."""
    return game_start(market) is not None and bool(market.get("sportsMarketType") or market.get("gameStartTime"))


def current_ask(market: dict, outcome_index: int) -> float | None:
    """Price to buy the side he bought right now. bestAsk/bestBid are quoted for outcome 0."""
    ask, bid = _float(market.get("bestAsk")), _float(market.get("bestBid"))
    if outcome_index == 0 and ask:
        return ask
    if outcome_index == 1 and bid:
        return round(1 - bid, 4)
    prices = _json_list(market.get("outcomePrices"))
    if outcome_index < len(prices):
        return _float(prices[outcome_index])
    return None


def winner_index(market: dict) -> int | None | str:
    """Index of the winning outcome once Polymarket closes the market; 'void' for 50/50."""
    if not market.get("closed"):
        return None
    prices = [_float(p) for p in _json_list(market.get("outcomePrices"))]
    for i, p in enumerate(prices):
        if p is not None and p >= 0.99:
            return i
    if prices and all(p is not None and abs(p - 0.5) < 0.02 for p in prices):
        return "void"
    return None


# ------------------------------------------------------------------ state + ledger

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        state = {}
    state.setdefault("seen", [])
    state.setdefault("positions", {})
    state.setdefault("seeded", [])
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_ledger(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_ledger(rows: list[dict], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def trade_key(t: dict) -> str:
    return f"{t.get('transactionHash')}:{t.get('asset')}:{t.get('size')}:{t.get('price')}"


# ------------------------------------------------------------------ alerts

def send_push(title: str, message: str, url: str | None = None, post=requests.post) -> bool:
    user, token = os.getenv("PUSHOVER_USER_KEY"), os.getenv("PUSHOVER_APP_TOKEN")
    if not user or not token:
        print("PUSH SKIPPED (no Pushover secrets):", title, "|", message.replace("\n", " | "))
        return False
    payload = {"token": token, "user": user, "title": title[:250], "message": message[:1024], "priority": "1"}
    if url:
        payload["url"], payload["url_title"] = url, "Open on Polymarket"
    try:
        r = post(PUSH_URL, data=payload, timeout=15)
    except requests.RequestException as exc:
        print("Pushover error:", exc)
        return False
    if getattr(r, "status_code", 500) >= 400:
        print("Pushover error:", r.status_code, getattr(r, "text", "")[:300])
        return False
    return True


def _eastern(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        local = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # pragma: no cover
        local = dt
    return local.strftime("%a %-I:%M %p ET")


def alert_text(row: dict) -> tuple[str, str]:
    his = float(row["his_price"])
    copy = _float(row.get("copy_price"))
    max_pay = min(0.99, his + CHASE_CENTS)
    title = f"Follow {row['trader']}: {row['outcome']}"
    lines = [
        f"{row['question']} ({row['sport']} {row['market_type'] or 'game'})",
        f"He bought ${float(row['his_usdc']):,.0f} on {row['outcome']} at {his * 100:.0f}¢ ({american(his)})",
        f"Now: {copy * 100:.0f}¢ ({american(copy)})" if copy else "Now: price unavailable",
        f"Copy only at {max_pay * 100:.0f}¢ ({american(max_pay)}) or better",
        f"Starts {_eastern(parse_ts(row['game_start']))}",
    ]
    if copy and copy > max_pay:
        lines.insert(3, "⚠️ Price already moved past the copy limit")
    return title, "\n".join(lines)


# ------------------------------------------------------------------ one round

def run_round(
    *,
    traders: Iterable[Trader] = TRADERS,
    now: datetime | None = None,
    get_trades: Callable[[str], list[dict]] = fetch_trades,
    get_market: Callable[[str], dict | None] = fetch_market,
    push: Callable[[str, str, str | None], bool] = send_push,
    state_path: Path = STATE_PATH,
    ledger_path: Path = LEDGER_PATH,
) -> dict:
    now = now or utcnow()
    state = load_state(state_path)
    ledger = load_ledger(ledger_path)
    seen = set(state["seen"])
    seen_order = list(state["seen"])
    positions: dict = state["positions"]
    signal_ids = {r["signal_id"] for r in ledger}
    markets: dict[str, dict | None] = {}
    summary = {"new_trades": 0, "signals": 0, "pushed": 0, "settled": 0, "errors": []}

    def market(cid: str) -> dict | None:
        if cid not in markets:
            try:
                markets[cid] = get_market(cid)
            except Exception as exc:  # network hiccup: skip this market this round
                summary["errors"].append(f"market {cid[:10]}: {exc}")
                markets[cid] = None
        return markets[cid]

    for trader in traders:
        try:
            trades = get_trades(trader.wallet)
        except Exception as exc:
            summary["errors"].append(f"{trader.name}: {exc}")
            continue
        first_look = trader.wallet not in state["seeded"]
        touched: set[str] = set()
        for t in sorted(trades, key=lambda x: x.get("timestamp") or 0):
            key = trade_key(t)
            if key in seen:
                continue
            seen.add(key)
            seen_order.append(key)
            if first_look or str(t.get("side", "")).upper() != "BUY":
                continue  # first look only records history so we don't alert on old bets
            cid = t.get("conditionId")
            if not cid:
                continue
            summary["new_trades"] += 1
            pkey = f"{trader.wallet}|{cid}|{t.get('outcomeIndex')}"
            pos = positions.setdefault(pkey, {
                "trader": trader.name, "wallet": trader.wallet, "condition_id": cid,
                "outcome": t.get("outcome"), "outcome_index": t.get("outcomeIndex"),
                "title": t.get("title"), "event_slug": t.get("eventSlug"),
                "pre_usdc": 0.0, "pre_shares": 0.0, "live_usdc": 0.0,
                "first_ts": t.get("timestamp"), "last_ts": t.get("timestamp"), "alerted": False,
            })
            m = market(cid)
            if m is None:  # couldn't read the market: forget this fill so next round retries it
                seen.discard(key)
                seen_order.pop()
                summary["new_trades"] -= 1
                continue
            if not is_sports_game(m):
                pos["skip"] = "not a single game"
                continue
            start = game_start(m)
            ts = parse_ts(t.get("timestamp"))
            size, price = _float(t.get("size")) or 0.0, _float(t.get("price")) or 0.0
            if ts and start and ts < start:
                pos["pre_usdc"] += size * price
                pos["pre_shares"] += size
            else:
                pos["live_usdc"] += size * price
            pos["last_ts"] = t.get("timestamp")
            pos["game_start"] = iso(start)
            touched.add(pkey)
        if first_look:
            state["seeded"].append(trader.wallet)

        for pkey in touched:
            pos = positions[pkey]
            if pos["alerted"] or pos["pre_usdc"] < MIN_USDC or not pos["pre_shares"]:
                continue
            start = parse_ts(pos.get("game_start"))
            if not start or start <= now:
                continue  # game already started: too late to copy
            m = market(pos["condition_id"]) or {}
            idx = int(pos["outcome_index"] or 0)
            his = round(pos["pre_usdc"] / pos["pre_shares"], 4)
            copy = current_ask(m, idx)
            sid = f"{pos['wallet'][:10]}-{pos['condition_id'][:12]}-{idx}"
            pos["alerted"] = True
            if sid in signal_ids:
                continue
            row = {
                "signal_id": sid, "trader": pos["trader"], "wallet": pos["wallet"],
                "sport": (pos.get("event_slug") or "").split("-")[0].upper() or "?",
                "market_type": m.get("sportsMarketType") or "", "event": pos.get("event_slug") or "",
                "question": m.get("question") or pos.get("title") or "", "outcome": pos.get("outcome") or "",
                "outcome_index": idx, "condition_id": pos["condition_id"], "event_slug": pos.get("event_slug") or "",
                "game_start": pos["game_start"], "first_buy": iso(parse_ts(pos.get("first_ts"))),
                "alert_time": iso(now), "his_usdc": round(pos["pre_usdc"], 2), "his_price": his,
                "copy_price": copy if copy is not None else "", "in_band": in_band(his),
                "pushed": False, "status": "open", "settled_time": "", "pl_copy": "", "pl_his": "",
            }
            summary["signals"] += 1
            if row["in_band"] and summary["pushed"] < MAX_PUSHES_PER_RUN:
                title, msg = alert_text(row)
                url = f"https://polymarket.com/event/{row['event_slug']}" if row["event_slug"] else None
                if push(title, msg, url):
                    row["pushed"] = True
                    summary["pushed"] += 1
            ledger.append(row)
            signal_ids.add(sid)

    summary["settled"] = settle(ledger, now, market)

    # prune old state
    cutoff = (now - KEEP_POSITIONS).timestamp()
    state["positions"] = {k: v for k, v in positions.items() if float(v.get("last_ts") or 0) >= cutoff}
    state["seen"] = seen_order[-KEEP_SEEN:]
    state.pop("last_run", None)  # no timestamp: an idle round changes nothing, so nothing is committed
    save_state(state, state_path)
    save_ledger(ledger, ledger_path)
    return summary


def _pl(price: float | None, won: bool) -> float | None:
    if not price or not 0 < price < 1:
        return None
    return round(PAPER_STAKE * (1 / price - 1), 2) if won else -PAPER_STAKE


def settle(ledger: list[dict], now: datetime, market: Callable[[str], dict | None]) -> int:
    n = 0
    for row in ledger:
        if row.get("status") != "open":
            continue
        start = parse_ts(row.get("game_start"))
        if not start or now - start < SETTLE_AFTER:
            continue
        m = market(row["condition_id"])
        if not m:
            continue
        w = winner_index(m)
        if w is None:
            continue
        if w == "void":
            row.update(status="void", pl_copy=0.0, pl_his=0.0)
        else:
            won = int(row["outcome_index"]) == w
            copy = _float(row.get("copy_price")) or _float(row.get("his_price"))
            row.update(status="won" if won else "lost", pl_copy=_pl(copy, won), pl_his=_pl(_float(row["his_price"]), won))
        row["settled_time"] = iso(now)
        n += 1
    return n


# ------------------------------------------------------------------ dashboard math

SOCCER = {"epl", "ucl", "uel", "uecl", "lal", "sea", "bun", "fl1", "unl", "efl", "mls", "fifwc", "ere", "por", "facup"}
SPORT_NAMES = {"nfl": "NFL", "cfb": "College football", "ncaaf": "College football", "nba": "NBA",
               "cbb": "College basketball", "wnba": "WNBA", "nhl": "NHL", "mlb": "MLB", "atp": "Tennis",
               "wta": "Tennis", "boxing": "Boxing", "ufc": "UFC/MMA", "mma": "UFC/MMA"}


def sport_group(row: dict) -> str:
    code = str(row.get("sport") or "").lower()
    if code in SOCCER:
        return "Soccer"
    return SPORT_NAMES.get(code, code.upper() or "Other")


def _stats(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("status") in ("won", "lost")]
    wins = sum(r["status"] == "won" for r in done)
    pl_copy = sum(_float(r.get("pl_copy")) or 0 for r in done)
    pl_his = sum(_float(r.get("pl_his")) or 0 for r in done)
    staked = PAPER_STAKE * len(done)
    return {"signals": len(rows), "open": sum(r.get("status") == "open" for r in rows),
            "wins": wins, "losses": len(done) - wins, "pl_copy": round(pl_copy, 2), "pl_his": round(pl_his, 2),
            "roi_copy": pl_copy / staked if staked else None, "roi_his": pl_his / staked if staked else None}


def _alerted(rows: list[dict], only_band: bool) -> list[dict]:
    return [r for r in rows if not only_band or str(r.get("in_band")) == "True"]


def summarize(rows: list[dict], only_band: bool = True) -> list[dict]:
    rows = _alerted(rows, only_band)
    return [{"trader": name, **_stats([r for r in rows if r["trader"] == name])}
            for name in sorted({r["trader"] for r in rows} | {t.name for t in TRADERS})]


def summarize_by_sport(rows: list[dict], only_band: bool = True) -> list[dict]:
    """One line per trader + sport, biggest sample first, so losing sports are easy to cut."""
    rows = _alerted(rows, only_band)
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["trader"], sport_group(r)), []).append(r)
    out = [{"trader": t, "sport": s, **_stats(g)} for (t, s), g in groups.items()]
    return sorted(out, key=lambda x: (x["trader"], -(x["wins"] + x["losses"]), -x["signals"]))
