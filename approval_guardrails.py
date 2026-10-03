from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
DAILY_RISK_BUDGET_DOLLARS = 75.0


def event_key(ticker: str) -> str:
    """Collapse Kalshi game/spread/total tickers to the shared event segment."""
    parts = [p for p in str(ticker or "").strip().upper().split("-") if p]
    if len(parts) >= 3:
        return "-".join(parts[1:-1])
    if len(parts) == 2:
        return parts[1]
    return parts[0] if parts else ""


def sport_prefix(ticker: str) -> str:
    head = str(ticker or "").strip().upper().split("-", 1)[0]
    return re.sub(r"(?:TEAMTOTAL|SPREAD|TOTAL|MONEYLINE|GAME|ML)$", "", head)


def market_family(ticker: str) -> str:
    head = str(ticker or "").strip().upper().split("-", 1)[0]
    if "SPREAD" in head:
        return "spread"
    if "TOTAL" in head:
        return "total"
    return "other"


def _position_side(row: dict) -> str:
    try:
        value = float(row.get("position_fp") if row.get("position_fp") is not None else row.get("position") or 0)
    except (TypeError, ValueError):
        return ""
    if value > 0:
        return "YES"
    if value < 0:
        return "NO"
    return ""


def _order_side(row: dict) -> str:
    side = str(row.get("outcome_side") or row.get("side") or "").upper()
    if side in {"YES", "NO"}:
        return side
    # Kalshi's event-order endpoint represents PM Edge BUY YES as a bid and
    # BUY NO as an ask on the YES book. Map those wire-level sides back to the
    # exposure side so resting maker orders participate in duplicate guards.
    if side == "BID":
        return "YES"
    if side == "ASK":
        return "NO"
    return ""


def _order_is_live_exposure(row: dict) -> bool:
    """True for an order that should prevent a duplicate PM Edge submission."""
    status = str(row.get("status") or row.get("order_status") or "").strip().lower()
    if status in {"canceled", "cancelled", "expired", "rejected", "failed"}:
        return False
    # If Kalshi omits status in a response, a concrete order id/client id is
    # still enough evidence that the exposure was already submitted.
    return bool(
        status
        or str(row.get("order_id") or row.get("kalshi_order_id") or "").strip()
        or str(row.get("client_order_id") or "").strip()
    )


def _created_today(row: dict, now: datetime) -> bool:
    raw = str(row.get("created_time") or row.get("created_at") or "").strip()
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return True
        return dt.astimezone(EASTERN).date() == now.astimezone(EASTERN).date()
    except Exception:
        return True


def _order_cost(row: dict) -> float:
    try:
        count = float(row.get("initial_count_fp") or row.get("initial_count") or row.get("count") or 0)
    except (TypeError, ValueError):
        return 0.0
    side = _order_side(row)
    try:
        price = float(row.get("yes_price_dollars") or 0) if side == "YES" else float(row.get("no_price_dollars") or 0)
    except (TypeError, ValueError):
        return 0.0
    return count * price if count > 0 and 0 < price < 1 else 0.0


def evaluate_position_guard(
    *,
    ticker: str,
    side: str,
    candidate_risk: float,
    positions: list[dict],
    orders: list[dict],
    now: datetime,
    daily_risk_budget: float = DAILY_RISK_BUDGET_DOLLARS,
) -> dict:
    ticker = str(ticker or "").strip().upper()
    side = str(side or "").strip().upper()
    candidate_event = event_key(ticker)
    candidate_family = market_family(ticker)
    candidate_sport = sport_prefix(ticker)

    open_pairs: set[tuple[str, str]] = set()
    for row in positions or []:
        pos_ticker = str(row.get("ticker") or row.get("market_ticker") or "").strip().upper()
        pos_side = _position_side(row)
        if pos_ticker and pos_side:
            open_pairs.add((pos_ticker, pos_side))

    same_pair_open = (ticker, side) in open_pairs
    if same_pair_open:
        return {"blocked": True, "reason": "same_position_exists"}

    active_order_pairs: set[tuple[str, str]] = set()
    for row in orders or []:
        if not _order_is_live_exposure(row):
            continue
        ot = str(row.get("ticker") or row.get("market_ticker") or "").strip().upper()
        oside = _order_side(row)
        if ot and oside:
            active_order_pairs.add((ot, oside))
    if (ticker, side) in active_order_pairs:
        return {"blocked": True, "reason": "same_order_exists"}

    for pos_ticker, pos_side in open_pairs:
        if event_key(pos_ticker) != candidate_event:
            continue
        if pos_ticker == ticker and pos_side == side:
            continue
        if (
            candidate_family in {"spread", "total"}
            and market_family(pos_ticker) == candidate_family
            and pos_side == side
        ):
            return {"blocked": True, "reason": "second_ladder_rung"}
        return {"blocked": True, "reason": "event_position_exists"}

    todays_pm_orders = [
        row for row in (orders or [])
        if str(row.get("client_order_id") or "").startswith("pmedge-")
        and _created_today(row, now)
    ]
    open_today_pairs: set[tuple[str, str]] = set()
    total_open_risk = 0.0
    sport_open_risk = 0.0

    for row in todays_pm_orders:
        ot = str(row.get("ticker") or row.get("market_ticker") or "").strip().upper()
        oside = _order_side(row)
        pair = (ot, oside)
        if not ot or not oside or pair not in open_pairs:
            continue
        open_today_pairs.add(pair)
        risk = _order_cost(row)
        total_open_risk += risk
        if sport_prefix(ot) == candidate_sport:
            sport_open_risk += risk

    sport_pairs = {pair for pair in open_today_pairs if sport_prefix(pair[0]) == candidate_sport}

    projected_total_risk = total_open_risk + max(0.0, float(candidate_risk))
    if projected_total_risk > daily_risk_budget + 1e-9:
        return {"blocked": True, "reason": "daily_open_risk_cap"}

    return {
        "blocked": False,
        "reason": "ok",
        "open_risk": round(total_open_risk, 6),
        "sport_open_risk": round(sport_open_risk, 6),
        "sport_open_positions": len(sport_pairs),
    }
