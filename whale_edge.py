"""PM Edge Whale Edge: follow verified Kalshi traders without blindly chasing.

This lane is intentionally separate from the sportsbook-derived PM Edge model.
It watches a curated roster of profitable public traders through KalshiSpy,
recovers the trader's recent entry price, re-checks the live Kalshi market, and
only creates a Whale Edge signal when our order can be entered at the trader's
price or better (or as a patient maker order below it).

No autonomous real-money order is placed here. Signals flow into the existing
mobile review/submit path and are tracked under lane="whale".
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dashboard_data
import signal_tracker
import watcher
from kalshispy_client import KalshiSpyClient, KalshiSpyError

STATE_PATH = Path(os.getenv("PM_EDGE_WHALE_STATE_PATH", ".pm_edge_whale_state.json"))
LEDGER_PATH = Path(os.getenv("PM_EDGE_WHALE_LEDGER_PATH", "pm_edge_whale_ledger.csv"))

DEFAULT_WATCHLIST = (
    "norrisfan,autofade,aggressive.doberman7942,attemptmost,lost.buck"
)
WATCHLIST = [
    x.strip()
    for x in os.getenv("PM_EDGE_WHALE_TRADERS", DEFAULT_WATCHLIST).split(",")
    if x.strip()
]
WATCHSET = {x.casefold() for x in WATCHLIST}
POLL_MINUTES = max(15, int(os.getenv("PM_EDGE_WHALE_POLL_MINUTES", "60")))
MIN_TRADE_CONTRACTS = max(0, int(os.getenv("PM_EDGE_WHALE_MIN_TRADE_CONTRACTS", "100")))
MIN_REFERENCE_CENTS = max(1, int(os.getenv("PM_EDGE_WHALE_MIN_REFERENCE_CENTS", "15")))
MAX_REFERENCE_CENTS = min(99, int(os.getenv("PM_EDGE_WHALE_MAX_REFERENCE_CENTS", "85")))
MAX_REFERENCE_AGE_MINUTES = max(
    30, int(os.getenv("PM_EDGE_WHALE_MAX_REFERENCE_AGE_MINUTES", "360"))
)
MAX_REST_GAP_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_MAX_REST_GAP_CENTS", "5")))
BETTER_BY_CENTS = max(0, int(os.getenv("PM_EDGE_WHALE_BETTER_BY_CENTS", "1")))
MODEL_DISAGREE_TOLERANCE = max(
    0.0, float(os.getenv("PM_EDGE_WHALE_MODEL_DISAGREE_TOLERANCE", "0.03"))
)
FOOTBALL_ONLY = os.getenv("PM_EDGE_WHALE_FOOTBALL_ONLY", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
MAX_NON_FOOTBALL_ALERTS_PER_RUN = max(
    0, int(os.getenv("PM_EDGE_WHALE_MAX_NON_FOOTBALL_ALERTS_PER_RUN", "2"))
)
SOURCE_VERSION = 29

LEDGER_FIELDS = [
    "observed_at",
    "decision",
    "reason",
    "nickname",
    "trade_id",
    "ticker",
    "side",
    "question",
    "football",
    "reference_price_cents",
    "current_ask_cents",
    "target_price_cents",
    "price_improvement_cents",
    "execution",
    "contracts",
    "notional_usd",
    "trader_lifetime_pnl_usd",
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


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "references": {}, "sent": {}, "ignored": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    payload.setdefault("references", {})
    payload.setdefault("sent", {})
    payload.setdefault("ignored", {})
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
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _poll_due(state: dict, *, now: datetime | None = None) -> bool:
    last = _parse_iso(state.get("last_poll_at"))
    if last is None:
        return True
    now = (now or _now()).astimezone(timezone.utc)
    return (now - last).total_seconds() >= POLL_MINUTES * 60


def _position_key(nickname: str, ticker: str, side: str) -> str:
    return f"{nickname.casefold()}|{ticker.upper()}|{side.upper()}"


def _is_football(ticker: str, question: str = "") -> bool:
    text = f"{ticker} {question}".upper()
    return bool(
        re.search(
            r"(^|[^A-Z])(NFL|NCAAF|CFB|COLLEGE FOOTBALL|FOOTBALL)([^A-Z]|$)",
            text,
        )
        or str(ticker).upper().startswith(("KXNFL", "KXNCAAF", "KXCFB"))
    )


def _trade_age_minutes(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b", text)
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(2)
    if unit.startswith("s"):
        return amount / 60.0
    if unit.startswith("m"):
        return amount
    if unit.startswith("h"):
        return amount * 60.0
    return amount * 1440.0


def _exact_trade(trades: list[dict], *, ticker: str, side: str) -> dict | None:
    ticker = str(ticker or "").upper()
    side = str(side or "").upper()
    candidates = [
        t for t in trades
        if str(t.get("ticker") or "").upper() == ticker
        and str(t.get("side") or "").upper() == side
        and not bool(t.get("market_closed"))
    ]
    if not candidates:
        return None
    return candidates[0]


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
    return yes if str(side).upper() == "YES" else 1.0 - yes


def _evaluate_price(
    *,
    reference_cents: int,
    current_ask: float | None,
    model_fair_side: float | None = None,
) -> dict:
    if reference_cents < MIN_REFERENCE_CENTS or reference_cents > MAX_REFERENCE_CENTS:
        return {"eligible": False, "reason": "reference_price_outside_band"}
    if current_ask is None or not (0.0 < current_ask < 1.0):
        return {"eligible": False, "reason": "missing_live_ask"}

    ref = reference_cents / 100.0
    ask_cents = int(round(current_ask * 100))

    if current_ask <= ref + 1e-9:
        target = current_ask
        execution = "TAKE"
    else:
        gap_cents = ask_cents - reference_cents
        if gap_cents > MAX_REST_GAP_CENTS:
            return {
                "eligible": False,
                "reason": "wait_for_better_price",
                "current_ask_cents": ask_cents,
            }
        target_cents = max(MIN_REFERENCE_CENTS, reference_cents - BETTER_BY_CENTS)
        target = target_cents / 100.0
        execution = "MAKE"

    if model_fair_side is not None and model_fair_side + MODEL_DISAGREE_TOLERANCE < target:
        return {
            "eligible": False,
            "reason": "pm_edge_model_disagrees",
            "current_ask_cents": ask_cents,
            "target_price_cents": int(round(target * 100)),
        }

    improvement = reference_cents - int(round(target * 100))
    model_edge = None if model_fair_side is None else model_fair_side - target
    return {
        "eligible": True,
        "reason": "price_ok",
        "execution": execution,
        "target": round(target, 4),
        "hard_max": round(ref, 4),
        "current_ask_cents": ask_cents,
        "target_price_cents": int(round(target * 100)),
        "price_improvement_cents": improvement,
        "model_edge": model_edge,
    }


def _ledger_base(
    *,
    nickname: str,
    position: dict,
    reference: dict | None = None,
) -> dict:
    return {
        "observed_at": _iso(),
        "nickname": nickname,
        "trade_id": str((reference or {}).get("id") or ""),
        "ticker": str(position.get("ticker") or ""),
        "side": str(position.get("side") or "").upper(),
        "question": str(position.get("question") or ""),
        "football": _is_football(position.get("ticker"), position.get("question")),
        "reference_price_cents": (reference or {}).get("price_cents"),
        "contracts": (reference or {}).get("contracts"),
        "notional_usd": (reference or {}).get("notional_usd"),
        "trader_lifetime_pnl_usd": (reference or {}).get("trader_lifetime_pnl_usd"),
    }


def _signal(
    *,
    nickname: str,
    position: dict,
    reference: dict,
    snapshot: dict,
    evaluation: dict,
) -> dict:
    ticker = str(position.get("ticker") or "")
    side = str(position.get("side") or "").upper()
    question = str(position.get("question") or snapshot.get("title") or ticker)
    football = _is_football(ticker, question)
    model_fair_side = _market_fair_side(ticker, side)

    fair_yes = None
    if model_fair_side is not None:
        fair_yes = model_fair_side if side == "YES" else 1.0 - model_fair_side

    fair_entry = {
        "event_id": str(reference.get("id") or f"whale-{ticker}"),
        "sport_key": "whale_football" if football else "whale_sports",
        "sport_title": "Football" if football else "Sports",
        "market_type": "whale_copy",
        "fair_yes_prob": fair_yes,
        "n_books": None,
        "dispersion": None,
        "fetched_at": _iso(),
        "commence_time": str(position.get("closes_at") or snapshot.get("close_time") or ""),
    }
    q = SimpleNamespace(
        side=side,
        execution=evaluation["execution"],
        recommended_limit=evaluation["target"],
        hard_max=evaluation["hard_max"],
        net_edge=evaluation.get("model_edge"),
        pmedge_risk_multiplier=0.5,
        pmedge_audit_bucket="whale_follow",
    )
    market = {"ticker": ticker, "title": question}
    result = signal_tracker.record_signal(
        market,
        fair_entry,
        q,
        lane="whale",
        source_version=SOURCE_VERSION,
    )
    return {**result, "model_fair_side": model_fair_side}


def _alert_message(
    *,
    nickname: str,
    position: dict,
    reference: dict,
    evaluation: dict,
    model_fair_side: float | None,
) -> str:
    side = str(position.get("side") or "").upper()
    question = str(position.get("question") or position.get("ticker") or "")
    ref = int(reference.get("price_cents") or 0)
    target = int(evaluation.get("target_price_cents") or 0)
    live = int(evaluation.get("current_ask_cents") or 0)
    lines = [
        f"@{nickname} bought {side} @ {ref}¢",
        f"{question[:180]}",
    ]
    if evaluation["execution"] == "TAKE":
        delta = ref - live
        price_text = f"Our ask: {live}¢"
        if delta > 0:
            price_text += f" — {delta}¢ BETTER"
        elif delta == 0:
            price_text += " — same price"
        lines.extend([
            price_text,
            f"BUY {side} @ {target}¢ OR LOWER",
        ])
    else:
        lines.extend([
            f"Current ask: {live}¢",
            f"REST BUY {side} @ {target}¢ — {ref - target}¢ better than whale",
            f"DO NOT CHASE ABOVE {ref}¢",
        ])
    if model_fair_side is not None:
        lines.append(f"PM Edge fair check: {model_fair_side * 100:.1f}%")
    else:
        lines.append("PM Edge fair check: unavailable; Whale lane tracked separately")
    contracts = _safe_int(reference.get("contracts"))
    if contracts:
        lines.append(f"Observed whale trade: {contracts:,} contracts")
    return "\n".join(lines)


def run_once(*, client: KalshiSpyClient | None = None, now: datetime | None = None) -> dict:
    now = (now or _now()).astimezone(timezone.utc)
    state = _load_state()
    if not _poll_due(state, now=now):
        return {"enabled": True, "polled": False, "signals": 0, "reason": "poll_interval"}

    client = client or KalshiSpyClient.from_env()
    if client is None:
        return {"enabled": False, "polled": False, "signals": 0, "reason": "missing_api_key"}

    try:
        whales = client.get_whales(limit=40, sort="lifetime_pnl")
    except KalshiSpyError as exc:
        print("WHALE_EDGE_API_ERROR", exc.status_code, str(exc)[:240])
        return {"enabled": True, "polled": False, "signals": 0, "reason": "api_error"}

    watched: list[tuple[str, dict]] = []
    for whale in whales:
        nickname = str(whale.get("nickname") or "")
        if nickname.casefold() not in WATCHSET:
            continue
        for position in whale.get("positions") or []:
            if not isinstance(position, dict):
                continue
            if str(position.get("status") or "open").lower() != "open":
                continue
            side = str(position.get("side") or "").upper()
            ticker = str(position.get("ticker") or "")
            if side not in {"YES", "NO"} or not ticker:
                continue
            watched.append((nickname, position))

    watched.sort(
        key=lambda x: (
            not _is_football(x[1].get("ticker"), x[1].get("question")),
            -(_safe_float(x[1].get("position_value_usd")) or 0.0),
        )
    )

    trades_by_trader: dict[str, list[dict]] = {}
    sent = 0
    nonfootball_sent = 0
    evaluated = 0
    open_keys = set()

    for nickname, position in watched:
        ticker = str(position.get("ticker") or "")
        side = str(position.get("side") or "").upper()
        key = _position_key(nickname, ticker, side)
        open_keys.add(key)
        football = _is_football(ticker, position.get("question"))

        if key in state["sent"] or key in state["ignored"]:
            continue
        if FOOTBALL_ONLY and not football:
            state["ignored"][key] = {"reason": "football_only", "at": _iso(now)}
            continue
        if not football and nonfootball_sent >= MAX_NON_FOOTBALL_ALERTS_PER_RUN:
            continue

        reference = state["references"].get(key)
        if not isinstance(reference, dict):
            if nickname not in trades_by_trader:
                try:
                    trades_by_trader[nickname] = client.search_trader_trades(
                        nickname,
                        min_amount=MIN_TRADE_CONTRACTS,
                    )
                except KalshiSpyError as exc:
                    print("WHALE_EDGE_TRADE_SEARCH_ERROR", nickname, exc.status_code, str(exc)[:200])
                    continue
            reference = _exact_trade(
                trades_by_trader[nickname],
                ticker=ticker,
                side=side,
            )
            if reference is None:
                row = _ledger_base(nickname=nickname, position=position)
                row.update({"decision": "SKIP", "reason": "no_matching_recent_trade"})
                _append_ledger(row)
                continue
            age = _trade_age_minutes(reference.get("placed_ago"))
            if age is not None and age > MAX_REFERENCE_AGE_MINUTES:
                row = _ledger_base(nickname=nickname, position=position, reference=reference)
                row.update({"decision": "SKIP", "reason": "reference_trade_too_old"})
                _append_ledger(row)
                state["ignored"][key] = {"reason": "reference_trade_too_old", "at": _iso(now)}
                continue
            state["references"][key] = reference

        ref_cents = _safe_int(reference.get("price_cents"))
        if ref_cents is None:
            continue

        try:
            snapshot = dashboard_data.market_snapshot(ticker)
        except Exception as exc:
            print("WHALE_EDGE_MARKET_ERROR", ticker, type(exc).__name__, str(exc)[:180])
            continue

        if str(snapshot.get("status") or "").lower() not in {"open", "active"}:
            state["ignored"][key] = {"reason": "market_not_open", "at": _iso(now)}
            continue

        ask = snapshot.get("yes_ask") if side == "YES" else snapshot.get("no_ask")
        model_fair_side = _market_fair_side(ticker, side)
        evaluation = _evaluate_price(
            reference_cents=ref_cents,
            current_ask=_safe_float(ask),
            model_fair_side=model_fair_side,
        )
        evaluated += 1

        row = _ledger_base(nickname=nickname, position=position, reference=reference)
        row.update({
            "current_ask_cents": evaluation.get("current_ask_cents"),
            "target_price_cents": evaluation.get("target_price_cents"),
            "price_improvement_cents": evaluation.get("price_improvement_cents"),
            "execution": evaluation.get("execution"),
            "model_fair_side": model_fair_side,
            "model_edge": evaluation.get("model_edge"),
        })

        if not evaluation.get("eligible"):
            row.update({"decision": "WAIT" if evaluation.get("reason") == "wait_for_better_price" else "SKIP",
                        "reason": evaluation.get("reason")})
            _append_ledger(row)
            if evaluation.get("reason") in {
                "reference_price_outside_band",
                "pm_edge_model_disagrees",
            }:
                state["ignored"][key] = {"reason": evaluation.get("reason"), "at": _iso(now)}
            continue

        signal = _signal(
            nickname=nickname,
            position=position,
            reference=reference,
            snapshot=snapshot,
            evaluation=evaluation,
        )
        if not signal.get("recorded") and signal.get("reason") != "duplicate":
            row.update({"decision": "ERROR", "reason": signal.get("reason")})
            _append_ledger(row)
            continue

        signal_id = str(signal.get("signal_id") or "")
        row.update({
            "decision": "SIGNAL",
            "reason": "whale_price_guard_passed",
            "signal_id": signal_id,
        })
        _append_ledger(row)

        title = "🐋 PM EDGE WHALE — FOOTBALL" if football else "🐋 PM EDGE WHALE — SPORTS"
        message = _alert_message(
            nickname=nickname,
            position=position,
            reference=reference,
            evaluation=evaluation,
            model_fair_side=signal.get("model_fair_side"),
        )
        watcher.send_push(title, message, priority=0)
        state["sent"][key] = {
            "signal_id": signal_id,
            "sent_at": _iso(now),
            "reference_price_cents": ref_cents,
            "target_price_cents": evaluation.get("target_price_cents"),
        }
        sent += 1
        if not football:
            nonfootball_sent += 1

    state["last_poll_at"] = _iso(now)
    # Keep state compact: only references for positions still open or already sent.
    state["references"] = {
        key: value for key, value in state["references"].items()
        if key in open_keys or key in state["sent"]
    }
    _save_state(state)
    result = {
        "enabled": True,
        "polled": True,
        "watched_positions": len(watched),
        "evaluated": evaluated,
        "signals": sent,
    }
    print("PM_EDGE_WHALE " + json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    result = run_once()
    if not result.get("enabled"):
        print(
            "PM_EDGE_WHALE_DISABLED missing KALSHISPY_API_KEY; "
            "Whale Edge remains dormant and makes no signals."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
