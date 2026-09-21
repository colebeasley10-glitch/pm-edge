from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import dashboard_data

app = FastAPI(title="PM Edge Mobile API", version="1.0.0")

_allowed = [x.strip() for x in os.getenv("PM_EDGE_MOBILE_ALLOWED_ORIGINS", "").split(",") if x.strip()]
if _allowed:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed,
        allow_credentials=False,
        allow_methods=["GET"],
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
        if pd.isna(value):
            return None
        return float(value)
    if isinstance(value, (int, bool, str)):
        return value
    if pd.isna(value):
        return None
    return str(value)


def _records(df: pd.DataFrame, *, limit: int = 200) -> list[dict]:
    if df.empty:
        return []
    rows = []
    for row in df.head(limit).to_dict("records"):
        rows.append({k: _clean(v) for k, v in row.items()})
    return rows


def _load() -> dict[str, pd.DataFrame]:
    data = dashboard_data.load_data()
    data["orders"] = dashboard_data.enrich_orders(data["orders"], data["signals"])
    return data


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "pm-edge-mobile-api"}


@app.get("/v1/dashboard", dependencies=[Depends(_auth)])
def dashboard() -> dict:
    data = _load()
    actual = dashboard_data.actual_summary(data["orders"])
    model = dashboard_data.opportunity_summary(data["signal_outcomes"])
    new = dashboard_data.new_signals(data["signals"], data["orders"]).head(8)
    state = dashboard_data.read_state()
    last = dashboard_data.last_model_activity(data)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actual": actual,
        "model": model,
        "new_bets": _records(new, limit=8),
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
        df = dashboard_data.open_order_rows(data["orders"]).copy()
        if not df.empty:
            live_rows = []
            for row in df.head(25).to_dict("records"):
                item = dict(row)
                ticker = str(item.get("ticker") or "")
                side = str(item.get("side") or "").upper()
                try:
                    snap = dashboard_data.market_snapshot(ticker) if ticker else {}
                except Exception:
                    snap = {}
                current_bid = snap.get("yes_bid") if side == "YES" else snap.get("no_bid")
                try:
                    filled = float(item.get("filled_shares") or 0)
                except (TypeError, ValueError):
                    filled = 0.0
                try:
                    entry_cost = float(item.get("entry_cost") or 0)
                except (TypeError, ValueError):
                    entry_cost = 0.0
                current_value = filled * float(current_bid) if current_bid is not None and filled > 0 else None
                item["current_bid"] = current_bid
                item["current_value"] = current_value
                item["unrealized_pl"] = (current_value - entry_cost) if current_value is not None and entry_cost > 0 else None
                item["kalshi_status"] = snap.get("status")
                live_rows.append(item)
            df = pd.DataFrame(live_rows)
    else:
        df = data["orders"].copy()

    if sport and "sport_title" in df.columns:
        df = df[df["sport_title"].fillna("").astype(str).str.casefold() == sport.casefold()]
    if lane and "lane" in df.columns:
        df = df[df["lane"].fillna("").astype(str).str.casefold() == lane.casefold()]

    sort_col = "alerted_at" if status == "new" else "trade_date"
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=False)
    return {"items": _records(df, limit=250)}


@app.get("/v1/bets/{signal_id}/approval", dependencies=[Depends(_auth)])
def approval(signal_id: str) -> dict:
    data = _load()
    signals = data["signals"]
    if signals.empty:
        raise HTTPException(status_code=404, detail="Signal not found")
    match = signals[signals["signal_id"].fillna("").astype(str) == signal_id]
    if "record_type" in match.columns:
        match = match[match["record_type"].fillna("signal").astype(str) == "signal"]
    if match.empty:
        raise HTTPException(status_code=404, detail="Signal not found")
    row = match.sort_values("alerted_at", ascending=False).iloc[0].to_dict()
    url = dashboard_data.approval_url_for_signal(row)
    if not url:
        raise HTTPException(status_code=409, detail="Approval link unavailable")
    return {
        "signal_id": signal_id,
        "approval_url": url,
        "sizing": dashboard_data.size_signal(row),
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
        "tracking": {
            "signals": len(signal_rows),
            "candidate_decisions": len(data["candidates"]),
            "clv_rows": len(data["clv"]),
            "fill_rows": len(data["fills"]),
            "signal_outcomes": len(data["signal_outcomes"]),
        },
    }
