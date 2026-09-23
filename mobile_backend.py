from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import approval_guardrails
import approval_links
import dashboard_data
import signal_tracker
from kalshi_readonly import KalshiReadOnlyClient
from kalshi_submit import KalshiSubmitClient, KalshiSubmitError

# mobile-backend-deploy-v2
app = FastAPI(title="PM Edge Mobile API", version="1.0.0")
MAX_ORDER_RISK = 10.0

_allowed = [
    item.strip()
    for item in os.getenv("PM_EDGE_MOBILE_ALLOWED_ORIGINS", "").split(",")
    if item.strip()
]
if _allowed:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


def _auth(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("PM_EDGE_MOBILE_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Mobile API token is not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        dt = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, float):
        return None if pd.isna(value) else float(value)
    if isinstance(value, (int, bool, str)):
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _records(df: pd.DataFrame, *, limit: int = 300) -> list[dict]:
    if df.empty:
        return []
    return [
        {key: _clean(value) for key, value in row.items()}
        for row in df.head(limit).to_dict("records")
    ]


def _load() -> dict[str, pd.DataFrame]:
    data = dashboard_data.load_data()
    data["orders"] = dashboard_data.enrich_orders(data["orders"], data["signals"])
    return data


def _account_snapshot() -> dict:
    client = KalshiReadOnlyClient.from_env()
    if client is None:
        return {"connected": False, "balance": None}
    try:
        payload = client.get_balance()
    except Exception:
        return {"connected": False, "balance": None}
    raw = payload.get("balance")
    if raw is None:
        raw = payload.get("balance_dollars")
    try:
        balance = float(raw)
        if balance > 1000:
            balance /= 100.0
    except (TypeError, ValueError):
        balance = None
    return {"connected": True, "balance": balance}


def _signal_row(signal_id: str) -> dict:
    data = _load()
    signals = data["signals"]
    if signals.empty or "signal_id" not in signals.columns:
        raise HTTPException(status_code=404, detail="Signal not found")
    rows = signals[signals["signal_id"].fillna("").astype(str) == str(signal_id)]
    if "record_type" in rows.columns:
        rows = rows[rows["record_type"].fillna("signal").astype(str) == "signal"]
    if rows.empty:
        raise HTTPException(status_code=404, detail="Signal not found")
    if "alerted_at" in rows.columns:
        rows = rows.sort_values("alerted_at", ascending=False)
    return rows.iloc[0].to_dict()


def _market_ask(snapshot: dict, side: str) -> float | None:
    value = snapshot.get("yes_ask") if side == "YES" else snapshot.get("no_ask")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0 <= result <= 1 else None


def _market_bid(snapshot: dict, side: str) -> float | None:
    value = snapshot.get("yes_bid") if side == "YES" else snapshot.get("no_bid")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0 <= result <= 1 else None


def _submit_client() -> KalshiSubmitClient:
    try:
        client = KalshiSubmitClient.from_env()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Kalshi connection error: {exc}") from exc
    if client is None:
        raise HTTPException(status_code=503, detail="Kalshi write connection is not configured")
    return client


def _client_order_id(signal_id: str) -> str:
    return approval_links.client_order_id(signal_id, f"native-v1:{signal_id}")


def _event_start_epoch(row: dict) -> int | None:
    raw = str(row.get("commence_time") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def _review_payload(row: dict, client: KalshiSubmitClient) -> dict:
    signal_id = str(row.get("signal_id") or "")
    side = str(row.get("side") or "").upper()
    ticker = str(row.get("ticker") or "")
    execution = str(row.get("execution") or "MAKE").upper()
    if execution not in {"MAKE", "TAKE"}:
        execution = "MAKE"

    sizing = dashboard_data.size_signal(row)
    if not sizing:
        raise HTTPException(status_code=409, detail="Could not size this signal")
    limit_price = float(sizing["price"])
    shares = int(sizing["shares"])
    max_loss = float(sizing["max_loss"])
    if max_loss > MAX_ORDER_RISK + 1e-9:
        raise HTTPException(status_code=409, detail="Order exceeds the $10 per-order cap")

    try:
        market = dashboard_data.market_snapshot(ticker)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Live market check failed: {exc}") from exc

    live_ask = _market_ask(market, side)
    if live_ask is None:
        raise HTTPException(status_code=503, detail="Could not read the live Kalshi ask")

    price_ok = execution == "MAKE" or live_ask <= limit_price + 1e-9
    if execution == "MAKE" and live_ask > limit_price + 1e-9:
        price_message = (
            f"Limit order will rest at {limit_price * 100:.1f}¢ and will not chase "
            f"the {live_ask * 100:.1f}¢ ask."
        )
    elif price_ok:
        price_message = (
            f"Live ask {live_ask * 100:.1f}¢ is within the "
            f"{limit_price * 100:.1f}¢ maximum."
        )
    else:
        price_message = (
            f"Live ask {live_ask * 100:.1f}¢ is above the "
            f"{limit_price * 100:.1f}¢ maximum."
        )

    try:
        orders = client.get_orders(limit=200)
        positions = client.get_positions(limit=200)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Kalshi risk check failed: {exc}") from exc

    client_id = _client_order_id(signal_id)
    existing = None
    for order in orders:
        if str(order.get("client_order_id") or "") == client_id:
            existing = order
            break

    guard = approval_guardrails.evaluate_position_guard(
        ticker=ticker,
        side=side,
        candidate_risk=max_loss,
        positions=positions,
        orders=orders,
        now=datetime.now(timezone.utc),
    )
    can_submit = bool(price_ok and not guard.get("blocked") and existing is None)

    return {
        "signal_id": signal_id,
        "ticker": ticker,
        "market_title": str(row.get("market_title") or market.get("title") or ticker),
        "sport_title": str(row.get("sport_title") or ""),
        "market_type": str(row.get("market_type") or ""),
        "lane": str(row.get("lane") or "primary"),
        "side": side,
        "execution": execution,
        "fair_probability": _clean(row.get("fair_probability")),
        "net_edge": _clean(row.get("net_edge")),
        "books": _clean(row.get("books")),
        "limit_price": limit_price,
        "live_ask": live_ask,
        "shares": shares,
        "max_loss": max_loss,
        "gross_profit_if_correct": float(sizing["gross_profit_if_correct"]),
        "price_ok": price_ok,
        "price_message": price_message,
        "guard": guard,
        "existing_order": existing,
        "kalshi_ready": True,
        "can_submit": can_submit,
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "pm-edge-mobile-api", "version": "1.0.0"}


@app.get("/v1/dashboard", dependencies=[Depends(_auth)])
def dashboard() -> dict:
    data = _load()
    actual = dashboard_data.actual_summary(data["orders"])
    model = dashboard_data.opportunity_summary(data["signal_outcomes"])
    new = dashboard_data.new_signals(data["signals"], data["orders"]).head(8)
    state = dashboard_data.read_state()
    last = dashboard_data.last_model_activity(data)
    history = data["orders"].copy()
    if "trade_date" in history.columns:
        history = history.sort_values("trade_date", ascending=False)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "account": _account_snapshot(),
        "actual": actual,
        "model": model,
        "new_bets": _records(new, limit=8),
        "recent_activity": _records(history, limit=6),
        "open_count": len(dashboard_data.open_order_rows(data["orders"])),
        "system": {
            "last_model_activity": _clean(last),
            "odds_credits_today": (state.get("daily_usage") or {}).get("credits"),
            "drawdown_active": bool((state.get("drawdown_circuit") or {}).get("active")),
        },
    }


@app.get("/v1/bets", dependencies=[Depends(_auth)])
def bets(
    status: str = Query(default="new", pattern="^(new|open|history)$"),
    sport: str | None = None,
    lane: str | None = None,
) -> dict:
    data = _load()
    if status == "new":
        df = dashboard_data.new_signals(data["signals"], data["orders"])
    elif status == "open":
        df = dashboard_data.open_order_rows(data["orders"])
    else:
        df = data["orders"].copy()

    if sport and "sport_title" in df.columns:
        df = df[df["sport_title"].fillna("").astype(str).str.casefold() == sport.casefold()]
    if lane and "lane" in df.columns:
        df = df[df["lane"].fillna("").astype(str).str.casefold() == lane.casefold()]

    sort_col = "alerted_at" if status == "new" else "trade_date"
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=False)
    return {"items": _records(df, limit=300)}


@app.get("/v1/live", dependencies=[Depends(_auth)])
def live() -> dict:
    data = _load()
    open_orders = dashboard_data.open_order_rows(data["orders"])
    if "trade_date" in open_orders.columns:
        open_orders = open_orders.sort_values("trade_date", ascending=False)

    items = []
    for row in open_orders.head(40).to_dict("records"):
        ticker = str(row.get("ticker") or "")
        side = str(row.get("side") or "").upper()
        try:
            market = dashboard_data.market_snapshot(ticker)
        except Exception:
            market = {"status": "unknown"}

        try:
            qty = float(row.get("filled_shares") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            entry_cost = float(row.get("entry_cost") or 0)
        except (TypeError, ValueError):
            entry_cost = 0.0
        bid = _market_bid(market, side)
        current_value = qty * bid if qty > 0 and bid is not None else None
        unrealized = current_value - entry_cost if current_value is not None and entry_cost > 0 else None

        item = {key: _clean(value) for key, value in row.items()}
        item.update(
            {
                "kalshi_status": market.get("status"),
                "current_bid": bid,
                "current_value": current_value,
                "unrealized_pl": unrealized,
            }
        )
        items.append(item)
    return {"items": items}


@app.get("/v1/bets/{signal_id}/review", dependencies=[Depends(_auth)])
def review(signal_id: str) -> dict:
    return _review_payload(_signal_row(signal_id), _submit_client())


@app.post("/v1/bets/{signal_id}/submit", dependencies=[Depends(_auth)])
def submit(signal_id: str) -> dict:
    row = _signal_row(signal_id)
    client = _submit_client()
    review_data = _review_payload(row, client)

    if review_data.get("existing_order") is not None:
        return {
            "submitted": False,
            "already_exists": True,
            "order": review_data["existing_order"],
            "review": review_data,
        }
    if not review_data.get("can_submit"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Order is not currently eligible for submission",
                "review": review_data,
            },
        )

    ticker = str(review_data["ticker"])
    side = str(review_data["side"])
    execution = str(review_data["execution"])
    limit_price = float(review_data["limit_price"])
    shares = int(review_data["shares"])
    client_id = _client_order_id(signal_id)

    order_kwargs = {
        "ticker": ticker,
        "outcome_side": side,
        "count": shares,
        "max_price": limit_price,
        "execution": execution,
        "client_order_id": client_id,
    }
    if execution == "MAKE":
        start_epoch = _event_start_epoch(row)
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        order_kwargs["expires_at"] = start_epoch
        order_kwargs["is_live"] = bool(start_epoch is not None and start_epoch <= now_epoch)

    try:
        result = client.create_buy_order(**order_kwargs)
    except KalshiSubmitError as exc:
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail="Kalshi reports this order already exists") from exc
        raise HTTPException(status_code=502, detail=f"Kalshi rejected the order: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Order submission failed: {exc}") from exc

    trade = {
        "signal_id": signal_id,
        "ticker": ticker,
        "side": side,
        "lane": str(row.get("lane") or "primary"),
        "execution": execution,
        "limit_price": limit_price,
        "fair_probability": _clean(row.get("fair_probability")),
        "net_edge": _clean(row.get("net_edge")),
        "books": _clean(row.get("books")),
        "dispersion": _clean(row.get("dispersion")),
        "fair_fetched_at": str(row.get("fair_fetched_at") or ""),
        "source_version": _clean(row.get("source_version")),
    }
    try:
        signal_tracker.record_submission(
            trade,
            client_order_id=client_id,
            order_result=result,
        )
    except Exception:
        pass

    return {
        "submitted": True,
        "already_exists": False,
        "order": result,
        "review": review_data,
    }


