from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import performance_tracker
from dashboard_data import size_signal

SIGNAL_LEDGER = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))
OUTCOME_LEDGER = Path(os.getenv("PM_EDGE_SIGNAL_OUTCOME_LEDGER_PATH", "pm_edge_signal_outcomes.csv"))
MAX_CHECKS_PER_RUN = max(1, int(os.getenv("PM_EDGE_SIGNAL_OUTCOME_MAX_CHECKS_PER_RUN", "60")))

FIELDS = [
    "signal_id","trade_date","ticker","side","lane","sport_title","market_type",
    "recommended_limit","shares","hypothetical_risk","market_result",
    "signal_outcome","hypothetical_gross_pl","hypothetical_roi",
    "settled_at","captured_at",
]


def _dt(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _existing_ids(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return {str(row.get("signal_id") or "") for row in csv.DictReader(fh)}
    except OSError:
        return set()


def _signal_won(side: str, market_result: str) -> str:
    side = str(side or "").upper()
    result = str(market_result or "").lower()
    if result == "void":
        return "PUSH"
    if result not in {"yes","no"} or side not in {"YES","NO"}:
        return "UNKNOWN"
    return "WIN" if side.lower() == result else "LOSS"


def track_signal_outcomes(
    *,
    now: datetime | None = None,
    market_loader=performance_tracker._load_market_settlement,
    max_checks: int = MAX_CHECKS_PER_RUN,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not SIGNAL_LEDGER.exists():
        return {"checked":0,"settled":0,"pending":0}

    try:
        signals = pd.read_csv(SIGNAL_LEDGER)
    except Exception:
        return {"checked":0,"settled":0,"pending":0}
    if signals.empty:
        return {"checked":0,"settled":0,"pending":0}
    if "record_type" in signals.columns:
        signals = signals[signals["record_type"].fillna("signal").astype(str).eq("signal")]

    existing = _existing_ids(OUTCOME_LEDGER)
    candidates = []
    for row in signals.to_dict("records"):
        signal_id = str(row.get("signal_id") or "")
        if not signal_id or signal_id in existing:
            continue
        commence = _dt(row.get("commence_time"))
        alerted = _dt(row.get("alerted_at"))
        reference = commence or alerted
        if reference is None or reference > now:
            continue
        if reference < now - timedelta(days=14):
            continue
        candidates.append(row)

    candidates.sort(key=lambda r: str(r.get("commence_time") or r.get("alerted_at") or ""))
    checked = settled = 0
    for row in candidates[:max(1, int(max_checks))]:
        checked += 1
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        try:
            settlement = market_loader(ticker)
        except Exception:
            continue
        if not settlement:
            continue
        result = str(settlement.get("market_result") or "").lower()
        if result not in {"yes","no","void"}:
            continue

        price = _float(row.get("recommended_limit"))
        side = str(row.get("side") or "").upper()
        order = size_signal(row)
        if price is None or order is None or side not in {"YES","NO"}:
            continue
        shares = int(order["shares"])
        risk = float(price) * shares
        signal_outcome = _signal_won(side, result)
        if signal_outcome == "WIN":
            gross_pl = shares * (1.0 - price)
        elif signal_outcome == "LOSS":
            gross_pl = -risk
        else:
            gross_pl = 0.0

        output = {
            "signal_id":str(row.get("signal_id") or ""),
            "trade_date":str(row.get("trade_date") or ""),
            "ticker":ticker,
            "side":side,
            "lane":str(row.get("lane") or ""),
            "sport_title":str(row.get("sport_title") or ""),
            "market_type":str(row.get("market_type") or ""),
            "recommended_limit":round(price,6),
            "shares":shares,
            "hypothetical_risk":round(risk,6),
            "market_result":result.upper(),
            "signal_outcome":signal_outcome,
            "hypothetical_gross_pl":round(gross_pl,6),
            "hypothetical_roi":round(gross_pl / risk,6) if risk > 0 else "",
            "settled_at":str(settlement.get("settled_time") or ""),
            "captured_at":now.isoformat().replace("+00:00","Z"),
        }
        OUTCOME_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        write_header = not OUTCOME_LEDGER.exists() or OUTCOME_LEDGER.stat().st_size == 0
        with OUTCOME_LEDGER.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(output)
        existing.add(output["signal_id"])
        settled += 1

    return {"checked":checked,"settled":settled,"pending":max(0,len(candidates)-settled)}


if __name__ == "__main__":
    print(track_signal_outcomes())
