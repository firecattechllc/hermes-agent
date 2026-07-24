"""Provider-neutral normalization and narrow adjustment-governance helpers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sigil.portfolio import BrokerageExecution, BrokeragePortfolioSnapshot

from .models import (
    AccountingAdjustmentApproval,
    PortfolioLedgerConflictError,
    PortfolioLedgerEntry,
    PortfolioLedgerEvent,
    PortfolioLedgerEventType,
    canonical_digest,
    decimal_text,
)


class NormalizedBrokerActivityIngestor:
    """Converts supplied observations; it never calls provider endpoints."""

    def execution_event(
        self,
        execution: BrokerageExecution,
        *,
        account_binding: str,
        ledger_identity: str,
        source_provider: str,
        source_response_digest: str,
        acquired_at: datetime,
        accounting_policy_version: str,
        source_complete: bool,
        source_truncated: bool,
        instrument_type: str = "EQUITY",
        currency: str = "USD",
    ) -> PortfolioLedgerEvent:
        quantity = Decimal(execution.filled_quantity)
        price = Decimal(execution.fill_price)
        gross = quantity * price
        fees = Decimal(execution.fees) if execution.fees is not None else Decimal(0)
        taxes = Decimal(0)
        if execution.side == "BUY":
            event_type = PortfolioLedgerEventType.BUY_FILL
            payload = {
                "symbol": execution.symbol,
                "instrument_type": instrument_type,
                "provider_order_id": execution.provider_order_id,
                "client_order_id": execution.client_order_id,
                "provider_execution_id": execution.provider_execution_id,
                "quantity": _text(quantity),
                "fill_price": _text(price),
                "gross_consideration": _text(gross),
                "fees": _text(fees),
                "taxes": "0",
                "net_cash_impact": _text(gross + fees + taxes),
                **_settlement(execution),
            }
        else:
            event_type = PortfolioLedgerEventType.SELL_FILL
            payload = {
                "symbol": execution.symbol,
                "instrument_type": instrument_type,
                "provider_order_id": execution.provider_order_id,
                "client_order_id": execution.client_order_id,
                "provider_execution_id": execution.provider_execution_id,
                "quantity": _text(quantity),
                "fill_price": _text(price),
                "gross_proceeds": _text(gross),
                "fees": _text(fees),
                "taxes": "0",
                "net_proceeds": _text(gross - fees - taxes),
                **_settlement(execution),
            }
        return PortfolioLedgerEvent(
            account_binding,
            ledger_identity,
            event_type,
            canonical_digest(
                {
                    "provider": source_provider,
                    "source_record_id": execution.provider_execution_id,
                    "normalized_payload": payload,
                }
            ),
            source_provider,
            execution.provider_execution_id,
            source_response_digest,
            execution.executed_at,
            execution.executed_at,
            acquired_at,
            currency,
            payload,
            accounting_policy_version,
            source_complete,
            source_truncated,
        )

    def snapshot_valuation_event(
        self,
        snapshot: BrokeragePortfolioSnapshot,
        *,
        ledger_identity: str,
        source_provider: str,
        accounting_policy_version: str,
    ) -> PortfolioLedgerEvent:
        return PortfolioLedgerEvent(
            snapshot.account_binding,
            ledger_identity,
            PortfolioLedgerEventType.VALUATION_OBSERVATION,
            snapshot.snapshot_id,
            source_provider,
            snapshot.snapshot_id,
            canonical_digest(snapshot.canonical_value()),
            snapshot.account.provider_timestamp,
            snapshot.account.provider_timestamp,
            snapshot.acquired_completed_at,
            snapshot.account.currency,
            {"valuation_identity": snapshot.snapshot_id},
            accounting_policy_version,
            snapshot.complete,
            (
                snapshot.positions_truncated
                or snapshot.orders_truncated
                or snapshot.executions_truncated
            ),
        )


class AccountingAdjustmentGovernance:
    """Validates exact single-use injected approvals for one proposal."""

    def approval_event(
        self,
        proposal: PortfolioLedgerEvent,
        approval: AccountingAdjustmentApproval,
        *,
        acquired_at: datetime,
        source_response_digest: str,
        existing_entries: tuple[PortfolioLedgerEntry, ...],
    ) -> PortfolioLedgerEvent:
        if proposal.event_type is not PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_PROPOSED:
            raise PortfolioLedgerConflictError("approval requires an adjustment proposal")
        terms = proposal.payload
        if (
            approval.account_binding != proposal.account_binding
            or approval.proposal_identity != proposal.event_identity
            or approval.reason_code != terms["reason_code"]
            or approval.amount != terms["amount"]
            or approval.affected_fields != tuple(terms["affected_fields"])  # type: ignore[arg-type]
            or approval.evidence_digest != terms["evidence_digest"]
        ):
            raise PortfolioLedgerConflictError("approval does not match exact proposal terms")
        if any(
            item.event.event_type
            is PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED
            and item.event.payload.get("approval_id") == approval.approval_id
            for item in existing_entries
        ):
            raise PortfolioLedgerConflictError("adjustment approval was already consumed")
        return PortfolioLedgerEvent(
            proposal.account_binding,
            proposal.ledger_identity,
            PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED,
            approval.approval_digest,
            "sigil-operator-approval",
            approval.approval_id,
            source_response_digest,
            approval.approved_at,
            approval.approved_at,
            acquired_at,
            proposal.currency,
            {
                "proposal_identity": proposal.event_identity,
                "approval_id": approval.approval_id,
                "approval_digest": approval.approval_digest,
            },
            proposal.accounting_policy_version,
        )

    def adjustment_event(
        self,
        proposal: PortfolioLedgerEvent,
        approval_event: PortfolioLedgerEvent,
        *,
        acquired_at: datetime,
        source_response_digest: str,
    ) -> PortfolioLedgerEvent:
        if approval_event.payload.get("proposal_identity") != proposal.event_identity:
            raise PortfolioLedgerConflictError("adjustment approval binding changed")
        return PortfolioLedgerEvent(
            proposal.account_binding,
            proposal.ledger_identity,
            PortfolioLedgerEventType.CASH_ADJUSTMENT,
            canonical_digest(
                {
                    "proposal": proposal.event_identity,
                    "approval": approval_event.event_identity,
                }
            ),
            "sigil-governed-adjustment",
            f"adjustment-{proposal.event_identity[:24]}",
            source_response_digest,
            approval_event.source_timestamp,
            proposal.effective_at,
            acquired_at,
            proposal.currency,
            {
                "amount": proposal.payload["amount"],
                "reason_code": proposal.payload["reason_code"],
                "proposal_event_identity": proposal.event_identity,
                "approval_id": approval_event.payload["approval_id"],
                "approval_digest": approval_event.payload["approval_digest"],
            },
            proposal.accounting_policy_version,
        )


def _text(value: Decimal) -> str:
    result = decimal_text(value, "normalized amount", nonnegative=False)
    assert result is not None
    return result


def _settlement(execution: BrokerageExecution) -> dict[str, object]:
    settlement = dict(execution.settlement_metadata).get("settlement_date")
    return {} if settlement is None else {"settlement_date": settlement}
