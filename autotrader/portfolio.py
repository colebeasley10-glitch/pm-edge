"""Isolated deterministic paper portfolio. No network or live-order capability."""
from dataclasses import dataclass, field
from decimal import Decimal
from .config import DEFAULT_CONFIG

D = lambda value: Decimal(str(value))

@dataclass
class Position:
    ticker: str
    side: str
    contracts: int
    entry: Decimal
    entry_fee: Decimal

@dataclass
class PaperPortfolio:
    cash: Decimal = field(default_factory=lambda: DEFAULT_CONFIG.starting_bankroll)
    positions: dict = field(default_factory=dict)
    realized_pnl: Decimal = D("0")
    ledger: list = field(default_factory=list)
    max_position_fraction: Decimal = D("0.02")
    max_total_fraction: Decimal = D("0.10")

    def exposure(self):
        return sum((p.contracts * p.entry for p in self.positions.values()), D("0"))

    def buy(self, ticker, side, contracts, limit, executable_ask, available, fee_per_contract=D("0")):
        """Conservative simulated taker fill at executable ask, not stale limit."""
        price, ask, fee = D(limit), D(executable_ask), D(fee_per_contract)
        if side not in ("YES", "NO") or not ticker or not isinstance(contracts, int) or contracts < 1:
            raise ValueError("Invalid paper order")
        if not (D("0") < ask <= price < D("1")) or fee < 0 or available < contracts:
            return False
        if ticker in self.positions:  # no duplicate or opposing side exposure
            return False
        cost = contracts * (ask + fee)
        if (cost > self.cash or cost > DEFAULT_CONFIG.starting_bankroll * self.max_position_fraction
                or self.exposure() + cost > DEFAULT_CONFIG.starting_bankroll * self.max_total_fraction):
            return False
        self.cash -= cost
        self.positions[ticker] = Position(ticker, side, contracts, ask, fee)
        self.ledger.append({"action": "BUY", "ticker": ticker, "side": side, "contracts": contracts,
                            "price": str(ask), "fee": str(fee * contracts)})
        return True

    def sell(self, ticker, executable_bid, available, fee_per_contract=D("0")):
        """Exit an entire position only when executable bid depth suffices."""
        p = self.positions.get(ticker)
        if p is None:
            return False
        bid, fee = D(executable_bid), D(fee_per_contract)
        if not (D("0") <= bid <= D("1")) or fee < 0 or available < p.contracts:
            return False
        proceeds = p.contracts * (bid - fee)
        pnl = proceeds - p.contracts * (p.entry + p.entry_fee)
        self.cash += proceeds
        self.realized_pnl += pnl
        self.ledger.append({"action": "SELL", "ticker": ticker, "contracts": p.contracts,
                            "price": str(bid), "fee": str(fee * p.contracts), "pnl": str(pnl)})
        del self.positions[ticker]
        return True

    def settle(self, ticker, winning_side):
        p = self.positions.get(ticker)
        if p is None or winning_side not in ("YES", "NO"):
            return False
        proceeds = D(p.contracts) if p.side == winning_side else D("0")
        pnl = proceeds - p.contracts * (p.entry + p.entry_fee)
        self.cash += proceeds
        self.realized_pnl += pnl
        self.ledger.append({"action": "SETTLE", "ticker": ticker, "winner": winning_side, "pnl": str(pnl)})
        del self.positions[ticker]
        return True

    def snapshot(self, bids=None):
        """Unrealized values are bid-marked; absent bids are conservatively zero."""
        bids = bids or {}
        liquidation = sum((p.contracts * max(D("0"), D(bids.get(t, "0")))
                           for t, p in self.positions.items()), D("0"))
        equity = self.cash + liquidation
        return {"cash": str(self.cash), "equity": str(equity),
                "realized_pnl": str(self.realized_pnl),
                "unrealized_pnl": str(liquidation - self.exposure()
                                      - sum((p.contracts * p.entry_fee for p in self.positions.values()), D("0"))),
                "open_positions": len(self.positions)}
