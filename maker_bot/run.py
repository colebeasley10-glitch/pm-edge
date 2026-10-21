"""One Maker Bot round: python -m maker_bot.run

Live only when MAKER_BOT_ENABLED=1 and Kalshi write keys are present; otherwise a dry
run that logs the orders it would have posted (mode=dry, no keys touched).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from maker_bot import core


def kalshi_committed_today(orders: list[dict], rows: list[dict], now: datetime) -> float:
    """Today's spend per Kalshi's own order history (resting at full price, finished at filled count).
    Orders we have no ledger row for count at the full per-order cap, so this never undercounts."""
    import kalshi_submit
    by_cid = {r["client_order_id"]: r for r in rows}
    day, total = core.et_day(now), 0.0
    for o in orders:
        cid = str(o.get("client_order_id") or "")
        if not cid.startswith(core.PREFIX):
            continue
        ts = kalshi_submit._order_epoch(o)
        if ts is None or core.et_day(datetime.fromtimestamp(ts, timezone.utc)) != day:
            continue
        status = str(o.get("status") or "").lower()
        done = status in {"canceled", "cancelled", "executed", "filled", "expired"}
        initial = core.f(o.get("initial_count_fp") or o.get("initial_count") or o.get("count"), 0.0) or 0.0
        count = core._order_fill(o) if done else initial
        r = by_cid.get(cid)
        if r is None:
            total += core.CEIL_PER_ORDER if count > 0 or not done else 0.0
            continue
        price = core.f(r.get("price"), 0.0)
        if count > 0:
            total += count * price + core.maker_fee(price, int(round(count)))
    return round(total, 2)


def main() -> dict:
    now = datetime.now(timezone.utc)
    rows = core.load()
    live = core.live_enabled()
    mode = "live" if live else "dry"
    status: dict = {"at": core.iso(now), "mode": mode, "unit": core.unit(),
                    "daily_budget": core.daily_budget()}

    client, orders = None, []
    if live:
        from kalshi_submit import KalshiSubmitClient
        client = KalshiSubmitClient.from_env()
        orders = client.get_all_orders(lookback_hours=72)
        status["reconcile"] = core.reconcile(rows, orders, now)

    # settle filled bets from Kalshi's official results (public data, no keys)
    open_tickers = sorted({r["ticker"] for r in rows if r.get("status") == core.FILLED})
    if open_tickers:
        from research_lab.public_data import PublicKalshi
        try:
            markets = PublicKalshi().markets(open_tickers)
            status["marked"] = core.mark(rows, markets, now)      # markout before the result
            status["settled"] = core.settle(rows, markets, now)
        except Exception as exc:
            status["settle_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    candidates, why = core.load_candidates(now)
    status["candidates"] = len(candidates)
    status["candidates_note"] = why
    picked, skips = core.plan(candidates, rows, now, mode)
    if live:
        # never trust the ledger alone for money: stop if Kalshi shows more spent today
        ledger_today = core.committed_today(rows, core.et_day(now))
        kalshi_today = kalshi_committed_today(orders, rows, now)
        status["spent_today"] = {"ledger": ledger_today, "kalshi": kalshi_today}
        room = core.daily_budget() - max(ledger_today, kalshi_today)
        keep = []
        for c in picked:
            need = c["contracts"] * c["price"] + core.maker_fee(c["price"], c["contracts"])
            if need <= room + 1e-9:
                keep.append(c)
                room -= need
            else:
                skips["daily_budget"] = skips.get("daily_budget", 0) + 1
        picked = keep

    posted = []
    for c in picked:
        row = core.new_row(c, now, mode)
        if live:
            core.place(client, c, row)
        rows.append(row)
        posted.append(f'{row["label"]}: {row["contracts"]} @ {float(row["price"]) * 100:.0f}¢ '
                      f'(fair {float(row["fair"]) * 100:.1f}¢, edge {float(row["edge"]) * 100:.1f}%'
                      f'{", sharp" if row["sharp"] else ""}) until {row["expires_at"][11:16]}Z')
    status.update(posted=len(posted), orders=posted, skips=skips,
                  summary={"live": core.summarize(rows, "live"), "dry": core.summarize(rows, "dry")})
    core.save(rows)
    core.STATUS.write_text(json.dumps(status, indent=2, default=str))
    print(json.dumps(status, indent=2, default=str))
    return status


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"::error title=Maker Bot failed::{type(exc).__name__}: {str(exc)[:400]}")
        raise
