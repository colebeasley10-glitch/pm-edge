"""Live $2 Sharp: copy each new Sharp paper pick with a real Kalshi order.

Real money, so every limit is enforced here in code, not just in config:

* at most $2.00 of contracts per bet (fee on top, a few cents);
* at most $105.00 spent per Eastern day, fees included (no limit on the number of bets);
* the daily spend is the larger of this ledger and Kalshi's own order history,
  so a lost ledger commit can't double the budget;
* orders are immediate-or-cancel at the price the paper pick saw, so we never
  chase a moved market and nothing rests on the book;
* client_order_id is derived from the paper trade id, so a retried round
  can't place the same bet twice;
* only picks copying a crowd of at least LIVE_SHARP_MIN_TRADES trades (default 20) go out.
  On paper, Sharp picks built on fewer trades lost money every day (-22% ROI vs +22%
  for 20+ through Oct 14), so real money skips them. Paper Sharp still takes them all,
  which keeps the comparison running.

Off unless LIVE_SHARP_ENABLED=1 and Kalshi write keys are present.
"""
from __future__ import annotations

import csv
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from league import book

EASTERN = ZoneInfo("America/New_York")
LEDGER = Path(os.getenv("LIVE_SHARP_LEDGER_PATH", "live_sharp_trades.csv"))
PREFIX = "pmls-"

# Hard ceilings. Env can lower these, never raise them.
CEIL_PER_BET = 2.00
CEIL_DAILY = 105.00

FIELDS = [
    "trade_id", "paper_trade_id", "client_order_id", "placed_at", "day", "ticker", "title", "side",
    "limit_price", "contracts_wanted", "status", "order_id", "filled", "avg_price", "cost", "fee",
    "close_time", "result", "settled_at", "pl", "paper_entry", "note",
    "ends_at", "last_mid", "last_mid_at", "clv_cents", "event_ticker",
    "sold", "sale_proceeds", "sale_fee",
    "drift_cents", "drift_at",
]

# How long after the buy the price-drift reading is taken. The old clv_cents is the last mid
# before settlement, which for in-play markets is mostly the score, not the line; drift is
# the first mark at least this long after the fill, so it measures whether the market kept
# moving our way after we copied the flow.
DRIFT_AFTER = timedelta(minutes=25)


def _env_float(name: str, default: float, ceiling: float) -> float:
    try:
        v = float(os.getenv(name, default))
    except ValueError:
        v = default
    return max(0.0, min(v, ceiling))


def per_bet() -> float:
    return _env_float("LIVE_SHARP_MAX_PER_BET", CEIL_PER_BET, CEIL_PER_BET)


def daily_budget() -> float:
    return _env_float("LIVE_SHARP_DAILY_BUDGET", CEIL_DAILY, CEIL_DAILY)



def min_trades() -> int:
    try:
        return max(0, int(os.getenv("LIVE_SHARP_MIN_TRADES", "20")))
    except ValueError:
        return 20


def flow_trades(pick: dict) -> int | None:
    """How many trades the Sharp pick copied, read from its reason ("$4,044 bet YES at 60¢ (63 trades)")."""
    m = re.search(r"\((\d+) trades?\)", str(pick.get("reason") or ""))
    return int(m.group(1)) if m else None


def enabled() -> bool:
    if os.getenv("LIVE_SHARP_ENABLED", "").strip().lower() not in {"1", "true", "on", "yes"}:
        return False
    import kalshi_submit
    return kalshi_submit.write_configured()


def et_day(now: datetime) -> str:
    return now.astimezone(EASTERN).strftime("%Y%m%d")


def size(price: float, budget: float | None = None) -> int:
    """Whole contracts whose price total stays at or under the per-bet cap (and the room left today)."""
    cap = per_bet() if budget is None else min(per_bet(), budget)
    if not (0 < price < 1) or cap <= 0:
        return 0
    n = int(math.floor(cap / price + 1e-9))
    # the fee comes on top; just make sure stake + fee still fits what's left today
    while n > 0 and budget is not None and n * price + book.taker_fee(price, n) > budget + 1e-9:
        n -= 1
    return n


