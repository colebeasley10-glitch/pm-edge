"""One-cycle paper AutoTrader runner.

Reads public Kalshi orderbooks and fresh PM Edge sportsbook fair values.
No authenticated Kalshi client and no live order submission.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from pmedge.kalshi import KalshiPublicClient
from .collector import PUBLIC_BASE
from .engine import PaperEngine
from .probability import load_pm_edge_ledger
from .scanner import scan_public
from .storage import load_portfolio, save_portfolio


def run_once(*, client=None, state_path="data/autotrader-paper.json",
             runs_path="data/autotrader-paper-runs.jsonl",
             ledger_path="pm_edge_candidate_ledger.csv", max_markets=3000,
             max_orderbooks=250, probability_max_age_seconds=300, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    candidates = scan_public(
        client, max_markets=max_markets, max_orderbooks=max_orderbooks
    )
    fair = load_pm_edge_ledger(
        ledger_path, now=now, max_age_seconds=probability_max_age_seconds
    )
    tickers = {c.ticker for c in candidates}
    quote_ages = {ticker: 0 for ticker in tickers}
    event_ages = {ticker: fair.ages.get(ticker) for ticker in tickers}
    engine = PaperEngine(portfolio=portfolio)
    result = engine.on_candidates(
        candidates,
        probabilities=fair.probabilities,
        quote_ages=quote_ages,
        event_data_ages=event_ages,
    )
    save_portfolio(engine.portfolio, state_path)

    run = {
        "observed_at": now.isoformat(),
        "provider": "pm_edge_sportsbook_fair_value",
        "provider_scope": "sports markets already matched by PM Edge",
        "candidate_count": len(candidates),
        "fresh_probability_count": len(fair.probabilities),
        "paper_fills": result["paper_fills"],
        "portfolio": result["portfolio"],
        "decisions": engine.decisions,
    }
    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":")) + "\n")
    return run
