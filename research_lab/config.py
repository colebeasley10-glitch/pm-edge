"""Frozen strategy definitions.

Each strategy version is frozen on its start date: thresholds, entry rules, and
bankroll are recorded here and never edited in place. To change a strategy, add a
new version (e.g. VALUE_ONLY v2) with its own ledger; the old version keeps running
or is retired, and its history is never rewritten.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.getenv("RESEARCH_LAB_DATA_DIR", "research_lab/data"))

# Shared paper-portfolio rules (spec section 4). Paper only; no real-money switch exists.
STARTING_BANKROLL = 500.00
TARGET_STAKE = 10.00          # ~$10 per opportunity, whole contracts, fees on top
MAX_EVENT_EXPOSURE = 20.00    # per Kalshi event (game), fees included
DAILY_EXPOSURE_CAP = 50.00    # new money committed per Eastern day, per strategy
TAKER_FEE_RATE = 0.07         # Kalshi general taker fee: ceil(0.07 * C * P * (1-P)) per order


@dataclass(frozen=True)
class StrategySpec:
    id: str
    version: int
    label: str
    started: str              # ISO date the frozen rules took effect
    description: str
    params: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.id}_v{self.version}"


VALUE_ONLY = StrategySpec(
    id="VALUE_ONLY", version=1, label="Value Only", started="2026-10-17",
    description=("Buys a Kalshi contract when sportsbook consensus (power de-vig) says it is worth at "
                 "least 3 points more than the current Kalshi ask, after fees and buffers."),
    params={
        "min_net_edge": 0.03,          # after taker fee + buffers, at the executable ask
        "model_buffer": 0.01,          # model-error buffer subtracted from edge
        "slippage_buffer": 0.0025,
        "max_edge": 0.15,              # larger "edges" are treated as data errors and rejected
        "min_books": 3,                # independent sportsbooks in the consensus
        "max_dispersion": 0.055,       # max disagreement between books
        "max_fair_age_min": 90,        # sportsbook prices older than this are stale
        "max_quote_age_min": 10,       # Kalshi order book must be fetched within this window
        "min_price": 0.10, "max_price": 0.90,
        "max_spread": 0.06,
        "pregame_only": True,          # no positions after kickoff
        "devig": "power",
    },
)

HYBRID_SHARP_VALUE = StrategySpec(
    id="HYBRID_SHARP_VALUE", version=1, label="Hybrid: Value + Sharp", started="2026-10-17",
    description=("Same sportsbook-value test as Value Only, and it also requires recent public Kalshi "
                 "trade flow on the same side. Flow is a filter only; it never raises the fair price."),
    params={
        **VALUE_ONLY.params,
        "flow_lookback_min": 45,
        "flow_min_trades": 5,          # public trade prints on our side in the lookback
        "flow_min_notional": 250.0,    # dollars traded on our side
        "flow_min_share": 0.60,        # our side's share of all notional traded in the market
        "flow_max_age_min": 45,        # latest qualifying print no older than this
    },
)

STRUCTURAL_ARB = StrategySpec(
    id="STRUCTURAL_ARB", version=1, label="Structural Arbitrage", started="2026-10-17",
    description=("Looks for provable price inconsistencies between related Kalshi contracts: "
                 "ladder (nested strike) pairs and baskets of NO on mutually exclusive outcomes. "
                 "Every leg must be fillable at the same snapshot after fees."),
    params={
        "min_locked_profit": 0.02,     # guaranteed $ per unit after all fees
        "max_events": 400,             # open events scanned per run (public API)
        "max_legs": 8,                 # skip NO baskets with more outcomes than this
        "max_orderbook_checks": 40,
        "max_quote_age_min": 10,
    },
)

# v2: same rules as v1, but fair value comes from Pinnacle (PinnWire free feed) instead of
# the main scanner's multi-book consensus, which stops whenever the Odds API runs out.
# One sharp book replaces "3+ books agree": min_books=1, and Pinnacle's own margin must be
# normal (two-way overround <= 1.10). Prices must be from this round (<= 45 min old).
_PINNACLE = {"fair_source": "pinnacle", "min_books": 1, "max_dispersion": 1.0, "max_fair_age_min": 45,
             "max_overround": 1.10}

VALUE_ONLY_V2 = StrategySpec(
    id="VALUE_ONLY", version=2, label="Value Only (Pinnacle)", started="2026-10-17",
    description=("Value Only rules, priced off Pinnacle's de-vigged line (free PinnWire feed): buys when "
                 "Pinnacle says a Kalshi contract is worth 3+ points more than the current ask after fees."),
    params={**VALUE_ONLY.params, **_PINNACLE},
)

HYBRID_SHARP_VALUE_V2 = StrategySpec(
    id="HYBRID_SHARP_VALUE", version=2, label="Hybrid (Pinnacle)", started="2026-10-17",
    description=("Hybrid rules priced off Pinnacle: a 3+ point edge against Pinnacle's fair line and "
                 "recent public Kalshi trade flow on the same side."),
    params={**HYBRID_SHARP_VALUE.params, **_PINNACLE},
)

EV_FINDER = StrategySpec(
    id="EV_FINDER", version=1, label="+EV Finder", started="2026-10-17",
    description=("OddsJam-style: compares Kalshi, Polymarket, DraftKings, Bovada, Fliff, Novig and ProphetX "
                 "moneylines to Pinnacle's fair line and paper-bets the best price when EV is 3%+. "
                 "Alerts you to place sportsbook bets by hand."),
    params={
        "min_ev": 0.03,              # fair probability x decimal odds - 1
        "max_ev": 0.20,              # bigger is almost always stale or mismatched data
        "min_arb": 0.005,            # arbitrage must lock at least 0.5%
        "max_price_age_min": 120,    # venue price no older than this
        "max_overround": 1.10,       # Pinnacle's own margin must be normal
        "polymarket_fee_rate": 0.01, # conservative allowance for Polymarket taker costs
        "markets": "moneyline",
    },
)

RESEARCH_STRATEGIES = [VALUE_ONLY, HYBRID_SHARP_VALUE, STRUCTURAL_ARB, VALUE_ONLY_V2, HYBRID_SHARP_VALUE_V2,
                       EV_FINDER]
BY_KEY = {s.key: s for s in RESEARCH_STRATEGIES}

SHARP_EXISTING_LABEL = "Existing Sharp (paper)"
