"""Continuous isolated paper AutoTrader runner.

Reads public Kalshi orderbooks plus independent PM Edge fair values. This module
has no authenticated Kalshi client and cannot submit real-money orders.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

from pmedge.kalshi import KalshiPublicClient, normalize_market
from .collector import PUBLIC_BASE
from .engine import PaperEngine
from .probability import load_pm_edge_api, load_pm_edge_ledger
from .scanner import scan_public
from .storage import load_portfolio, save_portfolio
from .recorder import start_recorder_thread
from .recorder_store import RecorderStore
from .exit_engine import ExitInputs, evaluate_exit
from .fees import prediction_fee
from .thesis import PositionThesis
from .learning import update_learning_state, load_learning_state


def _utcnow():
    return datetime.now(timezone.utc)


_KALSHI_WRITE_ENV_NAMES = (
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_PRIVATE_KEY",
    "KALSHI_WRITE_API_KEY_ID",
    "KALSHI_WRITE_PRIVATE_KEY",
)
_KALSHI_SECRET_ENV_RE = re.compile(r"KALSHI.*(?:KEY|SECRET|PRIVATE|PEM)", re.IGNORECASE)


def _assert_no_kalshi_write_credentials():
    present = sorted({
        name
        for name, value in os.environ.items()
        if str(value or "").strip()
        and (name in _KALSHI_WRITE_ENV_NAMES or _KALSHI_SECRET_ENV_RE.search(name))
    })
    if present:
        raise RuntimeError(
            "Paper AutoTrader refuses to start with Kalshi write credentials present: "
            + ",".join(present)
        )


def _file_stats(path):
    p = Path(path)
    if not p.exists():
        return {"exists": False, "rows": 0, "mtime_ns": None, "size": 0}
    try:
        with p.open("r", encoding="utf-8", errors="replace") as handle:
            rows = max(0, sum(1 for _ in handle) - 1)
        st = p.stat()
        return {"exists": True, "rows": rows, "mtime_ns": st.st_mtime_ns, "size": st.st_size}
    except OSError as exc:
        return {"exists": True, "rows": None, "mtime_ns": None, "size": None, "error": str(exc)}


def _watcher_summary(stdout):
    prefix = "PM_EDGE_RUN_SUMMARY "
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix):])
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
    return {}


def _kalshi_schema_probe(client):
    """Public/read-only shape probe used when the fair-value funnel is empty."""
    try:
        markets = client.list_open_markets(max_markets=5)
        if not markets:
            return {"market_count": 0}
        raw = markets[0] if isinstance(markets[0], dict) else {}
        ticker = str(raw.get("ticker") or "")
        probe = {
            "market_count": len(markets),
            "ticker": ticker,
            "market_keys": sorted(str(k) for k in raw.keys())[:100],
        }
        if ticker:
            book = client.get_orderbook(ticker, depth=1)
            probe["orderbook_keys"] = sorted(str(k) for k in (book or {}).keys())[:50]
            fp = (book or {}).get("orderbook_fp")
            if isinstance(fp, dict):
                probe["orderbook_fp_keys"] = sorted(str(k) for k in fp.keys())[:50]
        return probe
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


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
            price = Decimal(str(level[0]))
            size = Decimal(str(level[1]))
        except Exception:
            continue
        if not (Decimal("0") < price < Decimal("1")) or size <= 0:
            continue
        if best is None or price > best[0]:
            best = (price, size)
    return best


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


def _manage_open_positions(portfolio, client, fair, *, now, slippage_per_contract="0.005"):
    """Evaluate thesis-based paper exits using fresh independent fair values."""
    decisions = []
    slippage = Decimal(str(slippage_per_contract or "0"))
    for ticker in list(portfolio.positions):
        position = portfolio.positions.get(ticker)
        if position is None:
            continue
        raw_thesis = getattr(position, "thesis", None)
        if not isinstance(raw_thesis, dict):
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "legacy_position_without_thesis",
            })
            continue
        try:
            thesis = PositionThesis.from_dict(raw_thesis)
        except Exception as exc:
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "invalid_thesis",
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
            })
            continue

        key = (ticker, position.side)
        side_prob = fair.probabilities.get(key)
        if side_prob is None:
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "no_fresh_independent_fair",
            })
            continue
        yes_fair = (
            Decimal(str(side_prob))
            if position.side == "YES"
            else Decimal("1") - Decimal(str(side_prob))
        )
        context = fair.contexts.get(key) or {}
        try:
            sigma = Decimal(str(context.get("sigma"))) if context.get("sigma") not in (None, "") else thesis.entry_sigma
        except Exception:
            sigma = thesis.entry_sigma

        try:
            orderbook = client.get_orderbook(ticker, depth=20)
            best = _best_bid_for_side(orderbook, position.side)
        except Exception as exc:
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "exit_orderbook_unavailable",
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
            })
            continue
        if best is None:
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "no_executable_exit_bid",
            })
            continue

        raw_bid, available = best
        executable_bid = max(Decimal("0"), raw_bid - slippage)
        if executable_bid <= 0:
            decisions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "slippage_consumes_exit_bid",
            })
            continue
        fee_total = prediction_fee(position.contracts, executable_bid)
        fee_per_contract = fee_total / Decimal(position.contracts)

        event_start = _parse_time(thesis.event_start_at)
        time_to_event = (
            (event_start - now).total_seconds() if event_start is not None else None
        )
        remaining_hours = Decimal(str(max(
            0.25,
            (time_to_event / 3600.0) if time_to_event is not None and time_to_event > 0
            else float(thesis.expected_hold_hours),
        )))
        fair_age = fair.ages.get(key, fair.ages.get(ticker))

        exit_decision = evaluate_exit(
            thesis,
            ExitInputs(
                current_fair=yes_fair,
                current_sigma=sigma,
                fair_age_seconds=fair_age,
                executable_bid=executable_bid,
                exit_fee_per_contract=fee_per_contract,
                exit_available=int(available),
                time_to_event_seconds=time_to_event,
                expected_remaining_hold_hours=remaining_hours,
                opportunity_cost_per_capital_hour=Decimal("0"),
                event_started=bool(event_start is not None and now >= event_start),
            ),
            contracts=position.contracts,
        )

        row = {
            "ticker": ticker,
            "action": exit_decision.action,
            "reason": exit_decision.reason,
            "raw_bid": str(raw_bid),
            "simulated_exit_bid": str(executable_bid),
            "available": int(available),
            "fee_total": str(fee_total),
            "hold_value": str(exit_decision.hold_value) if exit_decision.hold_value is not None else None,
            "exit_value": str(exit_decision.exit_value) if exit_decision.exit_value is not None else None,
            "opportunity_cost": str(exit_decision.opportunity_cost),
            "fair_age_seconds": fair_age,
        }

        if exit_decision.action in {"SELL", "EXIT_THESIS"}:
            sold = portfolio.sell(
                ticker,
                executable_bid,
                int(available),
                fee_per_contract=fee_per_contract,
                reason=exit_decision.reason,
            )
            row["executed"] = sold
            if not sold:
                row["reason"] = f"{exit_decision.reason}:insufficient_exit_depth_or_risk_check"
        elif exit_decision.action == "REDUCE":
            qty = max(1, int(position.contracts * float(exit_decision.qty_fraction)))
            sold = portfolio.sell_partial(
                ticker,
                min(qty, position.contracts),
                executable_bid,
                int(available),
                fee_per_contract=fee_per_contract,
                reason=exit_decision.reason,
            )
            row["executed"] = sold
            row["contracts_requested"] = qty
        else:
            row["executed"] = False
        decisions.append(row)

    return decisions


def _settle_finished_positions(portfolio, client):
    settled = []
    for ticker in list(portfolio.positions):
        try:
            market = normalize_market(client.get_market(ticker))
        except Exception:
            continue
        result = str(market.get("result") or "").upper()
        status = str(market.get("status") or "").lower()
        settlement_value = market.get("settlement_value")
        # "closed" only means trading ended. Wait for a finalized/settled state
        # and use Kalshi's settlement value when present so special 0<x<1
        # settlements are represented correctly.
        if status in ("finalized", "settled"):
            did_settle = False
            if settlement_value is not None:
                did_settle = portfolio.settle_value(ticker, settlement_value)
            elif result in ("YES", "NO"):
                did_settle = portfolio.settle(ticker, result)
            if did_settle:
                settled.append({
                    "ticker": ticker,
                    "winner": result or None,
                    "yes_settlement_value": settlement_value,
                    "status": status,
                })
    return settled


def _refresh_state(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_refresh_state(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _refresh_local_fair_values(*, ledger_path, refresh_state_path, now,
                               refresh_interval_seconds=1800, timeout=240):
    """Refresh a local PM Edge candidate ledger only when the upstream feed is empty.

    This launches the existing paper-only PM Edge model with a separate state
    file and no notification/order credentials. Odds usage is hard-capped by
    AUTOTRADER_ODDS_DAILY_BUDGET.
    """
    state = _refresh_state(refresh_state_path)
    dedicated_key = os.environ.get("AUTOTRADER_ODDS_API_KEY", "").strip()
    shared_key = os.environ.get("ODDS_API_KEY", "").strip()
    odds_api_key = dedicated_key or shared_key
    if not odds_api_key:
        return {"attempted": False, "reason": "odds_api_key_unavailable"}
    key_source = "dedicated" if dedicated_key else "shared"
    dedicated_matches_shared = bool(dedicated_key and shared_key and dedicated_key == shared_key)
    key_fingerprint = hashlib.sha256(odds_api_key.encode("utf-8")).hexdigest()[:12]

    previous = state.get("last_attempt")
    try:
        previous_dt = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
        if previous_dt.tzinfo is None:
            previous_dt = previous_dt.replace(tzinfo=timezone.utc)
    except Exception:
        previous_dt = None
    # A newly supplied dedicated key should be testable immediately; only the
    # same key is subject to the normal refresh cooldown.
    same_key = str(state.get("key_fingerprint") or "") == key_fingerprint
    if previous_dt is not None and same_key:
        age = (now - previous_dt.astimezone(timezone.utc)).total_seconds()
        if age < refresh_interval_seconds:
            return {
                "attempted": False,
                "reason": "refresh_cooldown",
                "age_seconds": max(0, age),
                "odds_key_source": key_source,
                "dedicated_matches_shared": dedicated_matches_shared,
            }

    attempt = now.isoformat()
    ledger_before = _file_stats(ledger_path)
    _write_refresh_state(
        refresh_state_path,
        {"last_attempt": attempt, "status": "running", "key_fingerprint": key_fingerprint},
    )
    env = os.environ.copy()
    root = Path(ledger_path).parent
    env.update({
        "ODDS_API_KEY": odds_api_key,
        "PM_EDGE_MODE": "paper",
        "PM_EDGE_EXECUTION_MODE": "preview",
        "PM_EDGE_ORDER_SUBMISSION_ENABLED": "0",
        "PUSHOVER_USER_KEY": "",
        "PUSHOVER_APP_TOKEN": "",
        "STREAMLIT_APP_URL": "",
        "PM_EDGE_CANDIDATE_LEDGER_PATH": str(ledger_path),
        "PM_EDGE_WATCH_STATE": str(root / "autotrader-pm-edge-watch-state.json"),
        "PM_EDGE_SIGNAL_LEDGER_PATH": str(root / "autotrader-pm-edge-signals.csv"),
        "PM_EDGE_AUTO_LEDGER_PATH": str(root / "autotrader-pm-edge-auto.csv"),
        "PM_EDGE_ODDS_DAILY_BUDGET": os.environ.get("AUTOTRADER_ODDS_DAILY_BUDGET", "6"),
        "PM_EDGE_MAX_ODDS_REFRESH_PER_RUN": os.environ.get("AUTOTRADER_MAX_ODDS_REFRESH_PER_RUN", "2"),
        "PM_EDGE_MAX_BULK_SPORTS_PER_RUN": os.environ.get("AUTOTRADER_MAX_BULK_SPORTS_PER_RUN", "1"),
        "PM_EDGE_MAX_MARKETS": os.environ.get("AUTOTRADER_FAIR_REFRESH_MAX_MARKETS", "1500"),
        "PM_EDGE_MAX_FAIR_TTL_SECONDS": os.environ.get(
            "AUTOTRADER_FAIR_MAX_AGE_SECONDS", "2100"
        ),
        "PM_EDGE_PREGAME_ONLY": "1",
        "PM_EDGE_REJECT_PUSHABLE_LINES": "1",
        "PM_EDGE_PREGAME_BUFFER_SECONDS": "300",
        # Belt-and-suspenders: the fallback process gets no Kalshi write creds,
        # even if somebody later adds one to a shared Railway scope.
        "KALSHI_API_KEY_ID": "",
        "KALSHI_PRIVATE_KEY_PATH": "",
        "KALSHI_PRIVATE_KEY": "",
        "KALSHI_WRITE_API_KEY_ID": "",
        "KALSHI_WRITE_PRIVATE_KEY": "",
        "KALSHI_WRITE_PRIVATE_KEY_PATH": "",
    })
    for name in list(env):
        if _KALSHI_SECRET_ENV_RE.search(name):
            env[name] = ""
    try:
        completed = subprocess.run(
            [sys.executable, "pm_edge_runner_v28.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        summary = _watcher_summary(completed.stdout)
        if completed.returncode != 0:
            status = "error"
        elif not summary:
            # A successful process exit without the structured watcher summary is
            # not proof that discovery/pricing actually ran.
            status = "missing_status"
        elif summary.get("odds_hard_failure") and any(
            "OUT_OF_USAGE_CREDITS" in str(item).upper()
            or "USAGE QUOTA HAS BEEN REACHED" in str(item).upper()
            for item in (summary.get("odds_error_samples") or [])
        ):
            status = "odds_quota_exhausted"
        else:
            status = str(summary.get("status") or "missing_status")
        result = {
            "attempted": True,
            "status": status,
            "returncode": completed.returncode,
            "odds_key_source": key_source,
            "dedicated_matches_shared": dedicated_matches_shared,
            "watcher_summary": summary,
            "ledger_before": ledger_before,
            "ledger_after": _file_stats(ledger_path),
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-1600:],
        }
    except subprocess.TimeoutExpired:
        result = {
            "attempted": True, "status": "timeout",
            "ledger_before": ledger_before, "ledger_after": _file_stats(ledger_path),
        }
    except Exception as exc:
        result = {
            "attempted": True, "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "ledger_before": ledger_before, "ledger_after": _file_stats(ledger_path),
        }

    _write_refresh_state(
        refresh_state_path,
        {
            "last_attempt": attempt,
            "status": result.get("status", result.get("reason", "unknown")),
            "key_fingerprint": key_fingerprint,
        },
    )
    return result


def _load_fair_values(*, api_base, api_token, ledger_path, now, max_age_seconds,
                      refresh_state_path=None, allow_refresh=False,
                      refresh_interval_seconds=1800, clock=None):
    api_error = None
    api_diagnostics = {}

    # Prefer the AutoTrader-owned local sportsbook ledger when it is fresh.
    # It is generated against the same current Kalshi universe used by this
    # worker. The shared mobile API can legitimately contain fresh timestamps
    # for signals tied to markets that are no longer open, which is unsuitable
    # as an execution universe for AutoTrader.
    local = load_pm_edge_ledger(
        ledger_path, now=now, max_age_seconds=max_age_seconds,
    )
    if local.probabilities:
        return local, "pm_edge_local_ledger", {
            "attempted": False, "reason": "local_fresh"
        }

    if api_base and api_token:
        try:
            snap = load_pm_edge_api(
                api_base, api_token, now=now,
                max_age_seconds=max_age_seconds,
            )
            api_diagnostics = getattr(snap, "diagnostics", {}) or {}
            if snap.probabilities:
                return snap, "pm_edge_mobile_api", {
                    "attempted": False,
                    "reason": "upstream_fresh",
                    "api_diagnostics": api_diagnostics,
                }
        except Exception as exc:
            api_error = f"{type(exc).__name__}: {exc}"

    refresh = {"attempted": False, "reason": "refresh_disabled"}
    if allow_refresh and refresh_state_path:
        refresh = _refresh_local_fair_values(
            ledger_path=ledger_path,
            refresh_state_path=refresh_state_path,
            now=now,
            refresh_interval_seconds=refresh_interval_seconds,
        )
        reload_now = (
            clock().astimezone(timezone.utc)
            if clock is not None else datetime.now(timezone.utc)
        )
        local = load_pm_edge_ledger(
            ledger_path, now=reload_now, max_age_seconds=max_age_seconds,
        )
        if local.probabilities:
            return local, "autotrader_local_pm_edge_refresh", refresh

    if api_error:
        refresh["api_error"] = api_error
    if api_diagnostics:
        refresh["api_diagnostics"] = api_diagnostics
    return local, "pm_edge_no_fresh_fair_values", refresh


def run_once(*, client=None, state_path="data/autotrader-paper.json",
             runs_path="data/autotrader-paper-runs.jsonl",
             ledger_path="pm_edge_candidate_ledger.csv", max_markets=3000,
             max_orderbooks=250, probability_max_age_seconds=300,
             fee_per_contract=None, slippage_per_contract="0.005",
             max_quote_age_seconds=5, min_net_edge="0.05",
             fair_api_base=None, fair_api_token=None, now=None, clock=None,
             refresh_state_path="/data/autotrader-fair-refresh.json",
             allow_fair_refresh=False, refresh_interval_seconds=1800,
             learning_state_path=None, recorder_path=None):
    fixed_now = now
    clock = clock or ((lambda: fixed_now) if fixed_now is not None else _utcnow)
    started_at = clock().astimezone(timezone.utc)
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    settled = _settle_finished_positions(portfolio, client)

    fair, provider, fair_refresh = _load_fair_values(
        api_base=fair_api_base, api_token=fair_api_token,
        ledger_path=ledger_path, now=started_at,
        max_age_seconds=probability_max_age_seconds,
        refresh_state_path=refresh_state_path,
        allow_refresh=allow_fair_refresh,
        refresh_interval_seconds=refresh_interval_seconds,
        clock=clock,
    )
    position_management = _manage_open_positions(
        portfolio,
        client,
        fair,
        now=started_at,
        slippage_per_contract=slippage_per_contract,
    )

    fair_tickers = {ticker for ticker, _side in fair.probabilities}
    scanner_diagnostics = {}
    candidates = scan_public(
        client,
        max_markets=max_markets,
        max_orderbooks=min(max_orderbooks, len(fair_tickers)) if fair_tickers else 0,
        clock=clock,
        ticker_filter=fair_tickers,
        diagnostics=scanner_diagnostics,
    ) if fair_tickers else []
    decision_at = clock().astimezone(timezone.utc)
    schema_probe = _kalshi_schema_probe(client) if not fair.probabilities else {}
    quote_ages = {}
    for c in candidates:
        if c.observed_at is None:
            continue
        quote_ages[(c.ticker, c.side)] = (decision_at - c.observed_at).total_seconds()

    engine_contexts = {}
    for key, value in (fair.contexts or {}).items():
        enriched = dict(value or {})
        enriched["decision_at"] = decision_at.isoformat().replace("+00:00", "Z")
        enriched["fair_source"] = fair.sources.get(key, enriched.get("fair_source"))
        enriched["fair_age_seconds"] = fair.ages.get(key, fair.ages.get(key[0]))
        enriched.setdefault("lane", "value_edge")
        enriched.setdefault("lane_version", "value_edge_v1")
        engine_contexts[key] = enriched

    engine = PaperEngine(portfolio=portfolio, min_net_edge=Decimal(str(min_net_edge)))
    result = engine.on_candidates(
        candidates,
        probabilities=fair.probabilities,
        quote_ages=quote_ages,
        event_data_ages=fair.ages,
        contexts=engine_contexts,
        fee_per_contract=fee_per_contract,
        slippage_per_contract=slippage_per_contract,
        max_quote_age_seconds=max_quote_age_seconds,
        max_event_data_age_seconds=probability_max_age_seconds,
    )
    save_portfolio(engine.portfolio, state_path)

    matched = sum(1 for c in candidates if (c.ticker, c.side) in fair.probabilities)
    quote_fresh = sum(
        1 for age in quote_ages.values()
        if age is not None and 0 <= float(age) <= float(max_quote_age_seconds)
    )
    risk_ok = sum(
        1 for d in engine.decisions
        if d.get("qualified") and d.get("decision") not in {
            "paper_risk_rejected", "insufficient_paper_risk_budget", "invalid_simulated_fill"
        }
    )
    decision_summary = {}
    for d in engine.decisions:
        reason = str(d.get("decision") or "unknown")
        decision_summary[reason] = decision_summary.get(reason, 0) + 1
    top_decisions = sorted(
        [
            {
                "ticker": d.get("ticker"),
                "side": d.get("side"),
                "decision": d.get("decision"),
                "qualified": bool(d.get("qualified")),
                "net_edge": d.get("net_edge"),
                "fair_probability": d.get("fair_probability"),
                "quote_age_seconds": d.get("quote_age_seconds"),
                "fair_age_seconds": d.get("fair_age_seconds"),
            }
            for d in engine.decisions
            if d.get("net_edge") is not None
        ],
        key=lambda d: Decimal(str(d.get("net_edge") or "-999")),
        reverse=True,
    )[:10]
    upstream_funnel = ((fair_refresh or {}).get("watcher_summary") or {}).get("funnel") or {}
    run = {
        "observed_at": decision_at.isoformat(),
        "provider": provider,
        "provider_scope": "fresh PM Edge sportsbook-derived fair values",
        "runtime_config": {
            "fair_max_age_seconds": int(probability_max_age_seconds),
            "refresh_interval_seconds": int(refresh_interval_seconds),
            "odds_daily_budget": int(os.environ.get("AUTOTRADER_ODDS_DAILY_BUDGET", "6")),
            "max_bulk_sports_per_run": int(os.environ.get("AUTOTRADER_MAX_BULK_SPORTS_PER_RUN", "1")),
            "max_event_refreshes_per_run": int(os.environ.get("AUTOTRADER_MAX_ODDS_REFRESH_PER_RUN", "2")),
            "min_net_edge": str(min_net_edge),
        },
        "fair_refresh": fair_refresh,
        "kalshi_schema_probe": schema_probe,
        "candidate_count": len(candidates),
        "scanner_diagnostics": scanner_diagnostics,
        "fresh_probability_count": len(fair.probabilities),
        "fair_diagnostics": (
            getattr(fair, "diagnostics", {}) or {}
            or (fair_refresh or {}).get("api_diagnostics") or {}
        ),
        "matched_candidate_count": matched,
        "paper_fills": result["paper_fills"],
        "decision_summary": decision_summary,
        "top_decisions": top_decisions,
        "funnel": {
            **upstream_funnel,
            "fresh_probability": len(fair.probabilities),
            "matched_candidate": matched,
            "quote_fresh": quote_fresh,
            "risk_ok": risk_ok,
            "filled": result["paper_fills"],
        },
        "settlements": settled,
        "position_management": position_management,
        "portfolio": result["portfolio"],
        "decisions": engine.decisions,
    }
    if learning_state_path:
        extra_sources = []
        for lane, lane_runs, lane_state in (
            (
                "whale_flow",
                os.environ.get("WHALE_PAPER_RUNS", "/data/whale-paper-runs.jsonl"),
                os.environ.get("WHALE_PAPER_PORTFOLIO_STATE", "/data/whale-paper-portfolio.json"),
            ),
            (
                "quick_whale",
                os.environ.get("WHALE_QUICK_RUNS", "/data/quick-whale-paper-runs.jsonl"),
                os.environ.get("WHALE_QUICK_PORTFOLIO_STATE", "/data/quick-whale-paper-portfolio.json"),
            ),
        ):
            try:
                if Path(lane_runs).exists() or Path(lane_state).exists():
                    extra_sources.append({
                        "lane": lane,
                        "runs_path": lane_runs,
                        "portfolio": load_portfolio(lane_state),
                    })
            except Exception:
                continue
        try:
            learning = update_learning_state(
                runs_path=runs_path,
                portfolio=engine.portfolio,
                state_path=learning_state_path,
                production_min_net_edge=min_net_edge,
                recorder_path=recorder_path,
                current_run=run,
                extra_sources=extra_sources,
            )
            run["learning"] = {
                "status": learning.get("status"),
                "production_rules_unchanged": True,
                "settled_trades": (learning.get("sample") or {}).get("settled_trades", 0),
                "clv_samples": (learning.get("sample") or {}).get("clv_samples", 0),
                "by_lane": ((learning.get("segments") or {}).get("by_lane") or {}),
                "by_execution": ((learning.get("segments") or {}).get("by_execution") or {}),
                "whale_by_flow_notional": ((learning.get("segments") or {}).get("whale_by_flow_notional") or {}),
                "challenger": learning.get("challenger"),
            }
        except Exception as exc:
            run["learning"] = {
                "status": "error",
                "production_rules_unchanged": True,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }

    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":")) + "\n")
    return run



def _tail_jsonl(path, limit=50):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    rows = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows[-max(1, int(limit)):]


def _fair_value_rows(path, limit=1000):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    try:
        with p.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    rows = [r for r in rows if str(r.get("stage") or "").lower() == "score"]
    rows.sort(key=lambda r: str(r.get("fair_fetched_at") or r.get("evaluated_at") or ""), reverse=True)
    seen = set()
    out = []
    for row in rows:
        key = (str(row.get("ticker") or ""), str(row.get("side") or "").upper())
        if not key[0] or key[1] not in {"YES", "NO"} or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _paper_status(state_path, runs_path, *, market_client=None, market_cache=None):
    portfolio = load_portfolio(state_path)
    runs = _tail_jsonl(runs_path, 50)
    last_run = runs[-1] if runs else None
    positions = []
    cache = market_cache if market_cache is not None else {}
    for ticker, pos in portfolio.positions.items():
        market = cache.get(ticker)
        if market is None and market_client is not None:
            try:
                market = normalize_market(market_client.get_market(ticker))
            except Exception:
                market = {}
            cache[ticker] = market
        market = market or {}
        side_label = (
            market.get("yes_sub_title")
            if str(pos.side).upper() == "YES"
            else market.get("no_sub_title")
        )
        positions.append({
            "ticker": ticker,
            "side": pos.side,
            "contracts": pos.contracts,
            "entry": str(pos.entry),
            "entry_fee": str(pos.entry_fee),
            "exposure_key": getattr(pos, "exposure_key", None),
            "fair_snapshot_id": getattr(pos, "fair_snapshot_id", None),
            "market_title": market.get("title") or None,
            "market_subtitle": market.get("subtitle") or None,
            "side_label": side_label or None,
            "event_ticker": market.get("event_ticker") or None,
            "close_time": market.get("close_time"),
        })
    settled = [x for x in portfolio.ledger if str(x.get("action") or "").upper() == "SETTLE"]
    wins = sum(1 for x in settled if Decimal(str(x.get("pnl") or "0")) > 0)
    losses = sum(1 for x in settled if Decimal(str(x.get("pnl") or "0")) < 0)
    recorder_status = {"enabled": False}
    if os.environ.get("AUTOTRADER_RECORDER_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
        recorder_path = os.environ.get(
            "AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"
        )
        try:
            recorder_store = RecorderStore(recorder_path)
            recorder_status = {
                "enabled": True,
                "db_path": recorder_path,
                "counts": recorder_store.counts(),
                "storage_bytes": recorder_store.storage_bytes(),
                "maker_probe_summary": recorder_store.maker_probe_summary(),
                "weather_calibration": recorder_store.weather_calibration_summary(),
            }
        except Exception as exc:
            recorder_status = {
                "enabled": True,
                "db_path": recorder_path,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
    return {
        "mode": "paper",
        "worker_online": True,
        "scan_interval_seconds": int(os.environ.get("AUTOTRADER_SCAN_INTERVAL_SECONDS", "300")),
        "starting_bankroll": "500.00",
        "portfolio": portfolio.snapshot(),
        "positions": positions,
        "record": f"{wins}-{losses}",
        "settled_count": len(settled),
        "blotter": portfolio.ledger[-100:],
        "last_run": last_run,
        "recent_runs": runs[-20:],
        "recorder": recorder_status,
        "learning": load_learning_state(
            os.environ.get("AUTOTRADER_LEARNING_STATE", "/data/autotrader-learning.json")
        ),
    }


def _start_status_server(*, state_path, runs_path, ledger_path, trigger_event, port, market_client=None,
                         whale_client=None, whale_trigger_event=None,
                         whale_state_path=None, whale_runner_state_path=None, whale_runs_path=None,
                         whale_scan_interval_seconds=None,
                         quick_whale_client=None, quick_whale_trigger_event=None,
                         quick_whale_state_path=None, quick_whale_runner_state_path=None,
                         quick_whale_runs_path=None, quick_whale_scan_interval_seconds=None):
    token = str(os.environ.get("AUTOTRADER_API_TOKEN", "") or "").strip()
    market_cache = {}

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
                return self._send(200, {"ok": True, "mode": "paper"})
            if not self._authorized():
                return self._send(401, {"detail": "AutoTrader authentication required"})
            if parsed.path == "/v1/status":
                return self._send(
                    200,
                    _paper_status(
                        state_path,
                        runs_path,
                        market_client=market_client,
                        market_cache=market_cache,
                    ),
                )
            if parsed.path == "/v1/whale-status":
                if whale_client is None or not whale_state_path or not whale_runner_state_path or not whale_runs_path:
                    return self._send(503, {"detail": "Whale paper worker unavailable"})
                from . import whale_paper_runner
                return self._send(
                    200,
                    whale_paper_runner._status(
                        whale_state_path,
                        whale_runner_state_path,
                        whale_runs_path,
                        whale_client,
                        strategy="whale_flow",
                        scan_interval_seconds=whale_scan_interval_seconds,
                    ),
                )
            if parsed.path == "/v1/quick-whale-status":
                if (
                    quick_whale_client is None
                    or not quick_whale_state_path
                    or not quick_whale_runner_state_path
                    or not quick_whale_runs_path
                ):
                    return self._send(503, {"detail": "Quick Whale paper worker unavailable"})
                from . import whale_paper_runner
                return self._send(
                    200,
                    whale_paper_runner._status(
                        quick_whale_state_path,
                        quick_whale_runner_state_path,
                        quick_whale_runs_path,
                        quick_whale_client,
                        strategy="quick_whale",
                        scan_interval_seconds=quick_whale_scan_interval_seconds,
                    ),
                )
            if parsed.path == "/v1/learning-status":
                return self._send(
                    200,
                    load_learning_state(
                        os.environ.get("AUTOTRADER_LEARNING_STATE", "/data/autotrader-learning.json")
                    ),
                )
            if parsed.path == "/v1/runs":
                try:
                    limit = min(100, max(1, int(parse_qs(parsed.query).get("limit", ["20"])[0])))
                except Exception:
                    limit = 20
                return self._send(200, {"items": _tail_jsonl(runs_path, limit)})
            if parsed.path == "/v1/fair-values":
                return self._send(200, {
                    "generated_at": _utcnow().isoformat().replace("+00:00", "Z"),
                    "items": _fair_value_rows(ledger_path),
                })
            return self._send(404, {"detail": "Not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if not self._authorized():
                return self._send(401, {"detail": "AutoTrader authentication required"})
            if parsed.path == "/v1/run-scan":
                trigger_event.set()
                return self._send(202, {"accepted": True, "mode": "paper"})
            if parsed.path == "/v1/whale-run-scan":
                if whale_trigger_event is None:
                    return self._send(503, {"detail": "Whale paper worker unavailable"})
                whale_trigger_event.set()
                return self._send(202, {"accepted": True, "mode": "paper", "strategy": "whale_flow"})
            if parsed.path == "/v1/quick-whale-run-scan":
                if quick_whale_trigger_event is None:
                    return self._send(503, {"detail": "Quick Whale paper worker unavailable"})
                quick_whale_trigger_event.set()
                return self._send(202, {"accepted": True, "mode": "paper", "strategy": "quick_whale"})
            return self._send(404, {"detail": "Not found"})

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    thread = threading.Thread(target=server.serve_forever, name="autotrader-status-http", daemon=True)
    thread.start()
    return server


def main():
    _assert_no_kalshi_write_credentials()
    parser = argparse.ArgumentParser(description="Isolated paper-only AutoTrader")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-markets", type=int, default=6000)
    parser.add_argument("--max-orderbooks", type=int, default=30)
    parser.add_argument("--state", default=os.environ.get("AUTOTRADER_STATE", "/data/autotrader-paper.json"))
    parser.add_argument("--runs", default=os.environ.get("AUTOTRADER_RUNS", "/data/autotrader-paper-runs.jsonl"))
    parser.add_argument("--ledger", default=os.environ.get("AUTOTRADER_LEDGER", "pm_edge_candidate_ledger.csv"))
    parser.add_argument("--fair-api-base", default=os.environ.get("AUTOTRADER_FAIR_API_BASE", ""))
    parser.add_argument("--fair-api-token", default=os.environ.get("AUTOTRADER_FAIR_API_TOKEN", ""))
    parser.add_argument("--probability-max-age", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_MAX_AGE_SECONDS", "300")))
    parser.add_argument("--max-quote-age", type=int, default=int(os.environ.get("AUTOTRADER_QUOTE_MAX_AGE_SECONDS", "5")))
    parser.add_argument("--fee", default=os.environ.get("AUTOTRADER_FEE_PER_CONTRACT", ""))
    parser.add_argument("--slippage", default=os.environ.get("AUTOTRADER_SLIPPAGE_PER_CONTRACT", "0.005"))
    parser.add_argument("--min-edge", default=os.environ.get("AUTOTRADER_MIN_NET_EDGE", "0.05"))
    parser.add_argument("--refresh-state", default=os.environ.get("AUTOTRADER_REFRESH_STATE", "/data/autotrader-fair-refresh.json"))
    parser.add_argument("--refresh-interval", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_REFRESH_INTERVAL_SECONDS", "1800")))
    parser.add_argument("--allow-fair-refresh", action="store_true", default=os.environ.get("AUTOTRADER_ALLOW_FAIR_REFRESH", "0").strip().lower() in {"1","true","yes","on"})
    args = parser.parse_args()
    if args.interval < 30 or args.max_markets < 1 or args.max_orderbooks < 1:
        parser.error("interval must be >= 30 seconds; market limits must be >= 1")

    client = KalshiPublicClient(PUBLIC_BASE)
    trigger_event = threading.Event()
    run_lock = threading.Lock()

    whale_enabled = os.environ.get("WHALE_PAPER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    whale_trigger_event = threading.Event() if whale_enabled else None
    whale_client = KalshiPublicClient(PUBLIC_BASE) if whale_enabled else None
    whale_state_path = os.environ.get("WHALE_PAPER_PORTFOLIO_STATE", "/data/whale-paper-portfolio.json")
    whale_runner_state_path = os.environ.get("WHALE_PAPER_RUNNER_STATE", "/data/whale-paper-state.json")
    whale_runs_path = os.environ.get("WHALE_PAPER_RUNS", "/data/whale-paper-runs.jsonl")
    whale_interval = max(60, int(os.environ.get("WHALE_PAPER_SCAN_INTERVAL_SECONDS", "300")))
    whale_lookback = max(10, int(os.environ.get("WHALE_PAPER_LOOKBACK_MINUTES", "20")))
    whale_max_flows = max(1, int(os.environ.get("WHALE_PAPER_MAX_FLOWS_PER_SCAN", "30")))
    whale_slippage = os.environ.get("WHALE_PAPER_SLIPPAGE", "0.005")

    quick_whale_enabled = os.environ.get("WHALE_QUICK_PAPER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    quick_whale_new_entries_enabled = os.environ.get(
        "WHALE_QUICK_NEW_ENTRIES_ENABLED", "1"
    ).strip().lower() in {"1", "true", "yes", "on"}
    quick_whale_trigger_event = threading.Event() if quick_whale_enabled else None
    quick_whale_client = KalshiPublicClient(PUBLIC_BASE) if quick_whale_enabled else None
    quick_whale_state_path = os.environ.get("WHALE_QUICK_PORTFOLIO_STATE", "/data/quick-whale-paper-portfolio.json")
    quick_whale_runner_state_path = os.environ.get("WHALE_QUICK_RUNNER_STATE", "/data/quick-whale-paper-state.json")
    quick_whale_runs_path = os.environ.get("WHALE_QUICK_RUNS", "/data/quick-whale-paper-runs.jsonl")
    quick_whale_interval = max(30, int(os.environ.get("WHALE_QUICK_SCAN_INTERVAL_SECONDS", "60")))
    quick_whale_lookback = max(1, int(os.environ.get("WHALE_QUICK_LOOKBACK_MINUTES", "2")))
    quick_whale_max_flows = max(1, int(os.environ.get("WHALE_QUICK_MAX_FLOWS_PER_SCAN", "8")))
    quick_whale_slippage = os.environ.get("WHALE_QUICK_SLIPPAGE", "0.005")
    quick_whale_max_age = max(5, int(os.environ.get("WHALE_QUICK_MAX_FLOW_AGE_SECONDS", "75")))
    quick_whale_min_close = max(15, int(os.environ.get("WHALE_QUICK_MIN_SECONDS_TO_CLOSE", "60")))
    quick_whale_backoff = max(
        quick_whale_interval,
        int(os.environ.get("WHALE_QUICK_RATE_LIMIT_BACKOFF_SECONDS", "180")),
    )

    if whale_enabled:
        from . import whale_paper_runner

        def whale_loop():
            print(json.dumps({
                "whale_paper": "starting",
                "mode": "paper",
                "interval_seconds": whale_interval,
                "paid_odds_api_calls": 0,
            }), flush=True)
            while True:
                try:
                    whale_paper_runner.run_once(
                        client=whale_client,
                        state_path=whale_state_path,
                        runner_state_path=whale_runner_state_path,
                        runs_path=whale_runs_path,
                        lookback_minutes=whale_lookback,
                        max_flows=whale_max_flows,
                        slippage=whale_slippage,
                    )
                except Exception as exc:
                    print(json.dumps({
                        "whale_paper_error": type(exc).__name__,
                        "message": str(exc)[:300],
                    }), flush=True)
                whale_trigger_event.wait(timeout=whale_interval)
                whale_trigger_event.clear()

        threading.Thread(
            target=whale_loop,
            name="whale-paper-worker",
            daemon=True,
        ).start()

    if quick_whale_enabled:
        from . import whale_paper_runner

        def quick_whale_loop():
            print(json.dumps({
                "quick_whale_paper": "starting",
                "mode": "paper",
                "interval_seconds": quick_whale_interval,
                "max_flow_age_seconds": quick_whale_max_age,
                "min_seconds_to_close": quick_whale_min_close,
                "paid_odds_api_calls": 0,
                "new_entries_enabled": quick_whale_new_entries_enabled,
            }), flush=True)
            while True:
                wait_seconds = quick_whale_interval
                try:
                    result = whale_paper_runner.run_once(
                        client=quick_whale_client,
                        state_path=quick_whale_state_path,
                        runner_state_path=quick_whale_runner_state_path,
                        runs_path=quick_whale_runs_path,
                        lookback_minutes=quick_whale_lookback,
                        max_flows=quick_whale_max_flows,
                        slippage=quick_whale_slippage,
                        mode="quick",
                        max_flow_age_seconds=quick_whale_max_age,
                        min_seconds_to_close=quick_whale_min_close,
                        allow_new_entries=quick_whale_new_entries_enabled,
                    )
                    if result.get("rate_limited"):
                        wait_seconds = quick_whale_backoff
                except Exception as exc:
                    print(json.dumps({
                        "quick_whale_paper_error": type(exc).__name__,
                        "message": str(exc)[:300],
                    }), flush=True)
                    if "429" in str(exc):
                        wait_seconds = quick_whale_backoff
                quick_whale_trigger_event.wait(timeout=wait_seconds)
                quick_whale_trigger_event.clear()

        threading.Thread(
            target=quick_whale_loop,
            name="quick-whale-paper-worker",
            daemon=True,
        ).start()

    recorder = None
    recorder_thread = None
    recorder_enabled = (
        not args.once
        and os.environ.get("AUTOTRADER_RECORDER_ENABLED", "1").strip().lower()
        not in {"0", "false", "no", "off"}
    )
    if recorder_enabled:
        try:
            recorder, recorder_thread = start_recorder_thread()
            print(
                json.dumps(
                    {
                        "autotrader_recorder": "thread_started",
                        "db_path": recorder.store.path,
                        "mode": "paper",
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "autotrader_recorder": "startup_error",
                        "error": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                ),
                flush=True,
            )
            if os.environ.get("AUTOTRADER_RECORDER_REQUIRED", "0").strip().lower() in {
                "1", "true", "yes", "on"
            }:
                raise

    if not args.once and os.environ.get("AUTOTRADER_HTTP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
        http_port = int(os.environ.get("PORT", os.environ.get("AUTOTRADER_HTTP_PORT", "8080")))
        _start_status_server(
            state_path=args.state,
            runs_path=args.runs,
            ledger_path=args.ledger,
            trigger_event=trigger_event,
            port=http_port,
            market_client=client,
            whale_client=whale_client,
            whale_trigger_event=whale_trigger_event,
            whale_state_path=whale_state_path if whale_enabled else None,
            whale_runner_state_path=whale_runner_state_path if whale_enabled else None,
            whale_runs_path=whale_runs_path if whale_enabled else None,
            whale_scan_interval_seconds=whale_interval if whale_enabled else None,
            quick_whale_client=quick_whale_client,
            quick_whale_trigger_event=quick_whale_trigger_event,
            quick_whale_state_path=quick_whale_state_path if quick_whale_enabled else None,
            quick_whale_runner_state_path=quick_whale_runner_state_path if quick_whale_enabled else None,
            quick_whale_runs_path=quick_whale_runs_path if quick_whale_enabled else None,
            quick_whale_scan_interval_seconds=quick_whale_interval if quick_whale_enabled else None,
        )
        print(json.dumps({"autotrader_http": "listening", "port": http_port, "mode": "paper"}), flush=True)

    while True:
        try:
            with run_lock:
                result = run_once(
                    client=client,
                    state_path=args.state,
                    runs_path=args.runs,
                    ledger_path=args.ledger,
                    max_markets=args.max_markets,
                    max_orderbooks=args.max_orderbooks,
                    probability_max_age_seconds=args.probability_max_age,
                    fee_per_contract=args.fee,
                    slippage_per_contract=args.slippage,
                    max_quote_age_seconds=args.max_quote_age,
                    min_net_edge=args.min_edge,
                    fair_api_base=args.fair_api_base,
                    fair_api_token=args.fair_api_token,
                    refresh_state_path=args.refresh_state,
                    allow_fair_refresh=args.allow_fair_refresh,
                    refresh_interval_seconds=args.refresh_interval,
                    learning_state_path=os.environ.get(
                        "AUTOTRADER_LEARNING_STATE", "/data/autotrader-learning.json"
                    ),
                    recorder_path=os.environ.get(
                        "AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"
                    ),
                )
            print(json.dumps({k: v for k, v in result.items() if k != "decisions"}), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), flush=True)
            if args.once:
                raise
        if args.once:
            return
        trigger_event.wait(args.interval)
        trigger_event.clear()


if __name__ == "__main__":
    main()
