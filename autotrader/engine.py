"""Paper-only decision engine. No live-order submission capability."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from .portfolio import PaperPortfolio
from .scanner import discover
from .evaluator import evaluate
from .fees import prediction_fee
from .thesis import PositionThesis

D = lambda value: Decimal(str(value))


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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

            thesis = None
            try:
                decision_at = _parse_iso(context.get("decision_at")) or datetime.now(timezone.utc)
                event_start = _parse_iso(context.get("commence_time"))
                if event_start is not None:
                    hold_hours = max(
                        D("0.25"),
                        min(
                            D("168"),
                            D(str(max(0.0, (event_start - decision_at).total_seconds() / 3600.0))),
                        ),
                    )
                else:
                    hold_hours = D("24")
                sigma_raw = context.get("sigma")
                try:
                    sigma = D(sigma_raw) if sigma_raw not in (None, "") else D("0.03")
                except Exception:
                    sigma = D("0.03")
                side_fair = D(decision.fair_probability)
                yes_fair = side_fair if c.side == "YES" else D("1") - side_fair
                net_edge = D(decision.net_edge or "0")
                capital_per_contract = simulated_fill + effective_fee
                ev_per_capital_hour = (
                    net_edge / capital_per_contract / hold_hours
                    if capital_per_contract > 0 and hold_hours > 0
                    else D("0")
                )
                thesis = PositionThesis(
                    thesis_version="1",
                    lane=str(context.get("lane") or "value_edge"),
                    lane_version=str(context.get("lane_version") or "value_edge_v1"),
                    ticker=c.ticker,
                    side=c.side,
                    fair_snapshot_id=snapshot_id,
                    fair_source=str(context.get("fair_source") or "independent_fair"),
                    entry_fair=yes_fair,
                    entry_sigma=max(D("0"), sigma),
                    entry_edge_net=net_edge,
                    entry_price=simulated_fill,
                    entry_fee_per_contract=effective_fee,
                    entry_slippage_per_contract=slippage,
                    expected_hold_hours=hold_hours,
                    expected_ev_per_contract=net_edge,
                    expected_ev_per_capital_hour=ev_per_capital_hour,
                    event_start_at=(
                        event_start.isoformat().replace("+00:00", "Z")
                        if event_start is not None else None
                    ),
                    created_at=decision_at.isoformat().replace("+00:00", "Z"),
                    exposure_key=exposure_key,
                    cluster_ids=(f"event:{exposure_key}",),
                    execution_style="taker",
                    max_fair_age_seconds=int(max_event_data_age_seconds),
                ).to_dict()
            except Exception as exc:
                entry["thesis_build_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"

            filled = self.portfolio.buy(
                c.ticker, c.side, contracts,
                simulated_fill, simulated_fill,
                int(c.available), effective_fee,
                exposure_key=exposure_key,
                fair_snapshot_id=snapshot_id,
                thesis=thesis,
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
