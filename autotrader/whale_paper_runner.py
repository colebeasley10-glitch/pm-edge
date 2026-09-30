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
from dataclasses import dataclass, asdict
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
from autotrader.storage import load_portfolio, save_portfolio
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


def _default_runner_state():
    return {
        "schema": 1,
        "seen_trade_ids": [],
        "pending": [],
        "last_scan_at": None,
        "market_meta": {},
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
    return data


def _save_runner_state(state, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["schema"] = 1
    state["seen_trade_ids"] = list(dict.fromkeys(state.get("seen_trade_ids") or []))[-20000:]
    state["pending"] = list(state.get("pending") or [])[-200:]
    state["market_meta"] = dict(list((state.get("market_meta") or {}).items())[-500:])
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
):
    price = D(price)
    rough_fee = prediction_fee(1, price)
    contracts = _risk_contracts(portfolio, price, rough_fee, available)
    if contracts < 1:
        return False, {"decision": "risk_rejected", "reason": "insufficient_paper_risk_budget"}
    fee_total = prediction_fee(contracts, price)
    fee_per_contract = fee_total / D(contracts)
    thesis = {
        "source": "whale_flow",
        "source_trade_id": source_trade_id,
        "title": title,
        "flow_contracts": int(flow_contracts),
        "flow_notional_usd": float(flow_notional_usd),
        "reference_price_cents": int(reference_price_cents),
        "execution": execution,
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
        fair_snapshot_id=f"whale:{source_trade_id}" if source_trade_id else None,
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
        expiry = _parse_time(p.expires_at)
        if expiry is None or now >= expiry:
            decisions.append({
                "ticker": p.ticker, "side": p.side,
                "decision": "pending_expired", "reason": "maker_order_expired",
            })
            continue
        if p.ticker in portfolio.positions:
            continue
        touched = _trade_print_touches_target(trades, p)
        if not touched:
            kept.append(asdict(p))
            continue
        try:
            orderbook = client.get_orderbook(p.ticker, depth=20)
            ask = _best_ask_for_side(orderbook, p.side)
        except Exception:
            ask = None
        if ask is None or ask[0] > D(p.target):
            kept.append(asdict(p))
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
        )
        decisions.append({
            "ticker": p.ticker, "side": p.side,
            "title": p.title, "target_price": p.target,
            **result,
        })
        if not filled and result.get("reason") not in {"portfolio_guard", "insufficient_paper_risk_budget"}:
            kept.append(asdict(p))
    state["pending"] = kept
    return decisions


def _eligible_flow_rows(flows, client, state, portfolio, now, *, max_flows, slippage):
    cache = state.setdefault("market_meta", {})
    decisions = []
    pending_keys = {
        (str(x.get("ticker") or ""), str(x.get("side") or ""))
        for x in state.get("pending") or []
        if isinstance(x, dict)
    }
    ranked = sorted(
        flows,
        key=lambda x: (
            not whale_edge._is_football(x.get("ticker", "")),
            -float(x.get("flow_notional_usd") or 0),
        ),
    )[:max_flows]

    for flow in ranked:
        ticker = str(flow.get("ticker") or "")
        side = str(flow.get("side") or "").upper()
        row = {
            "ticker": ticker,
            "side": side,
            "source_trade_id": str(flow.get("trade_id") or ""),
            "flow_contracts": int(flow.get("flow_contracts") or 0),
            "flow_notional_usd": float(flow.get("flow_notional_usd") or 0),
            "flow_trade_count": int(flow.get("flow_trade_count") or 0),
            "reference_price_cents": int(flow.get("reference_price_cents") or 0),
            "football": bool(whale_edge._is_football(ticker)),
        }
        if whale_edge._is_fast_financial_market(ticker):
            row.update({"decision": "skipped", "reason": "fast_financial_market"})
            decisions.append(row)
            continue
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
            row.update({"decision": "skipped", "reason": "market_metadata_unavailable", "error": type(exc).__name__})
            decisions.append(row)
            continue
        if whale_edge._is_fast_financial_market(ticker, meta.get("title") or ""):
            row.update({"decision": "skipped", "reason": "fast_financial_market"})
            decisions.append(row)
            continue
        if str(meta.get("status") or "").lower() not in {"open", "active"}:
            row.update({"decision": "skipped", "reason": "market_not_open"})
            decisions.append(row)
            continue

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
            row.update({"decision": "skipped", "reason": "orderbook_unavailable", "error": type(exc).__name__})
            decisions.append(row)
            continue
        if ask is None:
            row.update({"decision": "skipped", "reason": "missing_executable_ask"})
            decisions.append(row)
            continue

        current_ask, available = ask
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
                "decision": "wait" if ev.get("reason") == "wait_for_better_price" else "skipped",
                "reason": ev.get("reason"),
            })
            decisions.append(row)
            continue

        target = D(ev.get("target"))
        if ev.get("execution") == "MAKE":
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
        )
        row.update(result)
        if filled:
            row["reason"] = "whale_flow_price_guard_passed"
        decisions.append(row)

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


