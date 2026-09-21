from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pandas as pd
import requests

import approval_links
from pm_edge_runner_v20 import calculate_share_order

SIGNAL_LEDGER = Path(os.getenv("PM_EDGE_SIGNAL_LEDGER_PATH", "pm_edge_signal_ledger.csv"))
AUTO_LEDGER = Path(os.getenv("PM_EDGE_AUTO_LEDGER_PATH", "pm_edge_auto_ledger.csv"))
CANDIDATE_LEDGER = Path(os.getenv("PM_EDGE_CANDIDATE_LEDGER_PATH", "pm_edge_candidate_ledger.csv"))
CLV_LEDGER = Path(os.getenv("PM_EDGE_CLV_LEDGER_PATH", "pm_edge_clv_ledger.csv"))
FILL_LEDGER = Path(os.getenv("PM_EDGE_FILL_LEDGER_PATH", "pm_edge_fill_ledger.csv"))
SIGNAL_OUTCOMES = Path(os.getenv("PM_EDGE_SIGNAL_OUTCOME_LEDGER_PATH", "pm_edge_signal_outcomes.csv"))
STATE_PATH = Path(os.getenv("PM_EDGE_WATCH_STATE", ".pm_edge_watch_state.json"))
KALSHI_PUBLIC_BASE = os.getenv(
    "PM_EDGE_KALSHI_PUBLIC_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")

_SIGNAL_ID_RE = re.compile(r"^pmedge-([0-9a-f]{20})-[A-Za-z0-9]+$")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def load_data() -> dict[str, pd.DataFrame]:
    return {
        "signals": _normalize_signals(_read_csv(SIGNAL_LEDGER)),
        "orders": _read_csv(AUTO_LEDGER),
        "candidates": _read_csv(CANDIDATE_LEDGER),
        "clv": _read_csv(CLV_LEDGER),
        "fills": _read_csv(FILL_LEDGER),
        "signal_outcomes": _read_csv(SIGNAL_OUTCOMES),
    }


