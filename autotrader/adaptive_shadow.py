"""Continuous Quick Whale adaptive learner (shadow-only).

This worker observes completed paper trades from Quick Whale v2/v3 and updates
contextual performance profiles after every newly completed trade. It produces
slow-moving recommendations but never mutates trading rules, bankroll controls,
portfolio state, or real-money settings.
"""
import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .learning import _completed_trades, _decimal, _flow_bucket, _price_bucket
from .storage import load_portfolio

SCHEMA = 1
DEFAULT_PRIOR_STRENGTH = 12.0
DEFAULT_STEP_LIMIT = 0.05
MIN_SIZE_MULTIPLIER = 0.25
MAX_SIZE_MULTIPLIER = 1.10
MAX_PROCESSED_IDS = 10000


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _json_number(value, places=6):
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError, OverflowError):
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _trade_id(sample):
    thesis = sample.get("thesis") or {}
    raw = "|".join(
        [
            str(sample.get("lane") or ""),
            str(sample.get("ticker") or ""),
            str(sample.get("side") or ""),
            str(sample.get("contracts") or ""),
            str(sample.get("entry_price") or ""),
            str(thesis.get("entered_at") or ""),
            str(thesis.get("source_trade_id") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _horizon_bucket(sample):
    thesis = sample.get("thesis") or {}
    entered = _parse_time(thesis.get("entered_at"))
    close = _parse_time(thesis.get("close_time"))
    if entered is None or close is None:
        return "unknown"
    seconds = max(0.0, (close - entered).total_seconds())
    if seconds < 15 * 60:
        return "<15m"
    if seconds < 60 * 60:
        return "15m-1h"
    if seconds < 6 * 60 * 60:
        return "1-6h"
    if seconds < 24 * 60 * 60:
        return "6-24h"
    if seconds < 7 * 24 * 60 * 60:
        return "1-7d"
    return "7d+"


def _series(sample):
    thesis = sample.get("thesis") or {}
    return str(
        thesis.get("series")
        or str(sample.get("ticker") or "").split("-")[0]
        or "unknown"
    )


def _context_keys(sample):
    lane = str(sample.get("lane") or "unknown")
    side = str(sample.get("side") or "unknown").upper()
    price = _price_bucket(sample.get("entry_price"))
    flow = _flow_bucket(sample.get("flow_notional_usd"))
    horizon = _horizon_bucket(sample)
    series = _series(sample)
    return [
        f"lane:{lane}",
        f"lane:{lane}|series:{series}",
        f"lane:{lane}|side:{side}",
        f"lane:{lane}|entry_price:{price}",
        f"lane:{lane}|flow:{flow}",
        f"lane:{lane}|horizon:{horizon}",
        f"lane:{lane}|series:{series}|side:{side}|entry_price:{price}",
    ]


def _empty_profile():
    return {
        "n": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "total_pnl": 0.0,
        "total_risked": 0.0,
        "mean_roi": 0.0,
        "ewma_roi": 0.0,
        "ewma_pnl": 0.0,
        "last_roi": None,
        "last_pnl": None,
        "updated_at": None,
    }


def _update_profile(profile, sample):
    pnl = float(_decimal(sample.get("pnl"), 0) or 0)
    risked = float(_decimal(sample.get("risked"), 0) or 0)
    roi = pnl / risked if risked > 0 else 0.0
    clipped_roi = _clamp(roi, -1.0, 3.0)

    n0 = int(profile.get("n") or 0)
    n1 = n0 + 1
    # Exact lifetime average plus a recency-sensitive EWMA. The EWMA has a
    # minimum 5% update rate, so old regimes slowly decay instead of dominating.
    old_mean = float(profile.get("mean_roi") or 0.0)
    mean_roi = old_mean + (clipped_roi - old_mean) / n1
    alpha = max(0.05, min(0.35, 1.0 / n1))
    if n0 == 0:
        ewma_roi = clipped_roi
        ewma_pnl = pnl
    else:
        ewma_roi = (1.0 - alpha) * float(profile.get("ewma_roi") or 0.0) + alpha * clipped_roi
        ewma_pnl = (1.0 - alpha) * float(profile.get("ewma_pnl") or 0.0) + alpha * pnl

    profile.update(
        {
            "n": n1,
            "wins": int(profile.get("wins") or 0) + (1 if pnl > 0 else 0),
            "losses": int(profile.get("losses") or 0) + (1 if pnl < 0 else 0),
            "pushes": int(profile.get("pushes") or 0) + (1 if pnl == 0 else 0),
            "total_pnl": float(profile.get("total_pnl") or 0.0) + pnl,
            "total_risked": float(profile.get("total_risked") or 0.0) + risked,
            "mean_roi": mean_roi,
            "ewma_roi": ewma_roi,
            "ewma_pnl": ewma_pnl,
            "last_roi": roi,
            "last_pnl": pnl,
            "updated_at": _utcnow().isoformat().replace("+00:00", "Z"),
        }
    )
    return profile


def _profile_signal(profile, prior_strength=DEFAULT_PRIOR_STRENGTH):
    n = int(profile.get("n") or 0)
    if n <= 0:
        return {
            "confidence": 0.0,
            "shrunk_roi": 0.0,
            "target_size_multiplier": 1.0,
        }
    confidence = n / (n + float(prior_strength))
    # Blend lifetime and recent ROI, then shrink toward zero while evidence is thin.
    blended = 0.55 * float(profile.get("ewma_roi") or 0.0) + 0.45 * float(profile.get("mean_roi") or 0.0)
    shrunk = blended * confidence

    if shrunk <= -0.12:
        target = 0.35
    elif shrunk <= -0.07:
        target = 0.50
    elif shrunk <= -0.04:
        target = 0.65
    elif shrunk <= -0.02:
        target = 0.80
    elif shrunk < 0.02:
        target = 0.95
    elif shrunk < 0.05:
        target = 1.00
    elif shrunk < 0.10:
        target = 1.05
    else:
        target = 1.10

    return {
        "confidence": round(confidence, 6),
        "shrunk_roi": round(shrunk, 6),
        "target_size_multiplier": target,
    }


def _step_toward(current, target, step_limit=DEFAULT_STEP_LIMIT):
    current = float(current)
    target = float(target)
    delta = _clamp(target - current, -abs(step_limit), abs(step_limit))
    return round(_clamp(current + delta, MIN_SIZE_MULTIPLIER, MAX_SIZE_MULTIPLIER), 4)


def _load_state(path):
    target = Path(path)
    if not target.exists():
        return {
            "schema": SCHEMA,
            "mode": "adaptive_shadow",
            "production_mutation_allowed": False,
            "processed_trade_ids": [],
            "profiles": {},
            "recommendations": {},
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema", SCHEMA)
    data.setdefault("mode", "adaptive_shadow")
    data["production_mutation_allowed"] = False
    data.setdefault("processed_trade_ids", [])
    data.setdefault("profiles", {})
    data.setdefault("recommendations", {})
    return data


def _save_state(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with NamedTemporaryFile(
            mode="w",
            dir=target.parent,
            prefix=".adaptive-shadow-",
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


def _append_log(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")


def _context_summary(state, lane, limit=5):
    prefix = f"lane:{lane}|"
    rows = []
    for key, profile in (state.get("profiles") or {}).items():
        if not str(key).startswith(prefix):
            continue
        signal = _profile_signal(profile)
        rows.append(
            {
                "context": key,
                "n": int(profile.get("n") or 0),
                "pnl": _json_number(profile.get("total_pnl"), 4),
                "roi": _json_number(
                    (float(profile.get("total_pnl") or 0.0) / float(profile.get("total_risked") or 1.0))
                    if float(profile.get("total_risked") or 0.0) > 0
                    else 0.0,
                    6,
                ),
                **signal,
            }
        )
    negative = sorted(rows, key=lambda x: (x["shrunk_roi"], -x["n"]))[:limit]
    positive = sorted(rows, key=lambda x: (-x["shrunk_roi"], -x["n"]))[:limit]
    return {"weakest": negative, "strongest": positive}


def _recompute_lane_recommendation(state, lane, step_limit=DEFAULT_STEP_LIMIT):
    profile = (state.get("profiles") or {}).get(f"lane:{lane}") or _empty_profile()
    signal = _profile_signal(profile)
    recs = state.setdefault("recommendations", {})
    previous = recs.get(lane) or {}
    current = float(previous.get("position_size_multiplier") or 1.0)
    next_value = _step_toward(current, signal["target_size_multiplier"], step_limit=step_limit)
    rec = {
        "lane": lane,
        "position_size_multiplier": next_value,
        "target_position_size_multiplier": signal["target_size_multiplier"],
        "confidence": signal["confidence"],
        "shrunk_roi": signal["shrunk_roi"],
        "settled_trades": int(profile.get("n") or 0),
        "realized_pnl": _json_number(profile.get("total_pnl"), 4),
        "realized_roi": _json_number(
            (float(profile.get("total_pnl") or 0.0) / float(profile.get("total_risked") or 1.0))
            if float(profile.get("total_risked") or 0.0) > 0
            else 0.0,
            6,
        ),
        "context": _context_summary(state, lane),
        "shadow_only": True,
        "production_rules_unchanged": True,
        "updated_at": _utcnow().isoformat().replace("+00:00", "Z"),
    }
    recs[lane] = rec
    return previous, rec


def learn_samples(
    samples,
    state,
    *,
    log_path=None,
    prior_strength=DEFAULT_PRIOR_STRENGTH,
    step_limit=DEFAULT_STEP_LIMIT,
):
    # prior_strength is accepted for forward compatibility; the current signal
    # function uses the guarded module default so tuning cannot leak into production.
    del prior_strength
    processed = set(str(x) for x in state.get("processed_trade_ids") or [])
    new_count = 0

    for sample in samples:
        trade_id = _trade_id(sample)
        if trade_id in processed:
            continue
        lane = str(sample.get("lane") or "unknown")
        pnl = float(_decimal(sample.get("pnl"), 0) or 0)
        risked = float(_decimal(sample.get("risked"), 0) or 0)
        roi = pnl / risked if risked > 0 else 0.0

        for key in _context_keys(sample):
            profile = (state.setdefault("profiles", {})).setdefault(key, _empty_profile())
            _update_profile(profile, sample)

        before, after = _recompute_lane_recommendation(state, lane, step_limit=step_limit)
        processed.add(trade_id)
        new_count += 1

        if log_path:
            _append_log(
                log_path,
                {
                    "observed_at": _utcnow().isoformat().replace("+00:00", "Z"),
                    "event": "trade_learned",
                    "trade_id": trade_id,
                    "lane": lane,
                    "ticker": sample.get("ticker"),
                    "series": _series(sample),
                    "side": sample.get("side"),
                    "entry_price": _json_number(sample.get("entry_price"), 4),
                    "entry_price_bucket": _price_bucket(sample.get("entry_price")),
                    "flow_bucket": _flow_bucket(sample.get("flow_notional_usd")),
                    "horizon_bucket": _horizon_bucket(sample),
                    "pnl": round(pnl, 4),
                    "roi": round(roi, 6),
                    "recommendation_before": before.get("position_size_multiplier"),
                    "recommendation_after": after.get("position_size_multiplier"),
                    "target": after.get("target_position_size_multiplier"),
                    "confidence": after.get("confidence"),
                    "production_rules_unchanged": True,
                },
            )

    ordered = list(processed)
    if len(ordered) > MAX_PROCESSED_IDS:
        ordered = ordered[-MAX_PROCESSED_IDS:]
    state["processed_trade_ids"] = ordered
    state["updated_at"] = _utcnow().isoformat().replace("+00:00", "Z")
    state["new_trades_learned_last_run"] = new_count
    state["production_mutation_allowed"] = False
    return state


def load_status(path, *, interval_seconds=60):
    """Return the dashboard-safe shadow-learning snapshot.

    Internal profile maps and processed trade identifiers stay on disk. The API
    exposes only aggregate recommendations and evidence summaries.
    """
    state = _load_state(path)
    updated_at = state.get("updated_at")
    updated = _parse_time(updated_at)
    now = _utcnow()
    stale_after = max(180, 3 * max(30, int(interval_seconds)))
    worker_online = bool(
        updated is not None and 0 <= (now - updated).total_seconds() <= stale_after
    )
    return {
        "mode": "adaptive_shadow",
        "worker_online": worker_online,
        "updated_at": updated_at,
        "new_trades_learned_last_run": int(state.get("new_trades_learned_last_run") or 0),
        "processed_trades": len(state.get("processed_trade_ids") or []),
        "source_completed_trades": state.get("source_completed_trades") or {},
        "recommendations": state.get("recommendations") or {},
        "controls": state.get("controls") or {
            "max_step_per_learned_trade": DEFAULT_STEP_LIMIT,
            "min_position_size_multiplier": MIN_SIZE_MULTIPLIER,
            "max_position_size_multiplier": MAX_SIZE_MULTIPLIER,
            "production_mutation_allowed": False,
            "real_money_mutation_allowed": False,
        },
        "production_rules_unchanged": True,
        "auto_apply_enabled": False,
    }


def run_once(
    *,
    v2_portfolio_path,
    v3_portfolio_path,
    state_path,
    log_path,
    recorder_path=None,
):
    state = _load_state(state_path)
    samples = []
    source_counts = {}

    recorder = None
    if recorder_path and Path(recorder_path).exists():
        try:
            from .recorder_store import RecorderStore
            recorder = RecorderStore(recorder_path)
        except Exception:
            recorder = None

    for lane, path in (
        ("quick_whale_v2", v2_portfolio_path),
        ("quick_whale_v3", v3_portfolio_path),
    ):
        try:
            portfolio = load_portfolio(path)
            rows = _completed_trades(portfolio, recorder=recorder, lane_hint=lane)
        except Exception:
            rows = []
        samples.extend(rows)
        source_counts[lane] = len(rows)

    learn_samples(samples, state, log_path=log_path)
    state["source_completed_trades"] = source_counts
    state["mode"] = "adaptive_shadow"
    state["production_rules_unchanged"] = True
    state["auto_apply_enabled"] = False
    state["controls"] = {
        "max_step_per_learned_trade": DEFAULT_STEP_LIMIT,
        "min_position_size_multiplier": MIN_SIZE_MULTIPLIER,
        "max_position_size_multiplier": MAX_SIZE_MULTIPLIER,
        "production_mutation_allowed": False,
        "real_money_mutation_allowed": False,
    }
    _save_state(state_path, state)
    return {
        "status": "ok",
        "mode": "adaptive_shadow",
        "new_trades_learned": state.get("new_trades_learned_last_run", 0),
        "processed_trades": len(state.get("processed_trade_ids") or []),
        "recommendations": state.get("recommendations") or {},
        "production_rules_unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser(description="Continuous Quick Whale adaptive shadow learner")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument(
        "--v2-portfolio",
        default=os.environ.get("WHALE_QUICK_V2_PORTFOLIO_STATE", "/data/quick-whale-v2-paper-portfolio.json"),
    )
    parser.add_argument(
        "--v3-portfolio",
        default=os.environ.get("WHALE_QUICK_V3_PORTFOLIO_STATE", "/data/quick-whale-v3-paper-portfolio.json"),
    )
    parser.add_argument(
        "--state",
        default=os.environ.get("ADAPTIVE_WHALE_SHADOW_STATE", "/data/quick-whale-adaptive-shadow.json"),
    )
    parser.add_argument(
        "--log",
        default=os.environ.get("ADAPTIVE_WHALE_SHADOW_LOG", "/data/quick-whale-adaptive-learning.jsonl"),
    )
    parser.add_argument(
        "--recorder",
        default=os.environ.get("AUTOTRADER_RECORDER_DB", "/data/autotrader-recorder.sqlite3"),
    )
    args = parser.parse_args()

    while True:
        result = run_once(
            v2_portfolio_path=args.v2_portfolio,
            v3_portfolio_path=args.v3_portfolio,
            state_path=args.state,
            log_path=args.log,
            recorder_path=args.recorder,
        )
        print(json.dumps({"ADAPTIVE_WHALE_SHADOW": result}, separators=(",", ":"), default=str), flush=True)
        if args.once:
            return
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    main()
