"""Read-only comparison of derived ledger state with Step 11 broker truth."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sigil.portfolio import BrokeragePortfolioSnapshot

from .models import (
    LedgerDiscrepancyCode as Code,
    PortfolioAccountingState,
    PortfolioLedgerDiscrepancy,
    PortfolioLedgerReconciliationReport,
    PortfolioValuation,
)


def _different(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left != right
    return Decimal(left) != Decimal(right)


class PortfolioLedgerReconciliationService:
    """Produces deterministic discrepancies and has no mutation capability."""

    def reconcile(
        self,
        state: PortfolioAccountingState,
        snapshot: BrokeragePortfolioSnapshot,
        *,
        created_at: datetime,
        maximum_snapshot_age: timedelta,
        valuation: PortfolioValuation | None = None,
    ) -> PortfolioLedgerReconciliationReport:
        discrepancies: list[PortfolioLedgerDiscrepancy] = []
        if state.account_binding != snapshot.account_binding:
            discrepancies.append(
                PortfolioLedgerDiscrepancy(
                    Code.ACCOUNT_MISMATCH,
                    "account",
                    state.account_binding,
                    snapshot.account_binding,
                )
            )
        if snapshot.account.currency != "USD":
            discrepancies.append(
                PortfolioLedgerDiscrepancy(
                    Code.CURRENCY_MISMATCH, "account", "USD", snapshot.account.currency
                )
            )
        self._compare(
            discrepancies,
            Code.DERIVED_CASH_MISMATCH,
            "cash",
            state.current_cash,
            snapshot.account.cash_balance,
        )
        if state.settled_cash is not None and snapshot.account.settled_cash is not None:
            self._compare(
                discrepancies,
                Code.SETTLED_CASH_MISMATCH,
                "settled_cash",
                state.settled_cash,
                snapshot.account.settled_cash,
            )
        ledger_positions = dict(state.position_quantities)
        broker_positions = {item.symbol: item for item in snapshot.positions}
        for symbol in sorted(set(ledger_positions) | set(broker_positions)):
            if symbol not in ledger_positions:
                discrepancies.append(
                    PortfolioLedgerDiscrepancy(
                        Code.MISSING_LEDGER_POSITION,
                        symbol,
                        None,
                        broker_positions[symbol].quantity,
                    )
                )
            elif symbol not in broker_positions:
                discrepancies.append(
                    PortfolioLedgerDiscrepancy(
                        Code.MISSING_BROKER_POSITION,
                        symbol,
                        ledger_positions[symbol],
                        None,
                    )
                )
            else:
                self._compare(
                    discrepancies,
                    Code.POSITION_QUANTITY_MISMATCH,
                    symbol,
                    ledger_positions[symbol],
                    broker_positions[symbol].quantity,
                )
                ledger_basis = dict(state.cost_basis_by_symbol).get(symbol)
                broker_basis = str(
                    Decimal(broker_positions[symbol].average_cost)
                    * Decimal(broker_positions[symbol].quantity)
                )
                self._compare(
                    discrepancies,
                    Code.COST_BASIS_MISMATCH,
                    symbol,
                    ledger_basis,
                    broker_basis,
                    material=False,
                )
        ledger_execution_ids = {
            str(record.sale_event_identity) for record in state.realized_records
        }
        broker_execution_ids = [item.provider_execution_id for item in snapshot.executions]
        if len(set(broker_execution_ids)) != len(broker_execution_ids):
            discrepancies.append(
                PortfolioLedgerDiscrepancy(
                    Code.DUPLICATE_BROKER_EXECUTION, "executions"
                )
            )
        # Provider execution IDs and event hashes are different namespaces. Absence is
        # reported only when a source ID is not represented by caller-supplied history.
        if broker_execution_ids and not state.last_processed_sequence:
            for execution_id in sorted(set(broker_execution_ids)):
                discrepancies.append(
                    PortfolioLedgerDiscrepancy(
                        Code.BROKER_EXECUTION_ABSENT, execution_id, None, execution_id
                    )
                )
        if ledger_execution_ids and not snapshot.executions_complete:
            discrepancies.append(
                PortfolioLedgerDiscrepancy(Code.LEDGER_EXECUTION_ABSENT, "execution_history")
            )
        if not snapshot.complete:
            discrepancies.append(
                PortfolioLedgerDiscrepancy(Code.PARTIAL_SNAPSHOT, snapshot.snapshot_id)
            )
        if (
            snapshot.positions_truncated
            or snapshot.orders_truncated
            or snapshot.executions_truncated
        ):
            discrepancies.append(
                PortfolioLedgerDiscrepancy(Code.TRUNCATED_SNAPSHOT, snapshot.snapshot_id)
            )
        if created_at - snapshot.acquired_completed_at > maximum_snapshot_age:
            discrepancies.append(
                PortfolioLedgerDiscrepancy(Code.STALE_SNAPSHOT, snapshot.snapshot_id)
            )
        if not state.history_complete:
            discrepancies.append(
                PortfolioLedgerDiscrepancy(
                    Code.INCOMPLETE_LEDGER_HISTORY, state.state_digest
                )
            )
        if valuation is not None and valuation.total_equity is not None:
            self._compare(
                discrepancies,
                Code.UNEXPLAINED_EQUITY_DIFFERENCE,
                "equity",
                valuation.total_equity,
                snapshot.account.equity,
            )
        ordered = tuple(
            sorted(
                discrepancies,
                key=lambda item: (
                    item.code.value,
                    item.subject,
                    item.ledger_value or "",
                    item.broker_value or "",
                ),
            )
        )
        return PortfolioLedgerReconciliationReport(
            state.account_binding,
            snapshot.snapshot_id,
            state.state_digest,
            ordered,
            created_at,
        )

    @staticmethod
    def _compare(
        target: list[PortfolioLedgerDiscrepancy],
        code: Code,
        subject: str,
        ledger_value: str | None,
        broker_value: str | None,
        *,
        material: bool = True,
    ) -> None:
        if _different(ledger_value, broker_value):
            target.append(
                PortfolioLedgerDiscrepancy(
                    code, subject, ledger_value, broker_value, material
                )
            )
