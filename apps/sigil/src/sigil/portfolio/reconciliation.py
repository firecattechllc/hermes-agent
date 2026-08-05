"""Read-only broker-to-journal reconciliation."""

from __future__ import annotations

from typing import Protocol

from sigil.execution import (
    ExecutionJournalEvent,
    ExecutionRecoveryClassification,
    ExecutionRecoveryInspection,
)

from .models import (
    BrokerageOrderState,
    BrokeragePortfolioSnapshot,
    PortfolioReconciliationReport,
    PortfolioStateDiscrepancy,
    PortfolioStateDiscrepancyCode as Code,
)


class ReadOnlyExecutionJournal(Protocol):
    def audit(self) -> tuple[ExecutionRecoveryInspection, ...]: ...
    def read_events(self, execution_id: str) -> tuple[ExecutionJournalEvent, ...]: ...


def _last_terms(events: tuple[ExecutionJournalEvent, ...]) -> dict[str, object]:
    for event in events:
        terms = event.payload.get("order_terms")
        if isinstance(terms, dict):
            return terms
    return {}


class PortfolioReconciliationService:
    """Compares immutable observations and never invokes any journal write method."""

    def reconcile(
        self,
        snapshot: BrokeragePortfolioSnapshot,
        journal: ReadOnlyExecutionJournal,
    ) -> PortfolioReconciliationReport:
        discrepancies: list[PortfolioStateDiscrepancy] = []
        inspections = journal.audit()
        broker_by_client = {item.client_order_id: item for item in snapshot.orders}
        represented: set[str] = set()
        for inspection in inspections:
            if inspection.classification is ExecutionRecoveryClassification.CORRUPT:
                discrepancies.append(
                    PortfolioStateDiscrepancy(Code.CORRUPT_JOURNAL, inspection.execution_id)
                )
                continue
            events = journal.read_events(inspection.execution_id)
            terms = _last_terms(events)
            client_id = inspection.client_order_id
            provider_id = inspection.provider_order_id
            order = broker_by_client.get(client_id or "")
            if order is None and provider_id:
                order = next(
                    (item for item in snapshot.orders if item.provider_order_id == provider_id),
                    None,
                )
            if order is None:
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.JOURNAL_ORDER_ABSENT,
                        inspection.execution_id,
                        client_id,
                        provider_id,
                    )
                )
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.RECONCILIATION_REQUIRED,
                        inspection.execution_id,
                        client_id,
                        provider_id,
                    )
                )
                continue
            represented.add(order.provider_order_id)
            discrepancies.append(
                PortfolioStateDiscrepancy(
                    Code.MATCHED,
                    inspection.execution_id,
                    order.client_order_id,
                    order.provider_order_id,
                )
            )
            if inspection.classification is ExecutionRecoveryClassification.RECONCILIATION_REQUIRED:
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.AMBIGUOUS_RESOLVED,
                        inspection.execution_id,
                        order.client_order_id,
                        order.provider_order_id,
                    )
                )
            if inspection.classification is ExecutionRecoveryClassification.CANCELLATION_RECONCILIATION_REQUIRED and order.broker_status == "CANCELLED":
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.CANCELLATION_RESOLVED,
                        inspection.execution_id,
                        order.client_order_id,
                        order.provider_order_id,
                    )
                )
            self._compare(
                discrepancies, inspection.execution_id, order, terms, "account_binding",
                snapshot.account_binding, Code.MISMATCHED_ACCOUNT
            )
            for field, broker_value, code in (
                ("symbol", order.symbol, Code.MISMATCHED_SYMBOL),
                ("side", order.side, Code.MISMATCHED_SIDE),
                ("quantity", order.quantity, Code.MISMATCHED_QUANTITY),
                ("notional_amount", order.notional, Code.MISMATCHED_NOTIONAL),
                ("order_type", order.order_type, Code.MISMATCHED_ORDER_TYPE),
                ("limit_price", order.limit_price, Code.MISMATCHED_LIMIT_PRICE),
            ):
                self._compare(
                    discrepancies, inspection.execution_id, order, terms, field, broker_value, code
                )
            if client_id and client_id != order.client_order_id:
                discrepancies.append(self._mismatch(Code.MISMATCHED_CLIENT_ORDER_ID, inspection.execution_id, order, "client_order_id", client_id, order.client_order_id))
            if provider_id and provider_id != order.provider_order_id:
                discrepancies.append(self._mismatch(Code.MISMATCHED_PROVIDER_ORDER_ID, inspection.execution_id, order, "provider_order_id", provider_id, order.provider_order_id))
            if order.terminal and inspection.classification not in {
                ExecutionRecoveryClassification.COMPLETE,
                ExecutionRecoveryClassification.REJECTED,
            }:
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.BROKER_TERMINAL_NOT_RECORDED,
                        inspection.execution_id,
                        order.client_order_id,
                        order.provider_order_id,
                    )
                )
            if (
                inspection.classification is ExecutionRecoveryClassification.COMPLETE
                and not order.terminal
            ):
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.TERMINAL_STATE_CONFLICT,
                        inspection.execution_id,
                        order.client_order_id,
                        order.provider_order_id,
                    )
                )
        for order in snapshot.orders:
            if order.provider_order_id not in represented:
                discrepancies.append(
                    PortfolioStateDiscrepancy(
                        Code.UNJOURNALED_BROKER_ORDER,
                        "broker-only",
                        order.client_order_id,
                        order.provider_order_id,
                    )
                )
        return PortfolioReconciliationReport(
            account_binding=snapshot.account_binding,
            snapshot_id=snapshot.snapshot_id,
            discrepancies=tuple(discrepancies),
            journal_execution_count=len(inspections),
            broker_order_count=len(snapshot.orders),
        )

    @staticmethod
    def _compare(
        target: list[PortfolioStateDiscrepancy],
        execution_id: str,
        order: BrokerageOrderState,
        terms: dict[str, object],
        field: str,
        broker_value: object,
        code: Code,
    ) -> None:
        journal_value = terms.get(field)
        if journal_value != broker_value and not (journal_value is None and broker_value is None):
            target.append(
                PortfolioReconciliationService._mismatch(
                    code, execution_id, order, field, journal_value, broker_value
                )
            )

    @staticmethod
    def _mismatch(
        code: Code,
        execution_id: str,
        order: BrokerageOrderState,
        field: str,
        journal_value: object,
        broker_value: object,
    ) -> PortfolioStateDiscrepancy:
        return PortfolioStateDiscrepancy(
            code,
            execution_id,
            order.client_order_id,
            order.provider_order_id,
            field,
            None if journal_value is None else str(journal_value),
            None if broker_value is None else str(broker_value),
        )
