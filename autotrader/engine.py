"""Paper-only decision engine. No live-order submission capability."""
from dataclasses import dataclass, field
from decimal import Decimal
from .portfolio import PaperPortfolio
from .scanner import discover
from .evaluator import evaluate
from .fees import prediction_fee

D = lambda value: Decimal(str(value))


@dataclass
class PaperEngine:
    portfolio: PaperPortfolio = field(default_factory=PaperPortfolio)
    decisions: list = field(default_factory=list)
    min_net_edge: Decimal = D("0.05")

    def on_snapshot(self, markets, *, probabilities, quote_ages, event_data_ages,
                    contexts=None, fee_per_contract=None, slippage_per_contract="0",
                    max_quote_age_seconds=2, max_event_data_age_seconds=300):
        return self.on_candidates(
            discover(markets), probabilities=probabilities,
            quote_ages=quote_ages, event_data_ages=event_data_ages,
            contexts=contexts,
            fee_per_contract=fee_per_contract,
            slippage_per_contract=slippage_per_contract,
            max_quote_age_seconds=max_quote_age_seconds,
            max_event_data_age_seconds=max_event_data_age_seconds,
        )

    def on_candidates(self, candidates, *, probabilities, quote_ages, event_data_ages,
                      contexts=None, fee_per_contract=None, slippage_per_contract="0",
                      max_quote_age_seconds=2, max_event_data_age_seconds=300):
        """Evaluate executable candidates against independent fair probabilities."""
        candidates = list(candidates)
        contexts = contexts or {}
        accepted = 0
        start_decisions = len(self.decisions)
        fee_override = None if fee_per_contract in (None, "") else D(fee_per_contract)
        slippage = D(slippage_per_contract)

        for c in candidates:
            key = (c.ticker, c.side)
            context = contexts.get(key) or {}
            quote_age = quote_ages.get(key, quote_ages.get(c.ticker))
            event_age = event_data_ages.get(key, event_data_ages.get(c.ticker))
            simulated_fill = c.executable_ask + slippage

            entry = {"ticker": c.ticker, "side": c.side}
            if quote_age is not None:
                entry["quote_age_seconds"] = round(float(quote_age), 3)
            if event_age is not None:
                entry["fair_age_seconds"] = round(float(event_age), 3)

            if not (D("0") < simulated_fill < D("1")):
                entry.update({"decision": "invalid_simulated_fill", "qualified": False})
                self.decisions.append(entry)
                continue

            risk_budget = min(
                self.portfolio.cash,
                self.portfolio.max_position_fraction * D("500"),
            )
            contracts = min(int(c.available), int(risk_budget / simulated_fill))
            total_fee = D("0")
            while contracts > 0:
                total_fee = (
                    fee_override * D(contracts)
                    if fee_override is not None
                    else prediction_fee(contracts, simulated_fill)
                )
                total_cost = D(contracts) * simulated_fill + total_fee
                if total_cost <= risk_budget:
                    break
                contracts -= 1

            if contracts < 1:
                entry.update({"decision": "insufficient_paper_risk_budget", "qualified": False})
                self.decisions.append(entry)
                continue

            # One order-level fee calculation drives BOTH the EV gate and the
            # eventual portfolio debit. This avoids rounding inconsistencies.
            effective_fee = total_fee / D(contracts)
            decision = evaluate(
                c,
                fair_probability=probabilities.get(key),
                fee_per_contract=effective_fee,
                slippage_per_contract=slippage,
                min_net_edge=self.min_net_edge,
                quote_age_seconds=quote_age,
                max_quote_age_seconds=max_quote_age_seconds,
                event_data_age_seconds=event_age,
                max_event_data_age_seconds=max_event_data_age_seconds,
            )
            entry.update({
                "decision": decision.reason,
                "qualified": decision.qualified,
                "contracts_considered": contracts,
                "fee_total": str(total_fee),
                "fee_per_contract_effective": str(effective_fee),
            })
            if decision.net_edge is not None:
                entry["net_edge"] = str(decision.net_edge)
            if decision.fair_probability is not None:
                entry["fair_probability"] = str(decision.fair_probability)
            if not decision.qualified:
                self.decisions.append(entry)
                continue

            exposure_key = str(context.get("event_id") or c.ticker)
            snapshot_id = str(context.get("fair_snapshot_id") or "") or None
            filled = self.portfolio.buy(
                c.ticker, c.side, contracts,
                simulated_fill, simulated_fill,
                int(c.available), effective_fee,
                exposure_key=exposure_key,
                fair_snapshot_id=snapshot_id,
            )
            entry["decision"] = "paper_filled" if filled else "paper_risk_rejected"
            if filled:
                accepted += 1
                entry["contracts"] = contracts
                entry["simulated_fill"] = str(simulated_fill)
                entry["slippage_per_contract"] = str(slippage)
                entry["exposure_key"] = exposure_key
                entry["fair_snapshot_id"] = snapshot_id
            self.decisions.append(entry)

        return {
            "candidates": len(candidates),
            "paper_fills": accepted,
            "decisions": len(self.decisions) - start_decisions,
            "portfolio": self.portfolio.snapshot(),
        }
