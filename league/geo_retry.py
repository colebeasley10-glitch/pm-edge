"""Second chance for Live $1 orders Kalshi blocked because the runner sat in Washington.

Runs as its own GitHub job right after a league round that hit the block, so it gets a
fresh runner, almost always in another data center. Real money; main branch only.
"""
from __future__ import annotations

import json
import sys

from league import live_sharp


def main() -> int:
    rows = live_sharp.load()
    if not any(live_sharp.geo_blocked(r) for r in rows):
        print("GEO_RETRY", json.dumps({"retried": 0}))
        return 0
    if not live_sharp.enabled():
        print("GEO_RETRY", json.dumps({"skipped": "live betting off"}))
        return 0
    out = live_sharp.retry_geo(rows, live_sharp.client_from_env())
    live_sharp.save(rows)
    print("GEO_RETRY", json.dumps({k: v for k, v in out.items() if k != "new"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
