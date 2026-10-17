"""Performance metrics with one shared definition for every strategy.

Realized net P/L = payout - cost - fees, on settled positions only.
Net ROI = realized net P/L / money committed (cost + fees) on those settled positions.
Every settled position counts, winners and losers alike. Open positions are reported
separately and never enter ROI.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path

from research_lab import config
from research_lab.ledger import Store, loads, parse_dt

SETTLED_WL = {"won", "lost"}
DONE = {"won", "lost", "void", "closed"}


def f(v, default=None):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _group(rows: list[dict]) -> dict:
    won = sum(r.get("status") == "won" for r in rows)
    lost = sum(r.get("status") == "lost" for r in rows)
    risked = sum(f(r.get("committed"), None) if f(r.get("committed"), None) is not None
                 else (f(r.get("cost"), 0) + f(r.get("fee"), 0)) for r in rows)
    pl = sum(f(r.get("pl"), 0.0) for r in rows)
    return {"bets": won + lost, "won": won, "lost": lost, "pl": round(pl, 2), "risked": round(risked, 2),
            "roi": (pl / risked) if risked else None}


def bucket_price(p: float | None) -> str:
    if p is None:
        return "n/a"
    edges = [0.2, 0.35, 0.5, 0.65, 0.8]
    labels = ["<20¢", "20-34¢", "35-49¢", "50-64¢", "65-79¢", "80¢+"]
    for e, lab in zip(edges, labels):
        if p < e:
            return lab
    return labels[-1]


def bucket_edge(e: float | None) -> str:
    if e is None:
        return "n/a"
    if e < 0.03:
        return "<3pt"
    if e < 0.05:
        return "3-5pt"
    if e < 0.08:
        return "5-8pt"
    return "8pt+"


def summarize(positions: list[dict], *, starting_bankroll: float | None = config.STARTING_BANKROLL,
              candidates: list[dict] | None = None, stats: dict | None = None) -> dict:
    settled = [p for p in positions if p.get("status") in DONE]
    wl = [p for p in settled if p.get("status") in SETTLED_WL]
    open_ = [p for p in positions if p.get("status") == "open"]
    fees = sum(f(p.get("fee"), 0.0) for p in settled)
    net = sum(f(p.get("pl"), 0.0) for p in settled)
    risked = sum(f(p.get("committed"), None) if f(p.get("committed"), None) is not None
                 else f(p.get("cost"), 0.0) + f(p.get("fee"), 0.0) for p in settled)
    # bankroll history by settlement time
    hist, bank, peak, dd = [], starting_bankroll or 0.0, starting_bankroll or 0.0, 0.0
    for p in sorted(settled, key=lambda p: str(p.get("settled_at") or "")):
        bank += f(p.get("pl"), 0.0)
        peak = max(peak, bank)
        dd = max(dd, peak - bank)
        hist.append({"at": p.get("settled_at"), "bankroll": round(bank, 2), "cum_pl": round(bank - (starting_bankroll or 0.0), 2)})
    # calibration (only positions with a model probability)
    cal = defaultdict(lambda: {"n": 0, "pred": 0.0, "won": 0})
    brier_terms = []
    for p in wl:
        fair = f(p.get("fair"))
        if fair is None:
            continue
        b = min(9, int(fair * 10))
        cal[b]["n"] += 1
        cal[b]["pred"] += fair
        cal[b]["won"] += p.get("status") == "won"
        brier_terms.append((fair - (1.0 if p.get("status") == "won" else 0.0)) ** 2)
    calibration = [{"bucket": f"{b * 10}-{b * 10 + 9}%", "n": v["n"], "avg_pred": round(v["pred"] / v["n"], 3),
                    "actual": round(v["won"] / v["n"], 3)} for b, v in sorted(cal.items())]
    clv = [f(p.get("clv_cents")) for p in wl if f(p.get("clv_cents")) is not None]

    def by(keyfn):
        g = defaultdict(list)
        for p in settled:
            g[keyfn(p)].append(p)
        return {k: _group(v) for k, v in sorted(g.items())}

    def flow_b(p):
        d = loads(p.get("details"), {}) or {}
        return d.get("flow_bucket") or "n/a"

    sig = {}
    if candidates is not None or stats is not None:
        c = (stats or {}).get("counters", {})
        sig = {
            "candidates_logged_14d": len(candidates or []),
            "filled_total": int(c.get("decision_filled", 0)) + int(c.get("decision_partial", 0)),
            "no_fill_total": int(c.get("decision_no_fill", 0)),
            "rejected_total": int(c.get("decision_rejected", 0)),
            "scanned_total": int(c.get("markets_scanned", 0)),
            "rejections": dict(sorted(((stats or {}).get("rejections") or {}).items(), key=lambda kv: -kv[1])),
        }
        tries = sig["filled_total"] + sig["no_fill_total"]
        sig["fill_rate"] = (sig["filled_total"] / tries) if tries else None
        recent = [r for r in (candidates or []) if r.get("decision") in {"FILLED", "PARTIAL", "NO_FILL"}]
        edges = [f(r.get("net_edge")) for r in recent if f(r.get("net_edge")) is not None]
        spreads = [f(r.get("spread")) for r in recent if f(r.get("spread")) is not None]
        ages = [f(r.get("quote_age_s")) for r in recent if f(r.get("quote_age_s")) is not None]
        sig["avg_net_edge"] = sum(edges) / len(edges) if edges else None
        sig["avg_spread"] = sum(spreads) / len(spreads) if spreads else None
        sig["avg_quote_age_s"] = sum(ages) / len(ages) if ages else None

    fairs = [f(p.get("fair")) for p in positions if f(p.get("fair")) is not None]
    return {
        "trades": len(positions), "won": sum(p.get("status") == "won" for p in settled),
        "lost": sum(p.get("status") == "lost" for p in settled),
        "voids": sum(p.get("status") in {"void", "closed"} for p in settled),
        "open": len(open_), "open_exposure": round(sum(f(p.get("committed"), 0.0) for p in open_), 2),
        "win_pct": (sum(p.get("status") == "won" for p in wl) / len(wl)) if wl else None,
        "risked": round(risked, 2), "fees": round(fees, 2), "gross_pl": round(net + fees, 2),
        "net_pl": round(net, 2), "roi": (net / risked) if risked else None,
        "avg_pl": (net / len(settled)) if settled else None,
        "bankroll": None if starting_bankroll is None else round(starting_bankroll + net, 2),
        "peak_bankroll": None if starting_bankroll is None else round(peak, 2),
        "max_drawdown": round(dd, 2), "history": hist,
        "avg_fair": (sum(fairs) / len(fairs)) if fairs else None,
        "brier": (sum(brier_terms) / len(brier_terms)) if brier_terms else None,
        "calibration": calibration, "clv_avg": (sum(clv) / len(clv)) if clv else None, "clv_n": len(clv),
        "by_category": by(lambda p: p.get("category") or "n/a"),
        "by_price": by(lambda p: bucket_price(f(p.get("entry_price")))),
        "by_edge": by(lambda p: bucket_edge(f(p.get("net_edge")))),
        "by_flow": by(flow_b),
        "by_week": by(lambda p: (parse_dt(p.get("settled_at")) or parse_dt(p.get("opened_at"))).strftime("%Y-W%V")
                      if (parse_dt(p.get("settled_at")) or parse_dt(p.get("opened_at"))) else "n/a"),
        "signal": sig,
    }


def store_summary(store: Store) -> dict:
    return summarize(store.positions, candidates=store.candidates, stats=store.stats)


# ---- existing Sharp (read-only benchmark) ----

SHARP_LEDGER = Path(os.getenv("LEAGUE_LEDGER_PATH", "league_trades.csv"))


def sharp_positions(path: Path | None = None) -> list[dict]:
    """Existing Sharp paper trades from the league ledger, mapped to lab fields. Read-only."""
    path = path or SHARP_LEDGER
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("strategy") == "sharp"]
    out = []
    for r in rows:
        cost, fee = f(r.get("cost"), 0.0), f(r.get("fee"), 0.0)
        out.append({**r, "position_id": r.get("trade_id"), "committed": round(cost + fee, 2),
                    "status": r.get("status") or "open", "fair": "", "net_edge": "",
                    "details": "{}"})
    return out


def sharp_summary(path: Path | None = None, since: str | None = None) -> dict:
    rows = sharp_positions(path)
    if since:
        rows = [r for r in rows if str(r.get("opened_at") or "") >= since]
    return summarize(rows, starting_bankroll=None)
