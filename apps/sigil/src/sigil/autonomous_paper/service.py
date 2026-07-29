"""Governed batch-to-order service for autonomous Alpaca paper execution."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from .alpaca import AlpacaPaperClient, AlpacaPaperError, AlpacaPaperTransportError
from .models import (
    ALPACA_PAPER_BASE_URL,
    CandidateResearch,
    ExecutionEnvironmentIdentity,
    PaperExecutionPolicy,
    decimal_value,
)
from .store import PaperExecutionStore, canonical, timestamp

MAX_RECENT_ITEMS = 200
LEVERAGED_INVERSE_TERMS = (
    "2X",
    "3X",
    "ULTRA",
    "ULTRAPRO",
    "INVERSE",
    "BEAR",
    "-2X",
    "-3X",
)
TERMINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})


def _safe_id(value: str) -> str:
    return "".join(character for character in value if character.isalnum())[:32]


def client_order_id(proposal_id: str, attempt: int = 1) -> str:
    material = f"sigil|paper|{proposal_id}|{attempt}"
    suffix = hashlib.sha256(material.encode()).hexdigest()[:16]
    return f"sigil-paper-{_safe_id(proposal_id)[:20]}-{attempt}-{suffix}"[:48]


def _audit(
    state: dict[str, Any],
    event: str,
    *,
    evidence_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    sequence = len(state["audit"]) + 1
    state["audit"].insert(
        0,
        {
            "audit_id": f"SIGIL-V2-AUD-{sequence:08d}",
            "evidence_id": evidence_id,
            "timestamp": timestamp(),
            "event": event,
            "environment": "paper",
            "live_execution": False,
            "broker_submission": bool(state["broker_submission"]),
            "details": details or {},
        },
    )
    del state["audit"][MAX_RECENT_ITEMS:]


def _bounded(items: list[dict[str, Any]]) -> None:
    del items[MAX_RECENT_ITEMS:]


class GovernedPaperExecutionService:
    """Single durable authority for v2.0 paper activation and submissions."""

    def __init__(
        self,
        store: PaperExecutionStore,
        client: AlpacaPaperClient,
        *,
        policy: PaperExecutionPolicy | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.policy = policy or PaperExecutionPolicy()

    def _identity(
        self, *, submission: bool, authenticated: bool, certification: bool = False
    ) -> ExecutionEnvironmentIdentity:
        return ExecutionEnvironmentIdentity(
            application_environment="paper",
            broker_environment="paper",
            broker_base_url=ALPACA_PAPER_BASE_URL,
            credential_environment="paper",
            broker_submission=submission,
            live_execution=False,
            order_mutations=submission,
            certification_mode=certification,
            paper_account_authenticated=authenticated,
        )

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        return self._projection(state)

    def activate(self) -> dict[str, Any]:
        account = self.client.account()
        positions = self._sanitize_positions(self.client.positions())
        orders = [self._sanitize_order(item) for item in self.client.open_orders()]
        with self.store.locked() as state:
            self._identity(submission=True, authenticated=account.get("status") == "ACTIVE")
            state.update(
                {
                    "activated": True,
                    "paused": False,
                    "kill_switch": False,
                    "broker_submission": True,
                    "reconciliation_complete": False,
                    "policy": self.policy.to_dict(),
                    "paper_cash": str(account.get("cash", "0")),
                    "positions": positions,
                    "orders": orders,
                }
            )
            self._reconcile_orders(state)
            state["reconciliation_complete"] = True
            state["last_reconciled_at"] = timestamp()
            _audit(
                state,
                "autonomous_paper_execution_activated",
                evidence_id=f"SIGIL-V2-ACTIVATE-{state['revision']}",
                details={
                    "account_status": account["status"],
                    "maximum_order_notional": "25.00",
                    "maximum_open_positions": 3,
                    "maximum_deployed_capital": "75.00",
                    "long_only": True,
                },
            )
            self.store.save(state)
            return self._projection(state)

    def deactivate(self) -> dict[str, Any]:
        with self.store.locked() as state:
            state.update(
                {
                    "activated": False,
                    "paused": False,
                    "kill_switch": True,
                    "broker_submission": False,
                }
            )
            _audit(
                state,
                "autonomous_paper_execution_deactivated",
                evidence_id=f"SIGIL-V2-DEACTIVATE-{state['revision']}",
            )
            self.store.save(state)
            return self._projection(state)

    def pause(self, *, emergency: bool = False) -> dict[str, Any]:
        with self.store.locked() as state:
            state["paused"] = True
            if emergency:
                state["kill_switch"] = True
                state["broker_submission"] = False
            _audit(
                state,
                "emergency_paper_stop" if emergency else "paper_execution_paused",
                evidence_id=f"SIGIL-V2-PAUSE-{state['revision']}",
            )
            self.store.save(state)
            return self._projection(state)

    def resume(self) -> dict[str, Any]:
        account = self.client.account()
        with self.store.locked() as state:
            if not state["activated"] or state["kill_switch"]:
                raise ValueError("governed activation is required before resume")
            self._identity(submission=True, authenticated=account["status"] == "ACTIVE")
            state.update({"paused": False, "broker_submission": True})
            _audit(
                state,
                "paper_execution_resumed",
                evidence_id=f"SIGIL-V2-RESUME-{state['revision']}",
            )
            self.store.save(state)
            return self._projection(state)

    def reconcile(self) -> dict[str, Any]:
        account = self.client.account()
        with self.store.locked() as state:
            unresolved_count = self._reconcile_orders(state)
            unresolved_count += self._reconcile_exit_orders(state)
            state["paper_cash"] = str(account.get("cash", "0"))
            state["positions"] = self._sanitize_positions(self.client.positions())
            state["orders"] = [self._sanitize_order(item) for item in self.client.open_orders()]
            state["reconciliation_complete"] = True
            state["last_reconciled_at"] = timestamp()
            _audit(
                state,
                "paper_reconciliation_complete",
                evidence_id=f"SIGIL-V2-RECON-{state['revision']}",
                details={"unresolved_intents": unresolved_count},
            )
            self.store.save(state)
            return self._projection(state)

    def evaluate_batch(
        self,
        research: list[CandidateResearch],
        *,
        cursor: int,
        batch_number: int,
        total_eligible: int,
        catalog_fresh: bool,
        portfolio_fresh: bool,
        runtime_healthy: bool,
        audit_available: bool,
        next_cycle_at: str | None = None,
        submit: bool = True,
    ) -> dict[str, Any]:
        """Rank a completed bounded batch and admit at most one paper entry."""
        if len(research) > 25:
            raise ValueError("research batch cannot exceed 25 symbols")
        with self.store.locked() as state:
            progress = state["progress"]
            symbols = [item.symbol for item in research]
            progress.update(
                {
                    "scheduler_state": "researching",
                    "current_cursor": cursor,
                    "current_batch": batch_number,
                    "symbols_in_batch": symbols,
                    "symbols_completed_cycle": cursor,
                    "total_eligible_symbols": total_eligible,
                    "coverage_percent": (
                        round((cursor / total_eligible) * 100, 2) if total_eligible else 0.0
                    ),
                    "last_completed_symbol": symbols[-1] if symbols else None,
                    "last_successful_research_at": timestamp(),
                    "next_cycle_at": next_cycle_at,
                }
            )
            ranked: list[tuple[CandidateResearch, tuple[str, ...]]] = []
            rejection_counter: Counter[str] = Counter()
            for candidate in research:
                reasons = self._candidate_rejections(
                    state,
                    candidate,
                    catalog_fresh=catalog_fresh,
                    portfolio_fresh=portfolio_fresh,
                    runtime_healthy=runtime_healthy,
                    audit_available=audit_available,
                )
                record = {
                    **candidate.to_dict(),
                    "ranking_key": [str(item) for item in candidate.score_key()],
                    "rejection_reasons": list(reasons),
                    "evaluated_at": timestamp(),
                }
                state["candidates"].insert(0, record)
                if reasons:
                    rejection_counter.update(reasons)
                    state["rejections"].insert(
                        0,
                        {
                            "symbol": candidate.symbol,
                            "reasons": list(reasons),
                            "timestamp": timestamp(),
                            "stage": "candidate_admission",
                        },
                    )
                ranked.append((candidate, reasons))
            ranked.sort(key=lambda item: item[0].score_key())
            eligible = [item for item, reasons in ranked if not reasons]
            progress["candidates_produced"] = len(research)
            progress["proposals_rejected"] = sum(bool(reasons) for _, reasons in ranked)
            progress["leading_rejection_reasons"] = dict(rejection_counter.most_common(5))
            _bounded(state["candidates"])
            _bounded(state["rejections"])
            if not eligible:
                progress["state"] = "no_qualified_candidate" if research else "awaiting_fresh_data"
                _audit(
                    state,
                    "research_batch_no_candidate",
                    evidence_id=f"SIGIL-V2-BATCH-{batch_number}-{cursor}",
                    details={
                        "symbols_considered": symbols,
                        "rejection_reasons": dict(rejection_counter),
                    },
                )
                self.store.save(state)
                return self._projection(state)
            winner = eligible[0]
            proposal_id = self._proposal_id(winner, batch_number)
            proposal = {
                "proposal_id": proposal_id,
                "symbol": winner.symbol,
                "side": "buy",
                "order_type": "market",
                "time_in_force": "day",
                "extended_hours": False,
                "maximum_notional": str(self.policy.maximum_order_notional),
                "strategy_score": str(winner.strategy_score),
                "confidence": str(winner.confidence),
                "ranking": 1,
                "status": "eligible",
                "created_at": timestamp(),
                "evidence_id": f"SIGIL-V2-PROPOSAL-{proposal_id}",
            }
            state["proposals"].insert(0, proposal)
            _bounded(state["proposals"])
            progress["proposals_produced"] = 1
            if not submit:
                progress["state"] = "order_admitted"
                self.store.save(state)
                return self._projection(state)
            self._submit_winner(state, winner, proposal)
            self.store.save(state)
            return self._projection(state)

    def record_batch_progress(
        self,
        symbols: list[str],
        *,
        cursor: int,
        batch_number: int,
        total_eligible: int,
        next_cycle_at: str | None,
        rejection_reason: str = "validated_market_research_unavailable",
    ) -> dict[str, Any]:
        """Project research progress when no complete strategy evidence exists."""
        if len(symbols) > 25:
            raise ValueError("research batch cannot exceed 25 symbols")
        with self.store.locked() as state:
            progress = state["progress"]
            progress.update(
                {
                    "scheduler_state": "scanning",
                    "current_cursor": cursor,
                    "current_batch": batch_number,
                    "symbols_in_batch": list(symbols),
                    "symbols_completed_cycle": cursor,
                    "total_eligible_symbols": total_eligible,
                    "coverage_percent": (
                        round((cursor / total_eligible) * 100, 2) if total_eligible else 0.0
                    ),
                    "last_completed_symbol": symbols[-1] if symbols else None,
                    "last_successful_research_at": timestamp(),
                    "candidates_produced": 0,
                    "proposals_produced": 0,
                    "proposals_rejected": 0,
                    "leading_rejection_reasons": {rejection_reason: len(symbols)},
                    "next_cycle_at": next_cycle_at,
                    "state": "awaiting_fresh_data",
                }
            )
            _audit(
                state,
                "research_batch_progress_recorded",
                evidence_id=f"SIGIL-V2-BATCH-{batch_number}-{cursor}",
                details={
                    "symbols_considered": list(symbols),
                    "admission_result": rejection_reason,
                    "broker_submission_attempted": False,
                },
            )
            self.store.save(state)
            return self._projection(state)

    def _submit_winner(
        self,
        state: dict[str, Any],
        candidate: CandidateResearch,
        proposal: dict[str, Any],
    ) -> None:
        if (
            not state["activated"]
            or state["paused"]
            or state["kill_switch"]
            or not state["broker_submission"]
        ):
            state["progress"]["state"] = "execution_disabled"
            proposal["status"] = "rejected"
            proposal["rejection_reasons"] = ["governed_activation_required"]
            return
        self._identity(submission=True, authenticated=True)
        clock = self.client.clock()
        if clock.get("is_open") is not True:
            state["progress"]["state"] = "awaiting_market_hours"
            proposal["status"] = "rejected"
            proposal["rejection_reasons"] = ["market_closed"]
            return
        try:
            notional = self._order_notional(state, candidate)
        except ValueError as error:
            reason = str(error)
            state["progress"]["state"] = "proposal_rejected"
            proposal["status"] = "rejected"
            proposal["rejection_reasons"] = [reason]
            state["rejections"].insert(
                0,
                {
                    "symbol": candidate.symbol,
                    "reasons": [reason],
                    "timestamp": timestamp(),
                    "stage": "order_sizing",
                },
            )
            _bounded(state["rejections"])
            return
        proposal_id = proposal["proposal_id"]
        stable_client_id = client_order_id(proposal_id)
        intent = {
            "intent_id": f"intent-{stable_client_id}",
            "proposal_id": proposal_id,
            "client_order_id": stable_client_id,
            "attempt": 1,
            "symbol": candidate.symbol,
            "side": "buy",
            "notional": str(notional),
            "time_in_force": "day",
            "extended_hours": False,
            "status": "submission_pending",
            "created_at": timestamp(),
            "attempt_history": [],
        }
        state["order_intents"].insert(0, intent)
        _bounded(state["order_intents"])
        state["progress"]["state"] = "order_submitting"
        _audit(
            state,
            "order_intent_persisted",
            evidence_id=intent["intent_id"],
            details={
                "client_order_id": stable_client_id,
                "symbol": candidate.symbol,
                "notional": str(notional),
            },
        )
        # Durably flush the immutable intent before any broker mutation.
        self.store.save(state)
        body = {
            "symbol": candidate.symbol,
            "notional": str(notional),
            "side": intent.get("side", "buy"),
            "type": "market",
            "time_in_force": "day",
            "extended_hours": False,
            "client_order_id": stable_client_id,
        }
        try:
            acknowledgement = self.client.submit_order(body)
        except AlpacaPaperTransportError as error:
            intent["attempt_history"].append(
                {"attempt": 1, "result": error.code, "ambiguous": error.ambiguous}
            )
            if error.ambiguous:
                intent["status"] = "reconciliation_required"
                state["progress"]["state"] = "reconciliation_required"
                existing = self.client.order_by_client_id(stable_client_id)
                if existing is not None:
                    intent["status"] = str(existing.get("status", "submitted"))
                    intent["provider_order_id"] = existing.get("id")
                    self._upsert_order(state, existing, stable_client_id)
            else:
                intent["status"] = "transport_failed_before_submission"
                state["progress"]["state"] = "execution_degraded"
            return
        except AlpacaPaperError as error:
            intent["status"] = "rejected"
            intent["broker_response_classification"] = str(error)
            state["progress"]["state"] = "proposal_rejected"
            return
        intent["status"] = str(acknowledgement.get("status", "submitted"))
        intent["provider_order_id"] = acknowledgement.get("id")
        intent["attempt_history"].append(
            {"attempt": 1, "result": "acknowledged", "ambiguous": False}
        )
        sanitized = self._sanitize_order(acknowledgement)
        state["orders"].insert(0, sanitized)
        _bounded(state["orders"])
        if intent["status"] in {"partially_filled", "filled"}:
            self._upsert_fill(state, acknowledgement, intent)
            if intent["status"] == "filled":
                self._ensure_exit_plan(state, acknowledgement, intent)
        proposal["status"] = "submitted"
        state["progress"]["state"] = (
            "order_filled" if sanitized.get("status") == "filled" else "order_accepted"
        )
        _audit(
            state,
            "paper_order_acknowledged",
            evidence_id=intent["intent_id"],
            details={
                "client_order_id": stable_client_id,
                "provider_order_id": sanitized.get("id"),
                "status": sanitized.get("status"),
            },
        )

    def _candidate_rejections(
        self,
        state: dict[str, Any],
        candidate: CandidateResearch,
        *,
        catalog_fresh: bool,
        portfolio_fresh: bool,
        runtime_healthy: bool,
        audit_available: bool,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        name = candidate.name.upper()
        if candidate.asset_class != "us_equity":
            reasons.append("unsupported_asset")
        if candidate.status != "active":
            reasons.append("inactive")
        if not candidate.tradable:
            reasons.append("not_tradable")
        if not candidate.fractionable:
            reasons.append("not_fractionable")
        if candidate.exchange.upper().startswith("OTC"):
            reasons.append("otc_forbidden")
        if candidate.leveraged_or_inverse or any(term in name for term in LEVERAGED_INVERSE_TERMS):
            reasons.append("leveraged_or_inverse_forbidden")
        if candidate.quote_age_seconds > self.policy.quote_freshness_seconds:
            reasons.append("stale_quote")
        if candidate.bars_age_seconds > self.policy.bars_freshness_seconds:
            reasons.append("stale_bars")
        if candidate.spread_basis_points > self.policy.maximum_spread_basis_points:
            reasons.append("excess_spread")
        if candidate.average_dollar_volume < self.policy.minimum_average_dollar_volume:
            reasons.append("insufficient_liquidity")
        if candidate.confidence < self.policy.minimum_confidence:
            reasons.append("insufficient_confidence")
        if candidate.strategy_score <= 0 or not candidate.expected_setup_positive:
            reasons.append("non_positive_setup")
        if not candidate.evidence_complete:
            reasons.append("incomplete_evidence")
        if candidate.conflicting_evidence:
            reasons.append("conflicting_evidence")
        if not catalog_fresh:
            reasons.append("stale_catalog")
        if not portfolio_fresh:
            reasons.append("stale_portfolio_reconciliation")
        if not runtime_healthy:
            reasons.append("runtime_degraded")
        if not audit_available:
            reasons.append("audit_unavailable")
        open_symbols = {
            item.get("symbol")
            for item in state["positions"] + state["orders"]
            if item.get("status") not in TERMINAL_ORDER_STATUSES
        }
        if candidate.symbol in open_symbols:
            reasons.append("duplicate_symbol")
        if len(state["positions"]) >= self.policy.maximum_open_positions:
            reasons.append("position_limit_reached")
        pending = sum(item.get("status") not in TERMINAL_ORDER_STATUSES for item in state["orders"])
        if pending >= self.policy.maximum_pending_entry_orders:
            reasons.append("pending_order_limit_reached")
        return tuple(sorted(set(reasons)))

    def _order_notional(self, state: dict[str, Any], candidate: CandidateResearch) -> Decimal:
        deployed = sum(
            decimal_value(item.get("market_value", "0"), "market_value")
            for item in state["positions"]
        )
        remaining = self.policy.maximum_deployed_capital - deployed
        cash = decimal_value(state.get("paper_cash", "10000.00"), "paper_cash")
        cash_available = cash - self.policy.minimum_cash_buffer
        notional = min(
            self.policy.maximum_order_notional,
            self.policy.maximum_symbol_exposure,
            remaining,
            cash_available,
        ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if notional <= 0:
            raise ValueError("governed allocation or cash buffer exhausted")
        if notional > Decimal("25.00"):
            raise ValueError("paper order exceeds maximum notional")
        return notional

    @staticmethod
    def _proposal_id(candidate: CandidateResearch, batch_number: int) -> str:
        material = canonical(
            {
                "batch": batch_number,
                "symbol": candidate.symbol,
                "score": str(candidate.strategy_score),
                "confidence": str(candidate.confidence),
            }
        )
        return f"sigil-v2-{hashlib.sha256(material).hexdigest()[:24]}"

    @staticmethod
    def _sanitize_order(order: dict[str, Any], client_id: str | None = None) -> dict[str, Any]:
        return {
            "id": order.get("id"),
            "client_order_id": order.get("client_order_id") or client_id,
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "time_in_force": order.get("time_in_force"),
            "status": order.get("status"),
            "notional": order.get("notional"),
            "qty": order.get("qty"),
            "filled_qty": order.get("filled_qty"),
            "created_at": order.get("created_at"),
            "updated_at": order.get("updated_at"),
        }

    @staticmethod
    def _sanitize_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": item.get("symbol"),
                "qty": item.get("qty"),
                "avg_entry_price": item.get("avg_entry_price"),
                "market_value": item.get("market_value"),
                "unrealized_pl": item.get("unrealized_pl"),
                "side": item.get("side"),
            }
            for item in positions
        ][:MAX_RECENT_ITEMS]

    def _upsert_order(self, state: dict[str, Any], order: dict[str, Any], client_id: str) -> None:
        sanitized = self._sanitize_order(order, client_id)
        state["orders"] = [
            item for item in state["orders"] if item.get("client_order_id") != client_id
        ]
        state["orders"].insert(0, sanitized)
        _bounded(state["orders"])

    def _reconcile_orders(self, state: dict[str, Any]) -> int:
        unresolved = [
            item for item in state["order_intents"] if item["status"] not in TERMINAL_ORDER_STATUSES
        ]
        for intent in unresolved:
            order = self.client.order_by_client_id(intent["client_order_id"])
            if order is None:
                intent["status"] = "proven_absent"
                intent["reconciliation"] = "no_order_exists"
            else:
                intent["status"] = str(order.get("status", "submitted"))
                intent["provider_order_id"] = order.get("id")
                self._upsert_order(state, order, intent["client_order_id"])
                if intent["status"] in {"partially_filled", "filled"}:
                    self._upsert_fill(state, order, intent)
                    if intent["status"] == "filled":
                        self._ensure_exit_plan(state, order, intent)
        return len(unresolved)

    @staticmethod
    def _upsert_fill(state: dict[str, Any], order: dict[str, Any], intent: dict[str, Any]) -> None:
        filled_quantity = order.get("filled_qty")
        if not filled_quantity or decimal_value(filled_quantity, "filled_qty") <= 0:
            return
        fill = {
            "fill_id": f"fill-{intent['client_order_id']}",
            "provider_order_id": order.get("id"),
            "client_order_id": intent["client_order_id"],
            "symbol": intent.get("symbol") or order.get("symbol"),
            "side": intent.get("side", "buy"),
            "filled_qty": str(filled_quantity),
            "filled_avg_price": order.get("filled_avg_price"),
            "status": order.get("status"),
            "entry_basis": order.get("filled_avg_price"),
            "updated_at": order.get("updated_at") or timestamp(),
        }
        state["fills"] = [item for item in state["fills"] if item.get("fill_id") != fill["fill_id"]]
        state["fills"].insert(0, fill)
        _bounded(state["fills"])

    def _ensure_exit_plan(
        self, state: dict[str, Any], order: dict[str, Any], intent: dict[str, Any]
    ) -> None:
        symbol = intent.get("symbol") or order.get("symbol")
        if any(item.get("symbol") == symbol for item in state["exit_plans"]):
            return
        basis = decimal_value(order.get("filled_avg_price"), "filled_avg_price")
        filled_at = order.get("filled_at") or order.get("updated_at") or timestamp()
        state["exit_plans"].insert(
            0,
            {
                "exit_plan_id": f"exit-plan-{intent['client_order_id']}",
                "symbol": symbol,
                "entry_client_order_id": intent["client_order_id"],
                "entry_basis": str(basis),
                "entry_quantity": str(order.get("filled_qty")),
                "entered_at": filled_at,
                "stop_price": str(
                    (
                        basis * (Decimal(1) - self.policy.exit_stop_loss_percent / Decimal(100))
                    ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
                ),
                "profit_price": str(
                    (
                        basis * (Decimal(1) + self.policy.exit_take_profit_percent / Decimal(100))
                    ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
                ),
                "maximum_holding_days": 10,
                "status": "paper_position_monitoring",
            },
        )
        _bounded(state["exit_plans"])

    def monitor_positions(
        self,
        prices: dict[str, Decimal],
        *,
        now: datetime,
        invalidated_symbols: frozenset[str] = frozenset(),
        emergency: bool = False,
    ) -> dict[str, Any]:
        """Evaluate and submit exactly-once exits for Sigil-owned paper positions."""
        preflight = self.store.load()
        if (
            not preflight["activated"]
            or not preflight["broker_submission"]
            or (preflight["paused"] and not emergency)
        ):
            return self._projection(preflight)
        clock = self.client.clock()
        with self.store.locked() as state:
            if state["live_execution"] is not False:
                raise ValueError("live execution is permanently disabled")
            if state["paused"] and not emergency:
                return self._projection(state)
            if not state["activated"] or not state["broker_submission"]:
                return self._projection(state)
            if clock.get("is_open") is not True:
                return self._projection(state)
            positions = {item.get("symbol"): item for item in state["positions"]}
            for plan in state["exit_plans"]:
                if plan.get("status") != "paper_position_monitoring":
                    continue
                symbol = plan["symbol"]
                position = positions.get(symbol)
                current = prices.get(symbol)
                if position is None or current is None:
                    continue
                if any(
                    item.get("symbol") == symbol
                    and item.get("status") not in TERMINAL_ORDER_STATUSES
                    for item in state["exit_intents"]
                ):
                    continue
                entered = datetime.fromisoformat(str(plan["entered_at"]))
                trigger = None
                if current <= decimal_value(plan["stop_price"], "stop_price"):
                    trigger = "protective_stop"
                elif current >= decimal_value(plan["profit_price"], "profit_price"):
                    trigger = "profit_taking"
                elif now.astimezone(UTC) - entered >= timedelta(
                    days=int(plan["maximum_holding_days"])
                ):
                    trigger = "maximum_holding_period"
                elif symbol in invalidated_symbols:
                    trigger = "strategy_invalidation"
                elif emergency:
                    trigger = "emergency_exit"
                if trigger is None:
                    continue
                exit_client_id = client_order_id(f"exit-{plan['exit_plan_id']}-{trigger}")
                intent = {
                    "intent_id": f"exit-intent-{exit_client_id}",
                    "client_order_id": exit_client_id,
                    "symbol": symbol,
                    "qty": str(position.get("qty")),
                    "side": "sell",
                    "trigger": trigger,
                    "status": "submission_pending",
                    "created_at": timestamp(),
                    "attempt_history": [],
                }
                state["exit_intents"].insert(0, intent)
                plan["status"] = "paper_exit_submitting"
                self.store.save(state)
                body = {
                    "symbol": symbol,
                    "qty": intent["qty"],
                    "side": "sell",
                    "type": "market",
                    "time_in_force": "day",
                    "extended_hours": False,
                    "client_order_id": exit_client_id,
                }
                try:
                    acknowledgement = self.client.submit_exit_order(body)
                except AlpacaPaperTransportError as error:
                    intent["status"] = "reconciliation_required"
                    intent["attempt_history"].append(
                        {
                            "attempt": 1,
                            "result": error.code,
                            "ambiguous": error.ambiguous,
                        }
                    )
                    if error.ambiguous:
                        existing = self.client.order_by_client_id(exit_client_id)
                        if existing is not None:
                            intent["status"] = str(existing.get("status", "submitted"))
                            intent["provider_order_id"] = existing.get("id")
                    continue
                except AlpacaPaperError as error:
                    intent["status"] = "rejected"
                    intent["broker_response_classification"] = str(error)
                    plan["status"] = "paper_exit_rejected"
                    continue
                intent["status"] = str(acknowledgement.get("status", "submitted"))
                intent["provider_order_id"] = acknowledgement.get("id")
                plan["status"] = "paper_exit_admitted"
                _audit(
                    state,
                    "paper_exit_acknowledged",
                    evidence_id=intent["intent_id"],
                    details={"symbol": symbol, "trigger": trigger},
                )
            _bounded(state["exit_intents"])
            self.store.save(state)
            return self._projection(state)

    def _reconcile_exit_orders(self, state: dict[str, Any]) -> int:
        unresolved = [
            item
            for item in state["exit_intents"]
            if item.get("status") not in TERMINAL_ORDER_STATUSES
        ]
        for intent in unresolved:
            order = self.client.order_by_client_id(intent["client_order_id"])
            if order is None:
                intent["status"] = "proven_absent"
                continue
            intent["status"] = str(order.get("status", "submitted"))
            intent["provider_order_id"] = order.get("id")
            if intent["status"] in {"partially_filled", "filled"}:
                self._upsert_fill(state, order, intent)
            for plan in state["exit_plans"]:
                if plan.get("symbol") == intent.get("symbol"):
                    plan["status"] = (
                        "closed"
                        if intent["status"] == "filled"
                        else "paper_exit_partial"
                        if intent["status"] == "partially_filled"
                        else f"paper_exit_{intent['status']}"
                    )
        return len(unresolved)

    def _projection(self, state: dict[str, Any]) -> dict[str, Any]:
        def last(name: str) -> dict[str, Any] | None:
            return state[name][0] if state[name] else None

        managed_symbols = {
            item.get("symbol") for item in state["fills"] if item.get("side") == "buy"
        }
        unmanaged_symbols = sorted(
            str(item.get("symbol"))
            for item in state["positions"]
            if item.get("symbol") not in managed_symbols
        )
        degraded_conditions = []
        if not state["reconciliation_complete"] and state["order_intents"]:
            degraded_conditions.append("reconciliation_required")
        if unmanaged_symbols:
            degraded_conditions.append("unmanaged_paper_position")
        return {
            "environment": "paper",
            "live_execution": False,
            "broker": "alpaca_paper",
            "broker_base_url": ALPACA_PAPER_BASE_URL,
            "broker_submission": bool(state["broker_submission"]),
            "activated": bool(state["activated"]),
            "paused": bool(state["paused"]),
            "kill_switch": bool(state["kill_switch"]),
            "revision": state["revision"],
            "evidence_identity": (state["audit"][0]["evidence_id"] if state["audit"] else None),
            "audit_identity": (state["audit"][0]["audit_id"] if state["audit"] else None),
            "degraded_conditions": degraded_conditions,
            "unmanaged_position_symbols": unmanaged_symbols,
            "policy": state["policy"] or self.policy.to_dict(),
            "progress": state["progress"],
            "open_positions": len(state["positions"]),
            "open_orders": sum(
                item.get("status") not in TERMINAL_ORDER_STATUSES for item in state["orders"]
            ),
            "deployed_paper_capital": str(
                sum(
                    decimal_value(item.get("market_value", "0"), "market_value")
                    for item in state["positions"]
                )
            ),
            "remaining_governed_allocation": str(
                max(
                    Decimal(0),
                    self.policy.maximum_deployed_capital
                    - sum(
                        decimal_value(item.get("market_value", "0"), "market_value")
                        for item in state["positions"]
                    ),
                )
            ),
            "last_order_intent": last("order_intents"),
            "last_submitted_order": last("orders"),
            "last_fill": last("fills"),
            "last_rejection": last("rejections"),
            "last_reconciliation": state["last_reconciled_at"],
            "exit_plan_count": len(state["exit_plans"]),
            "pending_exit_count": sum(
                item.get("status") not in TERMINAL_ORDER_STATUSES for item in state["exit_intents"]
            ),
            "last_exit_intent": last("exit_intents"),
        }

    def recent(self, kind: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        mapping = {
            "candidates": "candidates",
            "proposals": "proposals",
            "rejections": "rejections",
            "orders": "orders",
            "positions": "positions",
            "fills": "fills",
            "intents": "order_intents",
            "audit": "audit",
            "exit_plans": "exit_plans",
            "exit_intents": "exit_intents",
        }
        key = mapping.get(kind)
        if key is None:
            raise ValueError("unsupported paper execution collection")
        state = self.store.load()
        bounded_offset = max(0, int(offset))
        bounded_limit = min(100, max(1, int(limit)))
        items = state[key]
        return {
            "environment": "paper",
            "live_execution": False,
            "broker_submission": bool(state["broker_submission"]),
            "revision": state["revision"],
            "evidence_identity": (state["audit"][0]["evidence_id"] if state["audit"] else None),
            "audit_identity": (state["audit"][0]["audit_id"] if state["audit"] else None),
            "degraded_conditions": [],
            "offset": bounded_offset,
            "limit": bounded_limit,
            "total": len(items),
            "has_more": bounded_offset + bounded_limit < len(items),
            "items": items[bounded_offset : bounded_offset + bounded_limit],
        }
