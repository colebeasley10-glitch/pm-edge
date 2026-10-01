"""Read-only Quick Whale v2 report split by pagination version boundary.

Usage (on exported /data files, or inside the Railway shell):

    python tools/qw_v2_version_report.py \
        --portfolio /data/quick-whale-v2-paper-portfolio.json \
        --runs /data/quick-whale-v2-paper-runs.jsonl

Periods (see autotrader/whale_paper_runner.py):
  quick_whale_v2_pre_pagination  entries before 2026-10-01T17:37:55.407Z
  quick_whale_v2_pagination_v1   from that deploy (888340b) until the horizon fix
  quick_whale_v2_pagination_v2   entries stamped by the horizon-fix code

Never writes anything. Periods are reported separately and NEVER pooled; any
period with fewer than 40 settled trades is labelled descriptive_only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotrader.learning import _aggregate, _completed_trades  # noqa: E402
from autotrader.storage import jsonl_archives, load_portfolio  # noqa: E402
from autotrader.whale_paper_runner import (  # noqa: E402
    QUICK_V2_PAGINATION_BOUNDARY_AT, QUICK_V2_PAGINATION_BOUNDARY_COMMIT, quick_v2_version_for,
)


def _iter_runs(runs_path):
    files = [(apath, gz) for _d, _s, apath, gz in jsonl_archives(runs_path)]
    if Path(runs_path).exists():
        files.append((Path(runs_path), False))
    for path, gz in files:
        opener = gzip.open if gz else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(row, dict):
                        yield row
        except (OSError, EOFError):
            continue


def fill_times_by_source_trade(runs_path):
    """source_trade_id -> observed_at of the run that recorded the paper fill."""
    out = {}
    for run in _iter_runs(runs_path):
        observed = run.get("observed_at")
        for row in run.get("decisions") or []:
            if isinstance(row, dict) and row.get("decision") == "paper_filled":
                sid = str(row.get("source_trade_id") or "")
                if sid and sid not in out:
                    out[sid] = observed
    return out


def build_report(portfolio_path, runs_path):
    portfolio = load_portfolio(portfolio_path)
    fills = fill_times_by_source_trade(runs_path)
    samples = _completed_trades(portfolio, lane_hint="quick_whale_v2")
    periods = {}
    for sample in samples:
        thesis = sample.get("thesis") or {}
        entered = thesis.get("entered_at") or fills.get(str(thesis.get("source_trade_id") or ""))
        version = quick_v2_version_for(entered, thesis.get("lane_version"))
        sample["reporting_period"] = version
        periods.setdefault(version, []).append(sample)
    return {
        "boundary_at": QUICK_V2_PAGINATION_BOUNDARY_AT,
        "boundary_commit": QUICK_V2_PAGINATION_BOUNDARY_COMMIT,
        "open_positions": len(portfolio.positions),
        "periods": {name: _aggregate(rows) for name, rows in sorted(periods.items())},
        "note": "Periods are separate samples; do not pool. <40 trades = descriptive_only.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--portfolio", default="/data/quick-whale-v2-paper-portfolio.json")
    parser.add_argument("--runs", default="/data/quick-whale-v2-paper-runs.jsonl")
    args = parser.parse_args(argv)
    print(json.dumps(build_report(args.portfolio, args.runs), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
