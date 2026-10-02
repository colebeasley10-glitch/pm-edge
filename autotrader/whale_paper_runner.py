"""Always-on paper-only Whale Flow trader.

Uses Kalshi's public trade tape and public market/orderbook endpoints only.
It never loads authenticated Kalshi clients and never submits real orders.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from autotrader.collector import PUBLIC_BASE
from autotrader.fees import prediction_fee
from autotrader.paper_runner import _assert_no_kalshi_write_credentials, _settle_finished_positions
from autotrader.portfolio import PaperPortfolio
from autotrader.learning import _completed_trades
from autotrader.storage import append_jsonl, load_portfolio, save_portfolio, tail_jsonl
from pmedge.kalshi import KalshiPublicClient, normalize_market

import whale_edge

D = lambda value: Decimal(str(value))


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class PendingMaker:
    ticker: str
    side: str
    contracts: int
    target: str
    created_at: str
    expires_at: str
    source_trade_id: str
    flow_contracts: int
    flow_notional_usd: float
    reference_price_cents: int
    title: str
    exposure_key: str | None = None
    # Shadow conservative maker-fill tracking (observational only).
    shadow_volume: float = 0.0
    shadow_through_volume: float = 0.0
    shadow_first_fill_time: str | None = None
    shadow_seen_ids: list = field(default_factory=list)


MAKER_SHADOW_VERSION = "maker_tradethrough_v1"

# Reporting boundaries for Quick Whale v2 (analytical metadata only; no state
# is reset and no historical record is rewritten):
#   quick_whale_v2_pre_pagination : runs/entries before the first paginated
#                                   deploy (single 1,000-trade tape page)
#   quick_whale_v2_pagination_v1  : from that deploy until the horizon fix;
#                                   records carry no explicit label
#   quick_whale_v2_pagination_v2  : written by this code (window-filtered tape,
#                                   explicit horizon coverage, post-fetch
#                                   flow-age clock); label stamped explicitly
QUICK_V2_PAGINATION_BOUNDARY_AT = "2026-10-01T17:37:55.407Z"
QUICK_V2_PAGINATION_BOUNDARY_COMMIT = "888340bb380a76a1b76187cc6df435c3974b9141"
LANE_VERSIONS = {
    "whale_flow": "whale_flow_tape_horizon_v1",
    "quick_whale": "quick_whale_v1_tape_horizon_v1",
    "quick_whale_v2": "quick_whale_v2_pagination_v2",
    "quick_whale_v3": "quick_whale_v3_tape_horizon_v1",
}


def quick_v2_version_for(observed_at=None, explicit_lane_version=None):
    """Classify a Quick Whale v2 run/entry into its reporting period."""
    if explicit_lane_version:
        return str(explicit_lane_version)
    ts = _parse_time(observed_at)
    if ts is None:
        return "quick_whale_v2_unknown_period"
    boundary = _parse_time(QUICK_V2_PAGINATION_BOUNDARY_AT)
    return "quick_whale_v2_pre_pagination" if ts < boundary else "quick_whale_v2_pagination_v1"
V3_QUEUE_MAX = 50


def _default_runner_state():
    return {
        "schema": 1,
        "seen_trade_ids": [],
        "pending": [],
        "last_scan_at": None,
        "market_meta": {},
        "reaction_queue": [],
    }


def _load_runner_state(path):
    target = Path(path)
    if not target.exists():
        return _default_runner_state()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return _default_runner_state()
    if not isinstance(data, dict) or data.get("schema") != 1:
        return _default_runner_state()
    data.setdefault("seen_trade_ids", [])
    data.setdefault("pending", [])
    data.setdefault("last_scan_at", None)
    data.setdefault("market_meta", {})
    data.setdefault("reaction_queue", [])
    return data


def _save_runner_state(state, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["schema"] = 1
    state["seen_trade_ids"] = list(dict.fromkeys(state.get("seen_trade_ids") or []))[-20000:]
    state["pending"] = list(state.get("pending") or [])[-200:]
    state["market_meta"] = dict(list((state.get("market_meta") or {}).items())[-500:])
    state["reaction_queue"] = list(state.get("reaction_queue") or [])[-V3_QUEUE_MAX:]
    tmp = None
    try:
        with NamedTemporaryFile(
            mode="w", dir=target.parent, prefix=".whale-", suffix=".tmp",
            delete=False, encoding="utf-8",
        ) as handle:
            tmp = Path(handle.name)
            json.dump(state, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def _best_bid_for_side(orderbook, side):
    book = (orderbook or {}).get("orderbook_fp") or {}
    levels = (
        book.get("yes_dollars") or book.get("yes") or []
        if side == "YES"
        else book.get("no_dollars") or book.get("no") or []
    )
    best = None
    for level in levels or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            price = D(level[0])
            size = int(float(level[1]))
        except Exception:
            continue
        if not (D("0") < price < D("1")) or size < 1:
            continue
        if best is None or price > best[0]:
            best = (price, size)
    return best


def _best_ask_for_side(orderbook, side):
    opposite = "NO" if side == "YES" else "YES"
    bid = _best_bid_for_side(orderbook, opposite)
    if bid is None:
        return None
    price, size = bid
    ask = D("1") - price
    if not (D("0") < ask < D("1")):
        return None
    return ask, size


def _market_meta(client, ticker, cache):
    cached = cache.get(ticker)
    if isinstance(cached, dict) and cached.get("title"):
        return cached
    raw = client.get_market(ticker) or {}
    market = normalize_market(raw)
    meta = {
        "ticker": ticker,
        "event_ticker": str(raw.get("event_ticker") or market.get("event_ticker") or ""),
        "title": str(raw.get("title") or market.get("title") or ticker),
        "subtitle": str(raw.get("subtitle") or market.get("subtitle") or ""),
        "yes_sub_title": str(raw.get("yes_sub_title") or market.get("yes_sub_title") or ""),
        "no_sub_title": str(raw.get("no_sub_title") or market.get("no_sub_title") or ""),
        "status": str(raw.get("status") or market.get("status") or ""),
        "close_time": raw.get("close_time") or market.get("close_time"),
    }
    cache[ticker] = meta
    return meta


def _position_label(meta, side):
    if str(side).upper() == "YES":
        return meta.get("yes_sub_title") or meta.get("title")
    return meta.get("no_sub_title") or meta.get("title")


def _iso_or_none(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt is not None else None


def _position_timing(ticker, side, meta, *, quick):
    """Event/close times for CLV checkpoints, with provenance. Never fabricated:
    a field is None when no trustworthy source exists."""
    meta = meta or {}
    close_dt = _parse_time(meta.get("close_time"))
    out = {
        "close_time": _iso_or_none(close_dt),
        "close_time_provenance": "kalshi_market_close_time" if close_dt is not None else None,
        "event_start_at": None,
        "event_start_provenance": None,
    }
    if quick:
        return out
    try:
        _fair_side, fair_start = whale_edge._market_fair_context(ticker, side)
    except Exception:
        fair_start = None
    if fair_start is not None:
        out["event_start_at"] = _iso_or_none(fair_start)
        out["event_start_provenance"] = "pm_edge_fair_cache_commence_time"
        return out
    try:
        ticker_start = whale_edge.watcher.kalshi_ticker_game_time({"ticker": ticker})
    except Exception:
        ticker_start = None
    if ticker_start is not None:
        out["event_start_at"] = _iso_or_none(ticker_start)
        out["event_start_provenance"] = "kalshi_ticker_game_time"
    return out


def _trade_print_touches_target(trades, pending):
    created = _parse_time(pending.created_at)
    target = D(pending.target)
    for trade in trades:
        if str(trade.get("ticker") or "") != pending.ticker:
            continue
        ts = _parse_time(trade.get("created_time"))
        if created is not None and (ts is None or ts < created):
            continue
        side = whale_edge._trade_side(trade)
        if side != pending.side:
            continue
        price = whale_edge._trade_price(trade, side)
        if price is not None and D(price) <= target:
            return True
    return False


def _risk_contracts(portfolio, price, fee_per_contract, available):
    price = D(price)
    fee = D(fee_per_contract)
    risk_budget = min(
        portfolio.cash,
        portfolio.max_position_fraction * D("500"),
    )
    if price + fee <= 0:
        return 0
    count = min(int(available), int(risk_budget / (price + fee)))
    while count > 0:
        total_fee = prediction_fee(count, price)
        if D(count) * price + total_fee <= risk_budget:
            return count
        count -= 1
    return 0


def _paper_buy(
    portfolio,
    *,
    ticker,
    side,
    price,
    available,
    exposure_key,
    source_trade_id,
    title,
    flow_contracts,
    flow_notional_usd,
    reference_price_cents,
    execution,
    strategy="whale_flow",
    timing=None,
    meta=None,
    entered_at=None,
):
    price = D(price)
    rough_fee = prediction_fee(1, price)
    contracts = _risk_contracts(portfolio, price, rough_fee, available)
    if contracts < 1:
        return False, {"decision": "risk_rejected", "reason": "insufficient_paper_risk_budget"}
    fee_total = prediction_fee(contracts, price)
    fee_per_contract = fee_total / D(contracts)
    thesis = {
        "source": strategy,
        "source_trade_id": source_trade_id,
        "title": title,
        "flow_contracts": int(flow_contracts),
        "flow_notional_usd": float(flow_notional_usd),
        "reference_price_cents": int(reference_price_cents),
        "execution": execution,
        # Provenance for CLV checkpoints and segmented research reporting.
        **(timing or {}),
        "event_ticker": str((meta or {}).get("event_ticker") or ""),
        "series": str(ticker).split("-")[0],
        "lane_version": LANE_VERSIONS.get(strategy, f"{strategy}_unversioned"),
        "trade_tape_version": whale_edge.TRADE_TAPE_VERSION,
        "entered_at": _iso_or_none(entered_at) if entered_at is not None else None,
    }
    filled = portfolio.buy(
        ticker,
        side,
        contracts,
        price,
        price,
        int(available),
        fee_per_contract,
        exposure_key=exposure_key,
        fair_snapshot_id=f"{strategy}:{source_trade_id}" if source_trade_id else None,
        thesis=thesis,
    )
    if not filled:
        return False, {"decision": "risk_rejected", "reason": "portfolio_guard"}
    return True, {
        "decision": "paper_filled",
        "contracts": contracts,
        "price": str(price),
        "fee_total": str(fee_total),
    }


def _update_maker_shadow(p, trades):
    """Accumulate public trade volume at or better than the resting target
    printed AFTER the maker order was created. Observational only."""
    created = _parse_time(p.created_at)
    target = D(p.target)
    seen = set(p.shadow_seen_ids or [])
    for trade in trades:
        if str(trade.get("ticker") or "") != p.ticker:
            continue
        tid = str(trade.get("trade_id") or "")
        if not tid or tid in seen:
            continue
        ts = _parse_time(trade.get("created_time"))
        if created is None or ts is None or ts < created:
            continue
        price = whale_edge._trade_price(trade, p.side)
        if price is None or D(price) > target:
            continue
        count = float(whale_edge._trade_count(trade))
        if count <= 0:
            continue
        seen.add(tid)
        p.shadow_volume = float(p.shadow_volume or 0) + count
        if D(price) < target:
            p.shadow_through_volume = float(p.shadow_through_volume or 0) + count
        if p.shadow_first_fill_time is None:
            p.shadow_first_fill_time = _iso_or_none(ts)
    p.shadow_seen_ids = list(seen)[-2000:]


def _maker_shadow_fields(p):
    qty = min(int(p.contracts), int(float(p.shadow_volume or 0)))
    return {
        "maker_shadow_model_version": MAKER_SHADOW_VERSION,
        "maker_shadow_would_fill": qty >= 1,
        "maker_shadow_fill_qty": qty,
        "maker_shadow_target": p.target,
        "maker_shadow_trade_through_volume": round(float(p.shadow_volume or 0), 4),
        "maker_shadow_strictly_better_volume": round(float(p.shadow_through_volume or 0), 4),
        "maker_shadow_first_fill_time": p.shadow_first_fill_time,
        "maker_shadow_reason": (
            "tape_volume_at_or_better_than_target" if qty >= 1
            else "no_qualifying_trade_through_yet"
        ),
    }


def _process_pending(portfolio, client, state, trades, now, slippage):
    decisions = []
    kept = []
    market_cache = state.setdefault("market_meta", {})
    for raw in state.get("pending") or []:
        try:
            p = PendingMaker(**raw)
        except Exception:
            decisions.append({"decision": "pending_dropped", "reason": "invalid_pending_state"})
            continue
        try:
            _update_maker_shadow(p, trades)
        except Exception as exc:  # shadow must never affect the production path
            decisions.append({"ticker": p.ticker, "decision": "maker_shadow_error",
                              "reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
        shadow = _maker_shadow_fields(p)
        expiry = _parse_time(p.expires_at)
        if expiry is None or now >= expiry:
            decisions.append({
                "ticker": p.ticker, "side": p.side,
                "decision": "pending_expired", "reason": "maker_order_expired",
                **shadow,
            })
            continue
        if p.ticker in portfolio.positions:
            decisions.append({
                "ticker": p.ticker, "side": p.side,
                "decision": "pending_dropped", "reason": "position_already_open",
                **shadow,
            })
            continue
        touched = _trade_print_touches_target(trades, p)
        if not touched:
            kept.append(asdict(p))
            decisions.append({
                "ticker": p.ticker, "side": p.side, "decision": "pending_resting",
                "reason": "no_production_fill_trigger", "target_price": p.target, **shadow,
            })
            continue
        try:
            orderbook = client.get_orderbook(p.ticker, depth=20)
            ask = _best_ask_for_side(orderbook, p.side)
        except Exception:
            ask = None
        if ask is None or ask[0] > D(p.target):
            kept.append(asdict(p))
            decisions.append({
                "ticker": p.ticker, "side": p.side, "decision": "pending_resting",
                "reason": "ask_above_target", "target_price": p.target, **shadow,
            })
            continue
        target = D(p.target)
        filled, result = _paper_buy(
            portfolio,
            ticker=p.ticker,
            side=p.side,
            price=target,
            available=max(p.contracts, ask[1]),
            exposure_key=p.exposure_key,
            source_trade_id=p.source_trade_id,
            title=p.title,
            flow_contracts=p.flow_contracts,
            flow_notional_usd=p.flow_notional_usd,
            reference_price_cents=p.reference_price_cents,
            execution="MAKE",
            timing=_position_timing(p.ticker, p.side, market_cache.get(p.ticker), quick=False),
            meta=market_cache.get(p.ticker),
            entered_at=now,
        )
        decisions.append({
            "ticker": p.ticker, "side": p.side,
            "title": p.title, "target_price": p.target,
            **result,
            **shadow,
        })
        if not filled and result.get("reason") not in {"portfolio_guard", "insufficient_paper_risk_budget"}:
            kept.append(asdict(p))
    state["pending"] = kept
    return decisions


def _flow_age_seconds(flow, now):
    latest = flow.get("latest_time")
    if latest is None:
        return None
    if isinstance(latest, str):
        latest = _parse_time(latest)
    if latest is None:
        return None
    try:
        return max(0.0, (now - latest.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _seconds_to_close(meta, now):
    close_time = _parse_time(meta.get("close_time"))
    if close_time is None:
        return None
    return (close_time - now).total_seconds()


def _quick_v2_gate(
    *,
    reference_cents,
    current_bid,
    current_ask,
    flow_age_seconds,
    seconds_to_close,
    flow_trade_count,
    flow_notional_usd,
    block_trade=False,
    min_flow_age_seconds=8,
    max_flow_age_seconds=55,
    min_seconds_to_close=180,
    max_spread="0.04",
    max_adverse_mid_cents=1,
    max_follow_through_cents=2,
    min_flow_notional_usd=500,
    min_flow_trades=2,
):
    """Conservative confirmation gate for the second Quick Whale paper test."""
    try:
        ref = D(reference_cents) / D("100")
        bid = D(current_bid)
        ask = D(current_ask)
        age = float(flow_age_seconds)
        close_left = float(seconds_to_close)
        notional = D(flow_notional_usd)
        trades = int(flow_trade_count)
        spread_cap = D(max_spread)
        adverse = D(max_adverse_mid_cents) / D("100")
        follow = D(max_follow_through_cents) / D("100")
    except Exception:
        return {"eligible": False, "reason": "quick_v2_invalid_input"}

    if age < float(min_flow_age_seconds):
        return {"eligible": False, "reason": "quick_v2_wait_for_reaction"}
    if age > float(max_flow_age_seconds):
        return {"eligible": False, "reason": "quick_v2_flow_stale"}
    if close_left < float(min_seconds_to_close):
        return {"eligible": False, "reason": "quick_v2_too_close_to_close"}
    if not block_trade and trades < int(min_flow_trades):
        return {"eligible": False, "reason": "quick_v2_insufficient_flow_persistence"}
    if notional < D(min_flow_notional_usd):
        return {"eligible": False, "reason": "quick_v2_flow_too_small"}
    if not (D("0") < bid <= ask < D("1")):
        return {"eligible": False, "reason": "quick_v2_invalid_book"}

    spread = ask - bid
    if spread > spread_cap:
        return {"eligible": False, "reason": "quick_v2_spread_too_wide", "spread": str(spread)}

    midpoint = (bid + ask) / D("2")
    if midpoint < ref - adverse:
        return {
            "eligible": False,
            "reason": "quick_v2_adverse_move_after_whale",
            "midpoint": str(midpoint),
            "spread": str(spread),
        }
    if ask > ref + follow:
        return {
            "eligible": False,
            "reason": "quick_v2_follow_through_too_expensive",
            "midpoint": str(midpoint),
            "spread": str(spread),
        }
    return {
        "eligible": True,
        "reason": "quick_v2_confirmed",
        "midpoint": str(midpoint),
        "spread": str(spread),
        "confirmation_move": str(midpoint - ref),
    }


def _eligible_flow_rows(
    flows, client, state, portfolio, now, *,
    max_flows, slippage, mode="regular",
    max_flow_age_seconds=75, min_seconds_to_close=60,
    min_flow_age_seconds=8, max_spread="0.04",
    max_adverse_mid_cents=1, max_follow_through_cents=2,
    min_flow_notional_usd=500, min_flow_trades=2,
    reaction_queue=None,
):
    mode_name = str(mode).lower()
    quick_v3 = mode_name == "quick_v3"
    # Quick Whale v3 reuses v2's confirmation gate and caps verbatim.
    quick_v2 = mode_name in {"quick_v2", "quick_v3"}
    quick = mode_name in {"quick", "quick_v2", "quick_v3"}
    strategy = (
        "quick_whale_v3" if quick_v3
        else "quick_whale_v2" if quick_v2
        else ("quick_whale" if quick else "whale_flow")
    )
    cache = state.setdefault("market_meta", {})
    decisions = []
    pending_keys = {
        (str(x.get("ticker") or ""), str(x.get("side") or ""))
        for x in state.get("pending") or []
        if isinstance(x, dict)
    }
    # The regular Whale Flow lane prioritizes football. Quick Whale is the
    # opposite: it is explicitly a fast-financial-market lane, so fast tickers
    # must reach the inspection budget before unrelated football flow.
    if quick:
        ranked = sorted(
            flows,
            key=lambda x: (
                not whale_edge._is_fast_financial_market(x.get("ticker", "")),
                -float(x.get("flow_notional_usd") or 0),
            ),
        )
    else:
        ranked = sorted(
            flows,
            key=lambda x: (
                not whale_edge._is_football(x.get("ticker", "")),
                -float(x.get("flow_notional_usd") or 0),
            ),
        )

    evaluated_for_strategy = 0
    inspected = 0
    max_inspections = max(1, int(max_flows)) * (2 if quick else 1)
    for flow in ranked:
        ticker = str(flow.get("ticker") or "")
        side = str(flow.get("side") or "").upper()
        fast_from_ticker = whale_edge._is_fast_financial_market(ticker)
        if not quick and fast_from_ticker:
            continue

        row = {
            "ticker": ticker,
            "side": side,
            "source_trade_id": str(flow.get("trade_id") or ""),
            "flow_contracts": int(flow.get("flow_contracts") or 0),
            "flow_notional_usd": float(flow.get("flow_notional_usd") or 0),
            "flow_trade_count": int(flow.get("flow_trade_count") or 0),
            "reference_price_cents": int(flow.get("reference_price_cents") or 0),
            "football": bool(whale_edge._is_football(ticker)),
            "strategy": strategy,
        }

        if quick:
            inspected += 1
            if inspected > max_inspections:
                break

        if ticker in portfolio.positions or (ticker, side) in pending_keys:
            row.update({"decision": "skipped", "reason": "already_exposed_or_pending"})
            decisions.append(row)
            continue

        try:
            meta = _market_meta(client, ticker, cache)
            row["title"] = meta.get("title")
            row["market_title"] = meta.get("title")
            row["side_label"] = _position_label(meta, side)
            row["event_ticker"] = meta.get("event_ticker")
        except Exception as exc:
            row.update({
                "decision": "skipped",
                "reason": "market_metadata_unavailable",
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            })
            decisions.append(row)
            continue

        is_fast = fast_from_ticker or whale_edge._is_fast_financial_market(
            ticker, meta.get("title") or ""
        )
        if quick and not is_fast:
            continue
        if not quick and is_fast:
            continue

        evaluated_for_strategy += 1
        if evaluated_for_strategy > max(1, int(max_flows)):
            break

        if str(meta.get("status") or "").lower() not in {"open", "active"}:
            row.update({"decision": "skipped", "reason": "market_not_open"})
            decisions.append(row)
            continue

        if quick:
            age = _flow_age_seconds(flow, now)
            row["flow_age_seconds"] = age
            if age is None or age > float(max_flow_age_seconds):
                row.update({"decision": "skipped", "reason": "quick_flow_stale"})
                decisions.append(row)
                continue
            seconds_to_close = _seconds_to_close(meta, now)
            row["seconds_to_close"] = seconds_to_close
            if seconds_to_close is None or seconds_to_close < float(min_seconds_to_close):
                row.update({"decision": "skipped", "reason": "quick_market_too_close_to_close"})
                decisions.append(row)
                continue
        else:
            late_reason = whale_edge._late_entry_reason(ticker, side, now)
            if late_reason:
                row.update({"decision": "skipped", "reason": late_reason})
                decisions.append(row)
                continue

        exposure_key = str(meta.get("event_ticker") or ticker)
        if any(getattr(p, "exposure_key", None) == exposure_key for p in portfolio.positions.values()):
            row.update({"decision": "skipped", "reason": "same_event_exposure"})
            decisions.append(row)
            continue
        if any(str(x.get("exposure_key") or "") == exposure_key for x in state.get("pending") or []):
            row.update({"decision": "skipped", "reason": "same_event_pending"})
            decisions.append(row)
            continue

        try:
            orderbook = client.get_orderbook(ticker, depth=20)
            ask = _best_ask_for_side(orderbook, side)
        except Exception as exc:
            row.update({
                "decision": "skipped",
                "reason": "orderbook_unavailable",
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            })
            decisions.append(row)
            continue
        if ask is None:
            row.update({"decision": "skipped", "reason": "missing_executable_ask"})
            decisions.append(row)
            continue

        current_ask, available = ask
        current_bid_row = _best_bid_for_side(orderbook, side)
        current_bid = current_bid_row[0] if current_bid_row is not None else None
        if quick_v2:
            if current_bid is None:
                row.update({"decision": "skipped", "reason": "quick_v2_missing_executable_bid"})
                decisions.append(row)
                continue
            gate = _quick_v2_gate(
                reference_cents=row["reference_price_cents"],
                current_bid=current_bid,
                current_ask=current_ask,
                flow_age_seconds=row.get("flow_age_seconds"),
                seconds_to_close=row.get("seconds_to_close"),
                flow_trade_count=row.get("flow_trade_count"),
                flow_notional_usd=row.get("flow_notional_usd"),
                block_trade=bool(flow.get("block_trade")),
                min_flow_age_seconds=min_flow_age_seconds,
                max_flow_age_seconds=max_flow_age_seconds,
                min_seconds_to_close=min_seconds_to_close,
                max_spread=max_spread,
                max_adverse_mid_cents=max_adverse_mid_cents,
                max_follow_through_cents=max_follow_through_cents,
                min_flow_notional_usd=min_flow_notional_usd,
                min_flow_trades=min_flow_trades,
            )
            row.update({
                "current_bid_cents": int(round(float(current_bid) * 100)),
                "current_ask_cents": int(round(float(current_ask) * 100)),
                "confirmation_midpoint": gate.get("midpoint"),
                "confirmation_move": gate.get("confirmation_move"),
                "spread": gate.get("spread"),
            })
            if not gate.get("eligible"):
                if (
                    quick_v3
                    and reaction_queue is not None
                    and gate.get("reason") == "quick_v2_wait_for_reaction"
                ):
                    # Queue ONLY if waiting is the sole failure: re-run the same
                    # gate as if the reaction delay had elapsed right now.
                    probe = _quick_v2_gate(
                        reference_cents=row["reference_price_cents"],
                        current_bid=current_bid,
                        current_ask=current_ask,
                        flow_age_seconds=max(float(row.get("flow_age_seconds") or 0), float(min_flow_age_seconds)),
                        seconds_to_close=row.get("seconds_to_close"),
                        flow_trade_count=row.get("flow_trade_count"),
                        flow_notional_usd=row.get("flow_notional_usd"),
                        block_trade=bool(flow.get("block_trade")),
                        min_flow_age_seconds=min_flow_age_seconds,
                        max_flow_age_seconds=max_flow_age_seconds,
                        min_seconds_to_close=min_seconds_to_close,
                        max_spread=max_spread,
                        max_adverse_mid_cents=max_adverse_mid_cents,
                        max_follow_through_cents=max_follow_through_cents,
                        min_flow_notional_usd=min_flow_notional_usd,
                        min_flow_trades=min_flow_trades,
                    )
                    if probe.get("eligible"):
                        latest = flow.get("latest_time")
                        latest_dt = latest if isinstance(latest, datetime) else _parse_time(latest)
                        if latest_dt is not None:
                            reaction_queue.append({
                                "ticker": ticker, "side": side,
                                "trade_id": row["source_trade_id"],
                                "reference_price_cents": row["reference_price_cents"],
                                "flow_contracts": row["flow_contracts"],
                                "flow_notional_usd": row["flow_notional_usd"],
                                "flow_trade_count": row["flow_trade_count"],
                                "block_trade": bool(flow.get("block_trade")),
                                "latest_time": _iso_or_none(latest_dt),
                                "queued_at": _iso_or_none(now),
                                "eligible_at": _iso_or_none(
                                    latest_dt + timedelta(seconds=float(min_flow_age_seconds))
                                ),
                            })
                            del reaction_queue[:-V3_QUEUE_MAX]
                            row.update({"decision": "v3_reaction_queued", "reason": "quick_v2_wait_for_reaction"})
                            decisions.append(row)
                            continue
                    else:
                        row.update({
                            "decision": "skipped",
                            "reason": gate.get("reason"),
                            "v3_not_queued_reason": probe.get("reason"),
                        })
                        decisions.append(row)
                        continue
                row.update({"decision": "skipped", "reason": gate.get("reason")})
                decisions.append(row)
                continue
            ev = {
                "eligible": True,
                "reason": "quick_v2_confirmed",
                "execution": "TAKE",
                "target": float(current_ask),
                "current_ask_cents": int(round(float(current_ask) * 100)),
                "target_price_cents": int(round(float(current_ask) * 100)),
                "price_improvement_cents": row["reference_price_cents"] - int(round(float(current_ask) * 100)),
            }
        else:
            ev = whale_edge._evaluate_price(
                reference_cents=row["reference_price_cents"],
                current_ask=float(current_ask),
                model_fair_side=None,
            )
        row.update({
            "current_ask_cents": int(round(float(current_ask) * 100)),
            "execution": ev.get("execution"),
            "target_price_cents": ev.get("target_price_cents"),
            "price_improvement_cents": ev.get("price_improvement_cents"),
        })
        if not ev.get("eligible"):
            row.update({
                "decision": "skipped" if quick else ("wait" if ev.get("reason") == "wait_for_better_price" else "skipped"),
                "reason": "quick_price_moved_no_chase" if quick and ev.get("reason") == "wait_for_better_price" else ev.get("reason"),
            })
            decisions.append(row)
            continue

        target = D(ev.get("target"))
        if ev.get("execution") == "MAKE":
            if quick:
                row.update({"decision": "skipped", "reason": "quick_price_moved_no_chase"})
                decisions.append(row)
                continue
            contracts = max(1, int((D("10") / target).to_integral_value(rounding="ROUND_FLOOR")))
            start = whale_edge._trusted_start(ticker, side)
            max_expiry = now + timedelta(minutes=45)
            if start is not None:
                max_expiry = min(max_expiry, start - timedelta(minutes=5))
            if max_expiry <= now:
                row.update({"decision": "skipped", "reason": "maker_window_closed"})
                decisions.append(row)
                continue
            pending = PendingMaker(
                ticker=ticker,
                side=side,
                contracts=contracts,
                target=str(target),
                created_at=now.isoformat().replace("+00:00", "Z"),
                expires_at=max_expiry.isoformat().replace("+00:00", "Z"),
                source_trade_id=row["source_trade_id"],
                flow_contracts=row["flow_contracts"],
                flow_notional_usd=row["flow_notional_usd"],
                reference_price_cents=row["reference_price_cents"],
                title=str(meta.get("title") or ticker),
                exposure_key=exposure_key,
            )
            state.setdefault("pending", []).append(asdict(pending))
            pending_keys.add((ticker, side))
            row.update({
                "decision": "paper_maker_resting",
                "reason": "better_price_required",
                "target_price": str(target),
                "expires_at": pending.expires_at,
            })
            decisions.append(row)
            continue

        fill_price = min(D("0.9999"), current_ask + D(slippage))
        if quick and not quick_v2 and fill_price > D(row["reference_price_cents"]) / D("100"):
            row.update({
                "decision": "skipped",
                "reason": "quick_slippage_exceeds_whale_price",
                "simulated_fill_price_cents": float(fill_price * 100),
            })
            decisions.append(row)
            continue

        if quick_v2:
            v2_hard_max = (
                D(row["reference_price_cents"]) / D("100")
                + D(max_follow_through_cents) / D("100")
            )
            if fill_price > v2_hard_max:
                row.update({
                    "decision": "skipped",
                    "reason": "quick_v2_slippage_exceeds_confirmation_cap",
                    "simulated_fill_price_cents": float(fill_price * 100),
                })
                decisions.append(row)
                continue

        filled, result = _paper_buy(
            portfolio,
            ticker=ticker,
            side=side,
            price=fill_price,
            available=available,
            exposure_key=exposure_key,
            source_trade_id=row["source_trade_id"],
            title=str(meta.get("title") or ticker),
            flow_contracts=row["flow_contracts"],
            flow_notional_usd=row["flow_notional_usd"],
            reference_price_cents=row["reference_price_cents"],
            execution="TAKE",
            strategy=strategy,
            timing=_position_timing(ticker, side, meta, quick=quick),
            meta=meta,
            entered_at=now,
        )
        row.update(result)
        if filled:
            row["reason"] = (
                "quick_whale_v3_confirmed_entry"
                if quick_v3
                else "quick_whale_v2_confirmed_entry"
                if quick_v2
                else ("quick_whale_reproducible_price" if quick else "whale_flow_price_guard_passed")
            )
        decisions.append(row)

    return decisions


def _process_reaction_queue(queue, client, state, portfolio, now, *, allow_new_entries, gate_kwargs):
    """Quick Whale v3: re-evaluate flows that failed ONLY the reaction delay.

    Uses the identical v2 confirmation gate, caps and risk checks. Entries not
    yet eligible stay queued; everything else is resolved and logged.
    """
    decisions, kept = [], []
    max_age = float(gate_kwargs.get("max_flow_age_seconds") or 55)
    for item in list(queue):
        eligible_at = _parse_time(item.get("eligible_at"))
        latest = _parse_time(item.get("latest_time"))
        base = {
            "ticker": item.get("ticker"), "side": item.get("side"),
            "strategy": "quick_whale_v3", "v3_requeue_reevaluation": True,
            "v3_queued_at": item.get("queued_at"), "source_trade_id": item.get("trade_id"),
        }
        if eligible_at is None or latest is None:
            decisions.append({**base, "decision": "skipped", "reason": "v3_queue_invalid_entry"})
            continue
        if now < eligible_at:
            if (now - latest).total_seconds() > max_age:
                decisions.append({**base, "decision": "skipped", "reason": "quick_v2_flow_stale"})
            else:
                kept.append(item)
            continue
        if not allow_new_entries:
            decisions.append({**base, "decision": "skipped", "reason": "v3_new_entries_disabled"})
            continue
        flow = {
            "ticker": item.get("ticker"), "side": item.get("side"),
            "trade_id": item.get("trade_id"),
            "reference_price_cents": item.get("reference_price_cents"),
            "flow_contracts": item.get("flow_contracts"),
            "flow_notional_usd": item.get("flow_notional_usd"),
            "flow_trade_count": item.get("flow_trade_count"),
            "block_trade": bool(item.get("block_trade")),
            "latest_time": latest,
        }
        rows = _eligible_flow_rows([flow], client, state, portfolio, now, reaction_queue=None, **gate_kwargs)
        if not rows:
            rows = [{**base, "decision": "skipped", "reason": "v3_requeue_not_evaluated"}]
        for row in rows:
            row.update({k: v for k, v in base.items() if k not in row or k.startswith("v3_")})
        decisions.extend(rows)
    queue[:] = kept[-V3_QUEUE_MAX:]
    return decisions


def _live_bids(portfolio, client):
    bids = {}
    for ticker, position in portfolio.positions.items():
        try:
            bid = _best_bid_for_side(client.get_orderbook(ticker, depth=20), position.side)
        except Exception:
            bid = None
        if bid is not None:
            bids[ticker] = str(bid[0])
    return bids


def _status(state_path, runner_state_path, runs_path, client, *, strategy="whale_flow", scan_interval_seconds=None):
    portfolio = load_portfolio(state_path)
    state = _load_runner_state(runner_state_path)
    # Bounded reverse read; these run logs grow by one line per scan forever.
    runs = tail_jsonl(runs_path, 50)
    bids = _live_bids(portfolio, client)
    snap = portfolio.snapshot(bids=bids)
    positions = []
    cache = state.setdefault("market_meta", {})
    for ticker, pos in portfolio.positions.items():
        try:
            meta = _market_meta(client, ticker, cache)
        except Exception:
            meta = cache.get(ticker) or {}
        bid = D(bids.get(ticker, "0"))
        entry_cost = D(pos.contracts) * (pos.entry + pos.entry_fee)
        market_value = D(pos.contracts) * bid
        positions.append({
            "ticker": ticker,
            "side": pos.side,
            "contracts": pos.contracts,
            "entry": str(pos.entry),
            "entry_fee": str(pos.entry_fee),
            "current_bid": str(bid),
            "market_value": str(market_value),
            "open_pnl": str(market_value - entry_cost),
            "exposure_key": getattr(pos, "exposure_key", None),
            "market_title": meta.get("title"),
            "market_subtitle": meta.get("subtitle"),
            "side_label": _position_label(meta, pos.side),
            "event_ticker": meta.get("event_ticker"),
            "close_time": meta.get("close_time"),
            "thesis": getattr(pos, "thesis", None),
        })
    settled = [x for x in portfolio.ledger if str(x.get("action") or "").upper() == "SETTLE"]
    closed = _completed_trades(portfolio, lane_hint=strategy)
    wins = sum(1 for x in closed if D(x.get("pnl") or "0") > 0)
    losses = sum(1 for x in closed if D(x.get("pnl") or "0") < 0)
    interval = int(
        scan_interval_seconds
        if scan_interval_seconds is not None
        else os.environ.get(
            "WHALE_QUICK_SCAN_INTERVAL_SECONDS" if strategy == "quick_whale" else "WHALE_PAPER_SCAN_INTERVAL_SECONDS",
            "60" if strategy == "quick_whale" else "300",
        )
    )
    last_scan = _parse_time(state.get("last_scan_at"))
    last_scan_age = (_utcnow() - last_scan).total_seconds() if last_scan is not None else None
    return {
        "mode": "paper",
        "strategy": strategy,
        # Derived from the persisted scan time: the HTTP thread answering does
        # not prove the lane's worker thread is still scanning.
        "worker_online": bool(
            last_scan_age is not None and last_scan_age <= max(3 * interval, interval + 600)
        ),
        "last_scan_at": state.get("last_scan_at"),
        "last_scan_age_seconds": None if last_scan_age is None else round(last_scan_age, 1),
        "scan_interval_seconds": interval,
        "starting_bankroll": "500.00",
        "credit_policy": {
            "paid_odds_api_calls": 0,
            "source": "Kalshi public trade tape + public market/orderbook data",
            "monthly_paid_credit_target": 0,
        },
        "portfolio": snap,
        "positions": positions,
        "pending_orders": state.get("pending") or [],
        "reaction_queue_size": len(state.get("reaction_queue") or []),
        "record": f"{wins}-{losses}",
        "record_basis": "closed_positions_sold_or_settled",
        "closed_count": len(closed),
        "settled_count": len(settled),
        "blotter": portfolio.ledger[-100:],
        "last_run": runs[-1] if runs else None,
        "recent_runs": runs[-20:],
    }


def run_once(
    *,
    client=None,
    state_path="/data/whale-paper-portfolio.json",
    runner_state_path="/data/whale-paper-state.json",
    runs_path="/data/whale-paper-runs.jsonl",
    lookback_minutes=20,
    max_flows=30,
    slippage="0.005",
    mode="regular",
    max_flow_age_seconds=75,
    min_seconds_to_close=60,
    now=None,
    allow_new_entries=True,
    min_flow_age_seconds=8,
    max_spread="0.04",
    max_adverse_mid_cents=1,
    max_follow_through_cents=2,
    min_flow_notional_usd=500,
    min_flow_trades=2,
    quick_v2_max_settled_trades=25,
    quick_v2_max_realized_loss="50",
    max_reaction_wait_seconds=12,
    sleep_fn=None,
    clock=None,
    trade_tape=None,
):
    """One scan of a whale paper lane.

    quick_v2_max_* are the circuit-breaker limits for whichever quick
    confirmation lane is running (v2 or v3); each lane has its own portfolio
    file, so the settled count and realized P/L are never shared.
    """
    _assert_no_kalshi_write_credentials()
    fixed_now = now
    now = (now or _utcnow()).astimezone(timezone.utc)
    sleep_fn = sleep_fn or time.sleep
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    state = _load_runner_state(runner_state_path)

    settlements = _settle_finished_positions(portfolio, client)
    mode_name = str(mode).lower()
    quick_v3 = mode_name == "quick_v3"
    quick_v2 = mode_name == "quick_v2"
    breaker_prefix = "quick_v3" if quick_v3 else "quick_v2"
    circuit_breaker_reason = None
    if (quick_v2 or quick_v3) and allow_new_entries:
        settled_count = sum(
            1 for row in portfolio.ledger
            if str(row.get("action") or "").upper() == "SETTLE"
        )
        max_settled = max(1, int(quick_v2_max_settled_trades))
        max_loss = max(D("0"), D(quick_v2_max_realized_loss))
        if settled_count >= max_settled:
            allow_new_entries = False
            circuit_breaker_reason = f"{breaker_prefix}_review_sample_reached"
        elif portfolio.realized_pnl <= -max_loss:
            allow_new_entries = False
            circuit_breaker_reason = f"{breaker_prefix}_max_realized_loss"

    tape_diagnostics = {}
    prev_scan = _parse_time(state.get("last_scan_at"))
    window_min_ts = int(
        (now - timedelta(minutes=max(1, int(lookback_minutes)))).timestamp()
    )
    # Once a lane has a persisted scan watermark, older rows in the nominal
    # lookback have already been processed. Asking the shared collector only
    # for the continuity interval avoids reprocessing tens of thousands of
    # trades after seen_trade_ids rolls over.
    request_min_ts = window_min_ts
    if trade_tape is not None and prev_scan is not None:
        request_min_ts = max(
            window_min_ts,
            int((prev_scan - timedelta(seconds=2)).timestamp()),
        )

    if allow_new_entries:
        try:
            if trade_tape is not None:
                trades, tape_diagnostics = trade_tape.snapshot(
                    min_ts=request_min_ts,
                    now=now,
                )
                if not tape_diagnostics.get("trade_horizon_covered"):
                    trades = []
                    api_error = "SharedTradeTapeNotReady: requested continuity window not covered"
                else:
                    api_error = None
            else:
                trades = whale_edge._public_trades(
                    min_ts=request_min_ts,
                    diagnostics=tape_diagnostics,
                    now=now,
                )
                api_error = None

            # Trades are only safe to advance past if the tape reaches back to
            # the previous scan. Shared-tape snapshots report this explicitly.
            oldest_in = _parse_time(tape_diagnostics.get("trade_oldest_in_window_ts"))
            tape_diagnostics["trade_previous_scan_at"] = state.get("last_scan_at")
            tape_diagnostics["trade_request_min_ts"] = datetime.fromtimestamp(
                request_min_ts, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")
            tape_diagnostics["trade_covers_since_last_scan"] = (
                True if tape_diagnostics.get("trade_horizon_covered")
                else (None if prev_scan is None or oldest_in is None else oldest_in <= prev_scan)
            )
        except Exception as exc:
            trades = []
            api_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    else:
        trades = []
        api_error = None

    quick = mode_name in {"quick", "quick_v2", "quick_v3"}
    strategy = (
        "quick_whale_v3" if quick_v3
        else "quick_whale_v2" if quick_v2
        else ("quick_whale" if quick else "whale_flow")
    )
    pending_decisions = [] if quick else _process_pending(
        portfolio, client, state, trades, now, slippage
    )

    seen = set(state.get("seen_trade_ids") or [])
    fresh = [
        t for t in trades
        if str(t.get("trade_id") or "") and str(t.get("trade_id") or "") not in seen
    ]
    flows = whale_edge._aggregate(fresh)
    # Flow age / time-to-close are judged at the moment the tape is in hand.
    # Paging can take seconds; measuring from the scan start understated flow
    # age and weakened the stale-flow guard. (Fixed `now` in tests is kept.)
    eval_now = now if fixed_now is not None else _utcnow()
    gate_kwargs = dict(
        max_flows=max(1, int(max_flows)),
        slippage=slippage,
        mode=mode,
        max_flow_age_seconds=max_flow_age_seconds,
        min_seconds_to_close=min_seconds_to_close,
        min_flow_age_seconds=min_flow_age_seconds,
        max_spread=max_spread,
        max_adverse_mid_cents=max_adverse_mid_cents,
        max_follow_through_cents=max_follow_through_cents,
        min_flow_notional_usd=min_flow_notional_usd,
        min_flow_trades=min_flow_trades,
    )
    reaction_queue = state.setdefault("reaction_queue", []) if quick_v3 else None
    flow_decisions = _eligible_flow_rows(
        flows,
        client,
        state,
        portfolio,
        eval_now,
        max_flows=max(1, int(max_flows)),
        slippage=slippage,
        mode=mode,
        max_flow_age_seconds=max_flow_age_seconds,
        min_seconds_to_close=min_seconds_to_close,
        min_flow_age_seconds=min_flow_age_seconds,
        max_spread=max_spread,
        max_adverse_mid_cents=max_adverse_mid_cents,
        max_follow_through_cents=max_follow_through_cents,
        min_flow_notional_usd=min_flow_notional_usd,
        min_flow_trades=min_flow_trades,
        reaction_queue=reaction_queue,
    ) if api_error is None and allow_new_entries else []

    reaction_decisions = []
    if quick_v3 and reaction_queue:
        # Flows young enough to need a reaction delay become eligible within
        # seconds; the 60s scan cadence would always see them stale next time,
        # so wait (bounded) inside this scan and re-check with fresh books.
        due = [
            t for t in (_parse_time(q.get("eligible_at")) for q in reaction_queue)
            if t is not None and 0 < (t - eval_now).total_seconds() <= float(max_reaction_wait_seconds)
        ]
        recheck_now = eval_now
        if due and allow_new_entries:
            wait = max(0.0, (max(due) - eval_now).total_seconds())
            sleep_fn(wait)
            recheck_now = (
                clock().astimezone(timezone.utc) if clock is not None
                else (eval_now + timedelta(seconds=wait) if fixed_now is not None else _utcnow())
            )
        reaction_decisions = _process_reaction_queue(
            reaction_queue, client, state, portfolio, recheck_now,
            allow_new_entries=allow_new_entries and api_error is None,
            gate_kwargs=gate_kwargs,
        )

    for trade in trades:
        tid = str(trade.get("trade_id") or "")
        if tid:
            state.setdefault("seen_trade_ids", []).append(tid)
    # Never advance the continuity watermark across an uncovered/failed tape.
    if api_error is None:
        state["last_scan_at"] = now.isoformat().replace("+00:00", "Z")
    save_portfolio(portfolio, state_path)
    _save_runner_state(state, runner_state_path)

    all_decisions = pending_decisions + flow_decisions + reaction_decisions
    fills = sum(1 for x in all_decisions if x.get("decision") == "paper_filled")
    resting = sum(1 for x in all_decisions if x.get("decision") == "paper_maker_resting")
    decision_reasons = {}
    decision_counts = {}
    for item in all_decisions:
        reason = str(item.get("reason") or "unspecified")
        decision = str(item.get("decision") or "unspecified")
        decision_reasons[reason] = decision_reasons.get(reason, 0) + 1
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    # A 429 that survived retries mid-pagination no longer discards the tape:
    # the pages already fetched are used and the window is marked truncated
    # (trade_partial_after_error / trade_rate_limited). That case does NOT put
    # the lane into its long backoff; only a failed first page or a 429 on a
    # market/orderbook call does, exactly as before.
    rate_limited = bool(api_error and "429" in api_error) or any(
        "429" in str(x.get("error") or "") for x in all_decisions
    )
    run = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "evaluated_at": eval_now.isoformat().replace("+00:00", "Z"),
        "strategy": strategy,
        "lane_version": (
            LANE_VERSIONS.get(strategy, f"{strategy}_unversioned")
            + ("_shared_tape_v1" if trade_tape is not None else "")
        ),
        "new_entries_enabled": bool(allow_new_entries),
        "circuit_breaker_reason": circuit_breaker_reason,
        "public_trades": len(trades),
        "fresh_trades": len(fresh),
        "qualified_flows": len(flows),
        "paper_fills": fills,
        "maker_orders_resting": resting,
        "pending_orders": len(state.get("pending") or []),
        "reaction_queue_size": len(state.get("reaction_queue") or []) if quick_v3 else None,
        "trade_source": tape_diagnostics.get("trade_source") or "direct_pagination",
        "trade_tape": tape_diagnostics,
        "decision_reasons": decision_reasons,
        "decision_counts": decision_counts,
        "settlements": settlements,
        "api_error": api_error,
        "rate_limited": rate_limited,
        "credit_usage": {
            "paid_odds_api_calls": 0,
            "kalshi_public_trade_tape_calls": (
                0 if trade_tape is not None
                else (1 if api_error is None and allow_new_entries else 0)
            ),
        },
        "portfolio": portfolio.snapshot(),
        "decisions": all_decisions[-100:],
    }
    # Daily rotation + gzip + retention; each lane writes only its own log.
    append_jsonl(runs_path, run)
    prefix = (
        "QUICK_WHALE_V3_PAPER_RUN"
        if quick_v3 else "QUICK_WHALE_V2_PAPER_RUN"
        if quick_v2 else ("QUICK_WHALE_PAPER_RUN" if quick else "WHALE_PAPER_RUN")
    )
    print(prefix + " " + json.dumps({
        "observed_at": run["observed_at"],
        "strategy": strategy,
        "new_entries_enabled": bool(allow_new_entries),
        "circuit_breaker_reason": circuit_breaker_reason,
        "public_trades": run["public_trades"],
        "fresh_trades": run["fresh_trades"],
        "qualified_flows": run["qualified_flows"],
        "paper_fills": fills,
        "maker_orders_resting": resting,
        "pending_orders": run["pending_orders"],
        "paid_odds_api_calls": 0,
        "rate_limited": rate_limited,
        "api_error": api_error,
        "lane_version": run.get("lane_version"),
        "trade_pages_fetched": tape_diagnostics.get("trade_pages_fetched"),
        "trade_page_cap_hit": tape_diagnostics.get("trade_page_cap_hit"),
        "trade_horizon_covered": tape_diagnostics.get("trade_horizon_covered"),
        "trade_window_coverage_fraction": tape_diagnostics.get("trade_window_coverage_fraction"),
        "trade_covers_since_last_scan": tape_diagnostics.get("trade_covers_since_last_scan"),
        "trade_fetch_seconds": tape_diagnostics.get("trade_fetch_seconds"),
        "trade_page_order": tape_diagnostics.get("trade_page_order"),
        "trade_rate_limited": tape_diagnostics.get("trade_rate_limited"),
        "trade_rate_limit_retries": tape_diagnostics.get("trade_rate_limit_retries"),
        "trade_partial_after_error": tape_diagnostics.get("trade_partial_after_error"),
        "trade_source": run.get("trade_source"),
        "shared_collector_ready": tape_diagnostics.get("shared_collector_ready"),
        "decision_reasons": decision_reasons,
        "decision_counts": decision_counts,
    }, sort_keys=True), flush=True)
    return run


def _start_status_server(
    *,
    state_path,
    runner_state_path,
    runs_path,
    client,
    trigger_event,
    port,
):
    token = str(os.environ.get("WHALE_PAPER_API_TOKEN", "") or "").strip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _authorized(self):
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _send(self, status, payload):
            body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                return self._send(200, {"ok": True, "mode": "paper", "strategy": "whale_flow"})
            if not self._authorized():
                return self._send(401, {"detail": "Whale Paper authentication required"})
            if parsed.path == "/v1/status":
                return self._send(
                    200,
                    _status(state_path, runner_state_path, runs_path, client),
                )
            return self._send(404, {"detail": "Not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if not self._authorized():
                return self._send(401, {"detail": "Whale Paper authentication required"})
            if parsed.path == "/v1/run-scan":
                trigger_event.set()
                return self._send(202, {"accepted": True, "mode": "paper", "strategy": "whale_flow"})
            return self._send(404, {"detail": "Not found"})

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    thread = threading.Thread(target=server.serve_forever, name="whale-paper-http", daemon=True)
    thread.start()
    return server


def main():
    _assert_no_kalshi_write_credentials()
    parser = argparse.ArgumentParser(description="Always-on paper-only Whale Flow trader")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("WHALE_PAPER_SCAN_INTERVAL_SECONDS", "300")))
    parser.add_argument("--lookback-minutes", type=int, default=int(os.environ.get("WHALE_PAPER_LOOKBACK_MINUTES", "20")))
    parser.add_argument("--max-flows", type=int, default=int(os.environ.get("WHALE_PAPER_MAX_FLOWS_PER_SCAN", "30")))
    parser.add_argument("--slippage", default=os.environ.get("WHALE_PAPER_SLIPPAGE", "0.005"))
    parser.add_argument("--state", default=os.environ.get("WHALE_PAPER_PORTFOLIO_STATE", "/data/whale-paper-portfolio.json"))
    parser.add_argument("--runner-state", default=os.environ.get("WHALE_PAPER_RUNNER_STATE", "/data/whale-paper-state.json"))
    parser.add_argument("--runs", default=os.environ.get("WHALE_PAPER_RUNS", "/data/whale-paper-runs.jsonl"))
    args = parser.parse_args()
    if args.interval < 60 or args.lookback_minutes < 10 or args.max_flows < 1:
        parser.error("interval must be >=60, lookback >=10, max-flows >=1")

    client = KalshiPublicClient(PUBLIC_BASE)
    trigger_event = threading.Event()
    run_lock = threading.Lock()

    if not args.once and os.environ.get("WHALE_PAPER_HTTP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
        port = int(os.environ.get("PORT", os.environ.get("WHALE_PAPER_HTTP_PORT", "8080")))
        _start_status_server(
            state_path=args.state,
            runner_state_path=args.runner_state,
            runs_path=args.runs,
            client=client,
            trigger_event=trigger_event,
            port=port,
        )
        print(json.dumps({"whale_paper_http": "listening", "port": port, "mode": "paper"}), flush=True)

    while True:
        try:
            with run_lock:
                run_once(
                    client=client,
                    state_path=args.state,
                    runner_state_path=args.runner_state,
                    runs_path=args.runs,
                    lookback_minutes=args.lookback_minutes,
                    max_flows=args.max_flows,
                    slippage=args.slippage,
                )
        except Exception as exc:
            print("WHALE_PAPER_ERROR " + json.dumps({
                "error": type(exc).__name__,
                "message": str(exc)[:500],
            }), flush=True)
            if args.once:
                raise
        if args.once:
            return 0
        trigger_event.wait(timeout=args.interval)
        trigger_event.clear()


if __name__ == "__main__":
    raise SystemExit(main())
