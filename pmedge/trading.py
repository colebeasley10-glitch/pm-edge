from __future__ import annotations
from dataclasses import dataclass

from .kalshi import KalshiAuthClient, KalshiError, normalize_market


@dataclass(frozen=True)
class LiveOrderPlan:
    ticker: str
    side: str
    book_side: str
    submitted_price: float
    contracts: int
    post_only: bool
    time_in_force: str


def signal_to_order_plan(signal: dict) -> LiveOrderPlan:
    side = signal["side"]
    price = float(signal["entry_price"])
    if side == "YES":
        book_side = "bid"
        submitted_price = price
    elif side == "NO":
        # Kalshi V2 quotes event orders on the YES book: buying NO at N is asking YES at 1-N.
        book_side = "ask"
        submitted_price = 1.0 - price
    else:
        raise ValueError("Signal side must be YES or NO")

    is_maker = signal["execution"] == "MAKE"
    return LiveOrderPlan(
        signal["ticker"], side, book_side, submitted_price, int(signal["contracts"]),
        is_maker, "good_till_canceled" if is_maker else "immediate_or_cancel",
    )


def execute_signal_live(signal_id: int, settings, store):
    if not settings.live_enabled:
        raise KalshiError(
            "Live trading is not armed. Set PM_EDGE_MODE=live and "
            "PM_EDGE_LIVE_ARM_TOKEN=ARM_LIVE_TRADING only after paper validation."
        )

    signal = store.get_signal(signal_id)
    if not signal:
        raise KalshiError(f"Signal {signal_id} not found")
    if int(signal.get("contracts") or 0) <= 0:
        raise KalshiError("Signal has no approved contract size")

    signal_cost = float(signal.get("est_cost") or 0.0)
    if store.risk_cost_today() + signal_cost > settings.bankroll_dollars * settings.max_daily_pct + 1e-9:
        raise KalshiError("This order would exceed the daily risk cap")
    if store.risk_cost_event(signal.get("event_ticker") or "") + signal_cost > settings.bankroll_dollars * settings.max_event_pct + 1e-9:
        raise KalshiError("This order would exceed the event risk cap")

    client = KalshiAuthClient(
        settings.kalshi_base_url, settings.kalshi_api_key_id,
        settings.kalshi_private_key_path,
    )
    current = normalize_market(client.get_market(signal["ticker"]))
    entry = float(signal["entry_price"])
    hard_max = float(signal["hard_max"])
    if entry > hard_max + 1e-9:
        raise KalshiError("Stored entry exceeds hard max; refusing live order")

    # Reject stale TAKE signals if current ask has moved above hard max.
    if signal["execution"] == "TAKE":
        live_ask = current["yes_ask"] if signal["side"] == "YES" else current["no_ask"]
        if live_ask is None or live_ask > hard_max + 1e-9:
            raise KalshiError("Market moved beyond hard max; refusing stale taker order")
        entry = live_ask
        signal = dict(signal)
        signal["entry_price"] = entry

    plan = signal_to_order_plan(signal)
    response = client.place_event_order(
        ticker=plan.ticker,
        side=plan.book_side,
        count=plan.contracts,
        price=plan.submitted_price,
        post_only=plan.post_only,
        time_in_force=plan.time_in_force,
    )
    store.add_live_order(signal_id, {
        "ticker": plan.ticker, "side": plan.side, "book_side": plan.book_side,
        "submitted_price": plan.submitted_price, "contracts": plan.contracts,
    }, response)
    return response
