from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
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


def _fair_auth(authorization: str | None = Header(default=None)) -> None:
    """Separate read-only credential for AutoTrader fair-value access."""
    expected = os.getenv("PM_EDGE_FAIR_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Fair-value API token is not configured")
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
    # Use the already-configured authenticated Kalshi connection first. The
    # mobile service has write credentials, while the legacy read-only client
    # expects a different pair of environment variable names.
    client = KalshiSubmitClient.from_env()
    if client is None:
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


def _kalshi_order_side(row: dict) -> str:
    side = str(row.get("outcome_side") or row.get("side") or "").strip().upper()
    if side in {"YES", "NO"}:
        return side
    if side == "BID":
        return "YES"
    if side == "ASK":
        return "NO"
    return ""


def _live_kalshi_submitted_pairs() -> set[tuple[str, str]]:
    """Ticker/side exposures already submitted to Kalshi.

    This survives Railway restarts, unlike the local CSV submission append.
    Canceled/rejected orders do not suppress a future legitimate opportunity.
    """
    try:
        orders = _submit_client().get_orders(limit=200)
    except Exception:
        return set()

    pairs: set[tuple[str, str]] = set()
    for row in orders or []:
        # Availability is about whether PM Edge has already attempted this
        # exposure, not whether the order is still resting. Review treats an
        # existing client_order_id as terminal for that signal because reusing
        # the same id is not a fresh submission. Mirror that behavior here so
        # canceled/unfilled attempts cannot resurrect on Dashboard.
        client_id = str(row.get("client_order_id") or "").strip()
        if not client_id.startswith("pmedge-"):
            continue
        ticker = str(row.get("ticker") or row.get("market_ticker") or "").strip().upper()
        side = _kalshi_order_side(row)
        if ticker and side in {"YES", "NO"}:
            pairs.add((ticker, side))
    return pairs


def _exclude_submitted_pairs(df: pd.DataFrame, pairs: set[tuple[str, str]]) -> pd.DataFrame:
    if df.empty or not pairs or not {"ticker", "side"}.issubset(df.columns):
        return df
    keep = [
        (
            str(row.get("ticker") or "").strip().upper(),
            str(row.get("side") or "").strip().upper(),
        ) not in pairs
        for row in df.to_dict("records")
    ]
    return df[pd.Series(keep, index=df.index)]


