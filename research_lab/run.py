"""One Research Lab round (PAPER ONLY). Run: python -m research_lab.run

1. settle and mark every open paper position from Kalshi's official results;
2. load the main scanner's sportsbook snapshot (no Odds API calls);
3. fetch shared public Kalshi data once: markets, recent trade tape, open events;
4. let each frozen strategy decide independently and record into its own ledger;
5. reconcile every ledger, write research_lab/data/status.json, send the daily summary.
"""
from __future__ import annotations

import json
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from research_lab import config, metrics, pinnacle, report
from research_lab.context import RunContext
from research_lab.flow import aggregate
from research_lab.ledger import LedgerError, Store, iso, loads, now_utc, run_lock
from research_lab.public_data import PublicDataError, PublicKalshi
from research_lab.settle import settle_store, tickers_to_check
from research_lab.snapshot import load_snapshot
from research_lab.strategies.structural import run_structural
from research_lab.strategies.value import run_hybrid, run_value_only

RUNNERS = {
    config.VALUE_ONLY.key: run_value_only,
    config.HYBRID_SHARP_VALUE.key: run_hybrid,
    config.STRUCTURAL_ARB.key: run_structural,
    config.VALUE_ONLY_V2.key: run_value_only,
    config.HYBRID_SHARP_VALUE_V2.key: run_hybrid,
}


def status_path(base: Path | None = None) -> Path:
    return Path(base or config.DATA_DIR) / "status.json"


def reconcile(store: Store) -> list[str]:
    """Internal consistency checks. Returns a list of problems (empty = reconciled)."""
    problems = []
    ids = Counter(p.get("position_id") for p in store.positions)
    problems += [f"duplicate position {k}" for k, n in ids.items() if n > 1]
    for p in store.positions:
        cost, fee, committed = (metrics.f(p.get(k), None) for k in ("cost", "fee", "committed"))
        if None in (cost, fee, committed) or abs(cost + fee - committed) > 0.011:
            problems.append(f"{p.get('position_id')}: committed != cost + fee")
        if p.get("status") in metrics.DONE and metrics.f(p.get("pl"), None) is None:
            problems.append(f"{p.get('position_id')}: settled without P/L")
        legs = loads(p.get("legs"), None)
        if legs and abs(sum(metrics.f(x.get("cost"), 0) for x in legs) - (cost or 0)) > 0.011:
            problems.append(f"{p.get('position_id')}: legs don't add up")
    s = metrics.summarize(store.positions)
    cash = config.STARTING_BANKROLL + s["net_pl"] - s["open_exposure"]
    if cash < -0.01:
        problems.append("bankroll below zero")
    return problems


def pinnacle_round(ctx: RunContext, kalshi, pinn, base: Path) -> dict:
    """Fetch Pinnacle prices (if due) and the matching Kalshi series; fill ctx.pinnacle_quotes."""
    payloads, info = pinnacle.fetch_due(pinn, ctx.now, base)
    kalshi_events: dict[int, list] = {}
    for sid in payloads:
        evs = []
        for series in pinnacle.SPORTS[sid][1]:
            try:
                evs += kalshi.series_events(series)
            except PublicDataError:
                info["errors"].append(f"Kalshi series {series} unavailable")
        kalshi_events[sid] = evs
    quotes, markets, match_info = pinnacle.build_quotes(payloads, kalshi_events, ctx.now)
    ctx.pinnacle_quotes = quotes
    ctx.markets.update(markets)
    info.update(match_info)
    if not payloads and not info["errors"]:
        info["note"] = "no Pinnacle fetch due this round"
    return info


