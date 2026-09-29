"""Isolated Phase 1 AutoTrader settings: simulation only, no live orders."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaperConfig:
    starting_bankroll: Decimal = Decimal("500.00")
    currency: str = "USD"
    mode: str = "paper"
    sports_only: bool = False
    market_scope: str = "all_eligible"
    live_order_submission_enabled: bool = False

    def __post_init__(self):
        if self.starting_bankroll <= 0:
            raise ValueError("Paper bankroll must be positive")
        if self.mode != "paper" or self.live_order_submission_enabled:
            raise ValueError("Phase 1 is strictly paper-only")


DEFAULT_CONFIG = PaperConfig()