def load(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def save(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER
    if not rows and not path.exists():
        return
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(path)


def _spent(rows: list[dict], day: str) -> tuple[float, int]:
    total, n = 0.0, 0
    for r in rows:
        if r.get("day") != day or r.get("status") in {"skipped", "error"}:
            continue
        # an order that never filled cost nothing and isn't a bet, so it doesn't count toward today's orders
        if r.get("status") == "no_fill":
            continue
        n += 1
        # until a fill is known, count the full order at its limit price
        if r.get("status") == "submitted":
            total += (book.f(r.get("contracts_wanted"), 0) or 0) * (book.f(r.get("limit_price"), 0) or 0)
        else:
            total += (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
    return total, n


def _fills_for(client, order_id: str, ticker: str, side: str) -> tuple[float, float, float | None]:
    """(contracts filled, price total, fee total or None) from Kalshi's fills."""
    try:
        fills = client.get_fills(ticker=ticker, limit=100)
    except Exception:
        return 0.0, 0.0, None
    n = cost = 0.0
    fee: float | None = None
    for fl in fills:
        if str(fl.get("order_id") or "") != order_id:
            continue
        c = book.f(fl.get("count_fp") if fl.get("count_fp") is not None else fl.get("count"), 0.0) or 0.0
        p = book.f(fl.get("yes_price_dollars" if side == "YES" else "no_price_dollars"))
        if p is None:
            legacy = book.f(fl.get("yes_price" if side == "YES" else "no_price"))
            p = legacy / 100.0 if legacy is not None and legacy > 1 else legacy
        if c > 0 and p is not None:
            n += c
            cost += c * p
        fc = book.f(fl.get("fee_cost"))
        if fc is not None:
            fee = (fee or 0.0) + fc
    return n, cost, fee


def _record_fill(r: dict, order: dict, client) -> None:
    oid = str(order.get("order_id") or r.get("order_id") or "")
    r["order_id"] = oid
    side = r["side"]
    filled = book.f(order.get("fill_count_fp") if order.get("fill_count_fp") is not None
                    else order.get("fill_count"))
    n, cost, fee = _fills_for(client, oid, r["ticker"], side) if oid else (0.0, 0.0, None)
    if filled is None:
        filled = n
    if filled and not n:
        # fills endpoint lagging: assume the limit price (the worst we could have paid)
        cost = filled * (book.f(r.get("limit_price"), 0.0) or 0.0)
        n = filled
    if n <= 0:
        r.update({"status": "no_fill", "filled": 0, "cost": 0.0, "fee": 0.0, "pl": 0.0,
                  "note": "market moved before the order reached it"})
        return
    avg = cost / n
    if fee is None:
        fee = book.taker_fee(avg, int(round(n)))
    r.update({"status": "open", "filled": round(n, 2), "avg_price": round(avg, 4),
              "cost": round(cost, 2), "fee": round(fee, 2)})


UNCONFIRMED = "Unconfirmed:"
UNCONFIRMED_WINDOW = timedelta(hours=48)


def _is_rejection(exc: Exception) -> bool:
    """Kalshi answered and said no (has an HTTP status). Anything else is an unknown outcome."""
    return getattr(exc, "status_code", None) not in (None, 0)


def _confirm_unknown(r: dict, client, exc: Exception | None = None) -> bool:
    """Ask Kalshi whether an order with this client id exists; record it if so."""
    try:
        found = client.find_order_by_client_id(r["client_order_id"])
    except Exception:
        found = None
    if found:
        order = found.get("order", found) if isinstance(found, dict) else {}
        _record_fill(r, order, client)
        r["note"] = ("Confirmed on Kalshi after an unclear reply. " + str(r.get("note") or "")).strip()
        return True
    if exc is not None:
        r.update({"status": "error", "pl": 0.0,
                  "note": f"{UNCONFIRMED} {type(exc).__name__}: {str(exc)[:140]}"})
    return False


def reconcile_unconfirmed(rows: list[dict], client, *, now: datetime | None = None) -> int:
    """Re-check recent orders whose outcome was unclear (timeout). Returns how many turned up."""
    now = now or book.now_utc()
    found = 0
    for r in rows:
        if r.get("status") != "error" or not str(r.get("note") or "").startswith(UNCONFIRMED):
            continue
        placed = book.parse_dt(r.get("placed_at"))
        if placed is None or now - placed > UNCONFIRMED_WINDOW:
            continue
        if _confirm_unknown(r, client):
            found += 1
    return found


POOL_NAMES = {0: "Default", 1: "Combos", 2: "Crypto & Commodities", 3: "Tennis, Baseball, Basketball"}
WATCH_POOLS = {0, 3}   # where Sharp picks land (most are tennis -> 3)
LOW_POOL = 20.0        # alert below this many dollars


def _read_pools(client) -> tuple[dict[int, float], float | None]:
    pools, total = {}, None
    try:
        if hasattr(client, "get_pool_balances"):
            pools = client.get_pool_balances() or {}
        if hasattr(client, "get_balance_dollars"):
            total = client.get_balance_dollars()
    except Exception as exc:
        print("LIVE_SHARP_BALANCE_ERROR", type(exc).__name__, str(exc)[:120])
    if total is None and pools:
        total = sum(pools.values())
    return pools, total


def _pool_of(client, ticker: str) -> int | None:
    try:
        return client.get_market_pool(ticker) if hasattr(client, "get_market_pool") else None
    except Exception as exc:
        print("LIVE_SHARP_POOL_LOOKUP_ERROR", ticker, type(exc).__name__)
        return None


def _low_pools(pools: dict[int, float], needed: set[int]) -> list[dict]:
    if not pools:
        return []
    return [{"pool": i, "name": POOL_NAMES.get(i, f"Exchange {i}"), "cash": round(pools.get(i, 0.0), 2)}
            for i in sorted(WATCH_POOLS | needed) if pools.get(i, 0.0) < LOW_POOL]


def place(new_paper_rows: list[dict], client, *, now: datetime | None = None,
          rows: list[dict] | None = None) -> dict:
    """Place real orders for the new Sharp paper picks. Returns a summary; never raises on one bad order."""
    now = now or book.now_utc()
    rows = load() if rows is None else rows
    day = et_day(now)
    picks = [p for p in new_paper_rows if p.get("strategy") == "sharp"]
    out = {"placed": 0, "filled": 0, "skipped": {}, "spent_today": 0.0, "new": []}
    # Cash per Kalshi pool (exchange shard). An order is checked only against the cash in its
    # market's pool, and API orders never pull cash across pools, so the total balance can look
    # fine while e.g. the tennis pool is empty. Read every round so low pools get flagged even
    # on rounds with no picks.
    pools, total = _read_pools(client)
    out["cash"] = None if total is None else round(total, 2)
    out["pools"] = {str(k): round(v, 2) for k, v in sorted(pools.items())}
    print("LIVE_SHARP_BALANCE", out["cash"], "POOLS", out["pools"])
    needed: set[int] = set()
    if not picks:
        out["spent_today"] = round(_spent(rows, day)[0], 2)
        out["low_pools"] = _low_pools(pools, needed)
        return out

    spent, n_orders = _spent(rows, day)
    try:
        # Kalshi's record of what today's orders actually cost (filled contracts + fees; an
        # order that missed costs nothing); trust whichever is higher.
        spent = max(spent, client.committed_cost_for_prefix(f"{PREFIX}{day}-"))
    except Exception as exc:
        out["skipped"]["kalshi_unreachable"] = len(picks)
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return out
    seen = {r.get("paper_trade_id") for r in rows}

    def skip(why: str) -> None:
        out["skipped"][why] = out["skipped"].get(why, 0) + 1

    for p in picks:
        if p.get("trade_id") in seen:
            skip("already_placed")
            continue
        n_tr = flow_trades(p)
        if min_trades() and (n_tr is None or n_tr < min_trades()):
            skip("thin_flow")
            continue
        # one real bet per game per day: no re-buying the same market and no taking the other side
        ev = book.event_of(p)
        if any(book.event_of(r) == ev and r.get("status") not in {"error", "skipped", "no_fill"}
               and (r.get("status") == "open"
                    or now - (book.parse_dt(r.get("placed_at")) or now) < timedelta(hours=24))
               for r in rows):
            skip("already_bet_game")
            continue
        room = daily_budget() - spent
        price = book.f(p.get("entry_price"))
        if price is None or not (0 < price < 1):
            skip("bad_price")
            continue
        count = size(price, room)
        if count < 1:
            skip("daily_budget" if room < per_bet() else "too_expensive")
            continue
        # don't send an order the account can't pay for in full (it just errors or fills crumbs);
        # 3% headroom covers Kalshi's fee
        pool = _pool_of(client, p["ticker"]) if pools else None
        if pool is not None:
            needed.add(pool)
        cash = pools.get(pool) if pool is not None else total
        if cash is not None and count * price * 1.03 > cash:
            skip("low_cash")
            continue
        cid = f"{PREFIX}{day}-{p['trade_id']}"
        r = {
            "trade_id": cid, "paper_trade_id": p["trade_id"], "client_order_id": cid,
            "placed_at": book.iso(now), "day": day, "ticker": p["ticker"], "event_ticker": ev, "title": p.get("title", ""),
            "side": p["side"], "limit_price": round(price, 4), "contracts_wanted": count,
            "status": "submitted", "close_time": p.get("close_time", ""), "paper_entry": p.get("entry_price", ""),
        }
        try:
            existing = client.find_order_by_client_id(cid)
            order = existing or client.create_buy_order(
                ticker=p["ticker"], outcome_side=p["side"], count=count, max_price=price,
                execution="TAKE", client_order_id=cid)
            order = order.get("order", order) if isinstance(order, dict) else {}
            if not existing and not order.get("fill_count_fp") and not order.get("fill_count"):
                order = client.find_order_by_client_id(cid) or order
            _record_fill(r, order, client)
        except Exception as exc:
            r.update({"status": "error", "note": f"{type(exc).__name__}: {str(exc)[:160]}", "pl": 0.0})
            if not _is_rejection(exc):
                # A timeout or dropped connection doesn't mean the order failed: Kalshi may
                # have taken it. Look before calling it an error.
                _confirm_unknown(r, client, exc)
            if r["status"] == "error":
                skip("order_error")
        rows.append(r)
        seen.add(p["trade_id"])
        if r["status"] != "error":
            n_orders += 1
            out["placed"] += 1
            spent += (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
            used = (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
            if pool is not None and pool in pools:
                pools[pool] -= used
            if total is not None:
                total -= used
        if r["status"] == "open":
            out["filled"] += 1
            out["new"].append(r)
    out["spent_today"] = round(spent, 2)
    out["low_pools"] = _low_pools(pools, needed)
    return out


def _fill_price(fl: dict, side: str) -> float | None:
    p = book.f(fl.get("yes_price_dollars" if side == "YES" else "no_price_dollars"))
    if p is None:
        legacy = book.f(fl.get("yes_price" if side == "YES" else "no_price"))
        p = legacy / 100.0 if legacy is not None and legacy > 1 else legacy
    return p


def sync_sells(rows: list[dict], client, *, now: datetime | None = None) -> list[dict]:
    """Pick up manual cash-outs: sells on Kalshi of a ticker/side we hold an open live bet on.

    Sells are matched to open bets on the same ticker and side, oldest bet first, and only
    sells made after that bet was placed. A bet sold out completely finishes right away
    (won if the sale beat cost + fees, lost otherwise, result "sold"); a partial sale is
    remembered and added to the P/L when the market settles. Returns the bets that just finished.
    """
    now = now or book.now_utc()
    open_rows = [r for r in rows if r.get("status") == "open"]
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in open_rows:
        groups.setdefault((r["ticker"], r["side"]), []).append(r)
    done = []
    fills_by_ticker: dict[str, list[dict]] = {}
    # Our own buy orders are never cash-outs. A NO buy goes to Kalshi as an ask on the YES
    # book, and its fill can come back looking like a sell, which used to "cash out" every
    # NO bet a couple of minutes after it was placed, at its own entry price.
    own_orders = {str(r.get("order_id")) for r in rows if r.get("order_id")}
    for (ticker, side), group in groups.items():
        if ticker not in fills_by_ticker:
            try:
                fills_by_ticker[ticker] = client.get_fills(ticker=ticker, limit=200)
            except Exception:
                fills_by_ticker[ticker] = []
        group.sort(key=lambda r: str(r.get("placed_at") or ""))
        first = book.parse_dt(group[0].get("placed_at"))
        sells = []
        for fl in fills_by_ticker[ticker]:
            if fl.get("ticker") and fl.get("ticker") != ticker:
                continue
            if fl.get("order_id") and str(fl.get("order_id")) in own_orders:
                continue
            if str(fl.get("action") or "").lower() != "sell" or str(fl.get("side") or "").upper() != side:
                continue
            when = book.parse_dt(fl.get("created_time"))
            if first and when and when < first:
                continue
            c = book.f(fl.get("count_fp") if fl.get("count_fp") is not None else fl.get("count"), 0.0) or 0.0
            p = _fill_price(fl, side)
            if c > 0 and p is not None:
                sells.append((when, c, p, book.f(fl.get("fee_cost"))))
        sells.sort(key=lambda x: x[0] or now)
        # hand sold contracts to the bets oldest-first, never more than each bet holds
        pool = list(sells)
        for r in group:
            placed = book.parse_dt(r.get("placed_at"))
            held = book.f(r.get("filled"), 0.0) or 0.0
            n = proceeds = fee = 0.0
            last = None
            while pool and n < held - 1e-9:
                when, c, p, fc = pool[0]
                if placed and when and when < placed:
                    pool.pop(0)
                    continue
                take = min(c, held - n)
                share = take / c
                n += take
                proceeds += take * p
                fee += (fc * share) if fc is not None else book.taker_fee(p, max(1, int(round(take))))
                last = when
                if take >= c - 1e-9:
                    pool.pop(0)
                else:
                    pool[0] = (when, c - take, p, (fc * (1 - share)) if fc is not None else None)
            if n <= 0:
                continue
            r.update({"sold": round(n, 2), "sale_proceeds": round(proceeds, 4), "sale_fee": round(fee, 4)})
            if n >= held - 1e-9:
                cost = book.f(r.get("cost"), 0.0) or 0.0
                buy_fee = book.f(r.get("fee"), 0.0) or 0.0
                pl = round(proceeds - fee - cost - buy_fee, 2)
                avg_sale = proceeds / n
                r.update({"status": "won" if pl > 0 else "lost", "result": "sold",
                          "settled_at": book.iso(last or now), "pl": pl,
                          "note": f"cashed out at {avg_sale * 100:.0f}¢"})
                avg = book.f(r.get("avg_price"))
                if avg is not None:
                    r["clv_cents"] = round((avg_sale - avg) * 100.0, 2)
                done.append(r)
    return done


def settle(rows: list[dict], fetch_markets, *, now: datetime | None = None) -> list[dict]:
    """Settle open live bets from Kalshi's market result. Returns the rows that just finished."""
    now = now or book.now_utc()
    open_rows = [r for r in rows if r.get("status") == "open"]
    if not open_rows:
        return []
    markets = fetch_markets(sorted({r["ticker"] for r in open_rows}))
    done = []
    for r in open_rows:
        m = markets.get(r["ticker"])
        if not m:
            continue
        result = str(m.get("result") or "").lower()
        status = str(m.get("status") or "").lower()
        r["_market_status"] = status
        if m.get("expected_expiration_time"):
            r["ends_at"] = m["expected_expiration_time"]
        cost = book.f(r.get("cost"), 0.0) or 0.0
        fee = book.f(r.get("fee"), 0.0) or 0.0
        if result in {"yes", "no"}:
            won = (result == "yes") == (r["side"] == "YES")
            n = book.f(r.get("filled"), 0.0) or 0.0
            # contracts already cashed out don't pay at settlement; their sale does
            sold = min(n, book.f(r.get("sold"), 0.0) or 0.0)
            sale = (book.f(r.get("sale_proceeds"), 0.0) or 0.0) - (book.f(r.get("sale_fee"), 0.0) or 0.0)
            r.update({"status": "won" if won else "lost", "result": result, "settled_at": book.iso(now),
                      "pl": round(((n - sold) if won else 0.0) + sale - cost - fee, 2)})
            mark, avg = book.f(r.get("last_mid")), book.f(r.get("avg_price"))
            if mark is not None and avg is not None:
                r["clv_cents"] = round((mark - avg) * 100.0, 2)
            done.append(r)
        elif (pay := book.scalar_payout(m, r["side"])) is not None:
            # no-result game: Kalshi paid each contract a partial amount instead of $0/$1
            n = book.f(r.get("filled"), 0.0) or 0.0
            sold = min(n, book.f(r.get("sold"), 0.0) or 0.0)
            sale = (book.f(r.get("sale_proceeds"), 0.0) or 0.0) - (book.f(r.get("sale_fee"), 0.0) or 0.0)
            pl = round((n - sold) * pay + sale - cost - fee, 2)
            r.update({"status": "won" if pl > 0 else "lost" if pl < 0 else "void", "result": "scalar",
                      "settled_at": book.iso(now), "pl": pl,
                      "note": f"No result - Kalshi settled at {pay * 100:.0f}c per contract"})
            done.append(r)
        elif result in {"void", "voided", "all_no_void"} or status in {"voided", "canceled", "cancelled"}:
            r.update({"status": "void", "result": result or status, "settled_at": book.iso(now), "pl": 0.0})
            done.append(r)
        else:
            # still trading: mark to the current mid, same as the paper bets
            close = book.parse_dt(r.get("close_time")) or book.parse_dt(m.get("close_time"))
            mid = book.side_mid(m, r["side"])
            if mid is not None and status in {"open", "active", ""} and (close is None or now < close):
                r["last_mid"] = round(mid, 4)
                r["last_mid_at"] = book.iso(now)
                placed = book.parse_dt(r.get("placed_at"))
                avg = book.f(r.get("avg_price"))
                if (r.get("drift_cents") in (None, "") and placed and avg is not None
                        and now - placed >= DRIFT_AFTER):
                    r["drift_cents"] = round((mid - avg) * 100.0, 2)
                    r["drift_at"] = book.iso(now)
    return done


def refresh(rows: list[dict], fetch_markets, *, now: datetime | None = None) -> dict:
    """In-memory re-mark for the app's Refresh button; nothing is written."""
    import copy
    now = now or book.now_utc()
    work = copy.deepcopy(rows)
    n_open = sum(1 for r in work if r.get("status") == "open")
    done = settle(work, fetch_markets, now=now)
    stamp = book.iso(now)
    opn = [r for r in work if r.get("status") == "open"]
    marked = sum(1 for r in opn if r.get("last_mid_at") == stamp)
    found = sum(1 for r in opn if r.get("_market_status") is not None) + len(done)
    waiting = sum(1 for r in opn if r.get("_market_status") is not None and r.get("last_mid_at") != stamp)
    return {"rows": work, "marked": marked, "settled": len(done), "open": n_open, "found": found,
            "waiting": waiting, "checked_at": stamp}


def summary(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or book.now_utc()
    day = et_day(now)
    done = [r for r in rows if r.get("status") in {"won", "lost"}]
    staked = sum((book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0) for r in done)
    pl = sum(book.f(r.get("pl"), 0.0) or 0.0 for r in done)
    spent, n = _spent(rows, day)
    return {"won": sum(r["status"] == "won" for r in done), "lost": sum(r["status"] == "lost" for r in done),
            "open": sum(r.get("status") == "open" for r in rows), "pl": round(pl, 2), "staked": round(staked, 2),
            "roi": pl / staked if staked else None, "spent_today": round(spent, 2), "orders_today": n,
            "budget": daily_budget(), "no_fill": sum(r.get("status") == "no_fill" for r in rows)}


def client_from_env():
    import kalshi_submit
    c = kalshi_submit.KalshiSubmitClient.from_env()
    return c


def daily(rows: list[dict]) -> list[dict]:
    """Per Eastern day, oldest first: bets settled that day (record, P/L, ROI, running total)
    and orders sent that day (filled, missed because the price moved, blocked by Kalshi)."""
    days: dict[str, dict] = {}

    def row(day: str) -> dict:
        return days.setdefault(day, {"day": day, "won": 0, "lost": 0, "pl": 0.0, "staked": 0.0,
                                     "orders": 0, "filled": 0, "missed": 0, "blocked": 0})

    for r in rows:
        st = r.get("status")
        if st in {"won", "lost"}:
            when = book.parse_dt(r.get("settled_at"))
            if when:
                d = row(et_day(when))
                d[st] += 1
                d["pl"] += book.f(r.get("pl"), 0.0) or 0.0
                d["staked"] += (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
        if r.get("day") and st != "skipped":
            d = row(str(r["day"]))
            d["orders"] += 1
            if st == "no_fill":
                d["missed"] += 1
            elif st == "error":
                if "not_currently_allowed" in str(r.get("note") or ""):
                    d["blocked"] += 1
            elif st != "submitted":
                d["filled"] += 1
    out, total = [], 0.0
    for day in sorted(days):
        d = days[day]
        total += d["pl"]
        d["pl"] = round(d["pl"], 2)
        d["roi"] = d["pl"] / d["staked"] if d["staked"] else None
        d["total"] = round(total, 2)
        out.append(d)
    return out


def by_sport(rows: list[dict]) -> list[dict]:
    """Real-money bets grouped by sport (same labels as the paper "Sharp by market"),
    biggest group first: record, P/L, ROI on settled bets, plus how many are still open."""
    from league.scorecard import market_group
    groups: dict[str, dict] = {}
    for r in rows:
        st = r.get("status")
        if st not in {"won", "lost", "open"}:
            continue
        name = market_group(r.get("event_ticker") or r.get("ticker") or "")
        g = groups.setdefault(name, {"group": name, "won": 0, "lost": 0, "open": 0, "pl": 0.0, "staked": 0.0})
        if st == "open":
            g["open"] += 1
            continue
        g[st] += 1
        g["pl"] += book.f(r.get("pl"), 0.0) or 0.0
        g["staked"] += (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
    out = []
    for g in groups.values():
        g["bets"] = g["won"] + g["lost"]
        g["pl"] = round(g["pl"], 2)
        g["staked"] = round(g["staked"], 2)
        g["roi"] = g["pl"] / g["staked"] if g["staked"] else None
        out.append(g)
    return sorted(out, key=lambda g: (-g["bets"], -g["open"], g["group"]))


def _mean_ci(xs: list[float]) -> tuple[float | None, float | None]:
    """Mean and 95% half-width (normal approximation)."""
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return m, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m, 1.96 * sd / math.sqrt(len(xs))


# (bets needed, what to step up to, whether the low end of the ROI range must clear 0)
STAGES = [(500, "$3–5", False), (1000, "$5–10", True)]
RECENT = 250
# Goals a real edge should hit. ROI must clear Kalshi's fees with room to spare; drift of +1¢
# on ~50¢ contracts is about a 2% edge, the usual bar for "beating the close".
TARGET_ROI = 0.03
TARGET_DRIFT = 1.0      # cents
DRIFT_MIN_BETS = 100    # drift only counts toward a step once this many bets have a reading


def scorecard(rows: list[dict]) -> dict:
    """Is the live edge proven enough to raise the bet size? ROI and price drift with 95% ranges.

    ROI is per-bet return weighted by stake. A step up needs the bet count AND positive results;
    a negative last-RECENT-bets ROI says step back down."""
    done = [r for r in rows if r.get("status") in {"won", "lost"}]
    done.sort(key=lambda r: str(r.get("settled_at") or r.get("placed_at") or ""))
    rets, staked, pl = [], 0.0, 0.0
    for r in done:
        c = (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
        if c <= 0:
            continue
        p = book.f(r.get("pl"), 0.0) or 0.0
        staked, pl = staked + c, pl + p
        rets.append((c, p))
    n = len(rets)
    n_settled = len(done)  # includes crumb fills too small to carry a cost, so it matches the record
    roi = pl / staked if staked else None
    # spread of per-bet returns around the stake-weighted ROI
    half = None
    if n >= 2 and staked:
        mc = staked / n
        var = sum(((p - roi * c) / mc) ** 2 for c, p in rets) / (n - 1)
        half = 1.96 * math.sqrt(var / n)
    # drift is read ~30 min after the buy, so it doesn't wait for the bet to settle
    drift_rows = [r for r in rows if r.get("status") in {"open", "won", "lost"}]
    drift_m, drift_h = _mean_ci([d for r in drift_rows if (d := book.f(r.get("drift_cents"))) is not None])
    n_drift = sum(1 for r in drift_rows if book.f(r.get("drift_cents")) is not None)
    recent = rets[-RECENT:]
    recent_roi = (sum(p for _, p in recent) / sum(c for c, _ in recent)) if len(recent) >= RECENT else None

    stage, nxt = 0, None
    for i, (need, size, need_lo) in enumerate(STAGES):
        drift_ok = drift_m is None or n_drift < DRIFT_MIN_BETS or drift_m >= TARGET_DRIFT
        lo_ok = (not need_lo) or (roi is not None and half is not None and roi - half > 0)
        if n_settled >= need and roi is not None and roi >= TARGET_ROI and drift_ok and lo_ok:
            stage = i + 1
            continue
        nxt = {"need": need, "size": size, "to_go": max(0, need - n_settled), "need_lo": need_lo}
        break

    if recent_roi is not None and recent_roi < 0:
        verdict, tone = f"Last {RECENT} bets are negative: step back a size or hold.", "bad"
    elif stage and nxt is None:
        verdict, tone = f"Cleared every checkpoint — {STAGES[-1][1]} is supported.", "good"
    elif stage:
        verdict, tone = f"Cleared {STAGES[stage - 1][0]} bets — {STAGES[stage - 1][1]} is supported.", "good"
    elif nxt and nxt["to_go"] > 0:
        verdict, tone = f"Not proven yet — {nxt['to_go']} more settled bets to the first checkpoint.", "wait"
    else:
        verdict, tone = (f"Enough bets, but ROI or drift is under its goal "
                         f"(+{TARGET_ROI * 100:.0f}% / +{TARGET_DRIFT:.0f}¢) — hold at $2."), "bad"
    return {"n": n_settled, "n_priced": n, "staked": round(staked, 2), "pl": round(pl, 2), "roi": roi, "roi_half": half,
            "drift": drift_m, "drift_half": drift_h, "n_drift": n_drift, "recent_roi": recent_roi,
            "stage": stage, "next": nxt, "verdict": verdict, "tone": tone,
            "target_roi": TARGET_ROI, "target_drift": TARGET_DRIFT,
            "roi_hit": roi is not None and roi >= TARGET_ROI,
            "drift_hit": drift_m is not None and drift_m >= TARGET_DRIFT}


def vs_paper(rows: list[dict], paper_rows: list[dict]) -> dict | None:
    """Live vs paper Sharp over the same window: since the first live order.

    Paper's all-time record includes days before live existed, so comparing live to it
    makes live look worse than it is. Splits paper's settled Sharp bets since live started
    into the ones live copied and the ones it couldn't (no fill, blocked, skipped), and puts
    the live result on the copied ones beside them. ROI is P/L over stake incl. fees.
    """
    starts = [str(r.get("placed_at")) for r in rows if r.get("placed_at")]
    if not starts:
        return None
    start = min(starts)
    copied = {str(r.get("paper_trade_id")): r for r in rows
              if r.get("status") in {"won", "lost", "open"} and r.get("paper_trade_id")}

    def group(name):
        return {"group": name, "won": 0, "lost": 0, "pl": 0.0, "staked": 0.0}

    out = {"since": start, "paper": group("Paper, all picks"), "paper_copied": group("Paper, same bets"),
           "live": group("Live, same bets"), "paper_missed": group("Paper, bets live couldn't place")}

    def add(g, status, pl, staked):
        g[status] += 1
        g["pl"] += pl
        g["staked"] += staked

    for p in paper_rows:
        opened = p.get("opened_at")
        if p.get("strategy") != "sharp" or not isinstance(opened, str) or opened < start:
            continue
        st = p.get("status")
        if st not in {"won", "lost"}:
            continue
        pl = book.f(p.get("pl"), 0.0) or 0.0
        staked = (book.f(p.get("cost"), 0.0) or 0.0) + (book.f(p.get("fee"), 0.0) or 0.0)
        add(out["paper"], st, pl, staked)
        live = copied.get(str(p.get("trade_id")))
        if live is None:
            add(out["paper_missed"], st, pl, staked)
            continue
        add(out["paper_copied"], st, pl, staked)
        if live.get("status") in {"won", "lost"}:
            add(out["live"], live["status"], book.f(live.get("pl"), 0.0) or 0.0,
                (book.f(live.get("cost"), 0.0) or 0.0) + (book.f(live.get("fee"), 0.0) or 0.0))
    for k in ("paper", "paper_copied", "live", "paper_missed"):
        g = out[k]
        g["bets"] = g["won"] + g["lost"]
        g["pl"] = round(g["pl"], 2)
        g["staked"] = round(g["staked"], 2)
        g["roi"] = g["pl"] / g["staked"] if g["staked"] else None
    return out


# Kalshi rejects sports orders sent from Washington state, and GitHub's runners sometimes land
# there. Those orders are final: we do not resend them from another location, because that
# would be routing around an exchange restriction. They show up as errors in the ledger.