def run_round(*, now: datetime | None = None, kalshi=None, base: Path | None = None,
              snapshot_path: Path | None = None, send_report: bool = True, pinn=None) -> dict:
    now = now or now_utc()
    base = Path(base or config.DATA_DIR)
    kalshi = kalshi or PublicKalshi()
    out: dict = {"at": iso(now), "paper_only": True, "strategies": {}}
    with run_lock(base):
        stores: dict[str, Store] = {}
        for spec in config.RESEARCH_STRATEGIES:
            try:
                stores[spec.key] = Store(spec.key, base)
            except LedgerError as exc:
                out["strategies"][spec.key] = {"ok": False, "error": str(exc)}
        quotes, snap = load_snapshot(now, snapshot_path)
        snap["empty"] = not quotes
        out["snapshot"] = snap
        tickers = [q.ticker for q in quotes]
        for st in stores.values():
            tickers += tickers_to_check(st)
        try:
            markets = kalshi.markets(tickers) if tickers else {}
        except PublicDataError:
            markets = {}
        # 1. settlement first, so freed bankroll is available this round
        for key, st in stores.items():
            out["strategies"].setdefault(key, {})["settle"] = settle_store(st, markets, now)
        # 2. shared data
        ctx = RunContext(now=now, kalshi=kalshi, quotes=quotes, markets=markets)
        if not quotes:
            ctx.notes.append("No sportsbook prices this round: the main scanner priced 0 markets "
                             "(Odds API credits may be used up). Value and Hybrid can't evaluate anything.")
        try:
            trades, complete = kalshi.trades(
                min_ts=int((now - timedelta(minutes=config.HYBRID_SHARP_VALUE.params["flow_lookback_min"])).timestamp()))
            ctx.flows = aggregate(trades, now - timedelta(minutes=config.HYBRID_SHARP_VALUE.params["flow_lookback_min"]))
            ctx.flow_complete = complete
        except PublicDataError:
            ctx.flow_complete = False
            ctx.notes.append("trade tape unavailable; Hybrid can't confirm flow this round")
        try:
            out["pinnacle"] = pinnacle_round(ctx, kalshi, pinn or pinnacle.PinnWire(), base)
        except Exception as exc:  # Pinnacle problems only affect the v2 strategies
            out["pinnacle"] = {"errors": [f"{type(exc).__name__}: {str(exc)[:160]}"]}
        try:
            ctx.events = kalshi.open_events(config.STRUCTURAL_ARB.params["max_events"])
        except PublicDataError:
            ctx.notes.append("open events unavailable; Structural skipped this round")
        # 3. independent strategy decisions
        for spec in config.RESEARCH_STRATEGIES:
            st = stores.get(spec.key)
            if st is None:
                continue
            info = out["strategies"].setdefault(spec.key, {})
            try:
                res = RUNNERS[spec.key](spec, st, ctx)
                st.bump("markets_scanned", res.get("scanned", 0))
                if spec.key == config.STRUCTURAL_ARB.key:
                    out["structural_run"] = res
                info.update({"ok": True, **res})
                st.record_run(now, True, {k: v for k, v in res.items() if isinstance(v, int)})
            except Exception as exc:  # one broken strategy never stops the others
                info.update({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                st.record_run(now, False, {"error": info["error"]})
                traceback.print_exc()
        # 4. reconcile and save
        all_ok = True
        for key, st in stores.items():
            problems = reconcile(st)
            out["strategies"][key]["reconcile_problems"] = problems[:10]
            all_ok = all_ok and not problems
            st.save(now)
        out["reconciled"] = all_ok and len(stores) == len(config.RESEARCH_STRATEGIES)
        out["data_failures"] = list(getattr(kalshi, "failures", []))[:20]
        out["book_errors"] = ctx.book_errors
        out["notes"] = ctx.notes[:20]
        _write_status(base, out, now, stores, send_report)
    return out


def _write_status(base: Path, out: dict, now: datetime, stores: dict, send_report: bool) -> None:
    path = status_path(base)
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text() or "{}")
        except ValueError:
            prev = {}
    today = report.today_key(now)
    day_stats = prev.get("day_stats") if prev.get("day_stats", {}).get("day") == today else {"day": today}
    day_stats["data_failures"] = int(day_stats.get("data_failures", 0)) + len(out.get("data_failures") or [])
    sr = out.get("structural_run") or {}
    day_stats["structural_qualified"] = int(day_stats.get("structural_qualified", 0)) + int(sr.get("qualified", 0))
    day_stats["structural_filled"] = int(day_stats.get("structural_filled", 0)) + int(sr.get("filled", 0))
    status = {**prev, "last_run": out, "day_stats": day_stats}
    ok_all = all(v.get("ok") for v in out["strategies"].values())
    status["last_success" if ok_all else "last_failure"] = out["at"]
    if send_report:
        due = report.due_day(now, prev.get("report_sent"))
        if due:
            summaries = {config.SHARP_EXISTING_LABEL: metrics.sharp_summary()}
            for spec in config.RESEARCH_STRATEGIES:
                if spec.key in stores:
                    summaries[spec.label] = metrics.summarize(stores[spec.key].positions)
            title, lines = report.build(due, summaries, {s.label: stores[s.key] for s in config.RESEARCH_STRATEGIES
                                                         if s.key in stores},
                                        {"structural_today": {"qualified": day_stats.get("structural_qualified", 0),
                                                              "filled": day_stats.get("structural_filled", 0)},
                                         "data_failures_today": day_stats.get("data_failures", 0),
                                         "snapshot": out.get("snapshot"), "reconciled": out.get("reconciled")})
            if report.send(title, lines):
                status["report_sent"] = due
            status["last_report_preview"] = {"title": title, "lines": lines}
    from research_lab.ledger import _atomic_write_text
    _atomic_write_text(path, json.dumps(status, indent=1, sort_keys=True, default=str))


def main() -> int:
    try:
        out = run_round()
    except LedgerError as exc:
        print("RESEARCH_LAB_LOCKED_OR_CORRUPT", str(exc))
        return 1
    brief = {k: {kk: vv for kk, vv in v.items() if kk in {"ok", "scanned", "qualified", "filled", "no_fill", "error"}}
             for k, v in out["strategies"].items()}
    print("RESEARCH_LAB_ROUND", json.dumps({"at": out["at"], "snapshot": out.get("snapshot"), "strategies": brief,
                                            "reconciled": out.get("reconciled")}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
