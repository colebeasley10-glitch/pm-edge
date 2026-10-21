"""Entry point: python -m trader_follow.run (one round; alerts + paper ledger only)."""
from __future__ import annotations

import json

from trader_follow.core import run_round
from trader_follow.scores import ScoreResolver


def main() -> None:
    scores = ScoreResolver()   # settles finished games from ESPN when Polymarket is slow to resolve
    summary = run_round(get_score=scores)
    if scores.errors:
        summary.setdefault("errors", []).extend(scores.errors[:5])
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
