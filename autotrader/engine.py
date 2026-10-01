"""Paper-only decision engine. No live-order submission capability."""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from .portfolio import PaperPortfolio
from .scanner import discover
from .evaluator import evaluate
from .fees import prediction_fee
from .thesis import PositionThesis
from .challengers import c1_fields, c3_fields

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

    def _size_and_gate(self, c, *, probability, quote_age, event_age, fee_override,
                       slippage, max_quote_age_seconds, max_event_data_age_seconds):
        """Production sizing + EV gate for one quote. Used for BOTH the initial
        evaluation and the pre-fill fresh-quote recheck, so the recheck can never
        apply a different (looser or tighter) rule than production."""
        simulated_fill = c.executable_ask + slippage
        out = {"simulated_fill": simulated_fill}
        if not (D("0") < simulated_fill < D("1")):
            out["reason"] = "invalid_simulated_fill"
            return out
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
            out["reason"] = "insufficient_paper_risk_budget"
            return out
        # One order-level fee calculation drives BOTH the EV gate and the
        # eventual portfolio debit. This avoids rounding inconsistencies.
        effective_fee = total_fee / D(contracts)
        decision = evaluate(
            c,
            fair_probability=probability,
            fee_per_contract=effective_fee,
            slippage_per_contract=slippage,
            min_net_edge=self.min_net_edge,
            quote_age_seconds=quote_age,
            max_quote_age_seconds=max_quote_age_seconds,
            event_data_age_seconds=event_age,
            max_event_data_age_seconds=max_event_data_age_seconds,
        )
        out.update({
            "reason": decision.reason,
            "decision": decision,
            "contracts": contracts,
            "total_fee": total_fee,
            "effective_fee": effective_fee,
        })
        return out

    def on_candidates(self, candidates, *, probabilities, quote_ages, event_data_ages,
                      contexts=None, fee_per_contract=None, slippage_per_contract="0",
                      max_quote_age_seconds=2, max_event_data_age_seconds=300,
                      requote=None, clock=None):
        """Evaluate executable candidates against independent fair probabilities.

        ``requote(candidate)`` (optional) returns a freshly fetched Candidate for
        the same ticker/side, or None. When supplied, every candidate that passes
        the production gate is re-quoted immediately before its simulated fill
        and must pass the SAME gate again on the fresh quote.
        """
        candidates = list(candidates)
        contexts = contexts or {}
        clock = clock or (lambda: datetime.now(timezone.utc))
        accepted = 0
        start_decisions = len(self.decisions)
        fee_override = None if fee_per_contract in (None, "") else D(fee_per_contract)
        slippage = D(slippage_per_contract)

        for c in candidates:
            key = (c.ticker, c.side)
            context = contexts.get(key) or {}
            quote_age = quote_ages.get(key, quote_ages.get(c.ticker))
            event_age = event_data_ages.get(key, event_data_ages.get(c.ticker))

            entry = {"ticker": c.ticker, "side": c.side}
            if quote_age is not None:
                entry["quote_age_seconds"] = round(float(quote_age), 3)
            if event_age is not None:
                entry["fair_age_seconds"] = round(float(event_age), 3)

            def finish(row):
                # Shadow challengers: logged beside the production decision only.
                row.update(c1_fields(
                    net_edge=row.get("net_edge"),
                    fair_age_s=event_age,
                    source_age_at_fair_s=context.get("source_age_s"),
                ))
                row.update(c3_fields(
                    market_type=context.get("market_type"),
                    side=c.side,
                    fair_provenance_version=context.get("fair_provenance_version"),
                ))
                self.decisions.append(row)

            gate = self._size_and_gate(
                c, probability=probabilities.get(key), quote_age=quote_age,
                event_age=event_age, fee_override=fee_override, slippage=slippage,
                max_quote_age_seconds=max_quote_age_seconds,
                max_event_data_age_seconds=max_event_data_age_seconds,
            )
            if "decision" not in gate:
                entry.update({"decision": gate["reason"], "qualified": False})
                finish(entry)
                continue
            decision = gate["decision"]
            contracts = gate["contracts"]
            total_fee = gate["total_fee"]
            effective_fee = gate["effective_fee"]
            simulated_fill = gate["simulated_fill"]
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
                finish(entry)
                continue

            if requote is not None:
                entry["initial_quote_price"] = str(c.executable_ask)
                entry["initial_quote_age_s"] = None if quote_age is None else round(float(quote_age), 3)
                started = time.monotonic()
                try:
                    fresh = requote(c)
                    recheck_error = None
                except Exception as exc:
                    fresh = None
                    recheck_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                entry["recheck_latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
                if fresh is None:
                    entry.update({
                        "recheck_quote_price": None,
                        "recheck_quote_age_s": None,
                        "recheck_net_edge": None,
                        "recheck_result": "rejected_no_executable_fresh_quote",
                        "decision": "recheck_rejected",
                        "qualified": False,
                    })
                    if recheck_error:
                        entry["recheck_error"] = recheck_error
                    finish(entry)
                    continue
                fresh_age = None
                if fresh.observed_at is not None:
                    fresh_age = (clock().astimezone(timezone.utc) - fresh.observed_at).total_seconds()
                fresh_gate = self._size_and_gate(
                    fresh, probability=probabilities.get(key), quote_age=fresh_age,
                    event_age=event_age, fee_override=fee_override, slippage=slippage,
                    max_quote_age_seconds=max_quote_age_seconds,
                    max_event_data_age_seconds=max_event_data_age_seconds,
                )
                fresh_decision = fresh_gate.get("decision")
                entry.update({
                    "recheck_quote_price": str(fresh.executable_ask),
                    "recheck_quote_age_s": None if fresh_age is None else round(fresh_age, 3),
                    "recheck_net_edge": (
                        str(fresh_decision.net_edge)
                        if fresh_decision is not None and fresh_decision.net_edge is not None
                        else None
                    ),
                })
                if fresh_decision is None or not fresh_decision.qualified:
                    entry.update({
                        "recheck_result": f"rejected:{fresh_gate.get('reason')}",
                        "decision": "recheck_rejected",
                        "qualified": False,
                    })
                    finish(entry)
                    continue
                # The fill uses the FRESH quote's price, size, fee and edge.
                entry["recheck_result"] = "passed"
                c = fresh
                quote_age = fresh_age
                decision = fresh_decision
                contracts = fresh_gate["contracts"]
                total_fee = fresh_gate["total_fee"]
                effective_fee = fresh_gate["effective_fee"]
                simulated_fill = fresh_gate["simulated_fill"]
                entry.update({
                    "contracts_considered": contracts,
                    "fee_total": str(total_fee),
                    "fee_per_contract_effective": str(effective_fee),
                    "net_edge": str(decision.net_edge),
                })

            exposure_key = str(context.get("event_id") or c.ticker)
            snapshot_id = str(context.get("fair_snapshot_id") or "") or None
            c3 = c3_fields(
                market_type=context.get("market_type"), side=c.side,
                fair_provenance_version=context.get("fair_provenance_version"),
            )

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
                lane_version = str(context.get("lane_version") or "value_edge_v1")
                if c3["challenger_c3_lane_version"] == "value_edge_v1_spreadfix":
                    lane_version = "value_edge_v1_spreadfix"
                thesis = PositionThesis(
                    thesis_version="1",
                    lane=str(context.get("lane") or "value_edge"),
                    lane_version=lane_version,
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
                # Research provenance (ignored by PositionThesis.from_dict).
                c1 = c1_fields(
                    net_edge=decision.net_edge, fair_age_s=event_age,
                    source_age_at_fair_s=context.get("source_age_s"),
                )
                thesis.update({
                    "execution": "TAKE",
                    "market_type": str(context.get("market_type") or ""),
                    "sport_key": str(context.get("sport_key") or ""),
                    "fair_age_s": None if event_age is None else round(float(event_age), 3),
                    "source_age_s": c1["challenger_c1_source_age_s"],
                    "quote_age_s": None if quote_age is None else round(float(quote_age), 3),
                    "fair_provider": str(context.get("fair_provider") or ""),
                    "fair_provenance_version": str(context.get("fair_provenance_version") or ""),
                    "recheck_result": entry.get("recheck_result"),
                    "challenger_c1_pass": c1["challenger_c1_pass"],
                    "challenger_c1_reason": c1["challenger_c1_reason"],
                    **c3,
                })
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
            finish(entry)

        return {
            "candidates": len(candidates),
            "paper_fills": accepted,
            "decisions": len(self.decisions) - start_decisions,
            "portfolio": self.portfolio.snapshot(),
        }
