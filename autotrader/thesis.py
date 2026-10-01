"""Versioned paper-trading thesis objects.

A thesis records what the bot believed when a position was opened.  It is
deliberately immutable so later exit/replay logic cannot silently rewrite the
original rationale.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any

D = lambda x: Decimal(str(x))


@dataclass(frozen=True)
class PositionThesis:
    thesis_version: str
    lane: str
    lane_version: str
    ticker: str
    side: str
    fair_snapshot_id: str | None
    fair_source: str
    entry_fair: Decimal
    entry_sigma: Decimal
    entry_edge_net: Decimal
    entry_price: Decimal
    entry_fee_per_contract: Decimal
    entry_slippage_per_contract: Decimal
    expected_hold_hours: Decimal
    expected_ev_per_contract: Decimal
    expected_ev_per_capital_hour: Decimal
    event_start_at: str | None
    created_at: str
    exposure_key: str | None = None
    cluster_ids: tuple[str, ...] = ()
    execution_style: str = "taker"
    invalidation_fair_move_sigma: Decimal = D("2.0")
    max_fair_age_seconds: int = 300
    freeze_at_event_start: bool = True

    def __post_init__(self):
        if self.side not in {"YES", "NO"}:
            raise ValueError("side must be YES or NO")
        if not (D("0") < self.entry_fair < D("1")):
            raise ValueError("entry_fair must be between 0 and 1")
        if self.entry_sigma < 0:
            raise ValueError("entry_sigma must be non-negative")
        if not (D("0") < self.entry_price < D("1")):
            raise ValueError("entry_price must be between 0 and 1")
        if self.expected_hold_hours <= 0:
            raise ValueError("expected_hold_hours must be positive")

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key, value in list(out.items()):
            if isinstance(value, Decimal):
                out[key] = str(value)
            elif isinstance(value, tuple):
                out[key] = list(value)
        return out

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "PositionThesis":
        decimal_fields = {
            "entry_fair", "entry_sigma", "entry_edge_net", "entry_price",
            "entry_fee_per_contract", "entry_slippage_per_contract",
            "expected_hold_hours", "expected_ev_per_contract",
            "expected_ev_per_capital_hour", "invalidation_fair_move_sigma",
        }
        # Theses may carry extra provenance/research keys (fair age, recheck
        # telemetry, challenger tags). Only the declared fields define the
        # thesis; ignoring extras keeps exit management working.
        from dataclasses import fields as _fields
        allowed = {f.name for f in _fields(cls)}
        data = {k: v for k, v in dict(row).items() if k in allowed}
        for key in decimal_fields:
            data[key] = D(data[key])
        data["cluster_ids"] = tuple(data.get("cluster_ids") or ())
        return cls(**data)
