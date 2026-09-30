"""Research-only feedback loop for paper AutoTrader lanes.

This module measures what the paper strategies are doing, builds segmented
performance summaries, and may propose a challenger configuration. It NEVER
changes production rules and has no order-submission capability.

The design deliberately separates:
  1. observation / measurement,
  2. challenger proposal,
  3. shadow comparison,
  4. human promotion.

That separation prevents a short run of wins or losses from silently rewriting
the production strategy.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

D = lambda value: Decimal(str(value))

SCHEMA = 1
MAX_RUNS_PER_LANE = 2500
MIN_EDGE_BUCKET_TRADES = 40
MIN_CHALLENGER_TRADES = 80
MIN_PROMOTION_TRADES = 200
CLV_MAX_SNAPSHOT_AGE_SECONDS = 900


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _decimal(value, default=None):
    if value in (None, ""):
        return default
    try:
        return D(value)
    except (InvalidOperation, ValueError, TypeError):
        return default


def _tail_jsonl(path, limit=MAX_RUNS_PER_LANE):
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return []
    rows = deque(maxlen=max(1, int(limit)))
    try:
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return list(rows)


def _json_number(value, places=6):
    try:
        return round(float(value), places)
    except Exception:
        return None


def _edge_bucket(edge):
    edge = _decimal(edge)
    if edge is None:
        return "unknown"
    if edge < D("0.05"):
        return "<5%"
    if edge < D("0.07"):
        return "5-7%"
    if edge < D("0.10"):
        return "7-10%"
    if edge < D("0.15"):
        return "10-15%"
    return "15%+"


def _flow_bucket(value):
    value = _decimal(value)
    if value is None:
        return "unknown"
    if value < D("250"):
        return "<$250"
    if value < D("1000"):
        return "$250-$1k"
    if value < D("5000"):
        return "$1k-$5k"
    return "$5k+"


def _aggregate(samples):
    pnl = sum((_decimal(x.get("pnl"), D("0")) for x in samples), D("0"))
    risked = sum((_decimal(x.get("risked"), D("0")) for x in samples), D("0"))
    wins = sum(1 for x in samples if _decimal(x.get("pnl"), D("0")) > 0)
    losses = sum(1 for x in samples if _decimal(x.get("pnl"), D("0")) < 0)
    pushes = len(samples) - wins - losses
    clvs = [
        _decimal(x.get("clv"))
        for x in samples
        if _decimal(x.get("clv")) is not None
    ]
    return {
        "trades": len(samples),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "pnl": _json_number(pnl, 4),
        "risked": _json_number(risked, 4),
        "roi": _json_number(pnl / risked if risked > 0 else D("0"), 6),
        "avg_clv": _json_number(
            sum(clvs, D("0")) / D(len(clvs)) if clvs else None, 6
        ) if clvs else None,
        "clv_samples": len(clvs),
    }


def _segment(samples, key_fn):
    groups = defaultdict(list)
    for sample in samples:
        groups[str(key_fn(sample))].append(sample)
    return {
        key: _aggregate(rows)
        for key, rows in sorted(groups.items(), key=lambda item: item[0])
    }


def _book_side_mid(book, side):
    if not isinstance(book, dict):
        return None
    prefix = "yes" if str(side).upper() == "YES" else "no"
    bid = _decimal(book.get(prefix + "_bid"))
    ask = _decimal(book.get(prefix + "_ask"))
    valid = [x for x in (bid, ask) if x is not None and D("0") <= x <= D("1")]
    if len(valid) == 2:
        return (valid[0] + valid[1]) / D("2")
    if len(valid) == 1:
        return valid[0]
    return None


def _closing_clv(sample, recorder):
    if recorder is None:
        return None
    thesis = sample.get("thesis") or {}
    target = _parse_iso(thesis.get("event_start_at"))
    if target is None or target > _utcnow():
        return None
    try:
        book = recorder.book_at(sample["ticker"], target)
    except Exception:
        return None
    if not book:
        return None
    received = _parse_iso(book.get("received_at"))
    if received is None:
        return None
    age = (target - received).total_seconds()
    if age < 0 or age > CLV_MAX_SNAPSHOT_AGE_SECONDS:
        return None
    closing_mid = _book_side_mid(book, sample.get("side"))
    entry = _decimal(sample.get("entry_price"))
    if closing_mid is None or entry is None:
        return None
    return closing_mid - entry


def _completed_trades(portfolio, recorder=None, lane_hint=None):
    active = {}
    completed = []

    for row in list(getattr(portfolio, "ledger", []) or []):
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").upper()
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue

        if action == "BUY":
            contracts = int(row.get("contracts") or 0)
            price = _decimal(row.get("price"))
            fee_total = _decimal(row.get("fee"), D("0"))
            if contracts < 1 or price is None:
                continue
            thesis = row.get("thesis") if isinstance(row.get("thesis"), dict) else {}
            lane = str(
                thesis.get("lane")
                or thesis.get("source")
                or lane_hint
                or "unknown"
            )
            risked = D(contracts) * price + fee_total
            active[ticker] = {
                "ticker": ticker,
                "side": str(row.get("side") or ""),
                "lane": lane,
                "lane_version": str(thesis.get("lane_version") or ""),
                "contracts": contracts,
                "remaining": contracts,
                "entry_price": str(price),
                "risked": str(risked),
                "pnl": D("0"),
                "thesis": thesis,
                "edge": thesis.get("entry_edge_net"),
                "sigma": thesis.get("entry_sigma"),
                "execution": str(thesis.get("execution") or thesis.get("execution_style") or ""),
                "flow_notional_usd": thesis.get("flow_notional_usd"),
                "reference_price_cents": thesis.get("reference_price_cents"),
            }
            continue

        current = active.get(ticker)
        if current is None:
            continue

        if action == "SELL":
            contracts = min(
                current["remaining"],
                max(0, int(row.get("contracts") or 0)),
            )
            if contracts < 1:
                continue
            current["pnl"] += _decimal(row.get("pnl"), D("0"))
            current["remaining"] -= contracts
        elif action == "SETTLE":
            current["pnl"] += _decimal(row.get("pnl"), D("0"))
            current["remaining"] = 0
        else:
            continue

        if current["remaining"] <= 0:
            sample = dict(current)
            sample["pnl"] = str(current["pnl"])
            risked = _decimal(sample.get("risked"), D("0"))
            sample["roi"] = str(current["pnl"] / risked) if risked > 0 else "0"
            clv = _closing_clv(sample, recorder)
            sample["clv"] = str(clv) if clv is not None else None
            completed.append(sample)
            active.pop(ticker, None)

    return completed


def _decision_summary(runs, lane_default):
    reasons = defaultdict(int)
    evaluated = filled = qualified = 0
    for run in runs:
        run_lane = str(run.get("strategy") or lane_default)
        for row in run.get("decisions") or []:
            if not isinstance(row, dict):
                continue
            evaluated += 1
            decision = str(row.get("decision") or row.get("reason") or "unknown")
            reasons[decision] += 1
            if bool(row.get("qualified")):
                qualified += 1
            if decision == "paper_filled":
                filled += 1
    return {
        "lane": lane_default,
        "runs": len(runs),
        "evaluated_decisions": evaluated,
        "qualified": qualified,
        "fills": filled,
        "decision_reasons": dict(
            sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
    }


def _combined_aggregate(samples, predicate):
    return _aggregate([x for x in samples if predicate(x)])


def _build_challenger(value_samples, production_min_edge):
    production_min_edge = _decimal(production_min_edge, D("0.05"))
    proposals = []
    challenger_min_edge = production_min_edge

    low = [
        x for x in value_samples
        if (edge := _decimal(x.get("edge"))) is not None
        and D("0.05") <= edge < D("0.07")
    ]
    higher = [
        x for x in value_samples
        if (edge := _decimal(x.get("edge"))) is not None and edge >= D("0.07")
    ]
    low_stats = _aggregate(low)
    high_stats = _aggregate(higher)

    if (
        production_min_edge <= D("0.05")
        and low_stats["trades"] >= MIN_EDGE_BUCKET_TRADES
        and high_stats["trades"] >= MIN_EDGE_BUCKET_TRADES
        and (low_stats["roi"] or 0) <= -0.03
        and (high_stats["roi"] or 0) - (low_stats["roi"] or 0) >= 0.05
    ):
        challenger_min_edge = D("0.07")
        proposals.append({
            "rule": "min_net_edge",
            "production_value": _json_number(production_min_edge),
            "challenger_value": 0.07,
            "evidence": {
                "5_7_pct": low_stats,
                "7_pct_plus": high_stats,
            },
            "reason": "Lower-edge settled trades are materially underperforming higher-edge trades.",
        })

    production_stats = _aggregate(value_samples)
    challenger_samples = [
        x for x in value_samples
        if (_decimal(x.get("edge")) is not None
            and _decimal(x.get("edge")) >= challenger_min_edge)
    ]
    challenger_stats = _aggregate(challenger_samples)

    if proposals and challenger_stats["trades"] >= MIN_CHALLENGER_TRADES:
        status = "shadow_testing"
    elif proposals:
        status = "challenger_proposed"
    else:
        status = "collecting_data"

    enough_for_promotion_review = (
        bool(proposals)
        and challenger_stats["trades"] >= MIN_PROMOTION_TRADES
        and production_stats["trades"] >= MIN_PROMOTION_TRADES
    )

    return {
        "status": status,
        "production_rules_unchanged": True,
        "auto_promotion_enabled": False,
        "version": "challenger_v1",
        "rules": {"min_net_edge": _json_number(challenger_min_edge)},
        "proposals": proposals,
        "retrospective_shadow": {
            "production": production_stats,
            "challenger": challenger_stats,
            "warning": "Retrospective selection is diagnostic, not proof of future edge.",
        },
        "promotion_gate": {
            "eligible_for_review": enough_for_promotion_review,
            "minimum_settled_trades": MIN_PROMOTION_TRADES,
            "requires_forward_shadow_evidence": True,
            "requires_manual_approval": True,
        },
    }


def load_learning_state(path):
    target = Path(path)
    if not target.exists():
        return {
            "schema": SCHEMA,
            "status": "collecting_data",
            "production_rules_unchanged": True,
            "auto_promotion_enabled": False,
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schema": SCHEMA,
            "status": "state_unreadable",
            "production_rules_unchanged": True,
            "auto_promotion_enabled": False,
        }
    return data if isinstance(data, dict) else {}


def _save_state(payload, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with NamedTemporaryFile(
            mode="w",
            dir=target.parent,
            prefix=".learning-",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"), default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def update_learning_state(
    *,
    runs_path,
    portfolio,
    state_path,
    production_min_net_edge="0.05",
    recorder_path=None,
    current_run=None,
    extra_sources=None,
):
    """Rebuild the research snapshot from paper history.

    extra_sources entries are dictionaries with:
      lane, runs_path, portfolio
    """
    recorder = None
    recorder_error = None
    if recorder_path:
        try:
            from .recorder_store import RecorderStore
            recorder = RecorderStore(recorder_path)
        except Exception as exc:
            recorder_error = f"{type(exc).__name__}: {str(exc)[:180]}"

    value_runs = _tail_jsonl(runs_path)
    if isinstance(current_run, dict):
        value_runs = value_runs + [current_run]

    all_samples = _completed_trades(portfolio, recorder=recorder, lane_hint="value_edge")
    lane_decisions = {
        "value_edge": _decision_summary(value_runs, "value_edge"),
    }

    for source in list(extra_sources or []):
        if not isinstance(source, dict):
            continue
        lane = str(source.get("lane") or "unknown")
        lane_runs = _tail_jsonl(source.get("runs_path") or "")
        lane_decisions[lane] = _decision_summary(lane_runs, lane)
        extra_portfolio = source.get("portfolio")
        if extra_portfolio is not None:
            all_samples.extend(
                _completed_trades(extra_portfolio, recorder=recorder, lane_hint=lane)
            )

    value_samples = [x for x in all_samples if x.get("lane") == "value_edge"]
    challenger = _build_challenger(value_samples, production_min_net_edge)

    lane_segments = _segment(all_samples, lambda x: x.get("lane") or "unknown")
    edge_segments = _segment(value_samples, lambda x: _edge_bucket(x.get("edge")))
    execution_segments = _segment(
        [x for x in all_samples if x.get("execution")],
        lambda x: f"{x.get('lane') or 'unknown'}:{str(x.get('execution') or '').upper()}",
    )
    flow_segments = _segment(
        [x for x in all_samples if _decimal(x.get("flow_notional_usd")) is not None],
        lambda x: f"{x.get('lane') or 'unknown'}:{_flow_bucket(x.get('flow_notional_usd'))}",
    )

    clv_samples = [x for x in all_samples if _decimal(x.get("clv")) is not None]
    payload = {
        "schema": SCHEMA,
        "updated_at": _utcnow().isoformat().replace("+00:00", "Z"),
        "mode": "paper_research_only",
        "status": challenger["status"],
        "production_rules_unchanged": True,
        "auto_promotion_enabled": False,
        "sample": {
            "settled_trades": len(all_samples),
            "value_edge_settled_trades": len(value_samples),
            "clv_samples": len(clv_samples),
            "lanes": sorted(lane_decisions),
        },
        "decision_tracking": lane_decisions,
        "segments": {
            "by_lane": lane_segments,
            "value_edge_by_entry_edge": edge_segments,
            "by_execution": execution_segments,
            "whale_by_flow_notional": flow_segments,
        },
        "clv": {
            "definition": "Side-contract midpoint immediately before recorded event start minus entry price.",
            "max_snapshot_age_seconds": CLV_MAX_SNAPSHOT_AGE_SECONDS,
            "samples": len(clv_samples),
            "summary": _aggregate(clv_samples),
            "recorder_error": recorder_error,
        },
        "challenger": challenger,
        "controls": {
            "minimum_edge_bucket_trades": MIN_EDGE_BUCKET_TRADES,
            "minimum_challenger_trades": MIN_CHALLENGER_TRADES,
            "minimum_promotion_review_trades": MIN_PROMOTION_TRADES,
            "production_mutation_allowed": False,
        },
    }
    _save_state(payload, state_path)
    return payload
