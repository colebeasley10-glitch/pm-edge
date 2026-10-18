"""Paper book shared by every league strategy.

One CSV row per paper trade. A trade is opened at the live Kalshi ask (taker,
with Kalshi's fee), marked to market each run while the market trades, and
settled from Kalshi's own result. The last pre-close mark is the closing price
used for CLV, so a strategy is judged on whether it beat the market's final
price, not only on wins and losses.
"""
from __future__ import annotations

import csv
import hashlib
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEDGER = Path(os.getenv("LEAGUE_LEDGER_PATH", "league_trades.csv"))
STAKE_DOLLARS = float(os.getenv("LEAGUE_STAKE_DOLLARS", "10"))
TAKER_FEE_RATE = float(os.getenv("LEAGUE_TAKER_FEE_RATE", "0.07"))
MIN_PRICE = 0.05
MAX_PRICE = 0.95

FIELDS = [
    "trade_id", "strategy", "opened_at", "ticker", "event_ticker", "title", "category",
    "side", "entry_price", "contracts", "cost", "fee", "fair", "edge", "reason",
    "close_time", "ends_at", "status", "result", "settled_at", "pl", "last_mid", "last_mid_at",
    "closing_mid", "clv_cents", "ask_5m", "mid_5m", "checked_5m_at",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def f(value, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def taker_fee(price: float, contracts: int, rate: float = TAKER_FEE_RATE) -> float:
    """Kalshi general fee: ceil to the cent of rate * C * P * (1 - P)."""
    raw = rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0 - 1e-9) / 100.0


def fee_per_contract(price: float, contracts: int = 10) -> float:
    return taker_fee(price, contracts) / max(1, contracts)


def side_quotes(market: dict, side: str) -> tuple[float | None, float | None]:
    """(bid, ask) for the chosen side, in dollars."""
    yb, ya = f(market.get("yes_bid")), f(market.get("yes_ask"))
    nb, na = f(market.get("no_bid")), f(market.get("no_ask"))
    if side == "YES":
        bid, ask = yb, ya
        if ask is None and nb is not None:
            ask = 1.0 - nb
        if bid is None and na is not None:
            bid = 1.0 - na
    else:
        bid, ask = nb, na
        if ask is None and yb is not None:
            ask = 1.0 - yb
        if bid is None and ya is not None:
            bid = 1.0 - ya
    return bid, ask


def net_edge(fair: float, ask: float) -> float:
    contracts = max(1, int(STAKE_DOLLARS // ask))
    return fair - ask - fee_per_contract(ask, contracts)


@dataclass
class Pick:
    strategy: str
    market: dict              # normalized Kalshi market (pmedge.kalshi.normalize_market)
    side: str                 # YES / NO
    fair: float | None        # strategy's probability for `side`; None = no model (e.g. follow)
    reason: str
    category: str = ""
    min_edge: float = 0.03
    extra: dict = field(default_factory=dict)

    def ask(self) -> float | None:
        return side_quotes(self.market, self.side)[1]

    def edge(self) -> float | None:
        a = self.ask()
        if a is None or self.fair is None:
            return None
        return net_edge(self.fair, a)


def load(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def save(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(path)


def _trade_id(strategy: str, ticker: str, side: str, opened: datetime) -> str:
    return hashlib.sha1(f"{strategy}|{ticker}|{side}|{iso(opened)}".encode()).hexdigest()[:16]


# Strategies limited to one bet per game (Kalshi event): no taking both sides of one match,
# and no doubling up on one result worded two ways ("A wins" + "B does not win").
# Weather and crypto are left out: their events are price/temperature brackets, not games.
ONE_PER_GAME = {"sharp", "polymarket", "books"}


def event_of(row_or_market: dict) -> str:
    """Kalshi event ticker (the game); derived from the market ticker when missing."""
    ev = str(row_or_market.get("event_ticker") or "")
    if ev:
        return ev
    t = str(row_or_market.get("ticker") or "")
    return t.rsplit("-", 1)[0] if "-" in t else t


def gate(pick: Pick, rows: list[dict], *, now: datetime | None = None) -> tuple[bool, str]:
    """Shared rules every strategy must pass before a paper trade opens."""
    now = now or now_utc()
    m = pick.market
    ticker = str(m.get("ticker") or "")
    if not ticker or pick.side not in {"YES", "NO"}:
        return False, "bad_pick"
    status = str(m.get("status") or "").lower()
    if status and status not in {"open", "active"}:
        return False, "market_not_open"
    close = parse_dt(m.get("close_time"))
    if close is not None and close <= now + timedelta(minutes=5):
        return False, "closing"
    bid, ask = side_quotes(m, pick.side)
    if ask is None or not (MIN_PRICE <= ask <= MAX_PRICE):
        return False, "price_out_of_range"
    if bid is not None and ask - bid > 0.10:
        return False, "wide_spread"
    if pick.fair is not None:
        e = net_edge(pick.fair, ask)
        if e + 1e-9 < pick.min_edge:
            return False, "edge_below_min"
        if pick.fair - ask > 0.30:
            return False, "implausible_edge"
    if pick.strategy in ONE_PER_GAME:
        ev = event_of(m)
        for r in rows:
            if r.get("strategy") != pick.strategy or event_of(r) != ev or r.get("ticker") == ticker:
                continue
            if r.get("status") == "open" or (now - (parse_dt(r.get("opened_at")) or now)) < timedelta(hours=24):
                return False, "same_game"
    for r in rows:
        if r.get("ticker") != ticker:
            continue
        if r.get("status") == "open":
            return False, "already_holding"
        if r.get("strategy") == pick.strategy and (now - (parse_dt(r.get("opened_at")) or now)) < timedelta(hours=24):
            return False, "recent_duplicate"
    return True, "ok"


def open_trade(pick: Pick, rows: list[dict], *, now: datetime | None = None) -> dict:
    now = now or now_utc()
    m = pick.market
    _, ask = side_quotes(m, pick.side)
    contracts = max(1, int(STAKE_DOLLARS // ask))
    fee = taker_fee(ask, contracts)
    row = {
        "trade_id": _trade_id(pick.strategy, m["ticker"], pick.side, now),
        "strategy": pick.strategy,
        "opened_at": iso(now),
        "ticker": m["ticker"],
        "event_ticker": m.get("event_ticker", ""),
        "title": pick.extra.get("title") or m.get("title", ""),
        "category": pick.category,
        "side": pick.side,
        "entry_price": round(ask, 4),
        "contracts": contracts,
        "cost": round(contracts * ask, 2),
        "fee": round(fee, 2),
        "fair": "" if pick.fair is None else round(pick.fair, 4),
        "edge": "" if pick.fair is None else round(net_edge(pick.fair, ask), 4),
        "reason": pick.reason[:240],
        "close_time": m.get("close_time") or "",
        "ends_at": m.get("expected_expiration_time") or m.get("close_time") or "",
        "status": "open",
        "last_mid": "",
        "last_mid_at": "",
    }
    rows.append(row)
    return row


def side_mid(market: dict, side: str) -> float | None:
    bid, ask = side_quotes(market, side)
    if bid is None or ask is None or ask <= 0:
        return None
    return (bid + ask) / 2.0


def mark_cutoff(row: dict) -> datetime | None:
    """Time after which a row's mark stops moving, so last_mid is a real closing line.
    Weather: the start of the temperature day (marks after that just track observations)."""
    if row.get("strategy") == "weather":
        from league.strategies.weather import closing_cutoff
        return closing_cutoff(row.get("event_ticker") or "")
    return None


SETTLED_STATUSES = {"finalized", "settled", "determined"}


def scalar_payout(m: dict, side: str) -> float | None:
    """Per-contract payout for a market Kalshi settled at a price other than $0/$1.

    Happens when a game ends with no result (e.g. a rained-out cricket match): the
    result comes back "scalar" (or blank) with a settlement value between 0 and 1.
    Returns None while the market hasn't settled that way.
    """
    result = str(m.get("result") or "").lower()
    status = str(m.get("status") or "").lower()
    if result in {"yes", "no", "void", "voided", "all_no_void"} or status in {"voided", "canceled", "cancelled"}:
        return None
    value = f(m.get("settlement_value"))
    if value is None or not (result == "scalar" or status in SETTLED_STATUSES):
        return None
    value = min(1.0, max(0.0, value))
    return value if side == "YES" else 1.0 - value


def update(rows: list[dict], fetch_markets, *, now: datetime | None = None) -> dict:
    """Mark open trades to market and settle finished ones.

    fetch_markets(tickers) -> {ticker: normalized_market}
    """
    now = now or now_utc()
    open_rows = [r for r in rows if r.get("status") == "open"]
    if not open_rows:
        return {"marked": 0, "settled": 0}
    markets = fetch_markets(sorted({r["ticker"] for r in open_rows}))
    marked = settled = 0
    for r in open_rows:
        m = markets.get(r["ticker"])
        if not m:
            continue
        if m.get("expected_expiration_time"):
            r["ends_at"] = m["expected_expiration_time"]
        result = str(m.get("result") or "").lower()
        status = str(m.get("status") or "").lower()
        close = parse_dt(r.get("close_time")) or parse_dt(m.get("close_time"))
        if result in {"yes", "no"}:
            won = (result == "yes") == (r["side"] == "YES")
            contracts = int(f(r.get("contracts"), 0) or 0)
            cost = f(r.get("cost"), 0.0) or 0.0
            fee = f(r.get("fee"), 0.0) or 0.0
            r["status"] = "won" if won else "lost"
            r["result"] = result
            r["settled_at"] = iso(now)
            r["pl"] = round((contracts * 1.0 if won else 0.0) - cost - fee, 2)
            closing = f(r.get("last_mid"))
            cut, at = mark_cutoff(r), parse_dt(r.get("last_mid_at"))
            if cut is not None and (at is None or at >= cut):
                closing = None  # only a mark from before the cutoff counts as a closing line
            if closing is not None:
                r["closing_mid"] = round(closing, 4)
                r["clv_cents"] = round((closing - (f(r.get("entry_price"), 0.0) or 0.0)) * 100.0, 2)
            settled += 1
            continue
        pay = scalar_payout(m, r["side"])
        if pay is not None:
            contracts = int(f(r.get("contracts"), 0) or 0)
            cost = f(r.get("cost"), 0.0) or 0.0
            fee = f(r.get("fee"), 0.0) or 0.0
            pl = round(contracts * pay - cost - fee, 2)
            r["status"] = "won" if pl > 0 else "lost" if pl < 0 else "void"
            r["result"] = "scalar"
            r["settled_at"] = iso(now)
            r["pl"] = pl
            settled += 1
            continue
        if result in {"void", "voided", "all_no_void"} or status in {"voided", "canceled", "cancelled"}:
            r["status"] = "void"
            r["result"] = result or status
            r["settled_at"] = iso(now)
            r["pl"] = 0.0
            settled += 1
            continue
        # still trading: keep the latest pre-close mark as the closing line
        if mark_cutoff(r) is not None and now >= mark_cutoff(r):
            continue  # past this strategy's closing-line time: freeze the mark
        if close is None or now < close:
            mid = side_mid(m, r["side"])
            if mid is not None and status in {"open", "active", ""}:
                r["last_mid"] = round(mid, 4)
                r["last_mid_at"] = iso(now)
                marked += 1
    return {"marked": marked, "settled": settled}


def standings(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r.get("strategy") or "unknown", {
            "strategy": r.get("strategy") or "unknown", "picks": 0, "open": 0, "won": 0, "lost": 0,
            "pl": 0.0, "risked": 0.0, "clv_sum": 0.0, "clv_n": 0,
        })
        s["picks"] += 1
        st = r.get("status")
        if st == "open":
            s["open"] += 1
        elif st in {"won", "lost"}:
            s[st] += 1
            s["pl"] += f(r.get("pl"), 0.0) or 0.0
            s["risked"] += (f(r.get("cost"), 0.0) or 0.0) + (f(r.get("fee"), 0.0) or 0.0)
        clv = f(r.get("clv_cents"))
        if clv is not None:
            s["clv_sum"] += clv
            s["clv_n"] += 1
    rows_out = []
    for s in out.values():
        s["roi"] = s["pl"] / s["risked"] if s["risked"] else None
        s["clv"] = s["clv_sum"] / s["clv_n"] if s["clv_n"] else None
        rows_out.append(s)
    return sorted(rows_out, key=lambda s: (-(s["clv"] if s["clv"] is not None else -99), -s["pl"]))
