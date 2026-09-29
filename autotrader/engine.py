"""Paper-only decision engine. Call with independently timestamped data.

No network clients, API credentials, or live-order submission are imported here.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from .portfolio import PaperPortfolio
from .scanner import discover
from .evaluator import evaluate

D = lambda value: Decimal(str(value))

@dataclass
class PaperEngine:
    portfolio: PaperPortfolio = field(default_factory=PaperPortfolio)
    decisions: list = field(default_factory=list)
    min_net_edge: Decimal = D("0.05")

    def on_snapshot(self, markets, *, probabilities, quote_ages, event_data_ages,
                    fee_per_contract="0", slippage_per_contract="0"):
        """Evaluate one timestamped batch; missing inputs fail closed.

        probabilities keys are (ticker, side), produced by a separate,
        independently validated probability model. A missing model means
        NO trades. This engine never guesses probabilities from market prices.
        """
        candidates = discover(markets)
        accepted = 0
        for c in candidates:
            key = (c.ticker, c.side)
            decision = evaluate(
                c,
                fair_probability=probabilities.get(key),
                fee_per_contract=fee_per_contract,
                slippage_per_contract=slippage_per_contract,
                min_net_edge=self.min_net_edge,
                quote_age_seconds=quote_ages.get(c.ticker),
                event_data_age_seconds=event_data_ages.get(c.ticker),
            )
            entry = {"ticker": c.ticker, "side": c.side,
                     "decision": decision.reason, "qualified": decision.qualified}
            if not decision.qualified:
                self.decisions.append(entry)
                continue
            # Phase 1: only executable ask depth. Size remains limited by
            # PaperPortfolio's $500 account-level risk limits.
            cost_per_contract = c.executable_ask + D(fee_per_contract)
            budget = self.portfolio.cash
            position_limit = self.portfolio.max_position_fraction * D("500")
            contracts = min(int(c.available), int(min(budget, position_limit) / cost_per_contract))
            if contracts < 1:
                entry["decision"] = "insufficient_paper_risk_budget"
            else:
                filled = self.portfolio.buy(
                    c.ticker, c.side, contracts, c.executable_ask,
                    c.executable_ask, int(c.available), D(fee_per_contract))
                entry["decision"] = "paper_filled" if filled else "paper_risk_rejected"
                if filled:
                    accepted += 1
                    entry["contracts"] = contracts
            self.decisions.append(entry)
        return {"candidates": len(candidates), "paper_fills": accepted,
                "decisions": len(self.decisions), "portfolio": self.portfolio.snapshot()}