@app.get("/v1/analytics", dependencies=[Depends(_auth)])
def analytics() -> dict:
    data = _load()
    orders = data["orders"]
    return {
        "actual": dashboard_data.actual_summary(orders),
        "model": dashboard_data.opportunity_summary(data["signal_outcomes"]),
        "by_sport": _records(dashboard_data.aggregate_performance(orders, "sport_title")),
        "by_lane": _records(dashboard_data.aggregate_performance(orders, "lane")),
        "by_market_type": _records(dashboard_data.aggregate_performance(orders, "market_type")),
        "funnel": dashboard_data.signal_funnel(data["candidates"], data["signals"], orders),
    }


@app.get("/v1/system", dependencies=[Depends(_auth)])
def system() -> dict:
    data = _load()
    state = dashboard_data.read_state()
    last = dashboard_data.last_model_activity(data)
    signals = data["signals"]
    signal_rows = signals
    if not signals.empty and "record_type" in signals.columns:
        signal_rows = signals[signals["record_type"].fillna("signal").astype(str) == "signal"]
    return {
        "last_model_activity": _clean(last),
        "odds_credits_today": (state.get("daily_usage") or {}).get("credits"),
        "odds_budget_date": (state.get("daily_usage") or {}).get("date"),
        "drawdown": state.get("drawdown_circuit") or {},
        "guardrails": {
            "max_per_order_dollars": 10.0,
            "max_open_risk_dollars": 40.0,
            "max_open_positions_per_sport": approval_guardrails.MAX_SPORT_POSITIONS,
            "per_sport_dollar_cap": None,
        },
        "tracking": {
            "signals": len(signal_rows),
            "candidate_decisions": len(data["candidates"]),
            "clv_rows": len(data["clv"]),
            "fill_rows": len(data["fills"]),
            "signal_outcomes": len(data["signal_outcomes"]),
        },
    }
