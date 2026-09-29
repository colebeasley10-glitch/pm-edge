"""Continuous isolated paper AutoTrader runner.

Reads public Kalshi orderbooks plus independent PM Edge fair values. This module
has no authenticated Kalshi client and cannot submit real-money orders.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

from pmedge.kalshi import KalshiPublicClient, normalize_market
from .collector import PUBLIC_BASE
from .engine import PaperEngine
from .probability import load_pm_edge_api, load_pm_edge_ledger
from .scanner import scan_public
from .storage import load_portfolio, save_portfolio


def _utcnow():
    return datetime.now(timezone.utc)


def _settle_finished_positions(portfolio, client):
    settled = []
    for ticker in list(portfolio.positions):
        try:
            market = normalize_market(client.get_market(ticker))
        except Exception:
            continue
        result = str(market.get("result") or "").upper()
        status = str(market.get("status") or "").lower()
        if result in ("YES", "NO") and status not in ("active", "open"):
            if portfolio.settle(ticker, result):
                settled.append({"ticker": ticker, "winner": result})
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
    previous = state.get("last_attempt")
    try:
        previous_dt = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
        if previous_dt.tzinfo is None:
            previous_dt = previous_dt.replace(tzinfo=timezone.utc)
    except Exception:
        previous_dt = None
    if previous_dt is not None:
        age = (now - previous_dt.astimezone(timezone.utc)).total_seconds()
        if age < refresh_interval_seconds:
            return {"attempted": False, "reason": "refresh_cooldown", "age_seconds": max(0, age)}

    if not os.environ.get("ODDS_API_KEY", "").strip():
        return {"attempted": False, "reason": "odds_api_key_unavailable"}

    attempt = now.isoformat()
    _write_refresh_state(refresh_state_path, {"last_attempt": attempt, "status": "running"})
    env = os.environ.copy()
    root = Path(ledger_path).parent
    env.update({
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
    })
    try:
        completed = subprocess.run(
            [sys.executable, "pm_edge_runner_v28.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status = "ok" if completed.returncode == 0 else "failed"
        result = {
            "attempted": True,
            "status": status,
            "returncode": completed.returncode,
            "stdout_tail": (completed.stdout or "")[-1200:],
            "stderr_tail": (completed.stderr or "")[-800:],
        }
    except subprocess.TimeoutExpired:
        result = {"attempted": True, "status": "timeout"}
    except Exception as exc:
        result = {"attempted": True, "status": "error", "error": f"{type(exc).__name__}: {exc}"}

    _write_refresh_state(
        refresh_state_path,
        {"last_attempt": attempt, "status": result.get("status", result.get("reason", "unknown"))},
    )
    return result


def _load_fair_values(*, api_base, api_token, ledger_path, now, max_age_seconds,
                      refresh_state_path=None, allow_refresh=False,
                      refresh_interval_seconds=1800):
    api_error = None
    if api_base and api_token:
        try:
            snap = load_pm_edge_api(
                api_base, api_token, now=now,
                max_age_seconds=max_age_seconds,
            )
            if snap.probabilities:
                return snap, "pm_edge_mobile_api", {"attempted": False, "reason": "upstream_fresh"}
        except Exception as exc:
            api_error = f"{type(exc).__name__}: {exc}"

    local = load_pm_edge_ledger(
        ledger_path, now=now, max_age_seconds=max_age_seconds,
    )
    if local.probabilities:
        return local, "pm_edge_local_ledger", {"attempted": False, "reason": "local_fresh"}

    refresh = {"attempted": False, "reason": "refresh_disabled"}
    if allow_refresh and refresh_state_path:
        refresh = _refresh_local_fair_values(
            ledger_path=ledger_path,
            refresh_state_path=refresh_state_path,
            now=now,
            refresh_interval_seconds=refresh_interval_seconds,
        )
        local = load_pm_edge_ledger(
            ledger_path, now=now, max_age_seconds=max_age_seconds,
        )
        if local.probabilities:
            return local, "autotrader_local_pm_edge_refresh", refresh

    if api_error:
        refresh["api_error"] = api_error
    return local, "pm_edge_no_fresh_fair_values", refresh


def run_once(*, client=None, state_path="data/autotrader-paper.json",
             runs_path="data/autotrader-paper-runs.jsonl",
             ledger_path="pm_edge_candidate_ledger.csv", max_markets=3000,
             max_orderbooks=250, probability_max_age_seconds=300,
             fee_per_contract="0.01", slippage_per_contract="0.005",
             max_quote_age_seconds=5, min_net_edge="0.05",
             fair_api_base=None, fair_api_token=None, now=None, clock=None,
             refresh_state_path="/data/autotrader-fair-refresh.json",
             allow_fair_refresh=False, refresh_interval_seconds=1800):
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
    )
    fair_tickers = {ticker for ticker, _side in fair.probabilities}
    candidates = scan_public(
        client,
        max_markets=max_markets,
        max_orderbooks=min(max_orderbooks, len(fair_tickers)) if fair_tickers else 0,
        clock=clock,
        ticker_filter=fair_tickers,
    ) if fair_tickers else []
    decision_at = clock().astimezone(timezone.utc)
    quote_ages = {}
    for c in candidates:
        if c.observed_at is None:
            continue
        quote_ages[(c.ticker, c.side)] = (decision_at - c.observed_at).total_seconds()

    engine = PaperEngine(portfolio=portfolio, min_net_edge=Decimal(str(min_net_edge)))
    result = engine.on_candidates(
        candidates,
        probabilities=fair.probabilities,
        quote_ages=quote_ages,
        event_data_ages=fair.ages,
        fee_per_contract=fee_per_contract,
        slippage_per_contract=slippage_per_contract,
        max_quote_age_seconds=max_quote_age_seconds,
        max_event_data_age_seconds=probability_max_age_seconds,
    )
    save_portfolio(engine.portfolio, state_path)

    matched = sum(1 for c in candidates if (c.ticker, c.side) in fair.probabilities)
    run = {
        "observed_at": decision_at.isoformat(),
        "provider": provider,
        "provider_scope": "fresh PM Edge sportsbook-derived fair values",
        "fair_refresh": fair_refresh,
        "candidate_count": len(candidates),
        "fresh_probability_count": len(fair.probabilities),
        "matched_candidate_count": matched,
        "paper_fills": result["paper_fills"],
        "settlements": settled,
        "portfolio": result["portfolio"],
        "decisions": engine.decisions,
    }
    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":")) + "\n")
    return run


def main():
    parser = argparse.ArgumentParser(description="Isolated paper-only AutoTrader")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-markets", type=int, default=1000)
    parser.add_argument("--max-orderbooks", type=int, default=30)
    parser.add_argument("--state", default=os.environ.get("AUTOTRADER_STATE", "/data/autotrader-paper.json"))
    parser.add_argument("--runs", default=os.environ.get("AUTOTRADER_RUNS", "/data/autotrader-paper-runs.jsonl"))
    parser.add_argument("--ledger", default=os.environ.get("AUTOTRADER_LEDGER", "pm_edge_candidate_ledger.csv"))
    parser.add_argument("--fair-api-base", default=os.environ.get("AUTOTRADER_FAIR_API_BASE", ""))
    parser.add_argument("--fair-api-token", default=os.environ.get("AUTOTRADER_FAIR_API_TOKEN", ""))
    parser.add_argument("--probability-max-age", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_MAX_AGE_SECONDS", "300")))
    parser.add_argument("--max-quote-age", type=int, default=int(os.environ.get("AUTOTRADER_QUOTE_MAX_AGE_SECONDS", "5")))
    parser.add_argument("--fee", default=os.environ.get("AUTOTRADER_FEE_PER_CONTRACT", "0.01"))
    parser.add_argument("--slippage", default=os.environ.get("AUTOTRADER_SLIPPAGE_PER_CONTRACT", "0.005"))
    parser.add_argument("--min-edge", default=os.environ.get("AUTOTRADER_MIN_NET_EDGE", "0.05"))
    parser.add_argument("--refresh-state", default=os.environ.get("AUTOTRADER_REFRESH_STATE", "/data/autotrader-fair-refresh.json"))
    parser.add_argument("--refresh-interval", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_REFRESH_INTERVAL_SECONDS", "1800")))
    parser.add_argument("--allow-fair-refresh", action="store_true", default=os.environ.get("AUTOTRADER_ALLOW_FAIR_REFRESH", "0").strip().lower() in {"1","true","yes","on"})
    args = parser.parse_args()
    if args.interval < 30 or args.max_markets < 1 or args.max_orderbooks < 1:
        parser.error("interval must be >= 30 seconds; market limits must be >= 1")

    client = KalshiPublicClient(PUBLIC_BASE)
    while True:
        try:
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
            )
            print(json.dumps({k: v for k, v in result.items() if k != "decisions"}), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), flush=True)
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
