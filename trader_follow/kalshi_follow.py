"""Kalshi Follow: copy alerts when a proven Kalshi sports trader makes a big PRE-GAME bet.

PAPER + ALERTS ONLY. Same idea as the Polymarket Trader Follow, on Kalshi's public social
feed (only traders who leave their trades visible can be followed). Kalshi is where SharpIQ
already bets automatically, so a lane that proves itself here can later be copied for real.

Per round, for each followed nickname:
1. Read his newest trades (/v1/social/trades). He can be the taker or the resting maker:
   a maker's side is the opposite of what the taker bought.
2. Add up his pre-game buys per market side (game start = Kalshi's occurrence_datetime).
3. Once a side passes his minimum, log a signal at the current Kalshi ask; push it if his
   price is in the +150 to -200 band. Paper result: $10 at the alert price, settled from
   Kalshi's official result.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import requests

from trader_follow import core

SOCIAL = "https://api.elections.kalshi.com/v1/social"
MARKETS = "https://api.elections.kalshi.com/trade-api/v2"
DATA_DIR = core.DATA_DIR
STATE_PATH = DATA_DIR / "kalshi_state.json"
LEDGER_PATH = DATA_DIR / "kalshi_signals.csv"
GAME_SERIES = re.compile(r"^KX[A-Z0-9]*(GAME|SPREAD|TOTAL|MATCH|FIGHT)$")
KEEP_SEEN = 5000


@dataclass(frozen=True)
class KalshiTrader:
    nickname: str
    note: str = ""
    min_usd: float = 5_000.0


# Oct 25, 2026 from the Kalshi side of the Trader Scout (sports profit leaderboard, trades visible).
KALSHI_TRADERS: tuple[KalshiTrader, ...] = (
    KalshiTrader("BWmarkets", "Sports +$1.36M all-time, +$1.5M this month; +10% on $8M traded; mostly NFL", 5_000.0),
    KalshiTrader("lost.buck", "Sports +$435k all-time, +$148k this month; NFL; small sample (117 markets)", 2_000.0),
)

FIELDS = ["signal_id", "trader", "ticker", "event_ticker", "sport", "bet", "side", "title", "game_start",
          "first_buy", "alert_time", "his_usd", "his_price", "copy_price", "in_band", "pushed",
          "status", "settled_time", "pl_copy"]


# ------------------------------------------------------------------ data access
def _get(url: str, session=requests, **params) -> dict:
    r = session.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_trades(nickname: str, session=requests) -> list[dict]:
    d = _get(f"{SOCIAL}/trades", session, nickname=nickname, limit=100)
    return d.get("trades") or []


def fetch_market(ticker: str, session=requests) -> dict | None:
    d = _get(f"{MARKETS}/markets/{ticker}", session)
    return d.get("market")


def fetch_event_title(event_ticker: str, session=requests) -> str:
    try:
        return (_get(f"{MARKETS}/events/{event_ticker}", session).get("event") or {}).get("title") or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ helpers
def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def his_side(trade: dict, nickname: str) -> str | None:
    """'yes' / 'no' for a BUY by `nickname`, None for his sells (exits)."""
    side = str(trade.get("taker_side") or "").lower()
    action = str(trade.get("taker_action") or "buy").lower()
    if side not in {"yes", "no"}:
        return None
    other = "no" if side == "yes" else "yes"
    if str(trade.get("taker_nickname") or "").lower() == nickname.lower():
        return side if action == "buy" else None
    if str(trade.get("maker_nickname") or "").lower() == nickname.lower():
        # taker bought `side` from him -> he took the other side; taker sold `side` to him -> he bought it
        return other if action == "buy" else side
    return None


def side_price(trade: dict, side: str, market: dict | None) -> float | None:
    """Price he paid for `side`. The feed's single 'price' is read as the YES price unless the
    market's current prices say it's clearly the price of the other side."""
    raw = _f(trade.get("price_dollars")) or ((_f(trade.get("price")) or 0) / 100.0)
    if not raw or not 0 < raw < 1:
        return None
    as_yes = raw if side == "yes" else 1 - raw
    m = market or {}
    yb, ya = _px(m, "yes_bid"), _px(m, "yes_ask")
    if yb is not None and ya is not None and 0 < yb <= ya < 1:
        mid_side = (yb + ya) / 2 if side == "yes" else 1 - (yb + ya) / 2
        alt = 1 - as_yes
        if abs(alt - mid_side) + 0.03 < abs(as_yes - mid_side):
            return round(alt, 4)
    return round(as_yes, 4)


def _px(m: dict, name: str) -> float | None:
    """A Kalshi price in dollars, from the *_dollars field or the older cents field."""
    v = _f(m.get(f"{name}_dollars"))
    if v is None:
        c = _f(m.get(name))
        v = c / 100.0 if c is not None else None
    return v


def ask_for(market: dict | None, side: str) -> float | None:
    a = _px(market or {}, "yes_ask" if side == "yes" else "no_ask")
    return a if a is not None and 0 < a < 1 else None


def game_start(market: dict | None) -> datetime | None:
    v = (market or {}).get("occurrence_datetime")
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


_SPREAD = re.compile(r"^(.+?) wins? by (?:over|more than) ([\d.]+)", re.I)
_TOTAL = re.compile(r"(?:over|above) ([\d.]+)", re.I)


def label(market: dict, side: str, event_title: str) -> str:
    """Plain words: 'Titans +3.5', 'Browns ML', 'Under 44.5'."""
    from league.strategies.polymarket import halves
    title = str(market.get("title") or "")
    sub = str(market.get("yes_sub_title") or "")
    teams = halves(event_title) or []
    ticker = str(market.get("ticker") or "")
    m = _SPREAD.search(sub) or _SPREAD.search(title)
    if "SPREAD" in ticker and m:
        team, n = m.group(1).strip(), m.group(2)
        if side == "yes":
            return f"{team} -{n}"
        opp = next((t for t in teams if t and team.lower() not in t.lower() and t.lower() not in team.lower()), "")
        return f"{opp} +{n}" if opp else f"Against {team} -{n}"
    t = _TOTAL.search(sub) or _TOTAL.search(title)
    if "TOTAL" in ticker and t:
        return f"{'Over' if side == 'yes' else 'Under'} {t.group(1)}"
    if "GAME" in ticker or "MATCH" in ticker or "FIGHT" in ticker:
        team = sub or title
        if side == "yes":
            return f"{team} ML"
        opp = next((x for x in teams if x and team.lower() not in x.lower() and x.lower() not in team.lower()), "")
        return f"{opp} ML" if opp else f"Against {team} (no draw)"
    return f"{side.upper()} — {sub or title}"


def sport_of(ticker: str) -> str:
    from trader_follow.kalshi import sport_of as s
    return s(ticker)


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True))


def load_ledger(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_ledger(rows: list[dict], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def alert_text(row: dict) -> tuple[str, str]:
    his = float(row["his_price"])
    copy = _f(row.get("copy_price"))
    max_pay = min(0.99, his + core.CHASE_CENTS)
    lines = [
        f"Bet: {row['bet']}  ({row['sport']}, Kalshi)",
        f"He bought ${float(row['his_usd']):,.0f} at {his * 100:.0f}¢ ({core.american(his)})",
        f"Kalshi now: {copy * 100:.0f}¢ ({core.american(copy)})" if copy else "Kalshi now: price unavailable",
        f"Take it anywhere at {core.american(max_pay)} or better (Kalshi {max_pay * 100:.0f}¢)",
        f"Starts {core._eastern(datetime.fromisoformat(row['game_start']))}",
    ]
    if copy and copy > max_pay:
        lines.insert(3, "⚠️ Kalshi price already moved past the copy limit")
    return f"Follow {row['trader']} (Kalshi): {row['bet']}", "\n".join(lines)


def _pl(price: float | None, won: bool) -> float | None:
    if not price or not 0 < price < 1:
        return None
    return round(core.PAPER_STAKE * (1 / price - 1), 2) if won else -core.PAPER_STAKE


# ------------------------------------------------------------------ one round
def run_round(*, traders: Iterable[KalshiTrader] = KALSHI_TRADERS, now: datetime | None = None,
              get_trades: Callable[[str], list[dict]] = fetch_trades,
              get_market: Callable[[str], dict | None] = fetch_market,
              get_event_title: Callable[[str], str] = fetch_event_title,
              push: Callable[..., bool] = core.send_push,
              state_path: Path = STATE_PATH, ledger_path: Path = LEDGER_PATH) -> dict:
    now = now or datetime.now(timezone.utc)
    state = load_state(state_path)
    seen = list(state.get("seen") or [])
    seen_set = set(seen)
    seeded = set(state.get("seeded") or [])
    positions: dict = state.get("positions") or {}
    ledger = load_ledger(ledger_path)
    ids = {r["signal_id"] for r in ledger}
    markets: dict[str, dict | None] = {}
    summary = {"new_trades": 0, "signals": 0, "pushed": 0, "settled": 0, "errors": []}

    def market(t: str) -> dict | None:
        if t not in markets:
            try:
                markets[t] = get_market(t)
            except Exception as exc:
                summary["errors"].append(f"market {t}: {type(exc).__name__}")
                markets[t] = None
        return markets[t]

    for tr in traders:
        try:
            trades = get_trades(tr.nickname)
        except Exception as exc:
            summary["errors"].append(f"{tr.nickname}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        first = tr.nickname not in seeded
        touched = set()
        for t in sorted(trades, key=lambda x: x.get("create_date") or ""):
            tid = str(t.get("trade_id") or "")
            if not tid or tid in seen_set:
                continue
            seen_set.add(tid)
            seen.append(tid)
            if first:
                continue
            ticker = str(t.get("ticker") or "")
            if not GAME_SERIES.match(ticker.split("-", 1)[0]):
                continue
            side = his_side(t, tr.nickname)
            if side is None:
                continue
            m = market(ticker)
            if m is None:
                seen_set.discard(tid)
                seen.pop()
                continue
            start = game_start(m)
            ts = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00"))
            if not start or ts >= start:
                continue          # live/in-game or no start time: never counts
            price = side_price(t, side, m)
            n = _f(t.get("count_fp")) or _f(t.get("count")) or 0.0
            if not price or not n:
                continue
            summary["new_trades"] += 1
            key = f"{tr.nickname}|{ticker}|{side}"
            pos = positions.setdefault(key, {"trader": tr.nickname, "ticker": ticker, "side": side,
                                             "usd": 0.0, "contracts": 0.0, "first": t.get("create_date"),
                                             "start": start.isoformat(), "alerted": False})
            pos["usd"] += n * price
            pos["contracts"] += n
            touched.add(key)
        seeded.add(tr.nickname)

        for key in touched:
            pos = positions[key]
            start = datetime.fromisoformat(pos["start"])
            if pos["alerted"] or pos["usd"] < tr.min_usd or start <= now:
                continue
            pos["alerted"] = True
            sid = f"k-{key}"
            if sid in ids:
                continue
            m = market(pos["ticker"]) or {}
            his = round(pos["usd"] / pos["contracts"], 4)
            copy = ask_for(m, pos["side"])
            ev = str(m.get("event_ticker") or pos["ticker"].rsplit("-", 1)[0])
            row = {"signal_id": sid, "trader": tr.nickname, "ticker": pos["ticker"], "event_ticker": ev,
                   "sport": sport_of(pos["ticker"]), "bet": label(m, pos["side"], get_event_title(ev)),
                   "side": pos["side"], "title": m.get("title") or "", "game_start": pos["start"],
                   "first_buy": pos["first"], "alert_time": core.iso(now), "his_usd": round(pos["usd"], 2),
                   "his_price": his, "copy_price": copy if copy is not None else "", "in_band": core.in_band(his),
                   "pushed": False, "status": "open", "settled_time": "", "pl_copy": ""}
            summary["signals"] += 1
            if row["in_band"]:
                title, msg = alert_text(row)
                if push(title, msg, f"https://kalshi.com/markets/{ev.split('-')[0].lower()}", url_title="Open on Kalshi"):
                    row["pushed"] = True
                    summary["pushed"] += 1
            ledger.append(row)
            ids.add(sid)

    # settle from Kalshi's official result
    for row in ledger:
        if row.get("status") != "open":
            continue
        m = market(row["ticker"])
        res = str((m or {}).get("result") or "").lower()
        if res not in {"yes", "no"}:
            if res in {"void", "voided"}:
                row.update(status="void", pl_copy=0.0, settled_time=core.iso(now))
                summary["settled"] += 1
            continue
        won = res == row["side"]
        price = _f(row.get("copy_price")) or _f(row.get("his_price"))
        row.update(status="won" if won else "lost", pl_copy=_pl(price, won), settled_time=core.iso(now))
        summary["settled"] += 1

    cutoff = now.timestamp() - 7 * 86400
    state["positions"] = {k: v for k, v in positions.items()
                          if datetime.fromisoformat(v["start"]).timestamp() >= cutoff}
    state["seen"] = seen[-KEEP_SEEN:]
    state["seeded"] = sorted(seeded)
    save_state(state, state_path)
    save_ledger(ledger, ledger_path)
    return summary


def stats(rows: list[dict]) -> list[dict]:
    """Scoreboard per trader, alerted (in-band) signals only."""
    out = []
    for name in sorted({r["trader"] for r in rows} | {t.nickname for t in KALSHI_TRADERS}):
        rs = [r for r in rows if r["trader"] == name and str(r.get("in_band")) == "True"]
        done = [r for r in rs if r.get("status") in {"won", "lost"}]
        pl = sum(_f(r.get("pl_copy")) or 0 for r in done)
        staked = core.PAPER_STAKE * len(done)
        out.append({"trader": name, "signals": len(rs), "open": sum(r.get("status") == "open" for r in rs),
                    "wins": sum(r["status"] == "won" for r in done), "losses": sum(r["status"] == "lost" for r in done),
                    "pl_copy": round(pl, 2), "roi_copy": pl / staked if staked else None})
    return out