def _actionable_new_signals(df: pd.DataFrame, *, scan_limit: int) -> pd.DataFrame:
    """Return only signals that can actually be submitted right now.

    Price eligibility and position/risk guards must both pass. This prevents a
    card from appearing under Available only to be rejected after Review opens.
    """
    if df.empty:
        return df.copy()
    try:
        client = _submit_client()
        orders = client.get_orders(limit=200)
        positions = client.get_positions(limit=200)
    except Exception:
        # Fail closed: if account risk cannot be verified, do not advertise a
        # real-money order as actionable.
        return pd.DataFrame(columns=df.columns)

    now = datetime.now(timezone.utc)
    actionable: list[dict] = []
    for row in df.head(scan_limit).to_dict("records"):
        ticker = str(row.get("ticker") or "").strip()
        side = str(row.get("side") or "").strip().upper()
        sizing = dashboard_data.size_signal(row)
        if not ticker or side not in {"YES", "NO"} or not sizing:
            continue
        try:
            market = dashboard_data.market_snapshot(ticker)
            live_ask = _market_ask(market, side)
            max_price = float(sizing["price"])
            max_loss = float(sizing["max_loss"])
        except Exception:
            continue

        execution = str(row.get("execution") or "MAKE").upper()
        market_status = str(market.get("status") or "").lower()
        market_open = market_status in {"active", "open"}

        # Whale Flow stores the Kalshi market close time in commence_time.
        # Never advertise a whale card after that close time, even if Kalshi
        # still briefly reports the market as active/open while settling.
        is_whale_flow = (
            str(row.get("lane") or "").casefold() == "whale"
            and str(row.get("market_type") or "").casefold() == "whale_flow"
        )
        whale_close_epoch = _event_start_epoch(row) if is_whale_flow else None
        if whale_close_epoch is not None and whale_close_epoch <= int(now.timestamp()):
            continue

        price_actionable = (
            live_ask is not None
            and market_open
            and (
                (execution == "MAKE" and live_ask > max_price + 1e-9)
                or (execution != "MAKE" and live_ask <= max_price + 1e-9)
            )
        )
        if not price_actionable:
            continue

        signal_id = str(row.get("signal_id") or "").strip()
        expected_client_id = _client_order_id(signal_id) if signal_id else ""
        existing_order = next(
            (
                order
                for order in orders or []
                if expected_client_id
                and str(order.get("client_order_id") or "").strip() == expected_client_id
            ),
            None,
        )
        if existing_order is not None:
            print(
                "AVAILABLE_FILTER_BLOCK",
                ticker,
                side,
                "existing_order",
                expected_client_id,
                flush=True,
            )
            continue

        guard = approval_guardrails.evaluate_position_guard(
            ticker=ticker,
            side=side,
            candidate_risk=max_loss,
            positions=positions,
            orders=orders,
            now=now,
        )
        if guard.get("blocked"):
            print(
                "AVAILABLE_FILTER_BLOCK",
                ticker,
                side,
                guard.get("reason"),
                flush=True,
            )
            continue
        actionable.append(row)

    return pd.DataFrame(actionable, columns=df.columns)


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
    except requests.exceptions.Timeout as exc:
        # Explicit timeout handling: 10s exceeded on Kalshi API call
        raise HTTPException(status_code=502, detail=f"Kalshi market timeout: {exc}") from exc
    except Exception as exc:
        # Other network/parsing errors
        raise HTTPException(status_code=503, detail=f"Live market check failed: {exc}") from exc

    live_ask = _market_ask(market, side)
    if live_ask is None:
        raise HTTPException(status_code=503, detail="Could not read the live Kalshi ask")

    market_status = str(market.get("status") or "").lower()
    market_open = market_status in {"active", "open"}

    is_whale_flow = (
        str(row.get("lane") or "").casefold() == "whale"
        and str(row.get("market_type") or "").casefold() == "whale_flow"
    )
    whale_close_epoch = _event_start_epoch(row) if is_whale_flow else None
    whale_expired = bool(
        whale_close_epoch is not None
        and whale_close_epoch <= int(datetime.now(timezone.utc).timestamp())
    )

    # TAKE must still be executable at or below the approved maximum.
    # MAKE is intentionally a patient post-only resting limit: it is valid when
    # the live ask is ABOVE our limit, because our order will rest rather than
    # chase the market. This is the normal Price Fish path.
    if whale_expired:
        price_ok = False
        price_message = "Expired: this Whale Flow market's close time has passed."
    elif execution == "MAKE":
        price_ok = bool(market_open and live_ask > limit_price + 1e-9)
        if price_ok:
            price_message = (
                f"Resting limit {limit_price * 100:.1f}¢ is below the "
                f"{live_ask * 100:.1f}¢ live ask. Order will wait for a fill."
            )
        elif market_open:
            price_message = (
                f"Live ask {live_ask * 100:.1f}¢ is at or below the "
                f"{limit_price * 100:.1f}¢ maker limit, so post-only submission is paused."
            )
        else:
            price_message = "Market is no longer open for a resting order."
    else:
        price_ok = bool(market_open and live_ask <= limit_price + 1e-9)
        if price_ok:
            price_message = (
                f"Live ask {live_ask * 100:.1f}¢ is within the "
                f"{limit_price * 100:.1f}¢ maximum."
            )
        elif market_open:
            price_message = (
                f"Expired: live ask {live_ask * 100:.1f}¢ is above the "
                f"{limit_price * 100:.1f}¢ maximum. This bet can no longer be submitted."
            )
        else:
            price_message = "Market is no longer open for immediate execution."

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
    return {"ok": True, "service": "pm-edge-mobile-api", "version": "1.0.1"}


@app.get("/v1/dashboard", dependencies=[Depends(_auth)])
def dashboard() -> dict:
    data = _load()
    actual = dashboard_data.actual_summary(data["orders"])
    model = dashboard_data.opportunity_summary(data["signal_outcomes"])
    new = dashboard_data.new_signals(data["signals"], data["orders"])
    new = _exclude_submitted_pairs(new, _live_kalshi_submitted_pairs())
    new = _actionable_new_signals(new, scan_limit=40).head(8)
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


