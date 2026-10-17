"""The Research Lab must never be able to submit a real order.

These tests fail the build if any research_lab module imports or names live
order-submission code, or if its Kalshi client could send anything but a GET.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).parent / "research_lab"
PAGES = [p for p in (Path(__file__).parent / "pages").glob("Research_Lab_*.py")]

FORBIDDEN_MODULES = {"kalshi_submit", "league.live_sharp", "execution_guard", "approval_links",
                     "approval_guardrails", "position_actions", "kalshi_readonly", "autotrader", "watcher",
                     "whale_edge", "league.run", "pmedge.trading", "mobile_backend"}
FORBIDDEN_NAMES = {"KalshiSubmitClient", "KalshiAuthClient", "create_buy_order", "create_sell_order",
                   "place_event_order", "cancel_event_order", "KALSHI_WRITE_API_KEY_ID",
                   "KALSHI_WRITE_PRIVATE_KEY", "KALSHI_PRIVATE_KEY", "LIVE_SHARP_ENABLED", "retry_geo"}


def _sources():
    return sorted(LAB.rglob("*.py")) + PAGES


def test_no_forbidden_imports_or_names():
    for path in _sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not any(a.name == m or a.name.startswith(m + ".") for m in FORBIDDEN_MODULES), \
                        f"{path}: imports {a.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module == m or node.module.startswith(m + ".") for m in FORBIDDEN_MODULES), \
                    f"{path}: imports from {node.module}"
            elif isinstance(node, ast.Name):
                assert node.id not in FORBIDDEN_NAMES, f"{path}: uses {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_NAMES, f"{path}: uses .{node.attr}"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in FORBIDDEN_NAMES, f"{path}: mentions {node.value}"


def test_importing_the_lab_loads_no_order_code_even_with_live_vars_set():
    code = ("import sys, importlib, pkgutil, research_lab\n"
            "for m in pkgutil.walk_packages(research_lab.__path__, 'research_lab.'):\n"
            "    importlib.import_module(m.name)\n"
            "bad = [m for m in sys.modules if m in %r or any(m.startswith(x + '.') for x in %r)]\n"
            "print('BAD', bad)\n" % (sorted(FORBIDDEN_MODULES), sorted(FORBIDDEN_MODULES)))
    env = {**os.environ, "LIVE_SHARP_ENABLED": "1", "KALSHI_WRITE_API_KEY_ID": "x", "KALSHI_WRITE_PRIVATE_KEY": "x"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=str(LAB.parent), timeout=60)
    assert out.returncode == 0, out.stderr
    assert "BAD []" in out.stdout, out.stdout


def test_public_client_only_ever_sends_get():
    for name in ("public_data.py", "pinnacle.py"):
        src = (LAB / name).read_text()
        for verb in (".post(", ".put(", ".delete(", ".patch(", ".request("):
            assert verb not in src, (name, verb)
        assert "session.get(" in src


def test_no_real_money_switch_in_config():
    from research_lab import PAPER_ONLY, config
    assert PAPER_ONLY is True
    text = (LAB / "config.py").read_text().lower()
    for word in ("live_enabled", "real_money", "enable_live", "submit_orders"):
        assert word not in text
    assert not any("live" in k.lower() for s in config.RESEARCH_STRATEGIES for k in s.params)


def test_lab_never_writes_production_ledgers():
    prod = {"league_trades.csv", "live_sharp_trades.csv", "league_status.json", "pm_edge_signal_ledger.csv",
            "pm_edge_auto_ledger.csv", "pm_edge_candidate_ledger.csv"}
    for path in _sources():
        src = path.read_text()
        for name in prod:
            if name in src:
                # only reading is allowed: the name may appear, but never next to a write call
                for line in src.splitlines():
                    if name in line:
                        assert "write" not in line and "\"w\"" not in line and "'w'" not in line, (path, line)
