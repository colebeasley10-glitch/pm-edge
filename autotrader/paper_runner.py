"""Continuous isolated paper AutoTrader runner.

Reads public Kalshi orderbooks plus independent PM Edge fair values. This module
has no authenticated Kalshi client and cannot submit real-money orders.
"""
import argparse
import json
import os
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


def _load_fair_values(*, api_base, api_token, ledger_path, now, max_age_seconds):
    if api_base and api_token:
        return (
            load_pm_edge_api(
                api_base, api_token, now=now,
                max_age_seconds=max_age_seconds,
            ),
            "pm_edge_mobile_api",
        )
    return (
        load_pm_edge_ledger(
            ledger_path, now=now, max_age_seconds=max_age_seconds,
        ),
        "pm_edge_sportsbook_ledger",
    )


def run_once(*, client=None, state_path="data/autotrader-paper.json",
             runs_path="data/autotrader-paper-runs.jsonl",
             ledger_path="pm_edge_candidate_ledger.csv", max_markets=3000,
             max_orderbooks=250, probability_max_age_seconds=300,
             fee_per_contract="0.01", slippage_per_contract="0.005",
             max_quote_age_seconds=5, min_net_edge="0.05",
             fair_api_base=None, fair_api_token=None, now=None, clock=None):
    fixed_now = now
    clock = clock or ((lambda: fixed_now) if fixed_now is not None else _utcnow)
    started_at = clock().astimezone(timezone.utc)
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    settled = _settle_finished_positions(portfolio, client)

    fair, provider = _load_fair_values(
        api_base=fair_api_base, api_token=fair_api_token,
        ledger_path=ledger_path, now=started_at,
        max_age_seconds=probability_max_age_seconds,
    )
    candidates = scan_public(
        client, max_markets=max_markets, max_orderbooks=max_orderbooks,
        clock=clock,
    )
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
