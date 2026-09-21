from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

BASELINE_SHA = "2a390a68a251cc1375241ddcceece6bffe3d922a"
UNPACK_SHA = "1a3506330bb948892998eb67fadbd0800ea478ce"
GOLDEN_PATH = Path("tests/golden/phase0_baseline.json")

# Lock the same baseline inputs on both sides of the unpack boundary.
os.environ["PM_EDGE_MIN_NET_EDGE"] = "0.04"
os.environ["PM_EDGE_MODEL_ERROR_BUFFER"] = "0.01"
os.environ["PM_EDGE_SLIPPAGE_BUFFER"] = "0.0025"
os.environ["PM_EDGE_DEVIG_METHOD"] = "proportional"

sys.path.insert(0, os.getcwd())

def _book(key: str, outcomes: list[dict]) -> dict:
    return {"markets": [{"key": key, "outcomes": outcomes}]}

def _repeat(book: dict, n: int) -> list[dict]:
    return [json.loads(json.dumps(book)) for _ in range(n)]

def _cases():
    h2h = {
        "spec": SimpleNamespace(market_type="h2h", selection="Alpha", point=None),
        "event": {"bookmakers": _repeat(_book("h2h", [
            {"name": "Alpha", "price": 1.5},
            {"name": "Beta", "price": 2.25},
        ]), 3)},
        "market": {"yes_bid": 0.45, "yes_ask": 0.48, "no_bid": 0.52, "no_ask": 0.55, "price_ranges": []},
    }
    total = {
        "spec": SimpleNamespace(market_type="total", selection="Over", point=44.5),
        "event": {"bookmakers": _repeat(_book("totals", [
            {"name": "Over", "price": 2.4, "point": 44.5},
            {"name": "Under", "price": 1.6, "point": 44.5},
        ]), 3)},
        "market": {"yes_bid": 0.50, "yes_ask": 0.53, "no_bid": 0.44, "no_ask": 0.47, "price_ranges": []},
    }
    spread = {
        "spec": SimpleNamespace(market_type="spread", selection="Alpha", point=-3.5),
        "event": {"bookmakers": _repeat(_book("spreads", [
            {"name": "Alpha", "price": 1.5, "point": -3.5},
            {"name": "Beta", "price": 2.0, "point": 3.5},
        ]), 4)},
        "market": {"yes_bid": 0.43, "yes_ask": 0.46, "no_bid": 0.54, "no_ask": 0.57, "price_ranges": []},
    }
    team_total = {
        "spec": SimpleNamespace(market_type="team_total", selection="Alpha", point=20.5),
        "event": {"bookmakers": _repeat(_book("team_totals", [
            {"name": "Over", "price": 1.9, "point": 20.5, "description": "Alpha"},
            {"name": "Under", "price": 1.9, "point": 20.5, "description": "Alpha"},
        ]), 3)},
        "market": {"yes_bid": 0.38, "yes_ask": 0.41, "no_bid": 0.59, "no_ask": 0.62, "price_ranges": []},
    }
    valid = _repeat(_book("h2h", [
        {"name": "Alpha", "price": 1.5},
        {"name": "Beta", "price": 2.5},
    ]), 3)
    bad = _book("h2h", [
        {"name": "Zulu", "price": 1.5},
        {"name": "Omega", "price": 2.5},
    ])
    book_fails_match = {
        "spec": SimpleNamespace(market_type="h2h", selection="Alpha", point=None),
        "event": {"bookmakers": valid + [bad]},
        "market": {"yes_bid": 0.48, "yes_ask": 0.51, "no_bid": 0.49, "no_ask": 0.52, "price_ranges": []},
    }
    return {
        "h2h": h2h,
        "total": total,
        "spread": spread,
        "team_total": team_total,
        "book_fails_match": book_fails_match,
    }

class _Settings:
    maker_fee_rate = 0.0175
    taker_fee_rate = 0.07

def snapshot() -> dict:
    import watcher

    out = {}
    for name, case in _cases().items():
        consensus = watcher.fair_from_event(
            case["spec"], case["event"], {}, min_books=3, max_dispersion=0.10
        )
        if consensus is None:
            raise AssertionError(f"{name}: fair_from_event returned None")
        fair_entry = {
            "fair_yes_prob": consensus.fair_prob,
            "n_books": consensus.n_books,
        }
        quote = watcher.score_market(case["market"], fair_entry, _Settings())
        out[name] = {
            "consensus": asdict(consensus),
            "quote": asdict(quote),
        }
    return out

def snapshot_bytes() -> bytes:
    return json.dumps(
        snapshot(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

def _snapshot_at_commit(sha: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="pmedge-phase0-") as td:
        worktree = Path(td) / "tree"
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), sha],
            capture_output=True, text=True,
        )
        if add.returncode != 0:
            raise SystemExit("FAIL: could not create worktree: " + add.stderr.strip())
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(worktree)
            run = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--emit"],
                cwd=worktree, env=env, capture_output=True,
            )
            if run.returncode != 0:
                raise SystemExit(
                    "FAIL: golden probe failed: "
                    + run.stderr.decode("utf-8", errors="replace").strip()
                )
            return run.stdout
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                capture_output=True,
            )


def _verify_parent() -> None:
    baseline = _snapshot_at_commit(BASELINE_SHA)
    unpacked = _snapshot_at_commit(UNPACK_SHA)
    golden = GOLDEN_PATH.read_bytes()

    if baseline != golden:
        raise SystemExit("FAIL: v28 baseline differs from committed golden artifact")
    if unpacked != golden:
        raise SystemExit("FAIL: Commit 1 unpack differs from committed golden artifact")
    if baseline != unpacked:
        raise SystemExit("FAIL: v28 baseline and Commit 1 unpack are not byte-identical")

    print("PHASE0 GOLDEN PASS: v28 baseline == Commit 1 unpack == committed artifact")

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check-golden"
    if mode == "--emit":
        sys.stdout.buffer.write(snapshot_bytes())
        return
    if mode == "--check-golden":
        if snapshot_bytes() != GOLDEN_PATH.read_bytes():
            raise SystemExit("FAIL: current Phase 0 output differs from golden artifact")
        print("PHASE0 GOLDEN PASS: current output matches committed artifact")
        return
    if mode == "--verify-parent":
        _verify_parent()
        return
    raise SystemExit(f"unknown mode: {mode}")

if __name__ == "__main__":
    main()
