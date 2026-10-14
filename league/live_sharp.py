"""Live $1 Sharp: copy each new Sharp paper pick with a real Kalshi order.

Real money, so every limit is enforced here in code, not just in config:

* at most $1.00 of contracts per bet (fee on top, a few cents);
* at most $50.00 spent per Eastern day, fees included (no limit on the number of bets);
* the daily spend is the larger of this ledger and Kalshi's own order history,
  so a lost ledger commit can't double the budget;
* orders are immediate-or-cancel at the price the paper pick saw, so we never
  chase a moved market and nothing rests on the book;
* client_order_id is derived from the paper trade id, so a retried round
  can't place the same bet twice.

Off unless LIVE_SHARP_ENABLED=1 and Kalshi write keys are present.
"""
from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from league import book

EASTERN = ZoneInfo("America/New_York")
LEDGER = Path(os.getenv("LIVE_SHARP_LEDGER_PATH", "live_sharp_trades.csv"))
PREFIX = "pmls-"

# Hard ceilings. Env can lower these, never raise them.
CEIL_PER_BET = 1.00
CEIL_DAILY = 50.00

FIELDS = [
    "trade_id", "paper_trade_id", "client_order_id", "placed_at", "day", "ticker", "title", "side",
    "limit_price", "contracts_wanted", "status", "order_id", "filled", "avg_price", "cost", "fee",
    "close_time", "result", "settled_at", "pl", "paper_entry", "note",
    "ends_at", "last_mid", "last_mid_at", "clv_cents", "event_ticker",
    "sold", "sale_proceeds", "sale_fee",
]


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


def place(new_paper_rows: list[dict], client, *, now: datetime | None = None,
          rows: list[dict] | None = None) -> dict:
    """Place real orders for the new Sharp paper picks. Returns a summary; never raises on one bad order."""
    now = now or book.now_utc()
    rows = load() if rows is None else rows
    day = et_day(now)
    picks = [p for p in new_paper_rows if p.get("strategy") == "sharp"]
    out = {"placed": 0, "filled": 0, "skipped": {}, "spent_today": 0.0, "new": []}
    if not picks:
        out["spent_today"] = round(_spent(rows, day)[0], 2)
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
            skip("order_error")
        rows.append(r)
        seen.add(p["trade_id"])
        if r["status"] != "error":
            n_orders += 1
            out["placed"] += 1
            spent += (book.f(r.get("cost"), 0.0) or 0.0) + (book.f(r.get("fee"), 0.0) or 0.0)
        if r["status"] == "open":
            out["filled"] += 1
            out["new"].append(r)
    out["spent_today"] = round(spent, 2)
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
