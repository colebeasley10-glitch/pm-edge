"""Keep tests away from committed runtime ledgers and production paths.

Several modules bind ledger paths at import time from PM_EDGE_*_PATH env vars
(defaulting to the committed CSV/JSON files in the repo root). A test that
calls one of their functions without passing an explicit path would otherwise
append fixture rows to real performance data. Call ``isolate_runtime_ledgers``
BEFORE importing those modules, and wrap test bodies in ``CommittedLedgerGuard``.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PATH_ENV = {
    "PM_EDGE_SIGNAL_LEDGER_PATH": "pm_edge_signal_ledger.csv",
    "PM_EDGE_FILL_LEDGER_PATH": "pm_edge_fill_ledger.csv",
    "PM_EDGE_CANDIDATE_LEDGER_PATH": "pm_edge_candidate_ledger.csv",
    "PM_EDGE_CLV_LEDGER_PATH": "pm_edge_clv_ledger_v2.csv",
    "PM_EDGE_AUTO_LEDGER_PATH": "pm_edge_auto_ledger.csv",
    "PM_EDGE_SIGNAL_OUTCOME_LEDGER_PATH": "pm_edge_signal_outcomes.csv",
    "PM_EDGE_PERFORMANCE_HISTORY_PATH": ".pm_edge_performance_history.json",
    "PM_EDGE_ORDER_QUEUE_PATH": ".pm_edge_order_queue.json",
    "PM_EDGE_WHALE_LEDGER_PATH": "pm_edge_whale_flow_ledger.csv",
    "PM_EDGE_WHALE_STATE_PATH": ".pm_edge_whale_state.json",
}

COMMITTED_FILES = sorted(set(PATH_ENV.values()) | {
    "pm_edge_bet_log.csv",
    "pm_edge_pinnacle_shadow.csv",
    ".pm_edge_watch_state.json",
    ".pm_edge_pinnacle_shadow_state.json",
})


def isolate_runtime_ledgers() -> Path:
    """Point every known ledger/state env path at a fresh temp directory."""
    root = Path(tempfile.mkdtemp(prefix="pm-edge-test-ledgers-"))
    atexit.register(shutil.rmtree, root, True)
    for env, name in PATH_ENV.items():
        os.environ[env] = str(root / name)
    return root


def _digest(path: Path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CommittedLedgerGuard:
    """Fail loudly if a test modified any committed ledger or state file."""

    def __init__(self, root: Path = REPO_ROOT):
        self.root = Path(root)

    def __enter__(self):
        self.before = {name: _digest(self.root / name) for name in COMMITTED_FILES}
        return self

    def __exit__(self, exc_type, exc, tb):
        changed = [
            name for name in COMMITTED_FILES
            if _digest(self.root / name) != self.before[name]
        ]
        if changed and exc_type is None:
            raise AssertionError(f"test modified committed runtime files: {changed}")
        return False