def _status(state_path, runner_state_path, runs_path, client):
    portfolio = load_portfolio(state_path)
    state = _load_runner_state(runner_state_path)
    runs = []
    p = Path(runs_path)
    if p.exists():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-50:]:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    runs.append(value)
        except OSError:
            pass
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
    wins = sum(1 for x in settled if D(x.get("pnl") or "0") > 0)
    losses = sum(1 for x in settled if D(x.get("pnl") or "0") < 0)
    return {
        "mode": "paper",
        "strategy": "whale_flow",
        "worker_online": True,
        "scan_interval_seconds": int(os.environ.get("WHALE_PAPER_SCAN_INTERVAL_SECONDS", "300")),
        "starting_bankroll": "500.00",
        "credit_policy": {
            "paid_odds_api_calls": 0,
            "source": "Kalshi public trade tape + public market/orderbook data",
            "monthly_paid_credit_target": 0,
        },
        "portfolio": snap,
        "positions": positions,
        "pending_orders": state.get("pending") or [],
        "record": f"{wins}-{losses}",
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
    now=None,
):
    _assert_no_kalshi_write_credentials()
    now = (now or _utcnow()).astimezone(timezone.utc)
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    state = _load_runner_state(runner_state_path)

    settlements = _settle_finished_positions(portfolio, client)
    try:
        trades = whale_edge._public_trades(
            min_ts=int((now - timedelta(minutes=max(10, int(lookback_minutes)))).timestamp())
        )
        api_error = None
    except Exception as exc:
        trades = []
        api_error = f"{type(exc).__name__}: {str(exc)[:240]}"

    pending_decisions = _process_pending(
        portfolio, client, state, trades, now, slippage
    )

    seen = set(state.get("seen_trade_ids") or [])
    fresh = [
        t for t in trades
        if str(t.get("trade_id") or "") and str(t.get("trade_id") or "") not in seen
    ]
    flows = whale_edge._aggregate(fresh)
    flow_decisions = _eligible_flow_rows(
        flows,
        client,
        state,
        portfolio,
        now,
        max_flows=max(1, int(max_flows)),
        slippage=slippage,
    ) if api_error is None else []

    for trade in trades:
        tid = str(trade.get("trade_id") or "")
        if tid:
            state.setdefault("seen_trade_ids", []).append(tid)
    state["last_scan_at"] = now.isoformat().replace("+00:00", "Z")
    save_portfolio(portfolio, state_path)
    _save_runner_state(state, runner_state_path)

    all_decisions = pending_decisions + flow_decisions
    fills = sum(1 for x in all_decisions if x.get("decision") == "paper_filled")
    resting = sum(1 for x in all_decisions if x.get("decision") == "paper_maker_resting")
    run = {
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "public_trades": len(trades),
        "fresh_trades": len(fresh),
        "qualified_flows": len(flows),
        "paper_fills": fills,
        "maker_orders_resting": resting,
        "pending_orders": len(state.get("pending") or []),
        "settlements": settlements,
        "api_error": api_error,
        "credit_usage": {
            "paid_odds_api_calls": 0,
            "kalshi_public_trade_tape_calls": 1 if api_error is None else 0,
        },
        "portfolio": portfolio.snapshot(),
        "decisions": all_decisions[-100:],
    }
    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":"), default=str) + "\n")
    print("WHALE_PAPER_RUN " + json.dumps({
        "observed_at": run["observed_at"],
        "public_trades": run["public_trades"],
        "fresh_trades": run["fresh_trades"],
        "qualified_flows": run["qualified_flows"],
        "paper_fills": fills,
        "maker_orders_resting": resting,
        "pending_orders": run["pending_orders"],
        "paid_odds_api_calls": 0,
        "api_error": api_error,
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
