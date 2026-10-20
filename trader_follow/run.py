"""Entry point: python -m trader_follow.run (one round; alerts + paper ledger only)."""
from __future__ import annotations

import json

from trader_follow.core import run_round


def main() -> None:
    summary = run_round()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
