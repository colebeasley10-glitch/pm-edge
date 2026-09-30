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
    exposure_key: str | None = None
    fair_snapshot_id: str | None = None
    thesis: dict | None = None

@dataclass
class PaperPortfolio:
    cash: Decimal = field(default_factory=lambda: DEFAULT_CONFIG.starting_bankroll)
    positions: dict = field(default_factory=dict)
    realized_pnl: Decimal = D("0")
    ledger: list = field(default_factory=list)
    max_position_fraction: Decimal = D("0.02")
    max_total_fraction: Decimal = D("0.10")

    def exposure(self):
        return sum((p.contracts * (p.entry + p.entry_fee) for p in self.positions.values()), D("0"))

    def buy(self, ticker, side, contracts, limit, executable_ask, available,
            fee_per_contract=D("0"), exposure_key=None, fair_snapshot_id=None,
            thesis=None):
        """Conservative simulated taker fill at executable ask, not stale limit."""
        price, ask, fee = D(limit), D(executable_ask), D(fee_per_contract)
        if side not in ("YES", "NO") or not ticker or not isinstance(contracts, int) or contracts < 1:
            raise ValueError("Invalid paper order")
        if not (D("0") < ask <= price < D("1")) or fee < 0 or available < contracts:
            return False
        if ticker in self.positions:  # no duplicate or opposing side exposure
            return False
        exposure_key = str(exposure_key or "").strip() or None
        fair_snapshot_id = str(fair_snapshot_id or "").strip() or None
        if exposure_key and any(p.exposure_key == exposure_key for p in self.positions.values()):
            return False
        if fair_snapshot_id and any(
            str(row.get("fair_snapshot_id") or "") == fair_snapshot_id
            for row in self.ledger
            if row.get("action") == "BUY"
        ):
            return False
        cost = contracts * (ask + fee)
        if (cost > self.cash or cost > DEFAULT_CONFIG.starting_bankroll * self.max_position_fraction
                or self.exposure() + cost > DEFAULT_CONFIG.starting_bankroll * self.max_total_fraction):
            return False
        self.cash -= cost
        thesis = dict(thesis) if isinstance(thesis, dict) else None
        self.positions[ticker] = Position(
            ticker, side, contracts, ask, fee, exposure_key, fair_snapshot_id, thesis
        )
        self.ledger.append({
            "action": "BUY", "ticker": ticker, "side": side, "contracts": contracts,
            "price": str(ask), "fee": str(fee * contracts),
            "exposure_key": exposure_key, "fair_snapshot_id": fair_snapshot_id,
            "thesis": thesis,
        })
        return True

    def sell_partial(self, ticker, contracts, executable_bid, available,
                     fee_per_contract=D("0"), reason=None):
        """Exit part or all of a paper position at executable bid depth."""
        p = self.positions.get(ticker)
        if p is None or not isinstance(contracts, int) or contracts < 1 or contracts > p.contracts:
            return False
        bid, fee = D(executable_bid), D(fee_per_contract)
        if not (D("0") <= bid <= D("1")) or fee < 0 or fee > bid or available < contracts:
            return False
        proceeds = contracts * (bid - fee)
        pnl = proceeds - contracts * (p.entry + p.entry_fee)
        self.cash += proceeds
        self.realized_pnl += pnl
        self.ledger.append({
            "action": "SELL", "ticker": ticker, "side": p.side,
            "contracts": contracts, "price": str(bid),
            "fee": str(fee * contracts), "pnl": str(pnl),
            "reason": str(reason or ""),
            "fair_snapshot_id": p.fair_snapshot_id,
        })
        if contracts == p.contracts:
            del self.positions[ticker]
        else:
            p.contracts -= contracts
        return True

    def sell(self, ticker, executable_bid, available, fee_per_contract=D("0"),
             reason=None):
        """Exit an entire position only when executable bid depth suffices."""
        p = self.positions.get(ticker)
        if p is None:
            return False
        return self.sell_partial(
            ticker, p.contracts, executable_bid, available,
            fee_per_contract=fee_per_contract, reason=reason,
        )

    def settle_value(self, ticker, yes_settlement_value):
        """Settle from Kalshi's finalized YES contract value (normally 0 or 1).

        Non-binary values are supported for rule-defined special settlements.
        There is no settlement fee.
        """
        p = self.positions.get(ticker)
        try:
            yes_value = D(yes_settlement_value)
        except Exception:
            return False
        if p is None or not (D("0") <= yes_value <= D("1")):
            return False
        per_contract = yes_value if p.side == "YES" else D("1") - yes_value
        proceeds = D(p.contracts) * per_contract
        pnl = proceeds - p.contracts * (p.entry + p.entry_fee)
        self.cash += proceeds
        self.realized_pnl += pnl
        self.ledger.append({
            "action": "SETTLE", "ticker": ticker,
            "yes_settlement_value": str(yes_value),
            "payout_per_contract": str(per_contract),
            "pnl": str(pnl),
        })
        del self.positions[ticker]
        return True

    def settle(self, ticker, winning_side):
        if winning_side not in ("YES", "NO"):
            return False
        return self.settle_value(ticker, D("1") if winning_side == "YES" else D("0"))

    def snapshot(self, bids=None):
        """Unrealized values are bid-marked; absent bids are conservatively zero."""
        bids = bids or {}
        liquidation = sum((p.contracts * max(D("0"), D(bids.get(t, "0")))
                           for t, p in self.positions.items()), D("0"))
        equity = self.cash + liquidation
        return {"cash": str(self.cash), "equity": str(equity),
                "realized_pnl": str(self.realized_pnl),
                "unrealized_pnl": str(liquidation - self.exposure()),
                "open_positions": len(self.positions)}
