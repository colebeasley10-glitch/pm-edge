"""Read-only public Kalshi observation loop; no live orders or fabricated fair values."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from .scanner import scan_public
from .storage import load_portfolio

PUBLIC_BASE = "https://external-api.kalshi.com/trade-api/v2"


def collect_once(client, *, state_path, observations_path, max_markets=3000, max_orderbooks=250):
    """Record timestamped executable quote candidates; never trade without a fair model."""
    observed_at = datetime.now(timezone.utc).isoformat()
    candidates = scan_public(
        client, max_markets=max_markets, max_orderbooks=max_orderbooks
    )
    portfolio = load_portfolio(state_path)
    output = {
        "observed_at": observed_at,
        "candidate_count": len(candidates),
        "paper_trades": 0,
        "reason": "independent_probability_model_not_connected",
        "portfolio": portfolio.snapshot(),
        "candidates": [
            {
                "ticker": c.ticker,
                "title": c.title,
                "side": c.side,
                "ask": str(c.executable_ask),
                "depth": str(c.available),
                "spread": str(c.spread),
                "volume_24h": str(c.volume_24h),
            }
            for c in candidates
        ],
    }
    target = Path(observations_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(output, separators=(",", ":")) + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description="Paper-only read-only Kalshi collector")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-markets", type=int, default=3000)
    parser.add_argument("--max-orderbooks", type=int, default=250)
    parser.add_argument(
        "--state",
        default=os.environ.get("AUTOTRADER_STATE", "data/autotrader-paper.json"),
    )
    parser.add_argument(
        "--observations",
        default=os.environ.get(
            "AUTOTRADER_OBSERVATIONS", "data/autotrader-observations.jsonl"
        ),
    )
    args = parser.parse_args()
    if args.interval < 30 or args.max_markets < 1 or args.max_orderbooks < 1:
        parser.error("interval must be >= 30 seconds; market limits must be >= 1")
    from pmedge.kalshi import KalshiPublicClient

    client = KalshiPublicClient(PUBLIC_BASE)
    while True:
        try:
            result = collect_once(
                client,
                state_path=args.state,
                observations_path=args.observations,
                max_markets=args.max_markets,
                max_orderbooks=args.max_orderbooks,
            )
            print(
                json.dumps({k: v for k, v in result.items() if k != "candidates"}),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps({"error": type(exc).__name__, "message": str(exc)}),
                flush=True,
            )
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
