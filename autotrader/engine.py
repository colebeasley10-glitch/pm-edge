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
            qualification_fee = (
                fee_override
                if fee_override is not None
                else prediction_fee(1, c.executable_ask)
            )
            decision = evaluate(
                c,
                fair_probability=probabilities.get(key),
                fee_per_contract=qualification_fee,
                slippage_per_contract=slippage,
                min_net_edge=self.min_net_edge,
                quote_age_seconds=quote_age,
                max_quote_age_seconds=max_quote_age_seconds,
                event_data_age_seconds=event_age,
                max_event_data_age_seconds=max_event_data_age_seconds,
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
            if quote_age is not None:
                entry["quote_age_seconds"] = round(float(quote_age), 3)
            if event_age is not None:
                entry["fair_age_seconds"] = round(float(event_age), 3)
            if not decision.qualified:
                self.decisions.append(entry)
                continue

            simulated_fill = c.executable_ask + slippage
            sizing_fee = (
                fee_override
                if fee_override is not None
                else prediction_fee(1, simulated_fill)
            )
            cost_per_contract = simulated_fill + sizing_fee
            if not (D("0") < simulated_fill < D("1")) or cost_per_contract <= 0:
                entry["decision"] = "invalid_simulated_fill"
                self.decisions.append(entry)
                continue
            budget = self.portfolio.cash
            position_limit = self.portfolio.max_position_fraction * D("500")
            contracts = min(
                int(c.available),
                int(min(budget, position_limit) / cost_per_contract),
            )
            if contracts < 1:
                entry["decision"] = "insufficient_paper_risk_budget"
            else:
                total_fee = (
                    fee_override * contracts
                    if fee_override is not None
                    else prediction_fee(contracts, simulated_fill)
                )
                effective_fee = total_fee / D(contracts)
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
                    entry["fee_total"] = str(total_fee)
                    entry["fee_per_contract_effective"] = str(effective_fee)
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
