"""Pinnacle shadow diagnostics for PM Edge.

This module is deliberately downstream of the production watcher. It reads the
candidate ledger after production scoring and writes a separate audit ledger.
It never changes fair_cache, qualification, alerts, sizing, or order submission.

Quota design:
- Production keeps first claim on PM_EDGE_ODDS_DAILY_BUDGET.
- Shadow may spend only unused capacity inside that same daily envelope.
- Bookmaker requests specify bookmakers=pinnacle, never a broad EU region.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from pmedge.fair_value import fair_from_event
from pmedge.matcher import load_aliases
from pmedge.pricing import price_side
from pmedge.settings import Settings


CANDIDATE_LEDGER = Path(os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv"))
WATCH_STATE = Path(os.getenv("PM_EDGE_WATCH_STATE", ".pm_edge_watch_state.json"))
SHADOW_LEDGER = Path(os.getenv("PM_EDGE_PINNACLE_SHADOW_LEDGER", "pm_edge_pinnacle_shadow.csv"))
SHADOW_STATE = Path(os.getenv("PM_EDGE_PINNACLE_SHADOW_STATE", ".pm_edge_pinnacle_shadow_state.json"))

SHARED_DAILY_BUDGET = int(os.getenv("PM_EDGE_ODDS_DAILY_BUDGET", "15"))
SHADOW_DAILY_CAP = max(0, int(os.getenv("PM_EDGE_PINNACLE_SHADOW_DAILY_CAP", "2")))
MIN_ACCOUNT_REMAINING = max(0, int(os.getenv("PM_EDGE_PINNACLE_MIN_ACCOUNT_REMAINING", "25")))
REQUEST_TIMEOUT = max(5, int(os.getenv("PM_EDGE_PINNACLE_TIMEOUT_SECONDS", "20")))

MARKET_KEY = {
    "h2h": "h2h",
    "spread": "spreads",
    "total": "totals",
}

FIELDS = [
    "shadow_id",
    "collected_at",
    "run_bucket_utc",
    "decision_id",
    "ticker",
    "event_id",
    "sport_key",
    "market_type",
    "selection",
    "point",
    "side",
    "lane",
    "production_decision",
    "production_net_edge",
    "production_side_prob",
    "kalshi_yes_bid",
    "kalshi_yes_ask",
    "kalshi_no_bid",
    "kalshi_no_ask",
    "kalshi_side_ask",
    "pinnacle_last_update",
    "pinnacle_quote_age_seconds",
    "pinnacle_yes_prob_power",
    "pinnacle_side_prob",
    "pinnacle_net_edge_at_snapshot",
    "pinnacle_minus_production_prob",
    "pinnacle_move_since_prior",
    "kalshi_move_since_prior",
    "shadow_signal",
    "pinnacle_raw_market_json",
    "requests_last",
    "requests_remaining",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return _now().date().isoformat()


def _f(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _load_shadow_state() -> dict:
    state = _read_json(SHADOW_STATE, {})
    if state.get("date") != _today():
        state = {"date": _today(), "credits": 0, "group_last": {}}
    state.setdefault("credits", 0)
    state.setdefault("group_last", {})
    return state


def _save_shadow_state(state: dict) -> None:
    SHADOW_STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _production_credits_today() -> int:
    state = _read_json(WATCH_STATE, {})
    daily = state.get("daily_usage") or {}
    if str(daily.get("date") or "") != _today():
        return 0
    return max(0, _i(daily.get("credits"), 0))


def _latest_score_rows() -> tuple[str | None, list[dict]]:
    if not CANDIDATE_LEDGER.exists() or CANDIDATE_LEDGER.stat().st_size == 0:
        return None, []
    with CANDIDATE_LEDGER.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    score_rows = [
        r for r in rows
        if str(r.get("stage") or "").lower() == "score"
        and str(r.get("event_id") or "").strip()
        and str(r.get("sport_key") or "").strip()
        and str(r.get("market_type") or "").strip() in MARKET_KEY
    ]
    if not score_rows:
        return None, []
    run_bucket = max(str(r.get("run_bucket_utc") or "") for r in score_rows)
    latest = [r for r in score_rows if str(r.get("run_bucket_utc") or "") == run_bucket]
    # One exact snapshot per ticker/side/lane is enough for the shadow request.
    dedup = {}
    for row in latest:
        key = (
            str(row.get("ticker") or ""),
            str(row.get("side") or ""),
            str(row.get("lane") or ""),
        )
        dedup[key] = row
    return run_bucket, list(dedup.values())


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _group_rows(rows: list[dict], state: dict) -> list[tuple[tuple[str, str], list[dict]]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (str(row.get("sport_key") or ""), str(row.get("market_type") or ""))
        if key[0] and key[1] in MARKET_KEY:
            grouped.setdefault(key, []).append(row)

    now = _now()
    scored = []
    for key, group in grouped.items():
        label = "|".join(key)
        last = _parse_ts((state.get("group_last") or {}).get(label))
        recent = bool(last and (now - last).total_seconds() < 6 * 3600)
        qualified = sum(str(r.get("qualifies") or "").lower() == "true" for r in group)
        max_edge = max((_f(r.get("net_edge")) or -1.0) for r in group)
        # Prefer groups with actionable candidates, while rotating away from
        # groups sampled in the last six hours when another group is available.
        rank = (1 if recent else 0, -qualified, -len(group), -max_edge, label)
        scored.append((rank, key, group))
    scored.sort(key=lambda x: x[0])
    return [(key, group) for _, key, group in scored]


def _pinnacle_request(api_key: str, sport_key: str, market_type: str, event_ids: list[str]):
    market = MARKET_KEY[market_type]
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "bookmakers": "pinnacle",
        "markets": market,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    ids = sorted({x for x in event_ids if x})
    if ids:
        params["eventIds"] = ",".join(ids)
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json(), {
        "requests_last": _i(response.headers.get("x-requests-last"), 0),
        "requests_remaining": _i(response.headers.get("x-requests-remaining"), -1),
    }


def _relevant_market_json(event: dict, market_type: str) -> str:
    key = MARKET_KEY.get(market_type)
    bookmaker = next(
        (b for b in (event.get("bookmakers") or []) if str(b.get("key") or "").lower() == "pinnacle"),
        None,
    )
    if not bookmaker:
        return ""
    markets = [m for m in (bookmaker.get("markets") or []) if m.get("key") == key]
    payload = {
        "bookmaker": bookmaker.get("key"),
        "title": bookmaker.get("title"),
        "last_update": bookmaker.get("last_update"),
        "markets": markets,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _book_last_update(event: dict) -> str:
    bookmaker = next(
        (b for b in (event.get("bookmakers") or []) if str(b.get("key") or "").lower() == "pinnacle"),
        None,
    )
    return str((bookmaker or {}).get("last_update") or "")


def _side_ask(row: dict) -> float | None:
    side = str(row.get("side") or "").upper()
    return _f(row.get("yes_ask" if side == "YES" else "no_ask"))


def _prior_rows() -> dict[tuple[str, str], dict]:
    if not SHADOW_LEDGER.exists() or SHADOW_LEDGER.stat().st_size == 0:
        return {}
    latest = {}
    try:
        with SHADOW_LEDGER.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (str(row.get("ticker") or ""), str(row.get("side") or ""))
                latest[key] = row
    except OSError:
        return {}
    return latest


def classify_signal(
    production_prob: float | None,
    pinnacle_prob: float | None,
    production_net_edge: float | None,
    pinnacle_net_edge: float | None,
    pinnacle_move: float | None = None,
    kalshi_move: float | None = None,
) -> str:
    if pinnacle_prob is None:
        return "PINNACLE_UNMATCHED"
    if (
        pinnacle_move is not None and kalshi_move is not None
        and abs(pinnacle_move) >= 0.015 and abs(kalshi_move) <= 0.005
    ):
        return "SHARP_MOVE_KALSHI_LAG"
    if pinnacle_net_edge is not None and pinnacle_net_edge <= 0:
        return "PINNACLE_DISAGREE"
    if production_prob is not None and pinnacle_prob >= production_prob - 0.01:
        return "PINNACLE_CONFIRM"
    if (
        production_net_edge is not None and pinnacle_net_edge is not None
        and pinnacle_net_edge >= max(0.0, production_net_edge - 0.02)
    ):
        return "PINNACLE_PARTIAL_CONFIRM"
    return "PINNACLE_WEAKER"


def _existing_shadow_ids() -> set[str]:
    if not SHADOW_LEDGER.exists() or SHADOW_LEDGER.stat().st_size == 0:
        return set()
    try:
        with SHADOW_LEDGER.open("r", encoding="utf-8", newline="") as fh:
            return {str(r.get("shadow_id") or "") for r in csv.DictReader(fh)}
    except OSError:
        return set()


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    existing = _existing_shadow_ids()
    rows = [r for r in rows if str(r.get("shadow_id") or "") not in existing]
    if not rows:
        return
    write_header = not SHADOW_LEDGER.exists() or SHADOW_LEDGER.stat().st_size == 0
    with SHADOW_LEDGER.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def collect() -> dict:
    api_key = os.getenv("ODDS_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "missing_odds_api_key"}

    run_bucket, candidates = _latest_score_rows()
    if not candidates:
        return {"status": "skipped", "reason": "no_latest_exact_candidates"}

    state = _load_shadow_state()
    production_credits = _production_credits_today()
    shadow_used = max(0, _i(state.get("credits"), 0))
    room = max(0, SHARED_DAILY_BUDGET - production_credits - shadow_used)
    allowance = min(SHADOW_DAILY_CAP - shadow_used, room)
    if allowance <= 0:
        _save_shadow_state(state)
        return {
            "status": "skipped",
            "reason": "shared_daily_budget_exhausted",
            "production_credits": production_credits,
            "shadow_credits": shadow_used,
        }

    settings = Settings()
    aliases = load_aliases()
    prior = _prior_rows()
    out_rows: list[dict] = []
    calls = 0
    matched = 0
    groups = _group_rows(candidates, state)

    for (sport_key, market_type), group in groups:
        if allowance <= 0:
            break
        try:
            events, usage = _pinnacle_request(
                api_key,
                sport_key,
                market_type,
                [str(r.get("event_id") or "") for r in group],
            )
        except (requests.RequestException, ValueError) as exc:
            print("PM_EDGE_PINNACLE_SHADOW_ERROR", sport_key, market_type, type(exc).__name__, str(exc)[:180])
            continue

        cost = max(0, _i(usage.get("requests_last"), 0))
        # Defensive accounting: a nonempty successful response should never be
        # treated as free if the provider omits the header unexpectedly.
        if events and cost <= 0:
            cost = 1
        allowance = max(0, allowance - cost)
        state["credits"] = max(0, _i(state.get("credits"), 0)) + cost
        state.setdefault("group_last", {})[f"{sport_key}|{market_type}"] = _iso()
        calls += 1

        remaining = _i(usage.get("requests_remaining"), -1)
        by_id = {str(e.get("id") or ""): e for e in (events or [])}
        collected_at = _iso()

        for row in group:
            event = by_id.get(str(row.get("event_id") or ""))
            pinnacle_yes = None
            last_update = ""
            raw_json = ""
            if event:
                spec = SimpleNamespace(
                    market_type=market_type,
                    selection=str(row.get("selection") or "") or None,
                    point=_f(row.get("point")),
                )
                consensus, _diag = fair_from_event(
                    spec,
                    event,
                    aliases,
                    min_books=1,
                    max_dispersion=1.0,
                    return_diagnostics=True,
                )
                if consensus is not None:
                    pinnacle_yes = _f(consensus.fair_prob)
                last_update = _book_last_update(event)
                raw_json = _relevant_market_json(event, market_type)

            side = str(row.get("side") or "").upper()
            pinnacle_side = None
            if pinnacle_yes is not None and side in {"YES", "NO"}:
                pinnacle_side = pinnacle_yes if side == "YES" else 1.0 - pinnacle_yes

            side_quote = None
            if pinnacle_side is not None and side in {"YES", "NO"}:
                side_quote = price_side(
                    side=side,
                    fair_prob=pinnacle_side,
                    best_bid=_f(row.get("yes_bid" if side == "YES" else "no_bid")),
                    best_ask=_f(row.get("yes_ask" if side == "YES" else "no_ask")),
                    min_net_edge=0.0,
                    nonfee_buffer=settings.nonfee_buffer,
                    maker_fee_rate=settings.maker_fee_rate,
                    taker_fee_rate=settings.taker_fee_rate,
                    tick_size=0.01,
                )

            prod_prob = _f(row.get("fair_probability"))
            prod_edge = _f(row.get("net_edge"))
            pin_edge = _f(getattr(side_quote, "net_edge", None))
            delta = None if prod_prob is None or pinnacle_side is None else pinnacle_side - prod_prob

            key = (str(row.get("ticker") or ""), side)
            prev = prior.get(key)
            p_move = None
            k_move = None
            if prev:
                prev_p = _f(prev.get("pinnacle_side_prob"))
                prev_k = _f(prev.get("kalshi_side_ask"))
                current_k = _side_ask(row)
                if prev_p is not None and pinnacle_side is not None:
                    p_move = pinnacle_side - prev_p
                if prev_k is not None and current_k is not None:
                    k_move = current_k - prev_k

            signal = classify_signal(prod_prob, pinnacle_side, prod_edge, pin_edge, p_move, k_move)
            quote_time = _parse_ts(last_update)
            quote_age = None
            if quote_time:
                quote_age = max(0.0, (_now() - quote_time).total_seconds())

            shadow_id = "|".join([
                str(run_bucket or ""),
                str(row.get("decision_id") or ""),
                "pinnacle",
            ])
            out = {
                "shadow_id": shadow_id,
                "collected_at": collected_at,
                "run_bucket_utc": run_bucket or "",
                "decision_id": str(row.get("decision_id") or ""),
                "ticker": str(row.get("ticker") or ""),
                "event_id": str(row.get("event_id") or ""),
                "sport_key": sport_key,
                "market_type": market_type,
                "selection": str(row.get("selection") or ""),
                "point": row.get("point") or "",
                "side": side,
                "lane": str(row.get("lane") or ""),
                "production_decision": str(row.get("decision") or ""),
                "production_net_edge": prod_edge,
                "production_side_prob": prod_prob,
                "kalshi_yes_bid": _f(row.get("yes_bid")),
                "kalshi_yes_ask": _f(row.get("yes_ask")),
                "kalshi_no_bid": _f(row.get("no_bid")),
                "kalshi_no_ask": _f(row.get("no_ask")),
                "kalshi_side_ask": _side_ask(row),
                "pinnacle_last_update": last_update,
                "pinnacle_quote_age_seconds": quote_age,
                "pinnacle_yes_prob_power": pinnacle_yes,
                "pinnacle_side_prob": pinnacle_side,
                "pinnacle_net_edge_at_snapshot": pin_edge,
                "pinnacle_minus_production_prob": delta,
                "pinnacle_move_since_prior": p_move,
                "kalshi_move_since_prior": k_move,
                "shadow_signal": signal,
                "pinnacle_raw_market_json": raw_json,
                "requests_last": cost,
                "requests_remaining": remaining,
            }
            out_rows.append(out)
            prior[key] = {k: "" if v is None else v for k, v in out.items()}
            if pinnacle_side is not None:
                matched += 1

        if remaining >= 0 and remaining < MIN_ACCOUNT_REMAINING:
            break

    _append(out_rows)
    _save_shadow_state(state)
    return {
        "status": "ok",
        "run_bucket_utc": run_bucket,
        "candidate_rows": len(candidates),
        "groups_available": len(groups),
        "api_calls": calls,
        "shadow_rows": len(out_rows),
        "pinnacle_exact_matches": matched,
        "production_credits": production_credits,
        "shadow_credits_today": state.get("credits", 0),
        "shared_daily_budget": SHARED_DAILY_BUDGET,
    }


def self_test() -> None:
    assert classify_signal(0.55, None, 0.04, None) == "PINNACLE_UNMATCHED"
    assert classify_signal(0.55, 0.52, 0.04, -0.01) == "PINNACLE_DISAGREE"
    assert classify_signal(0.55, 0.56, 0.04, 0.035) == "PINNACLE_CONFIRM"
    assert classify_signal(0.55, 0.57, 0.04, 0.04, 0.02, 0.0) == "SHARP_MOVE_KALSHI_LAG"
    print("Pinnacle shadow self-test: PASS")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        print("PM_EDGE_PINNACLE_SHADOW " + json.dumps(collect(), sort_keys=True))
