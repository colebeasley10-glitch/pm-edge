"""PM Edge v29: only alert when the edge exists at a price you can actually get.

Why: a 2026-10 audit of the candidate ledger found that ~99% of qualified
signals were MAKE orders resting ~7c below the live ask.  At the live ask,
Kalshi sat a median +0.3c from sportsbook consensus fair (1c spread), and
the Pinnacle shadow put qualified signals at about -1.7c.  The "edge" was the
discount we hoped to be filled at - and those fills mostly happen when the
price drops because news went against the position (adverse selection).

v29 adds one final gate on top of every v28 lane (Primary, Standard, Price
Fish):

* Net edge is recomputed at the CURRENT ASK with the taker fee and the usual
  model/slippage buffers.  If it is below PM_EDGE_TAKER_GATE_MIN_EDGE the
  signal is dropped (reason "no_taker_edge").
* A surviving signal is converted to TAKE at the ask, so the alert asks you
  to buy what is actually for sale instead of fishing.
* Gross taker edges above PM_EDGE_TAKER_GATE_IMPLAUSIBLE are treated as
  probable market-matching errors and dropped ("implausible_taker_edge").

Set PM_EDGE_TAKER_GATE=0 to fall back to exact v28 behavior.
"""
from __future__ import annotations

import copy
import os
from types import SimpleNamespace

import candidate_tracker
import pm_edge_runner_v28 as v28
import watcher
from pmedge.pricing import _hard_max, fee_per_contract

TAKER_GATE_ENABLED = str(os.getenv("PM_EDGE_TAKER_GATE", "1")).strip().lower() not in {"0", "false", "no", "off"}
TAKER_MIN_EDGE = float(os.getenv("PM_EDGE_TAKER_GATE_MIN_EDGE", "0.03"))
TAKER_IMPLAUSIBLE = float(os.getenv("PM_EDGE_TAKER_GATE_IMPLAUSIBLE", "0.10"))
TAKER_FEE_RATE = float(os.getenv("PM_EDGE_TAKER_FEE_RATE", "0.07"))

V29_STATS = {
    "taker_gate_checked": 0,
    "taker_gate_passed": 0,
    "taker_gate_no_edge": 0,
    "taker_gate_implausible": 0,
    "taker_gate_no_quote": 0,
}


