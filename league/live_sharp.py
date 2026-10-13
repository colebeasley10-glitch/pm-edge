"""Live $1 Sharp: copy each new Sharp paper pick with a real Kalshi order.

Real money, so every limit is enforced here in code, not just in config:

* at most $1.00 of contracts per bet (fee on top, a few cents);
* at most $30.00 spent per Eastern day, fees included, and at most 30 orders;
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
CEIL_DAILY = 30.00
CEIL_ORDERS = 30

FIELDS = [
    "trade_id", "paper_trade_id", "client_order_id", "placed_at", "day", "ticker", "title", "side",
    "limit_price", "contracts_wanted", "status", "order_id", "filled", "avg_price", "cost", "fee",
    "close_time", "result", "settled_at", "pl", "paper_entry", "note",
    "ends_at", "last_mid", "last_mid_at", "clv_cents",
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


def max_orders() -> int:
    return int(_env_float("LIVE_SHARP_MAX_ORDERS", CEIL_ORDERS, CEIL_ORDERS))


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
        # Kalshi's record of what we've already sent today; trust whichever is higher.
        spent = max(spent, client.submitted_cost_for_prefix(f"{PREFIX}{day}-"))
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
        # one real bet per market per day, even if Sharp keeps re-picking it
        if any(r.get("ticker") == p.get("ticker") and r.get("status") not in {"error", "skipped", "no_fill"}
               and (r.get("status") == "open"
                    or now - (book.parse_dt(r.get("placed_at")) or now) < timedelta(hours=24))
               for r in rows):
            skip("already_bet_market")
            continue
        if n_orders >= max_orders():
            skip("daily_order_cap")
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
            "placed_at": book.iso(now), "day": day, "ticker": p["ticker"], "title": p.get("title", ""),
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
        if m.get("expected_expiration_time"):
            r["ends_at"] = m["expected_expiration_time"]
        cost = book.f(r.get("cost"), 0.0) or 0.0
        fee = book.f(r.get("fee"), 0.0) or 0.0
        if result in {"yes", "no"}:
            won = (result == "yes") == (r["side"] == "YES")
            n = book.f(r.get("filled"), 0.0) or 0.0
            r.update({"status": "won" if won else "lost", "result": result, "settled_at": book.iso(now),
                      "pl": round((n if won else 0.0) - cost - fee, 2)})
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
    marked = sum(1 for r in work if r.get("status") == "open" and r.get("last_mid_at") == book.iso(now))
    return {"rows": work, "marked": marked, "settled": len(done), "open": n_open, "checked_at": book.iso(now)}


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
