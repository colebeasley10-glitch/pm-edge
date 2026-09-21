from __future__ import annotations
from .kalshi import normalize_market


def refresh_paper_orders(store, kalshi_client) -> dict:
    fills = 0
    resolutions = 0

    for order in store.open_paper_orders():
        market = normalize_market(kalshi_client.get_market(order["ticker"]))
        ask = market["yes_ask"] if order["side"] == "YES" else market["no_ask"]
        # Conservative touch-fill assumption: the opposite quote must trade through/touch our limit.
        if ask is not None and ask <= float(order["limit_price"]) + 1e-9:
            store.mark_paper_filled(order["id"], float(order["limit_price"]))
            fills += 1

    for order in store.unresolved_filled_orders():
        market = normalize_market(kalshi_client.get_market(order["ticker"]))
        result = str(market.get("result") or "").lower()
        if result not in {"yes", "no"}:
            continue
        side_won = (order["side"].lower() == result)
        payout = 1.0 if side_won else 0.0
        fill_price = float(order["fill_price"])
        fee = float(order["fee_per_contract"])
        pnl = int(order["contracts"]) * (payout - fill_price - fee)
        store.resolve_order(order["id"], result, pnl)
        resolutions += 1

    return {"paper_fills": fills, "paper_resolutions": resolutions}