def _f(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x / 100.0 if x > 1 else x


def side_ask(market: dict, side: str) -> float | None:
    if side == "YES":
        ask = _f(market.get("yes_ask"))
    else:
        ask = _f(market.get("no_ask"))
        if ask is None:
            yes_bid = _f(market.get("yes_bid"))
            ask = None if yes_bid is None else 1.0 - yes_bid
    if ask is None or not (0.0 < ask < 1.0):
        return None
    return ask


def side_fair(fair_entry: dict, q, side: str) -> float | None:
    fair_yes = _f(fair_entry.get("fair_yes_prob"))
    if fair_yes is not None and 0.0 < fair_yes < 1.0:
        return fair_yes if side == "YES" else 1.0 - fair_yes
    fp = _f(getattr(q, "fair_prob", None))
    return fp if fp is not None and 0.0 < fp < 1.0 else None


def taker_net_edge(fair: float, ask: float, *, buffer: float, fee_rate: float = TAKER_FEE_RATE) -> float:
    return fair - ask - buffer - fee_per_contract(ask, fee_rate)


def _with(q, **changes):
    out = copy.copy(q)
    for key, value in changes.items():
        object.__setattr__(out, key, value)
    return out


def apply_taker_gate(market: dict, fair_entry: dict, q, *, buffer: float,
                     min_edge: float = TAKER_MIN_EDGE,
                     implausible: float = TAKER_IMPLAUSIBLE):
    """Return (q_out, reason). reason is '' when q passed or was not qualifying."""
    if not getattr(q, "qualifies", False):
        return q, ""
    V29_STATS["taker_gate_checked"] += 1
    side = str(getattr(q, "side", "") or "").upper()
    ask = side_ask(market, side) if side in {"YES", "NO"} else None
    fair = side_fair(fair_entry, q, side) if side in {"YES", "NO"} else None

    def reject(reason):
        return _with(q, qualifies=False, execution="PASS", recommended_limit=None, reason=reason), reason

    if ask is None or fair is None:
        V29_STATS["taker_gate_no_quote"] += 1
        return reject("no_live_ask")
    gross = fair - ask
    if gross > implausible:
        V29_STATS["taker_gate_implausible"] += 1
        return reject("implausible_taker_edge")
    net = taker_net_edge(fair, ask, buffer=buffer)
    if net + 1e-9 < min_edge:
        V29_STATS["taker_gate_no_edge"] += 1
        return reject("no_taker_edge")

    V29_STATS["taker_gate_passed"] += 1
    hard = max(ask, _hard_max(fair, min_edge, buffer, TAKER_FEE_RATE))
    return _with(
        q,
        execution="TAKE",
        recommended_limit=round(ask, 4),
        hard_max=round(hard, 4),
        net_edge=net,
        best_ask=ask,
        reason="Edge holds at the current ask after taker fee and buffers (v29 taker gate).",
    ), ""


def install_v29_patch() -> None:
    watcher.WATCHER_VERSION = 29
    if not TAKER_GATE_ENABLED:
        return
    buffer = float(watcher.MODEL_BUFFER) + float(watcher.SLIPPAGE_BUFFER)
    original_score_market = watcher.score_market

    def score_market_v29(market, fair_entry, settings, base_min_edge=None):
        q = original_score_market(market, fair_entry, settings, base_min_edge=base_min_edge)
        q_out, reason = apply_taker_gate(market, fair_entry, q, buffer=buffer)
        if reason:
            try:
                candidate_tracker.record_candidate(
                    market, fair_entry, q,
                    lane=v28._lane_name("", q, base_min_edge=base_min_edge),
                    stage="taker_gate", decision="REJECTED", reason=reason,
                    source_version=29,
                )
            except Exception:
                pass
        return q_out

    watcher.score_market = score_market_v29

    original_send_push = watcher.send_push

    def send_push_v29(title, message, *args, **kwargs):
        title = str(title)
        if title.startswith("✅ PM Edge Watch") and "is live" in title:
            title = "✅ PM Edge Watch v29 Taker Gate is live"
            message = (str(message) + "\n"
                       f"v29: alerts only when net edge ≥ {TAKER_MIN_EDGE:.1%} at the CURRENT ask "
                       "(taker fee + buffers). No more fishing below market.")
        return original_send_push(title, message, *args, **kwargs)

    watcher.send_push = send_push_v29


def self_test() -> None:
    buf = 0.0125
    market = {"ticker": "KXTEST-1", "yes_bid": 0.47, "yes_ask": 0.48, "no_bid": 0.52, "no_ask": 0.53}

    # Typical v28 Price Fish: resting 7c below ask on a fairly priced market.
    fish = SimpleNamespace(qualifies=True, side="YES", execution="MAKE",
                           recommended_limit=0.41, hard_max=0.41, net_edge=0.045, best_ask=0.48)
    out, reason = apply_taker_gate(market, {"fair_yes_prob": 0.49}, fish, buffer=buf)
    assert reason == "no_taker_edge" and out.qualifies is False and out.recommended_limit is None
    assert fish.qualifies is True  # original object untouched

    # Real edge at the ask: converted to TAKE at the ask.
    out, reason = apply_taker_gate(market, {"fair_yes_prob": 0.55}, fish, buffer=buf)
    assert reason == "" and out.execution == "TAKE" and abs(out.recommended_limit - 0.48) < 1e-9
    assert out.net_edge >= TAKER_MIN_EDGE and out.hard_max >= 0.48

    # NO side uses the NO ask.
    no_q = SimpleNamespace(qualifies=True, side="NO", execution="MAKE", recommended_limit=0.45, hard_max=0.45, net_edge=0.05)
    out, reason = apply_taker_gate(market, {"fair_yes_prob": 0.40}, no_q, buffer=buf)  # NO fair 0.60 vs ask 0.53
    assert reason == "" and abs(out.recommended_limit - 0.53) < 1e-9

    # Huge gaps are almost always bad matches.
    out, reason = apply_taker_gate(market, {"fair_yes_prob": 0.75}, fish, buffer=buf)
    assert reason == "implausible_taker_edge"

    # Frozen dataclass QuoteDecision is handled too.
    from pmedge.pricing import QuoteDecision
    qd = QuoteDecision("YES", "MAKE", 0.49, 0.47, 0.48, 0.41, 0.41, 0.0, 0.08, 0.045, 0.01, True, "x")
    out, reason = apply_taker_gate(market, {"fair_yes_prob": 0.49}, qd, buffer=buf)
    assert reason == "no_taker_edge" and out.qualifies is False and qd.qualifies is True

    # Non-qualifying signals pass through untouched.
    p = SimpleNamespace(qualifies=False, side="YES")
    assert apply_taker_gate(market, {"fair_yes_prob": 0.9}, p, buffer=buf) == (p, "")
    print("PM Edge v29 taker-gate self-test: PASS")


def main() -> int:
    original_install_v28 = v28.install_v28_patch

    def install_v28_then_v29():
        original_install_v28()
        install_v29_patch()

    v28.install_v28_patch = install_v28_then_v29
    try:
        rc = v28.main()
    finally:
        v28.install_v28_patch = original_install_v28
    print("PM_EDGE_V29_STATS", V29_STATS)
    return rc


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(main())
