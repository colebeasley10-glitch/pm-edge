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
        """Evaluate a raw normalized market batch."""
        return self.on_candidates(
            discover(markets),
            probabilities=probabilities,
            quote_ages=quote_ages,
            event_data_ages=event_data_ages,
            fee_per_contract=fee_per_contract,
            slippage_per_contract=slippage_per_contract,
        )

    def on_candidates(self, candidates, *, probabilities, quote_ages, event_data_ages,
                      fee_per_contract="0", slippage_per_contract="0"):
        """Evaluate executable candidates produced by the public orderbook scanner.

        probabilities keys are (ticker, side), produced by an independent
        source. Missing or stale fair value means NO trade.
        """
        candidates = list(candidates)
        accepted = 0
        start_decisions = len(self.decisions)
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
            entry = {
                "ticker": c.ticker,
                "side": c.side,
                "decision": decision.reason,
                "qualified": decision.qualified,
            }
            if decision.net_edge is not None:
                entry["net_edge"] = str(decision.net_edge)
            if decision.fair_probability is not None:
                entry["fair_probability"] = str(decision.fair_probability)
            if not decision.qualified:
                self.decisions.append(entry)
                continue

            cost_per_contract = c.executable_ask + D(fee_per_contract)
            budget = self.portfolio.cash
            position_limit = self.portfolio.max_position_fraction * D("500")
            contracts = min(
                int(c.available),
                int(min(budget, position_limit) / cost_per_contract),
            )
            if contracts < 1:
                entry["decision"] = "insufficient_paper_risk_budget"
            else:
                filled = self.portfolio.buy(
                    c.ticker,
                    c.side,
                    contracts,
                    c.executable_ask,
                    c.executable_ask,
                    int(c.available),
                    D(fee_per_contract),
                )
                entry["decision"] = "paper_filled" if filled else "paper_risk_rejected"
                if filled:
                    accepted += 1
                    entry["contracts"] = contracts
            self.decisions.append(entry)

        return {
            "candidates": len(candidates),
            "paper_fills": accepted,
            "decisions": len(self.decisions) - start_decisions,
            "portfolio": self.portfolio.snapshot(),
        }
