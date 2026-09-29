"""Continuous isolated paper AutoTrader runner.

Reads public Kalshi orderbooks plus independent PM Edge fair values. This module
has no authenticated Kalshi client and cannot submit real-money orders.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

from pmedge.kalshi import KalshiPublicClient, normalize_market
from .collector import PUBLIC_BASE
from .engine import PaperEngine
from .probability import load_pm_edge_api, load_pm_edge_ledger
from .scanner import scan_public
from .storage import load_portfolio, save_portfolio


def _utcnow():
    return datetime.now(timezone.utc)


_KALSHI_WRITE_ENV_NAMES = (
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_PRIVATE_KEY",
    "KALSHI_WRITE_API_KEY_ID",
    "KALSHI_WRITE_PRIVATE_KEY",
)
_KALSHI_SECRET_ENV_RE = re.compile(r"KALSHI.*(?:KEY|SECRET|PRIVATE|PEM)", re.IGNORECASE)


def _assert_no_kalshi_write_credentials():
    present = sorted({
        name
        for name, value in os.environ.items()
        if str(value or "").strip()
        and (name in _KALSHI_WRITE_ENV_NAMES or _KALSHI_SECRET_ENV_RE.search(name))
    })
    if present:
        raise RuntimeError(
            "Paper AutoTrader refuses to start with Kalshi write credentials present: "
            + ",".join(present)
        )


def _file_stats(path):
    p = Path(path)
    if not p.exists():
        return {"exists": False, "rows": 0, "mtime_ns": None, "size": 0}
    try:
        with p.open("r", encoding="utf-8", errors="replace") as handle:
            rows = max(0, sum(1 for _ in handle) - 1)
        st = p.stat()
        return {"exists": True, "rows": rows, "mtime_ns": st.st_mtime_ns, "size": st.st_size}
    except OSError as exc:
        return {"exists": True, "rows": None, "mtime_ns": None, "size": None, "error": str(exc)}


def _watcher_summary(stdout):
    prefix = "PM_EDGE_RUN_SUMMARY "
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix):])
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
    return {}


def _kalshi_schema_probe(client):
    """Public/read-only shape probe used when the fair-value funnel is empty."""
    try:
        markets = client.list_open_markets(max_markets=5)
        if not markets:
            return {"market_count": 0}
        raw = markets[0] if isinstance(markets[0], dict) else {}
        ticker = str(raw.get("ticker") or "")
        probe = {
            "market_count": len(markets),
            "ticker": ticker,
            "market_keys": sorted(str(k) for k in raw.keys())[:100],
        }
        if ticker:
            book = client.get_orderbook(ticker, depth=1)
            probe["orderbook_keys"] = sorted(str(k) for k in (book or {}).keys())[:50]
            fp = (book or {}).get("orderbook_fp")
            if isinstance(fp, dict):
                probe["orderbook_fp_keys"] = sorted(str(k) for k in fp.keys())[:50]
        return probe
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _settle_finished_positions(portfolio, client):
    settled = []
    for ticker in list(portfolio.positions):
        try:
            market = normalize_market(client.get_market(ticker))
        except Exception:
            continue
        result = str(market.get("result") or "").upper()
        status = str(market.get("status") or "").lower()
        settlement_value = market.get("settlement_value")
        # "closed" only means trading ended. Wait for a finalized/settled state
        # and use Kalshi's settlement value when present so special 0<x<1
        # settlements are represented correctly.
        if status in ("finalized", "settled"):
            did_settle = False
            if settlement_value is not None:
                did_settle = portfolio.settle_value(ticker, settlement_value)
            elif result in ("YES", "NO"):
                did_settle = portfolio.settle(ticker, result)
            if did_settle:
                settled.append({
                    "ticker": ticker,
                    "winner": result or None,
                    "yes_settlement_value": settlement_value,
                    "status": status,
                })
    return settled


def _refresh_state(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_refresh_state(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _refresh_local_fair_values(*, ledger_path, refresh_state_path, now,
                               refresh_interval_seconds=1800, timeout=240):
    """Refresh a local PM Edge candidate ledger only when the upstream feed is empty.

    This launches the existing paper-only PM Edge model with a separate state
    file and no notification/order credentials. Odds usage is hard-capped by
    AUTOTRADER_ODDS_DAILY_BUDGET.
    """
    state = _refresh_state(refresh_state_path)
    previous = state.get("last_attempt")
    try:
        previous_dt = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
        if previous_dt.tzinfo is None:
            previous_dt = previous_dt.replace(tzinfo=timezone.utc)
    except Exception:
        previous_dt = None
    if previous_dt is not None:
        age = (now - previous_dt.astimezone(timezone.utc)).total_seconds()
        if age < refresh_interval_seconds:
            return {"attempted": False, "reason": "refresh_cooldown", "age_seconds": max(0, age)}

    odds_api_key = (
        os.environ.get("AUTOTRADER_ODDS_API_KEY", "").strip()
        or os.environ.get("ODDS_API_KEY", "").strip()
    )
    if not odds_api_key:
        return {"attempted": False, "reason": "odds_api_key_unavailable"}

    attempt = now.isoformat()
    ledger_before = _file_stats(ledger_path)
    _write_refresh_state(refresh_state_path, {"last_attempt": attempt, "status": "running"})
    env = os.environ.copy()
    root = Path(ledger_path).parent
    env.update({
        "ODDS_API_KEY": odds_api_key,
        "PM_EDGE_MODE": "paper",
        "PM_EDGE_EXECUTION_MODE": "preview",
        "PM_EDGE_ORDER_SUBMISSION_ENABLED": "0",
        "PUSHOVER_USER_KEY": "",
        "PUSHOVER_APP_TOKEN": "",
        "STREAMLIT_APP_URL": "",
        "PM_EDGE_CANDIDATE_LEDGER_PATH": str(ledger_path),
        "PM_EDGE_WATCH_STATE": str(root / "autotrader-pm-edge-watch-state.json"),
        "PM_EDGE_SIGNAL_LEDGER_PATH": str(root / "autotrader-pm-edge-signals.csv"),
        "PM_EDGE_AUTO_LEDGER_PATH": str(root / "autotrader-pm-edge-auto.csv"),
        "PM_EDGE_ODDS_DAILY_BUDGET": os.environ.get("AUTOTRADER_ODDS_DAILY_BUDGET", "6"),
        "PM_EDGE_MAX_ODDS_REFRESH_PER_RUN": os.environ.get("AUTOTRADER_MAX_ODDS_REFRESH_PER_RUN", "2"),
        "PM_EDGE_MAX_BULK_SPORTS_PER_RUN": os.environ.get("AUTOTRADER_MAX_BULK_SPORTS_PER_RUN", "1"),
        "PM_EDGE_MAX_MARKETS": os.environ.get("AUTOTRADER_FAIR_REFRESH_MAX_MARKETS", "1500"),
        "PM_EDGE_PREGAME_ONLY": "1",
        "PM_EDGE_REJECT_PUSHABLE_LINES": "1",
        "PM_EDGE_PREGAME_BUFFER_SECONDS": "300",
        # Belt-and-suspenders: the fallback process gets no Kalshi write creds,
        # even if somebody later adds one to a shared Railway scope.
        "KALSHI_API_KEY_ID": "",
        "KALSHI_PRIVATE_KEY_PATH": "",
        "KALSHI_PRIVATE_KEY": "",
        "KALSHI_WRITE_API_KEY_ID": "",
        "KALSHI_WRITE_PRIVATE_KEY": "",
        "KALSHI_WRITE_PRIVATE_KEY_PATH": "",
    })
    for name in list(env):
        if _KALSHI_SECRET_ENV_RE.search(name):
            env[name] = ""
    try:
        completed = subprocess.run(
            [sys.executable, "pm_edge_runner_v28.py"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        summary = _watcher_summary(completed.stdout)
        if completed.returncode != 0:
            status = "error"
        elif not summary:
            # A successful process exit without the structured watcher summary is
            # not proof that discovery/pricing actually ran.
            status = "missing_status"
        elif summary.get("odds_hard_failure") and any(
            "OUT_OF_USAGE_CREDITS" in str(item).upper()
            or "USAGE QUOTA HAS BEEN REACHED" in str(item).upper()
            for item in (summary.get("odds_error_samples") or [])
        ):
            status = "odds_quota_exhausted"
        else:
            status = str(summary.get("status") or "missing_status")
        result = {
            "attempted": True,
            "status": status,
            "returncode": completed.returncode,
            "watcher_summary": summary,
            "ledger_before": ledger_before,
            "ledger_after": _file_stats(ledger_path),
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-1600:],
        }
    except subprocess.TimeoutExpired:
        result = {
            "attempted": True, "status": "timeout",
            "ledger_before": ledger_before, "ledger_after": _file_stats(ledger_path),
        }
    except Exception as exc:
        result = {
            "attempted": True, "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "ledger_before": ledger_before, "ledger_after": _file_stats(ledger_path),
        }

    _write_refresh_state(
        refresh_state_path,
        {"last_attempt": attempt, "status": result.get("status", result.get("reason", "unknown"))},
    )
    return result


def _load_fair_values(*, api_base, api_token, ledger_path, now, max_age_seconds,
                      refresh_state_path=None, allow_refresh=False,
                      refresh_interval_seconds=1800):
    api_error = None
    api_diagnostics = {}
    if api_base and api_token:
        try:
            snap = load_pm_edge_api(
                api_base, api_token, now=now,
                max_age_seconds=max_age_seconds,
            )
            api_diagnostics = getattr(snap, "diagnostics", {}) or {}
            if snap.probabilities:
                return snap, "pm_edge_mobile_api", {"attempted": False, "reason": "upstream_fresh", "api_diagnostics": api_diagnostics}
        except Exception as exc:
            api_error = f"{type(exc).__name__}: {exc}"

    local = load_pm_edge_ledger(
        ledger_path, now=now, max_age_seconds=max_age_seconds,
    )
    if local.probabilities:
        return local, "pm_edge_local_ledger", {"attempted": False, "reason": "local_fresh"}

    refresh = {"attempted": False, "reason": "refresh_disabled"}
    if allow_refresh and refresh_state_path:
        refresh = _refresh_local_fair_values(
            ledger_path=ledger_path,
            refresh_state_path=refresh_state_path,
            now=now,
            refresh_interval_seconds=refresh_interval_seconds,
        )
        local = load_pm_edge_ledger(
            ledger_path, now=now, max_age_seconds=max_age_seconds,
        )
        if local.probabilities:
            return local, "autotrader_local_pm_edge_refresh", refresh

    if api_error:
        refresh["api_error"] = api_error
    if api_diagnostics:
        refresh["api_diagnostics"] = api_diagnostics
    return local, "pm_edge_no_fresh_fair_values", refresh


def run_once(*, client=None, state_path="data/autotrader-paper.json",
             runs_path="data/autotrader-paper-runs.jsonl",
             ledger_path="pm_edge_candidate_ledger.csv", max_markets=3000,
             max_orderbooks=250, probability_max_age_seconds=300,
             fee_per_contract=None, slippage_per_contract="0.005",
             max_quote_age_seconds=5, min_net_edge="0.05",
             fair_api_base=None, fair_api_token=None, now=None, clock=None,
             refresh_state_path="/data/autotrader-fair-refresh.json",
             allow_fair_refresh=False, refresh_interval_seconds=1800):
    fixed_now = now
    clock = clock or ((lambda: fixed_now) if fixed_now is not None else _utcnow)
    started_at = clock().astimezone(timezone.utc)
    client = client or KalshiPublicClient(PUBLIC_BASE)
    portfolio = load_portfolio(state_path)
    settled = _settle_finished_positions(portfolio, client)

    fair, provider, fair_refresh = _load_fair_values(
        api_base=fair_api_base, api_token=fair_api_token,
        ledger_path=ledger_path, now=started_at,
        max_age_seconds=probability_max_age_seconds,
        refresh_state_path=refresh_state_path,
        allow_refresh=allow_fair_refresh,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    fair_tickers = {ticker for ticker, _side in fair.probabilities}
    candidates = scan_public(
        client,
        max_markets=max_markets,
        max_orderbooks=min(max_orderbooks, len(fair_tickers)) if fair_tickers else 0,
        clock=clock,
        ticker_filter=fair_tickers,
    ) if fair_tickers else []
    decision_at = clock().astimezone(timezone.utc)
    schema_probe = _kalshi_schema_probe(client) if not fair.probabilities else {}
    quote_ages = {}
    for c in candidates:
        if c.observed_at is None:
            continue
        quote_ages[(c.ticker, c.side)] = (decision_at - c.observed_at).total_seconds()

    engine = PaperEngine(portfolio=portfolio, min_net_edge=Decimal(str(min_net_edge)))
    result = engine.on_candidates(
        candidates,
        probabilities=fair.probabilities,
        quote_ages=quote_ages,
        event_data_ages=fair.ages,
        contexts=fair.contexts,
        fee_per_contract=fee_per_contract,
        slippage_per_contract=slippage_per_contract,
        max_quote_age_seconds=max_quote_age_seconds,
        max_event_data_age_seconds=probability_max_age_seconds,
    )
    save_portfolio(engine.portfolio, state_path)

    matched = sum(1 for c in candidates if (c.ticker, c.side) in fair.probabilities)
    quote_fresh = sum(
        1 for age in quote_ages.values()
        if age is not None and 0 <= float(age) <= float(max_quote_age_seconds)
    )
    risk_ok = sum(
        1 for d in engine.decisions
        if d.get("qualified") and d.get("decision") not in {
            "paper_risk_rejected", "insufficient_paper_risk_budget", "invalid_simulated_fill"
        }
    )
    upstream_funnel = ((fair_refresh or {}).get("watcher_summary") or {}).get("funnel") or {}
    run = {
        "observed_at": decision_at.isoformat(),
        "provider": provider,
        "provider_scope": "fresh PM Edge sportsbook-derived fair values",
        "fair_refresh": fair_refresh,
        "kalshi_schema_probe": schema_probe,
        "candidate_count": len(candidates),
        "fresh_probability_count": len(fair.probabilities),
        "fair_diagnostics": (
            getattr(fair, "diagnostics", {}) or {}
            or (fair_refresh or {}).get("api_diagnostics") or {}
        ),
        "matched_candidate_count": matched,
        "paper_fills": result["paper_fills"],
        "funnel": {
            **upstream_funnel,
            "fresh_probability": len(fair.probabilities),
            "matched_candidate": matched,
            "quote_fresh": quote_fresh,
            "risk_ok": risk_ok,
            "filled": result["paper_fills"],
        },
        "settlements": settled,
        "portfolio": result["portfolio"],
        "decisions": engine.decisions,
    }
    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, separators=(",", ":")) + "\n")
    return run



def _tail_jsonl(path, limit=50):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    rows = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows[-max(1, int(limit)):]


def _fair_value_rows(path, limit=1000):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    try:
        with p.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    rows = [r for r in rows if str(r.get("stage") or "").lower() == "score"]
    rows.sort(key=lambda r: str(r.get("fair_fetched_at") or r.get("evaluated_at") or ""), reverse=True)
    seen = set()
    out = []
    for row in rows:
        key = (str(row.get("ticker") or ""), str(row.get("side") or "").upper())
        if not key[0] or key[1] not in {"YES", "NO"} or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _paper_status(state_path, runs_path):
    portfolio = load_portfolio(state_path)
    runs = _tail_jsonl(runs_path, 50)
    last_run = runs[-1] if runs else None
    positions = []
    for ticker, pos in portfolio.positions.items():
        positions.append({
            "ticker": ticker,
            "side": pos.side,
            "contracts": pos.contracts,
            "entry": str(pos.entry),
            "entry_fee": str(pos.entry_fee),
            "exposure_key": getattr(pos, "exposure_key", None),
            "fair_snapshot_id": getattr(pos, "fair_snapshot_id", None),
        })
    settled = [x for x in portfolio.ledger if str(x.get("action") or "").upper() == "SETTLE"]
    wins = sum(1 for x in settled if Decimal(str(x.get("pnl") or "0")) > 0)
    losses = sum(1 for x in settled if Decimal(str(x.get("pnl") or "0")) < 0)
    return {
        "mode": "paper",
        "worker_online": True,
        "scan_interval_seconds": int(os.environ.get("AUTOTRADER_SCAN_INTERVAL_SECONDS", "300")),
        "starting_bankroll": "500.00",
        "portfolio": portfolio.snapshot(),
        "positions": positions,
        "record": f"{wins}-{losses}",
        "settled_count": len(settled),
        "blotter": portfolio.ledger[-100:],
        "last_run": last_run,
        "recent_runs": runs[-20:],
    }


def _start_status_server(*, state_path, runs_path, ledger_path, trigger_event, port):
    token = str(os.environ.get("AUTOTRADER_API_TOKEN", "") or "").strip()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _authorized(self):
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _send(self, status, payload):
            body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                return self._send(200, {"ok": True, "mode": "paper"})
            if not self._authorized():
                return self._send(401, {"detail": "AutoTrader authentication required"})
            if parsed.path == "/v1/status":
                return self._send(200, _paper_status(state_path, runs_path))
            if parsed.path == "/v1/runs":
                try:
                    limit = min(100, max(1, int(parse_qs(parsed.query).get("limit", ["20"])[0])))
                except Exception:
                    limit = 20
                return self._send(200, {"items": _tail_jsonl(runs_path, limit)})
            if parsed.path == "/v1/fair-values":
                return self._send(200, {
                    "generated_at": _utcnow().isoformat().replace("+00:00", "Z"),
                    "items": _fair_value_rows(ledger_path),
                })
            return self._send(404, {"detail": "Not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if not self._authorized():
                return self._send(401, {"detail": "AutoTrader authentication required"})
            if parsed.path == "/v1/run-scan":
                trigger_event.set()
                return self._send(202, {"accepted": True, "mode": "paper"})
            return self._send(404, {"detail": "Not found"})

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    thread = threading.Thread(target=server.serve_forever, name="autotrader-status-http", daemon=True)
    thread.start()
    return server


def main():
    _assert_no_kalshi_write_credentials()
    parser = argparse.ArgumentParser(description="Isolated paper-only AutoTrader")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-markets", type=int, default=1000)
    parser.add_argument("--max-orderbooks", type=int, default=30)
    parser.add_argument("--state", default=os.environ.get("AUTOTRADER_STATE", "/data/autotrader-paper.json"))
    parser.add_argument("--runs", default=os.environ.get("AUTOTRADER_RUNS", "/data/autotrader-paper-runs.jsonl"))
    parser.add_argument("--ledger", default=os.environ.get("AUTOTRADER_LEDGER", "pm_edge_candidate_ledger.csv"))
    parser.add_argument("--fair-api-base", default=os.environ.get("AUTOTRADER_FAIR_API_BASE", ""))
    parser.add_argument("--fair-api-token", default=os.environ.get("AUTOTRADER_FAIR_API_TOKEN", ""))
    parser.add_argument("--probability-max-age", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_MAX_AGE_SECONDS", "300")))
    parser.add_argument("--max-quote-age", type=int, default=int(os.environ.get("AUTOTRADER_QUOTE_MAX_AGE_SECONDS", "5")))
    parser.add_argument("--fee", default=os.environ.get("AUTOTRADER_FEE_PER_CONTRACT", ""))
    parser.add_argument("--slippage", default=os.environ.get("AUTOTRADER_SLIPPAGE_PER_CONTRACT", "0.005"))
    parser.add_argument("--min-edge", default=os.environ.get("AUTOTRADER_MIN_NET_EDGE", "0.05"))
    parser.add_argument("--refresh-state", default=os.environ.get("AUTOTRADER_REFRESH_STATE", "/data/autotrader-fair-refresh.json"))
    parser.add_argument("--refresh-interval", type=int, default=int(os.environ.get("AUTOTRADER_FAIR_REFRESH_INTERVAL_SECONDS", "1800")))
    parser.add_argument("--allow-fair-refresh", action="store_true", default=os.environ.get("AUTOTRADER_ALLOW_FAIR_REFRESH", "0").strip().lower() in {"1","true","yes","on"})
    args = parser.parse_args()
    if args.interval < 30 or args.max_markets < 1 or args.max_orderbooks < 1:
        parser.error("interval must be >= 30 seconds; market limits must be >= 1")

    client = KalshiPublicClient(PUBLIC_BASE)
    trigger_event = threading.Event()
    run_lock = threading.Lock()
    if not args.once and os.environ.get("AUTOTRADER_HTTP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
        http_port = int(os.environ.get("PORT", os.environ.get("AUTOTRADER_HTTP_PORT", "8080")))
        _start_status_server(
            state_path=args.state,
            runs_path=args.runs,
            ledger_path=args.ledger,
            trigger_event=trigger_event,
            port=http_port,
        )
        print(json.dumps({"autotrader_http": "listening", "port": http_port, "mode": "paper"}), flush=True)

    while True:
        try:
            with run_lock:
                result = run_once(
                    client=client,
                    state_path=args.state,
                    runs_path=args.runs,
                    ledger_path=args.ledger,
                    max_markets=args.max_markets,
                    max_orderbooks=args.max_orderbooks,
                    probability_max_age_seconds=args.probability_max_age,
                    fee_per_contract=args.fee,
                    slippage_per_contract=args.slippage,
                    max_quote_age_seconds=args.max_quote_age,
                    min_net_edge=args.min_edge,
                    fair_api_base=args.fair_api_base,
                    fair_api_token=args.fair_api_token,
                    refresh_state_path=args.refresh_state,
                    allow_fair_refresh=args.allow_fair_refresh,
                    refresh_interval_seconds=args.refresh_interval,
                )
            print(json.dumps({k: v for k, v in result.items() if k != "decisions"}), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), flush=True)
            if args.once:
                raise
        if args.once:
            return
        trigger_event.wait(args.interval)
        trigger_event.clear()


if __name__ == "__main__":
    main()
