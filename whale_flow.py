"""PM Edge Whale Flow: free large-trade flow confirmation from Kalshi.

Whale Flow uses Kalshi's official unauthenticated public trades endpoint. It
does not identify or claim to identify individual traders. Instead it looks
for unusually large taker flow, aggregates repeated trades in the same market
and direction, checks the live Kalshi price, and requires PM Edge's independent
fair value to confirm positive edge before alerting.

Signals are recorded under lane="whale" so fills, unfilled orders, outcomes,
P/L and ROI remain separate from Primary, Standard and Fish. No autonomous
real-money order is placed here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dashboard_data
import signal_tracker
import watcher
from kalshi_public_trades import KalshiPublicTradesClient, KalshiPublicTradesError

STATE_PATH = Path(os.getenv("PM_EDGE_WHALE_FLOW_STATE_PATH", ".pm_edge_whale_flow_state.json"))
LEDGER_PATH = Path(os.getenv("PM_EDGE_WHALE_FLOW_LEDGER_PATH", "pm_edge_whale_flow_ledger.csv"))

POLL_MINUTES = max(5, int(os.getenv("PM_EDGE_WHALE_FLOW_POLL_MINUTES", "15")))
FLOW_WINDOW_MINUTES = max(5, int(os.getenv("PM_EDGE_WHALE_FLOW_WINDOW_MINUTES", "15")))
MAX_PAGES = max(1, min(10, int(os.getenv("PM_EDGE_WHALE_FLOW_MAX_PAGES", "3"))))
SINGLE_MIN_CONTRACTS = max(1.0, float(os.getenv("PM_EDGE_WHALE_FLOW_SINGLE_MIN_CONTRACTS", "500")))
SINGLE_MIN_NOTIONAL = max(1.0, float(os.getenv("PM_EDGE_WHALE_FLOW_SINGLE_MIN_NOTIONAL", "250")))
CLUSTER_MIN_TRADES = max(2, int(os.getenv("PM_EDGE_WHALE_FLOW_CLUSTER_MIN_TRADES", "3")))
CLUSTER_MIN_CONTRACTS = max(1.0, float(os.getenv("PM_EDGE_WHALE_FLOW_CLUSTER_MIN_CONTRACTS", "750")))
CLUSTER_MIN_NOTIONAL = max(1.0, float(os.getenv("PM_EDGE_WHALE_FLOW_CLUSTER_MIN_NOTIONAL", "300")))
MIN_REFERENCE_CENTS = max(1, int(os.getenv("PM_EDGE_WHALE_FLOW_MIN_REFERENCE_CENTS", "15")))
MAX_REFERENCE_CENTS = min(99, int(os.getenv("PM_EDGE_WHALE_FLOW_MAX_REFERENCE_CENTS", "85")))
MAX_REST_GAP_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_FLOW_MAX_REST_GAP_CENTS", "5")))
BETTER_BY_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_FLOW_BETTER_BY_CENTS", "1")))
MIN_MODEL_EDGE = max(0.0, float(os.getenv("PM_EDGE_WHALE_FLOW_MIN_MODEL_EDGE", "0.02")))
REQUIRE_MODEL_CONFIRMATION = os.getenv("PM_EDGE_WHALE_FLOW_REQUIRE_MODEL_CONFIRMATION", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
COOLDOWN_MINUTES = max(15, int(os.getenv("PM_EDGE_WHALE_FLOW_COOLDOWN_MINUTES", "90")))
MAX_SIGNALS_PER_RUN = max(1, int(os.getenv("PM_EDGE_WHALE_FLOW_MAX_SIGNALS_PER_RUN", "6")))
MAX_NON_FOOTBALL_ALERTS_PER_RUN = max(
    0, int(os.getenv("PM_EDGE_WHALE_FLOW_MAX_NON_FOOTBALL_ALERTS_PER_RUN", "2"))
)
SOURCE_VERSION = 30

LEDGER_FIELDS = [
    "observed_at",
    "decision",
    "reason",
    "flow_id",
    "ticker",
    "side",
    "question",
    "football",
    "trade_count",
    "contracts_total",
    "notional_total_usd",
    "largest_contracts",
    "largest_notional_usd",
    "block_trade_count",
    "reference_vwap_cents",
    "current_ask_cents",
    "target_price_cents",
    "price_improvement_cents",
    "execution",
    "model_fair_side",
    "model_edge",
    "signal_id",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "sent": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    payload.setdefault("sent", {})
    return payload


def _save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_ledger(row: dict, path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in LEDGER_FIELDS})


def _parse_iso(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _poll_due(state: dict, *, now: datetime) -> bool:
    last = _parse_iso(state.get("last_poll_at"))
    if last is None:
        return True
    return (now - last).total_seconds() >= POLL_MINUTES * 60


def _is_football(ticker: str, question: str = "") -> bool:
    text = f"{ticker} {question}".upper()
    return bool(
        re.search(r"(^|[^A-Z])(NFL|NCAAF|CFB|COLLEGE FOOTBALL|FOOTBALL)([^A-Z]|$)", text)
        or str(ticker).upper().startswith(("KXNFL", "KXNCAAF", "KXCFB"))
    )


def _trade_side(trade: dict) -> str | None:
    side = str(trade.get("taker_outcome_side") or trade.get("taker_side") or "").upper()
    return side if side in {"YES", "NO"} else None


def _trade_contracts(trade: dict) -> float:
    for key in ("count_fp", "count"):
        value = _safe_float(trade.get(key))
        if value is not None and value > 0:
            return value
    return 0.0


def _trade_price(trade: dict, side: str) -> float | None:
    keys = ("yes_price_dollars", "yes_price") if side == "YES" else ("no_price_dollars", "no_price")
    for key in keys:
        value = _safe_float(trade.get(key))
        if value is None:
            continue
        if value > 1:
            value /= 100.0
        if 0 < value < 1:
            return value
    return None


def _trade_time(trade: dict) -> datetime | None:
    return _parse_iso(trade.get("created_time"))


def _normalize_trade(trade: dict, *, now: datetime) -> dict | None:
    ticker = str(trade.get("ticker") or "").strip()
    side = _trade_side(trade)
    contracts = _trade_contracts(trade)
    price = _trade_price(trade, side or "") if side else None
    created = _trade_time(trade)
    if not ticker or side is None or contracts <= 0 or price is None or created is None:
        return None
    if created < now - timedelta(minutes=FLOW_WINDOW_MINUTES + 2) or created > now + timedelta(minutes=2):
        return None
    trade_id = str(trade.get("trade_id") or "")
    return {
        "trade_id": trade_id,
        "ticker": ticker,
        "side": side,
        "contracts": contracts,
        "price": price,
        "notional": contracts * price,
        "created_time": created,
        "is_block_trade": bool(trade.get("is_block_trade")),
    }


def _flow_id(trades: list[dict]) -> str:
    raw = "|".join(sorted(str(t.get("trade_id") or "") for t in trades))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _aggregate_flows(trades: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trade in trades:
        grouped[(trade["ticker"], trade["side"])].append(trade)

    flows: list[dict] = []
    for (ticker, side), rows in grouped.items():
        contracts_total = sum(float(r["contracts"]) for r in rows)
        notional_total = sum(float(r["notional"]) for r in rows)
        largest_contracts = max(float(r["contracts"]) for r in rows)
        largest_notional = max(float(r["notional"]) for r in rows)
        blocks = sum(1 for r in rows if r.get("is_block_trade"))
        vwap = notional_total / contracts_total if contracts_total > 0 else 0.0

        single_large = (
            largest_contracts >= SINGLE_MIN_CONTRACTS
            or largest_notional >= SINGLE_MIN_NOTIONAL
        )
        clustered = (
            len(rows) >= CLUSTER_MIN_TRADES
            and contracts_total >= CLUSTER_MIN_CONTRACTS
            and notional_total >= CLUSTER_MIN_NOTIONAL
        )
        if not (single_large or clustered or blocks > 0):
            continue

        flows.append({
            "flow_id": _flow_id(rows),
            "ticker": ticker,
            "side": side,
            "trades": rows,
            "trade_count": len(rows),
            "contracts_total": contracts_total,
            "notional_total": notional_total,
            "largest_contracts": largest_contracts,
            "largest_notional": largest_notional,
            "block_trade_count": blocks,
            "reference_price": vwap,
        })
    return flows


def _market_fair_side(ticker: str, side: str) -> float | None:
    try:
        state = watcher.load_state()
    except Exception:
        return None
    fair = (state.get("fair_cache") or {}).get(str(ticker))
    if not isinstance(fair, dict):
        return None
    yes = _safe_float(fair.get("fair_yes_prob"))
    if yes is None:
        return None
    return yes if side == "YES" else 1.0 - yes


def _evaluate_price(
    *,
    reference_price: float,
    current_ask: float | None,
    model_fair_side: float | None,
) -> dict:
    reference_cents = int(round(reference_price * 100))
    if reference_cents < MIN_REFERENCE_CENTS or reference_cents > MAX_REFERENCE_CENTS:
        return {"eligible": False, "reason": "reference_price_outside_band"}
    if current_ask is None or not (0.0 < current_ask < 1.0):
        return {"eligible": False, "reason": "missing_live_ask"}

    ask_cents = int(round(current_ask * 100))
    if current_ask <= reference_price + 1e-9:
        target = current_ask
        execution = "TAKE"
    else:
        if ask_cents - reference_cents > MAX_REST_GAP_CENTS:
            return {
                "eligible": False,
                "reason": "wait_for_better_price",
                "current_ask_cents": ask_cents,
            }
        target_cents = max(MIN_REFERENCE_CENTS, reference_cents - BETTER_BY_CENTS)
        target = target_cents / 100.0
        execution = "MAKE"

    if model_fair_side is None:
        if REQUIRE_MODEL_CONFIRMATION:
            return {
                "eligible": False,
                "reason": "no_pm_edge_confirmation",
                "current_ask_cents": ask_cents,
                "target_price_cents": int(round(target * 100)),
            }
        model_edge = None
    else:
        model_edge = model_fair_side - target
        if model_edge < MIN_MODEL_EDGE:
            return {
                "eligible": False,
                "reason": "insufficient_pm_edge",
                "current_ask_cents": ask_cents,
                "target_price_cents": int(round(target * 100)),
                "model_edge": model_edge,
            }

    return {
        "eligible": True,
        "reason": "flow_and_model_confirmed",
        "execution": execution,
        "target": round(target, 4),
        "hard_max": round(reference_price, 4),
        "current_ask_cents": ask_cents,
        "target_price_cents": int(round(target * 100)),
        "price_improvement_cents": reference_cents - int(round(target * 100)),
        "model_edge": model_edge,
    }


def _cooldown_active(state: dict, ticker: str, side: str, *, now: datetime) -> bool:
    item = (state.get("sent") or {}).get(f"{ticker.upper()}|{side}")
    if not isinstance(item, dict):
        return False
    sent_at = _parse_iso(item.get("sent_at"))
    if sent_at is None:
        return False
    return (now - sent_at).total_seconds() < COOLDOWN_MINUTES * 60


def _ledger_row(flow: dict, *, question: str = "") -> dict:
    return {
        "observed_at": _iso(),
        "flow_id": flow["flow_id"],
        "ticker": flow["ticker"],
        "side": flow["side"],
        "question": question,
        "football": _is_football(flow["ticker"], question),
        "trade_count": flow["trade_count"],
        "contracts_total": round(flow["contracts_total"], 4),
        "notional_total_usd": round(flow["notional_total"], 2),
        "largest_contracts": round(flow["largest_contracts"], 4),
        "largest_notional_usd": round(flow["largest_notional"], 2),
        "block_trade_count": flow["block_trade_count"],
        "reference_vwap_cents": round(flow["reference_price"] * 100, 2),
    }


def _record_signal(
    *,
    flow: dict,
    snapshot: dict,
    evaluation: dict,
    model_fair_side: float | None,
) -> dict:
    ticker = flow["ticker"]
    side = flow["side"]
    question = str(snapshot.get("title") or ticker)
    football = _is_football(ticker, question)
    fair_yes = None
    if model_fair_side is not None:
        fair_yes = model_fair_side if side == "YES" else 1.0 - model_fair_side

    fair_entry = {
        "event_id": f"whale-flow-{flow['flow_id']}",
        "sport_key": "whale_flow_football" if football else "whale_flow_sports",
        "sport_title": "Football" if football else "Sports",
        "market_type": "whale_flow",
        "fair_yes_prob": fair_yes,
        "n_books": None,
        "dispersion": None,
        "fetched_at": _iso(),
        "commence_time": str(snapshot.get("close_time") or ""),
    }
    q = SimpleNamespace(
        side=side,
        execution=evaluation["execution"],
        recommended_limit=evaluation["target"],
        hard_max=evaluation["hard_max"],
        net_edge=evaluation.get("model_edge"),
        pmedge_risk_multiplier=0.5,
        pmedge_audit_bucket="whale_flow",
    )
    return signal_tracker.record_signal(
        {"ticker": ticker, "title": question},
        fair_entry,
        q,
        lane="whale",
        source_version=SOURCE_VERSION,
    )


def _alert_message(
    *,
    flow: dict,
    snapshot: dict,
    evaluation: dict,
    model_fair_side: float,
) -> str:
    side = flow["side"]
    ref = int(round(flow["reference_price"] * 100))
    live = int(evaluation["current_ask_cents"])
    target = int(evaluation["target_price_cents"])
    edge = float(evaluation.get("model_edge") or 0.0)
    lines = [
        f"Public taker flow: {flow['contracts_total']:,.0f} contracts / ${flow['notional_total']:,.0f}",
        f"{flow['trade_count']} trade(s) in {FLOW_WINDOW_MINUTES}m — {side} VWAP {ref}¢",
        str(snapshot.get("title") or flow["ticker"])[:180],
    ]
    if evaluation["execution"] == "TAKE":
        delta = ref - live
        lines.append(f"Our ask: {live}¢" + (f" — {delta}¢ BETTER" if delta > 0 else " — same price"))
        lines.append(f"BUY {side} @ {target}¢ OR LOWER")
    else:
        lines.append(f"Current ask: {live}¢")
        lines.append(f"REST BUY {side} @ {target}¢ — do not chase above {ref}¢")
    lines.append(f"PM Edge fair: {model_fair_side * 100:.1f}% | edge at target: {edge * 100:+.1f}%")
    if flow["block_trade_count"]:
        lines.append(f"Block-trade prints in cluster: {flow['block_trade_count']}")
    return "\n".join(lines)


def run_once(
    *,
    client: KalshiPublicTradesClient | None = None,
    now: datetime | None = None,
) -> dict:
    now = (now or _now()).astimezone(timezone.utc)
    state = _load_state()
    if not _poll_due(state, now=now):
        return {"enabled": True, "polled": False, "signals": 0, "reason": "poll_interval"}

    client = client or KalshiPublicTradesClient.from_env()
    min_ts = int((now - timedelta(minutes=FLOW_WINDOW_MINUTES + 2)).timestamp())
    try:
        raw = client.get_recent_trades(min_ts=min_ts, max_pages=MAX_PAGES)
    except KalshiPublicTradesError as exc:
        print("WHALE_FLOW_API_ERROR", exc.status_code, str(exc)[:240])
        return {"enabled": True, "polled": False, "signals": 0, "reason": "api_error"}

    normalized = []
    seen_ids = set()
    for trade in raw:
        row = _normalize_trade(trade, now=now)
        if row is None:
            continue
        dedupe = row["trade_id"] or f"{row['ticker']}|{row['side']}|{row['created_time'].timestamp()}|{row['contracts']}|{row['price']}"
        if dedupe in seen_ids:
            continue
        seen_ids.add(dedupe)
        normalized.append(row)

    flows = _aggregate_flows(normalized)

    # Football first, then strongest dollar flow. Block prints get a large bonus.
    for flow in flows:
        flow["football_hint"] = _is_football(flow["ticker"])
        flow["score"] = (
            flow["notional_total"]
            + (100000.0 if flow["block_trade_count"] else 0.0)
            + (10000.0 if flow["football_hint"] else 0.0)
        )
    flows.sort(key=lambda x: x["score"], reverse=True)

    evaluated = 0
    signals = 0
    nonfootball_signals = 0
    waits = 0

    for flow in flows:
        if signals >= MAX_SIGNALS_PER_RUN:
            break
        ticker, side = flow["ticker"], flow["side"]
        if _cooldown_active(state, ticker, side, now=now):
            continue

        try:
            snapshot = dashboard_data.market_snapshot(ticker)
        except Exception as exc:
            row = _ledger_row(flow)
            row.update({"decision": "ERROR", "reason": f"market_snapshot:{type(exc).__name__}"})
            _append_ledger(row)
            continue

        question = str(snapshot.get("title") or ticker)
        football = _is_football(ticker, question)
        if not football and nonfootball_signals >= MAX_NON_FOOTBALL_ALERTS_PER_RUN:
            continue
        if str(snapshot.get("status") or "").lower() not in {"open", "active"}:
            row = _ledger_row(flow, question=question)
            row.update({"decision": "SKIP", "reason": "market_not_open"})
            _append_ledger(row)
            continue

        current_ask = snapshot.get("yes_ask") if side == "YES" else snapshot.get("no_ask")
        model_fair_side = _market_fair_side(ticker, side)
        evaluation = _evaluate_price(
            reference_price=flow["reference_price"],
            current_ask=_safe_float(current_ask),
            model_fair_side=model_fair_side,
        )
        evaluated += 1
        row = _ledger_row(flow, question=question)
        row.update({
            "current_ask_cents": evaluation.get("current_ask_cents"),
            "target_price_cents": evaluation.get("target_price_cents"),
            "price_improvement_cents": evaluation.get("price_improvement_cents"),
            "execution": evaluation.get("execution"),
            "model_fair_side": model_fair_side,
            "model_edge": evaluation.get("model_edge"),
        })

        if not evaluation.get("eligible"):
            row.update({"decision": "WAIT", "reason": evaluation.get("reason")})
            _append_ledger(row)
            waits += 1
            continue

        result = _record_signal(
            flow=flow,
            snapshot=snapshot,
            evaluation=evaluation,
            model_fair_side=model_fair_side,
        )
        if not result.get("recorded") and result.get("reason") != "duplicate":
            row.update({"decision": "ERROR", "reason": result.get("reason")})
            _append_ledger(row)
            continue

        signal_id = str(result.get("signal_id") or "")
        row.update({
            "decision": "SIGNAL",
            "reason": "public_flow_plus_pm_edge",
            "signal_id": signal_id,
        })
        _append_ledger(row)

        title = "🐋 PM EDGE WHALE FLOW — FOOTBALL" if football else "🐋 PM EDGE WHALE FLOW"
        watcher.send_push(
            title,
            _alert_message(
                flow=flow,
                snapshot=snapshot,
                evaluation=evaluation,
                model_fair_side=float(model_fair_side),
            ),
            priority=0,
        )
        state["sent"][f"{ticker.upper()}|{side}"] = {
            "sent_at": _iso(now),
            "signal_id": signal_id,
            "flow_id": flow["flow_id"],
            "reference_vwap_cents": round(flow["reference_price"] * 100, 2),
            "target_price_cents": evaluation.get("target_price_cents"),
        }
        signals += 1
        if not football:
            nonfootball_signals += 1

    # Prune cooldown history after one day.
    cutoff = now - timedelta(days=1)
    state["sent"] = {
        key: item for key, item in (state.get("sent") or {}).items()
        if isinstance(item, dict) and (_parse_iso(item.get("sent_at")) or cutoff) >= cutoff
    }
    state["last_poll_at"] = _iso(now)
    _save_state(state)

    result = {
        "enabled": True,
        "polled": True,
        "raw_trades": len(raw),
        "normalized_trades": len(normalized),
        "large_flow_clusters": len(flows),
        "evaluated": evaluated,
        "waits": waits,
        "signals": signals,
    }
    print("PM_EDGE_WHALE_FLOW " + json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