def _normalize_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "record_type" not in out.columns:
        out["record_type"] = "signal"
    for col in (
        "signal_id","ticker","side","lane","sport_title","sport_key","market_type",
        "market_title","execution","alerted_at","trade_date","commence_time",
        "client_order_id","submitted_at",
    ):
        if col not in out.columns:
            out[col] = ""
    for col in ("recommended_limit","hard_max","fair_probability","net_edge","books","dispersion"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def signal_id_from_client_order_id(value: str) -> str | None:
    match = _SIGNAL_ID_RE.match(str(value or "").strip())
    return match.group(1) if match else None


def _best_signal_lookup(signals: pd.DataFrame) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    by_id: dict[str, dict] = {}
    by_pair: dict[tuple[str, str], dict] = {}
    if signals.empty:
        return by_id, by_pair
    sigs = signals[signals["record_type"].fillna("signal").astype(str).eq("signal")].copy()
    if "alerted_at" in sigs.columns:
        sigs = sigs.sort_values("alerted_at")
    for row in sigs.to_dict("records"):
        sid = str(row.get("signal_id") or "")
        ticker = str(row.get("ticker") or "")
        side = str(row.get("side") or "").upper()
        if sid:
            by_id[sid] = row
        if ticker and side:
            by_pair[(ticker, side)] = row
    return by_id, by_pair


def enrich_orders(orders: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    if orders.empty:
        return orders.copy()
    out = orders.copy()
    by_id, by_pair = _best_signal_lookup(signals)
    enrich_cols = [
        "signal_id","market_title","sport_title","sport_key","market_type",
        "event_id","commence_time","fair_fetched_at","dispersion",
    ]
    for col in enrich_cols:
        if col not in out.columns:
            out[col] = pd.NA

    for idx, row in out.iterrows():
        sid = signal_id_from_client_order_id(row.get("client_order_id"))
        signal = by_id.get(sid or "")
        if signal is None:
            signal = by_pair.get((str(row.get("ticker") or ""), str(row.get("side") or "").upper()))
        if signal is None:
            continue
        if sid and (pd.isna(out.at[idx, "signal_id"]) or not str(out.at[idx, "signal_id"]).strip()):
            out.at[idx, "signal_id"] = sid
        for col in enrich_cols:
            current = out.at[idx, col]
            if pd.isna(current) or str(current).strip() in {"", "nan", "None"}:
                value = signal.get(col)
                if value is not None and not (isinstance(value, float) and math.isnan(value)):
                    out.at[idx, col] = value
        if str(row.get("lane") or "") in {"", "approval_link", "nan"}:
            lane = str(signal.get("lane") or "")
            if lane:
                out.at[idx, "lane"] = lane

    for col in ("net_pl","entry_cost","entry_fees","roi","filled_shares","requested_shares","limit_price","average_fill_price"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def actual_summary(orders: pd.DataFrame) -> dict:
    if orders.empty:
        return {
            "record":"0-0","wins":0,"losses":0,"net_pl":0.0,"roi":None,
            "risked":0.0,"settled":0,"unfilled":0,"open":0,
        }
    status = orders.get("status", pd.Series("", index=orders.index)).fillna("").astype(str)
    wins = int(status.eq("settled_win").sum())
    losses = int(status.eq("settled_loss").sum())
    net_pl = float(pd.to_numeric(orders.get("net_pl", 0), errors="coerce").fillna(0).sum())
    cost = pd.to_numeric(orders.get("entry_cost", 0), errors="coerce").fillna(0)
    fees = pd.to_numeric(orders.get("entry_fees", 0), errors="coerce").fillna(0) if "entry_fees" in orders.columns else 0
    risked = float((cost + fees).sum()) if not isinstance(fees, int) else float(cost.sum())
    settled_mask = status.str.startswith("settled_") | status.str.startswith("closed_early_")
    unfilled = int(status.eq("unfilled").sum())
    open_mask = ~settled_mask & ~status.eq("unfilled")
    return {
        "record":f"{wins}-{losses}",
        "wins":wins,
        "losses":losses,
        "net_pl":net_pl,
        "roi":(net_pl / risked) if risked > 0 else None,
        "risked":risked,
        "settled":int(settled_mask.sum()),
        "unfilled":unfilled,
        "open":int(open_mask.sum()),
    }


def opportunity_summary(outcomes: pd.DataFrame) -> dict:
    if outcomes.empty:
        return {"record":"0-0","wins":0,"losses":0,"net_pl":0.0,"roi":None,"settled":0}
    outcome = outcomes.get("signal_outcome", pd.Series("", index=outcomes.index)).fillna("").astype(str)
    wins = int(outcome.eq("WIN").sum())
    losses = int(outcome.eq("LOSS").sum())
    pl = float(pd.to_numeric(outcomes.get("hypothetical_gross_pl", 0), errors="coerce").fillna(0).sum())
    risk = float(pd.to_numeric(outcomes.get("hypothetical_risk", 0), errors="coerce").fillna(0).sum())
    return {
        "record":f"{wins}-{losses}",
        "wins":wins,
        "losses":losses,
        "net_pl":pl,
        "roi":(pl / risk) if risk > 0 else None,
        "settled":int(outcome.isin(["WIN","LOSS","PUSH"]).sum()),
    }


def submitted_signal_ids(signals: pd.DataFrame, orders: pd.DataFrame) -> set[str]:
    ids: set[str] = set()
    if not signals.empty:
        submitted = signals[signals["record_type"].fillna("signal").astype(str).eq("submission")]
        ids.update(str(x) for x in submitted.get("signal_id", []) if str(x) not in {"", "nan"})
    if not orders.empty and "client_order_id" in orders.columns:
        for value in orders["client_order_id"].fillna("").astype(str):
            sid = signal_id_from_client_order_id(value)
            if sid:
                ids.add(sid)
    return ids


def new_signals(signals: pd.DataFrame, orders: pd.DataFrame, *, now: datetime | None = None) -> pd.DataFrame:
    if signals.empty:
        return signals.copy()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sigs = signals[signals["record_type"].fillna("signal").astype(str).eq("signal")].copy()
    submitted = submitted_signal_ids(signals, orders)
    sigs = sigs[~sigs["signal_id"].fillna("").astype(str).isin(submitted)]

    # Historical pre-provenance orders cannot expose signal_id. For the Bet
    # Center only, suppress an alert when the account ledger already contains
    # the exact ticker + side + trade date. Settlement reconciliation remains
    # signal_id-only for the new provenance path.
    if not orders.empty and {"ticker", "side", "trade_date"}.issubset(orders.columns):
        placed_keys = {
            (
                str(r.get("ticker") or ""),
                str(r.get("side") or "").upper(),
                str(r.get("trade_date") or ""),
            )
            for r in orders.to_dict("records")
            if str(r.get("client_order_id") or "").startswith("pmedge-")
        }
        keep = [
            (
                str(r.get("ticker") or ""),
                str(r.get("side") or "").upper(),
                str(r.get("trade_date") or ""),
            ) not in placed_keys
            for r in sigs.to_dict("records")
        ]
        sigs = sigs[pd.Series(keep, index=sigs.index)]

    alerted = pd.to_datetime(sigs["alerted_at"], errors="coerce", utc=True)
    commence = pd.to_datetime(sigs["commence_time"], errors="coerce", utc=True)
    recent = alerted.ge(now - pd.Timedelta(hours=24))
    still_relevant = commence.ge(now - pd.Timedelta(hours=2))
    sigs = sigs[recent | still_relevant]
    return sigs.sort_values("alerted_at", ascending=False)


def _parse_time(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def signal_state(row: dict, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    commence = _parse_time(row.get("commence_time"))
    if commence and commence <= now:
        return "LIVE / STARTED"
    if commence:
        hours = (commence - now).total_seconds() / 3600.0
        if hours <= 2:
            return "STARTING SOON"
    return "UPCOMING"


def size_signal(row: dict) -> dict | None:
    try:
        price = float(row.get("recommended_limit"))
    except (TypeError, ValueError):
        return None
    lane = str(row.get("lane") or "primary").lower()
    if lane == "value":
        return calculate_share_order(price, target_profit=2.50, max_risk=5.00)
    return calculate_share_order(price, target_profit=5.00, max_risk=10.00)


def approval_url_for_signal(row: dict, *, base_url: str | None = None) -> str | None:
    signal_id = str(row.get("signal_id") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{20}", signal_id):
        return None
    order = size_signal(row)
    if not order:
        return None
    try:
        token = approval_links.create_approval_token(
            ticker=str(row.get("ticker") or ""),
            side=str(row.get("side") or "").upper(),
            lane=str(row.get("lane") or "primary"),
            execution=str(row.get("execution") or "MAKE").upper(),
            limit_price=float(order["price"]),
            shares=int(order["shares"]),
            max_loss=float(order["max_loss"]),
            fair_probability=_as_float(row.get("fair_probability")),
            net_edge=_as_float(row.get("net_edge")),
            books=_as_int(row.get("books")),
            dispersion=_as_float(row.get("dispersion")),
            source_version=_as_int(row.get("source_version")),
            fair_fetched_at=str(row.get("fair_fetched_at") or "") or None,
            signal_id=signal_id,
            ttl_seconds=1800,
            event_start_at=int(_parse_time(row.get("commence_time")).timestamp()) if _parse_time(row.get("commence_time")) else None,
            is_live=signal_state(row).startswith("LIVE"),
        )
        root = str(base_url or os.getenv("STREAMLIT_APP_URL", "")).strip()
        return approval_links.build_approval_url(root, token)
    except Exception:
        return None


def _as_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def last_model_activity(data: dict[str, pd.DataFrame]) -> datetime | None:
    times = []
    candidates = data.get("candidates", pd.DataFrame())
    signals = data.get("signals", pd.DataFrame())
    if not candidates.empty and "evaluated_at" in candidates.columns:
        x = pd.to_datetime(candidates["evaluated_at"], errors="coerce", utc=True).max()
        if pd.notna(x):
            times.append(x.to_pydatetime())
    if not signals.empty and "alerted_at" in signals.columns:
        x = pd.to_datetime(signals["alerted_at"], errors="coerce", utc=True).max()
        if pd.notna(x):
            times.append(x.to_pydatetime())
    return max(times) if times else None


def market_snapshot(ticker: str, *, getter=requests.get) -> dict:
    response = getter(f"{KALSHI_PUBLIC_BASE}/markets/{quote(str(ticker), safe='')}", timeout=10)
    response.raise_for_status()
    payload = response.json()
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        raise RuntimeError("Unexpected Kalshi market response")

    def px(prefix):
        for key in (f"{prefix}_dollars", prefix):
            value = market.get(key)
            if value in (None, ""):
                continue
            try:
                x = float(value)
            except (TypeError, ValueError):
                continue
            if key == prefix and x > 1:
                x /= 100.0
            return x
        return None

    return {
        "ticker":ticker,
        "title":str(market.get("title") or market.get("subtitle") or ticker),
        "status":str(market.get("status") or ""),
        "result":str(market.get("result") or ""),
        "yes_bid":px("yes_bid"),
        "yes_ask":px("yes_ask"),
        "no_bid":px("no_bid"),
        "no_ask":px("no_ask"),
    }


def open_order_rows(orders: pd.DataFrame) -> pd.DataFrame:
    if orders.empty:
        return orders.copy()
    status = orders.get("status", pd.Series("", index=orders.index)).fillna("").astype(str)
    mask = ~status.str.startswith("settled_") & ~status.str.startswith("closed_early_") & ~status.eq("unfilled")
    return orders[mask].copy()


def aggregate_performance(orders: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if orders.empty or group_col not in orders.columns:
        return pd.DataFrame()
    rows = []
    for name, group in orders.groupby(group_col, dropna=False):
        label = str(name) if str(name) not in {"nan","None",""} else "Unknown"
        summary = actual_summary(group)
        rows.append({
            group_col:label,
            "bets":summary["settled"],
            "record":summary["record"],
            "net_pl":round(summary["net_pl"], 2),
            "roi":summary["roi"],
            "risked":round(summary["risked"], 2),
        })
    return pd.DataFrame(rows).sort_values(["bets","net_pl"], ascending=[False,False])


def signal_funnel(candidates: pd.DataFrame, signals: pd.DataFrame, orders: pd.DataFrame) -> dict:
    qualified = 0
    if not candidates.empty:
        if "decision" in candidates.columns:
            qualified = int(candidates["decision"].fillna("").astype(str).eq("QUALIFIED").sum())
        elif "qualifies" in candidates.columns:
            qualified = int(candidates["qualifies"].fillna(False).astype(bool).sum())
    alerts = 0 if signals.empty else int(signals["record_type"].fillna("signal").astype(str).eq("signal").sum())
    placed = 0 if orders.empty else int(orders.get("client_order_id", pd.Series(dtype=str)).fillna("").astype(str).str.startswith("pmedge-").sum())
    filled = 0
    if not orders.empty and "filled_shares" in orders.columns:
        filled = int(pd.to_numeric(orders["filled_shares"], errors="coerce").fillna(0).gt(0).sum())
    return {"qualified":qualified,"alerts":alerts,"placed":placed,"filled":filled}