@app.get("/v1/fair-values", dependencies=[Depends(_fair_auth)])
def fair_values() -> dict:
    """Latest sportsbook-derived fair values for paper/analytics consumers.

    This endpoint is read-only and exposes scored candidate rows before alert,
    execution, or account guardrails are applied.
    """
    runtime_base = os.getenv("AUTOTRADER_FAIR_SOURCE_URL", "").strip().rstrip("/")
    runtime_token = os.getenv("AUTOTRADER_FAIR_SOURCE_TOKEN", "").strip()
    if runtime_base and runtime_token:
        try:
            response = requests.get(
                runtime_base + "/v1/fair-values",
                headers={"Authorization": f"Bearer {runtime_token}"},
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                return {
                    "generated_at": payload.get("generated_at")
                    or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "items": items,
                    "source": "autotrader_runtime",
                }
        except Exception:
            pass

    data = _load()
    df = data["candidates"].copy()
    if df.empty:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "items": [],
        }

    if "stage" in df.columns:
        df = df[df["stage"].fillna("").astype(str).str.casefold().eq("score")]
    required = {"ticker", "side", "fair_probability"}
    if not required.issubset(df.columns):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "items": [],
        }

    df = df.copy()
    df["fair_probability"] = pd.to_numeric(df["fair_probability"], errors="coerce")
    df = df[
        df["ticker"].fillna("").astype(str).str.len().gt(0)
        & df["side"].fillna("").astype(str).str.upper().isin(["YES", "NO"])
        & df["fair_probability"].between(0, 1, inclusive="neither")
    ]

    if "evaluated_at" in df.columns:
        df["_evaluated_sort"] = pd.to_datetime(df["evaluated_at"], errors="coerce", utc=True)
        df = df.sort_values("_evaluated_sort", ascending=False)
    df = df.drop_duplicates(subset=["ticker", "side"], keep="first")
    if "_evaluated_sort" in df.columns:
        df = df.drop(columns=["_evaluated_sort"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": _records(df, limit=1000),
    }


@app.get("/v1/bets", dependencies=[Depends(_auth)])
def bets(
    status: str = Query(default="new", pattern="^(new|open|unfilled|history)$"),
    sport: str | None = None,
    lane: str | None = None,
) -> dict:
    data = _load()
    if status == "new":
        df = dashboard_data.new_signals(data["signals"], data["orders"])
        df = _exclude_submitted_pairs(df, _live_kalshi_submitted_pairs())

        # Bet Center contains only genuinely actionable signals: live price
        # eligibility AND account/position guardrails must pass.
        df = _actionable_new_signals(df, scan_limit=80)
    elif status == "open":
        df = dashboard_data.open_order_rows(data["orders"])
    elif status == "unfilled":
        order_status = data["orders"].get("status", pd.Series("", index=data["orders"].index)).fillna("").astype(str)
        df = data["orders"][order_status.eq("unfilled")].copy()
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
    # Live is a strict subset of real Kalshi positions whose market has started.
    items = _kalshi_position_items()
    live_items = [
        item for item in items
        if str(item.get("kalshi_status") or "").lower() in {"active", "open"}
        and dashboard_data.signal_state(item).startswith("LIVE")
    ]
    return {"items": live_items}


@app.get("/v1/positions", dependencies=[Depends(_auth)])
def positions() -> dict:
    # All real non-zero Kalshi positions, including upcoming and live.
    return {"items": _kalshi_position_items()}


def _kalshi_position_items() -> list[dict]:
    data = _load()
    orders = data["orders"]
    try:
        positions = _submit_client().get_positions(limit=200)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Kalshi position check failed: {exc}") from exc

    items = []
    for position in positions:
        ticker = str(position.get("ticker") or "").strip()
        raw_position = position.get("position")
        if raw_position in (None, ""):
            raw_position = position.get("position_fp")
        try:
            signed_qty = float(raw_position or 0)
        except (TypeError, ValueError):
            signed_qty = 0.0
        if not ticker or signed_qty == 0:
            continue
        explicit_side = str(position.get("side") or position.get("outcome_side") or "").upper()
        side = explicit_side if explicit_side in {"YES", "NO"} else ("YES" if signed_qty > 0 else "NO")
        qty = abs(signed_qty)
        row = {}
        if not orders.empty and "ticker" in orders.columns:
            matches = orders[
                orders["ticker"].fillna("").astype(str).eq(ticker)
                & orders.get("side", pd.Series("", index=orders.index)).fillna("").astype(str).str.upper().eq(side)
            ].copy()
            if not matches.empty:
                if "trade_date" in matches.columns:
                    matches = matches.sort_values("trade_date", ascending=False)
                row = matches.iloc[0].to_dict()
        try:
            market = dashboard_data.market_snapshot(ticker)
        except Exception:
            market = {"status": "unknown"}
        bid = _market_bid(market, side)
        current_value = qty * bid if bid is not None else None
        item = {key: _clean(value) for key, value in row.items()}
        item.update({key: _clean(value) for key, value in position.items()})
        item.update({
            "ticker": ticker, "side": side, "filled_shares": qty,
            "kalshi_status": market.get("status"), "current_bid": bid,
            "current_value": current_value, "position_source": "kalshi",
        })
        effective_start = dashboard_data.effective_commence_time(item)
        if effective_start is not None:
            item["commence_time"] = effective_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        items.append(item)
    return items


def _score_sport_key(value: str) -> str | None:
    raw = str(value or "").lower()
    aliases = {
        "nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf",
        "mlb": "baseball_mlb", "kbo": "baseball_kbo", "npb": "baseball_npb",
        "wnba": "basketball_wnba", "nba": "basketball_nba",
    }
    if raw in aliases:
        return aliases[raw]
    if raw.startswith(("americanfootball_", "baseball_", "basketball_")):
        return raw
    return None


@app.get("/v1/scores", dependencies=[Depends(_auth)])
def scores() -> dict:
    """Live/final scores for events represented by tracked PM Edge bets."""
    key = os.getenv("ODDS_API_KEY", "").strip()
    if not key:
        return {"items": [], "available": False}
    data = _load()
    tracked = pd.concat([data["orders"], data["signals"]], ignore_index=True, sort=False)
    if tracked.empty:
        return {"items": [], "available": True}
    sports: set[str] = set()
    for _, row in tracked.iterrows():
        candidate = _score_sport_key(row.get("sport_key") or row.get("sport_title"))
        if candidate:
            sports.add(candidate)
    items: list[dict] = []
    for sport in sorted(sports):
        try:
            response = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sport}/scores",
                params={"apiKey": key, "daysFrom": 3, "dateFormat": "iso"},
                timeout=10,
            )
            response.raise_for_status()
            for event in response.json():
                items.append({
                    "id": event.get("id"),
                    "sport_key": event.get("sport_key") or sport,
                    "commence_time": event.get("commence_time"),
                    "completed": bool(event.get("completed")),
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "scores": event.get("scores") or [],
                    "last_update": event.get("last_update"),
                })
        except Exception:
            continue
    return {"items": items, "available": True}


@app.get("/v1/bets/{signal_id}/review", dependencies=[Depends(_auth)])
def review(signal_id: str) -> dict:
    return _review_payload(_signal_row(signal_id), _submit_client())


@app.post("/v1/bets/{signal_id}/submit", dependencies=[Depends(_auth)])
def submit(signal_id: str) -> dict:
    if os.getenv("PM_EDGE_ORDER_SUBMISSION_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=503, detail="Real-money order submission is disabled")
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
    except requests.exceptions.Timeout as exc:
        # Order submission timed out on Kalshi side: may or may not have been placed
        raise HTTPException(status_code=502, detail=f"Kalshi order submission timeout: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Order submission failed: {exc}") from exc

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
    except Exception as exc:
        # Log the failure but do not suppress: submission tracking is critical for idempotency.
        # The order WAS placed in Kalshi; failure here means the ledger is stale.
        import sys
        print(f"WARNING: Failed to record submission for signal {signal_id}: {exc}", file=sys.stderr)
        raise HTTPException(status_code=503, detail=f"Order placed but submission recording failed: {exc}") from exc

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
