"""Counterfactual accounting for the C2 exit challenger (shadow only).

When the production Value Edge lane exits a position, we record what was
sold and what the C2 challenger would have done at that moment. After the
market finalizes we compute what holding to settlement would have returned.
The production portfolio is never read for writing and never altered here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile

D = lambda value: Decimal(str(value))
MAX_RESOLVED = 1000
MAX_PENDING = 500


def _utc_iso(dt=None):
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_state(path):
    target = Path(path)
    if not target.exists():
        return {"schema": 1, "pending": [], "resolved": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": 1, "pending": [], "resolved": []}
    if not isinstance(data, dict):
        return {"schema": 1, "pending": [], "resolved": []}
    data.setdefault("pending", [])
    data.setdefault("resolved", [])
    return data


def save_state(state, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["schema"] = 1
    state["pending"] = list(state.get("pending") or [])[-MAX_PENDING:]
    state["resolved"] = list(state.get("resolved") or [])[-MAX_RESOLVED:]
    tmp = None
    try:
        with NamedTemporaryFile(mode="w", dir=target.parent, prefix=".c2-", suffix=".tmp",
                                delete=False, encoding="utf-8") as handle:
            tmp = Path(handle.name)
            json.dump(state, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def record_exit(state, *, ticker, side, contracts, entry_price, entry_fee_per_contract,
                exit_price, exit_fee_per_contract, production_exit_pnl, production_action,
                production_reason, c2, exited_at=None):
    state.setdefault("pending", []).append({
        "ticker": str(ticker),
        "side": str(side),
        "contracts": int(contracts),
        "entry_price": str(entry_price),
        "entry_fee_per_contract": str(entry_fee_per_contract),
        "exit_price": str(exit_price),
        "exit_fee_per_contract": str(exit_fee_per_contract),
        "production_exit_pnl": str(production_exit_pnl),
        "production_action": str(production_action),
        "production_reason": str(production_reason),
        "challenger_c2_would_exit": c2.get("challenger_c2_would_exit"),
        "challenger_c2_reason": c2.get("challenger_c2_reason"),
        "exited_at": _utc_iso(exited_at),
    })


def counterfactual(row, yes_settlement_value):
    """Hold-to-settlement P/L for the exited contracts and the C2 difference."""
    try:
        yes_value = D(yes_settlement_value)
        contracts = D(int(row["contracts"]))
        entry_cost = contracts * (D(row["entry_price"]) + D(row["entry_fee_per_contract"]))
        production = D(row["production_exit_pnl"])
    except (InvalidOperation, ValueError, TypeError, KeyError):
        return None
    per_contract = yes_value if str(row.get("side")).upper() == "YES" else D("1") - yes_value
    hold_pnl = contracts * per_contract - entry_cost
    would_exit = row.get("challenger_c2_would_exit")
    # C2 exits exactly when production does if it would also exit; otherwise it holds.
    challenger_pnl = production if would_exit else hold_pnl
    return {
        "production_exit_pnl": str(production),
        "counterfactual_hold_to_settlement_pnl": str(hold_pnl),
        "challenger_c2_pnl": str(challenger_pnl),
        "challenger_c2_pnl_difference": str(challenger_pnl - production),
    }


def resolve(state, get_market, *, normalize, max_checks=25, now=None):
    """Resolve pending exits whose markets have finalized. Returns count resolved."""
    kept, resolved = [], 0
    checks = 0
    for row in list(state.get("pending") or []):
        if checks >= max_checks:
            kept.append(row)
            continue
        checks += 1
        try:
            market = normalize(get_market(row["ticker"]))
        except Exception:
            kept.append(row)
            continue
        status = str(market.get("status") or "").lower()
        value = market.get("settlement_value")
        if value is None and str(market.get("result") or "").upper() in {"YES", "NO"}:
            value = 1 if str(market.get("result")).upper() == "YES" else 0
        if status not in {"finalized", "settled"} or value is None:
            kept.append(row)
            continue
        outcome = counterfactual(row, value)
        if outcome is None:
            kept.append(row)
            continue
        done = dict(row)
        done.update(outcome)
        done["yes_settlement_value"] = str(value)
        done["resolved_at"] = _utc_iso(now)
        state.setdefault("resolved", []).append(done)
        resolved += 1
    state["pending"] = kept
    return resolved


def summary(state):
    rows = list(state.get("resolved") or [])
    diff = sum((D(r.get("challenger_c2_pnl_difference") or "0") for r in rows), D("0"))
    disagreements = [r for r in rows if r.get("challenger_c2_would_exit") is False]
    return {
        "resolved_exits": len(rows),
        "pending_exits": len(state.get("pending") or []),
        "disagreements": len(disagreements),
        "total_c2_minus_production_pnl": str(diff),
        "interpretation": "descriptive_only" if len(rows) < 40 else "cautious_pattern_only",
    }
