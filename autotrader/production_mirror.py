"""Pre-registered Production Mirror allocator for paper research.

The allocator never submits orders. Strategy lanes emit intents; this module
ranks them with one fixed risk-adjusted score and applies shared-capital caps so
research-book results can be compared with a realistic $500 mirror portfolio.

v1 score:
    score = p_fill * max(0, edge - k*sigma - execution_cost)
            / max(expected_hold_hours, hold_floor)

All quantities are per dollar/contract fractions. The score is intentionally
simple and frozen before lane results are observed.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

D = lambda x: Decimal(str(x))


@dataclass(frozen=True)
class MirrorIntent:
    intent_id: str
    lane: str
    lane_version: str
    ticker: str
    side: str
    edge: Decimal
    sigma: Decimal
    execution_cost: Decimal
    p_fill: Decimal
    expected_hold_hours: Decimal
    requested_capital: Decimal
    event_id: str | None = None
    cluster_ids: tuple[str, ...] = ()
    fair_snapshot_id: str | None = None


@dataclass(frozen=True)
class MirrorDecision:
    intent_id: str
    accepted: bool
    score: Decimal
    allocated_capital: Decimal
    reason: str


@dataclass(frozen=True)
class MirrorPolicy:
    bankroll: Decimal = D("500")
    total_exposure_cap: Decimal = D("0.10")
    position_cap: Decimal = D("0.02")
    event_cap: Decimal = D("0.04")
    cluster_cap: Decimal = D("0.05")
    fair_snapshot_cap: Decimal = D("0.06")
    sigma_k: Decimal = D("1.0")
    hold_floor_hours: Decimal = D("0.25")
    min_score: Decimal = D("0")


def opportunity_score(intent: MirrorIntent, policy: MirrorPolicy) -> Decimal:
    adjusted = D(intent.edge) - policy.sigma_k * D(intent.sigma) - D(intent.execution_cost)
    if adjusted <= 0:
        return D("0")
    p_fill = min(D("1"), max(D("0"), D(intent.p_fill)))
    hours = max(policy.hold_floor_hours, D(intent.expected_hold_hours))
    return p_fill * adjusted / hours


def allocate(
    intents: Iterable[MirrorIntent],
    *,
    policy: MirrorPolicy = MirrorPolicy(),
    existing_total: Decimal = D("0"),
    existing_by_event: dict[str, Decimal] | None = None,
    existing_by_cluster: dict[str, Decimal] | None = None,
    existing_by_snapshot: dict[str, Decimal] | None = None,
) -> list[MirrorDecision]:
    """Allocate one shared paper bankroll under frozen v1 caps."""
    existing_by_event = dict(existing_by_event or {})
    existing_by_cluster = dict(existing_by_cluster or {})
    existing_by_snapshot = dict(existing_by_snapshot or {})
    total_cap = policy.bankroll * policy.total_exposure_cap
    per_position = policy.bankroll * policy.position_cap
    event_cap = policy.bankroll * policy.event_cap
    cluster_cap = policy.bankroll * policy.cluster_cap
    snapshot_cap = policy.bankroll * policy.fair_snapshot_cap

    scored = sorted(
        ((opportunity_score(x, policy), x) for x in intents),
        key=lambda pair: (pair[0], pair[1].intent_id),
        reverse=True,
    )
    decisions: list[MirrorDecision] = []
    total = D(existing_total)

    for score, intent in scored:
        requested = max(D("0"), D(intent.requested_capital))
        alloc = min(requested, per_position)
        if score <= policy.min_score:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), "nonpositive_risk_adjusted_score"))
            continue
        if alloc <= 0:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), "no_requested_capital"))
            continue
        if total + alloc > total_cap:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), "total_exposure_cap"))
            continue

        event_key = str(intent.event_id or "").strip()
        if event_key and existing_by_event.get(event_key, D("0")) + alloc > event_cap:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), "event_cap"))
            continue

        blocked_cluster = None
        for cluster in tuple(intent.cluster_ids or ()):
            key = str(cluster).strip()
            if key and existing_by_cluster.get(key, D("0")) + alloc > cluster_cap:
                blocked_cluster = key
                break
        if blocked_cluster:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), f"cluster_cap:{blocked_cluster}"))
            continue

        snap = str(intent.fair_snapshot_id or "").strip()
        if snap and existing_by_snapshot.get(snap, D("0")) + alloc > snapshot_cap:
            decisions.append(MirrorDecision(intent.intent_id, False, score, D("0"), "fair_snapshot_cap"))
            continue

        decisions.append(MirrorDecision(intent.intent_id, True, score, alloc, "accepted"))
        total += alloc
        if event_key:
            existing_by_event[event_key] = existing_by_event.get(event_key, D("0")) + alloc
        for cluster in tuple(intent.cluster_ids or ()):
            key = str(cluster).strip()
            if key:
                existing_by_cluster[key] = existing_by_cluster.get(key, D("0")) + alloc
        if snap:
            existing_by_snapshot[snap] = existing_by_snapshot.get(snap, D("0")) + alloc

    return decisions
