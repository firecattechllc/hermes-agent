"""Deterministic cash, lot, realized-gain, and valuation replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .models import (
    CompletenessStatus,
    HoldingPeriod,
    PortfolioAccountingPolicy,
    PortfolioAccountingState,
    PortfolioAccountingUnavailable,
    PortfolioLedgerEntry,
    PortfolioLedgerEventType as Type,
    PortfolioPositionLot,
    PortfolioValuation,
    PositionValuation,
    RealizedGainLossRecord,
    UnrealizedGainLossRecord,
    canonical_digest,
    decimal_text,
)


ZERO = Decimal(0)


@dataclass(slots=True)
class _MutableLot:
    identity: str
    account_binding: str
    symbol: str
    instrument_type: str
    acquisition_event_identity: str
    acquired_at: datetime
    original_quantity: Decimal
    remaining_quantity: Decimal
    original_basis: Decimal
    remaining_basis: Decimal
    acquisition_fees: Decimal
    currency: str

    def immutable(self) -> PortfolioPositionLot:
        unit_cost = self.original_basis / self.original_quantity
        return PortfolioPositionLot(
            lot_identity=self.identity,
            account_binding=self.account_binding,
            symbol=self.symbol,
            instrument_type=self.instrument_type,
            acquisition_event_identity=self.acquisition_event_identity,
            acquisition_timestamp=self.acquired_at,
            original_quantity=_text(self.original_quantity),
            remaining_quantity=_text(self.remaining_quantity),
            unit_cost=_text(unit_cost),
            allocated_acquisition_fees=_text(self.acquisition_fees),
            total_original_basis=_text(self.original_basis),
            remaining_basis=_text(self.remaining_basis),
            currency=self.currency,
        )


def _text(value: Decimal) -> str:
    result = decimal_text(value, "derived value", nonnegative=False)
    assert result is not None
    return result


class PortfolioAccountingEngine:
    """Pure replay engine. It cannot call a broker or mutate ledger history."""

    def replay(
        self,
        entries: tuple[PortfolioLedgerEntry, ...],
        policy: PortfolioAccountingPolicy,
    ) -> PortfolioAccountingState:
        if entries and any(
            entry.event.accounting_policy_version != policy.version for entry in entries
        ):
            raise PortfolioAccountingUnavailable("ledger policy version mismatch")
        account_binding = entries[0].account_binding if entries else ""
        cash = ZERO
        opening_cash = ZERO
        contributions = ZERO
        withdrawals = ZERO
        dividends = ZERO
        interest = ZERO
        fees = ZERO
        withholding = ZERO
        realized_total = ZERO
        lots: list[_MutableLot] = []
        realized: list[RealizedGainLossRecord] = []
        unresolved = 0
        history_complete = True
        for entry in entries:
            event = entry.event
            if event.account_binding != account_binding:
                raise PortfolioAccountingUnavailable("cross-account replay is forbidden")
            history_complete &= event.source_complete and not event.source_truncated
            payload = event.payload
            if event.event_type is Type.ACCOUNT_OPENING_BALANCE:
                if entry.sequence != 1:
                    raise PortfolioAccountingUnavailable("opening balance must be first")
                opening_cash = Decimal(str(payload["cash"]))
                cash += opening_cash
            elif event.event_type is Type.CASH_DEPOSIT:
                amount = Decimal(str(payload["amount"]))
                cash += amount
                contributions += amount
            elif event.event_type is Type.CASH_WITHDRAWAL:
                amount = Decimal(str(payload["amount"]))
                cash -= amount
                withdrawals += amount
            elif event.event_type is Type.BUY_FILL:
                amount = Decimal(str(payload["net_cash_impact"]))
                cash -= amount
                acquisition_fee = Decimal(str(payload["fees"])) + Decimal(
                    str(payload["taxes"])
                )
                quantity = Decimal(str(payload["quantity"]))
                basis = Decimal(str(payload["gross_consideration"])) + acquisition_fee
                lot_identity = canonical_digest(
                    {
                        "acquisition_event_identity": event.event_identity,
                        "quantity": _text(quantity),
                        "basis": _text(basis),
                    }
                )
                lots.append(
                    _MutableLot(
                        lot_identity,
                        account_binding,
                        str(payload["symbol"]),
                        str(payload["instrument_type"]),
                        event.event_identity,
                        event.effective_at,
                        quantity,
                        quantity,
                        basis,
                        basis,
                        acquisition_fee,
                        event.currency,
                    )
                )
                fees += Decimal(str(payload["fees"]))
                withholding += Decimal(str(payload["taxes"]))
            elif event.event_type is Type.SELL_FILL:
                sale_records = self._dispose_fifo(lots, entry, policy)
                realized.extend(sale_records)
                realized_total += sum(
                    (Decimal(record.realized_gain_loss) for record in sale_records), ZERO
                )
                cash += Decimal(str(payload["net_proceeds"]))
                fees += Decimal(str(payload["fees"]))
                withholding += Decimal(str(payload["taxes"]))
            elif event.event_type is Type.DIVIDEND:
                amount = Decimal(str(payload["amount"]))
                cash += amount
                dividends += amount
            elif event.event_type is Type.INTEREST:
                amount = Decimal(str(payload["amount"]))
                cash += amount
                interest += amount
            elif event.event_type is Type.FEE:
                amount = Decimal(str(payload["amount"]))
                cash -= amount
                fees += amount
            elif event.event_type is Type.TAX_WITHHOLDING:
                amount = Decimal(str(payload["amount"]))
                cash -= amount
                withholding += amount
            elif event.event_type in {Type.STOCK_SPLIT, Type.REVERSE_SPLIT}:
                ratio = Decimal(str(payload["numerator"])) / Decimal(
                    str(payload["denominator"])
                )
                matched = [lot for lot in lots if lot.symbol == payload["symbol"]]
                if not matched:
                    raise PortfolioAccountingUnavailable("split has no known position lots")
                for lot in matched:
                    lot.original_quantity *= ratio
                    lot.remaining_quantity *= ratio
            elif event.event_type is Type.POSITION_TRANSFER_IN:
                quantity = Decimal(str(payload["quantity"]))
                basis = Decimal(str(payload["total_basis"]))
                lots.append(
                    _MutableLot(
                        canonical_digest(
                            {"event": event.event_identity, "transfer_quantity": _text(quantity)}
                        ),
                        account_binding,
                        str(payload["symbol"]),
                        str(payload["instrument_type"]),
                        event.event_identity,
                        event.effective_at,
                        quantity,
                        quantity,
                        basis,
                        basis,
                        ZERO,
                        event.currency,
                    )
                )
            elif event.event_type is Type.POSITION_TRANSFER_OUT:
                self._consume_without_gain(
                    lots, str(payload["symbol"]), Decimal(str(payload["quantity"]))
                )
            elif event.event_type in {Type.CASH_ADJUSTMENT, Type.BROKER_CORRECTION}:
                cash += Decimal(str(payload["amount"]))
                if event.event_type is Type.CASH_ADJUSTMENT:
                    unresolved = max(0, unresolved - 1)
            elif event.event_type is Type.RECONCILIATION_ADJUSTMENT_PROPOSED:
                unresolved += 1
        open_lots = tuple(
            lot.immutable()
            for lot in sorted(lots, key=lambda item: (item.symbol, item.acquired_at, item.identity))
            if lot.remaining_quantity
        )
        quantities: dict[str, Decimal] = {}
        basis: dict[str, Decimal] = {}
        for lot in open_lots:
            quantities[lot.symbol] = quantities.get(lot.symbol, ZERO) + Decimal(
                lot.remaining_quantity
            )
            basis[lot.symbol] = basis.get(lot.symbol, ZERO) + Decimal(lot.remaining_basis)
        return PortfolioAccountingState(
            account_binding=account_binding,
            opening_cash=_text(opening_cash),
            current_cash=_text(cash),
            settled_cash=None,
            unsettled_cash=None,
            total_external_contributions=_text(contributions),
            total_external_withdrawals=_text(withdrawals),
            net_external_cash_flow=_text(contributions - withdrawals),
            position_quantities=tuple(
                (key, _text(value)) for key, value in sorted(quantities.items())
            ),
            open_lots=open_lots,
            cost_basis_by_symbol=tuple(
                (key, _text(value)) for key, value in sorted(basis.items())
            ),
            total_portfolio_cost_basis=_text(sum(basis.values(), ZERO)),
            cumulative_dividends=_text(dividends),
            cumulative_interest=_text(interest),
            cumulative_fees=_text(fees),
            cumulative_withholding=_text(withholding),
            cumulative_realized_gain_loss=_text(realized_total),
            realized_records=tuple(realized),
            unresolved_activity_count=unresolved,
            history_complete=history_complete,
            last_processed_sequence=entries[-1].sequence if entries else 0,
            ledger_chain_head=entries[-1].entry_hash if entries else "0" * 64,
            policy_version=policy.version,
        )

    @staticmethod
    def _dispose_fifo(
        lots: list[_MutableLot],
        sale: PortfolioLedgerEntry,
        policy: PortfolioAccountingPolicy,
    ) -> tuple[RealizedGainLossRecord, ...]:
        payload = sale.event.payload
        symbol = str(payload["symbol"])
        requested = Decimal(str(payload["quantity"]))
        available = sum(
            (lot.remaining_quantity for lot in lots if lot.symbol == symbol), ZERO
        )
        if requested > available:
            raise PortfolioAccountingUnavailable(
                "sell exceeds known quantity; incomplete history remains unresolved"
            )
        total_fee = Decimal(str(payload["fees"])) + Decimal(str(payload["taxes"]))
        gross_total = Decimal(str(payload["gross_proceeds"]))
        remaining = requested
        records: list[RealizedGainLossRecord] = []
        candidates = sorted(
            (lot for lot in lots if lot.symbol == symbol and lot.remaining_quantity),
            key=lambda lot: (lot.acquired_at, lot.identity),
        )
        for lot in candidates:
            if not remaining:
                break
            consumed = min(remaining, lot.remaining_quantity)
            basis = lot.remaining_basis * consumed / lot.remaining_quantity
            allocated_fee = total_fee * consumed / requested
            gross = gross_total * consumed / requested
            net = gross - allocated_fee
            gain = net - basis
            held = sale.event.effective_at - lot.acquired_at
            holding = (
                HoldingPeriod.LONG_TERM
                if held >= timedelta(days=365)
                else HoldingPeriod.SHORT_TERM
                if held >= timedelta(0)
                else HoldingPeriod.UNDETERMINED
            )
            identity = canonical_digest(
                {
                    "sale_event": sale.event.event_identity,
                    "lot": lot.identity,
                    "quantity": _text(consumed),
                    "policy": policy.version,
                }
            )
            records.append(
                RealizedGainLossRecord(
                    identity,
                    sale.event.event_identity,
                    symbol,
                    lot.identity,
                    _text(consumed),
                    _text(basis),
                    _text(allocated_fee),
                    _text(gross),
                    _text(net),
                    _text(gain),
                    holding,
                    policy.version,
                )
            )
            lot.remaining_quantity -= consumed
            lot.remaining_basis -= basis
            remaining -= consumed
        return tuple(records)

    @staticmethod
    def _consume_without_gain(
        lots: list[_MutableLot], symbol: str, quantity: Decimal
    ) -> None:
        available = sum(
            (lot.remaining_quantity for lot in lots if lot.symbol == symbol), ZERO
        )
        if quantity > available:
            raise PortfolioAccountingUnavailable("transfer exceeds known quantity")
        remaining = quantity
        for lot in sorted(lots, key=lambda item: (item.acquired_at, item.identity)):
            if lot.symbol != symbol or not lot.remaining_quantity or not remaining:
                continue
            consumed = min(remaining, lot.remaining_quantity)
            basis = lot.remaining_basis * consumed / lot.remaining_quantity
            lot.remaining_quantity -= consumed
            lot.remaining_basis -= basis
            remaining -= consumed


class PortfolioValuationService:
    """Values replayed state using only caller-supplied exact price observations."""

    def value(
        self,
        state: PortfolioAccountingState,
        *,
        valued_at: datetime,
        source_timestamp: datetime,
        acquired_at: datetime,
        portfolio_snapshot_identity: str,
        prices: tuple[tuple[str, str, datetime, str], ...],
        maximum_price_age: timedelta,
        market_data_identity: str | None = None,
    ) -> PortfolioValuation:
        price_map = {symbol: (price, observed, identity) for symbol, price, observed, identity in prices}
        positions: list[PositionValuation] = []
        unpriced: list[str] = []
        stale: list[str] = []
        market_total = ZERO
        for symbol, quantity_text in state.position_quantities:
            quantity = Decimal(quantity_text)
            supplied = price_map.get(symbol)
            if supplied is None:
                unpriced.append(symbol)
                positions.append(PositionValuation(symbol, quantity_text, None, None, None, False))
                continue
            price_text, observed_at, _ = supplied
            price = Decimal(str(decimal_text(price_text, "price", positive=True)))
            is_stale = valued_at - observed_at > maximum_price_age
            if is_stale:
                stale.append(symbol)
            value = quantity * price
            market_total += value
            positions.append(
                PositionValuation(
                    symbol, quantity_text, _text(price), _text(value), observed_at, is_stale
                )
            )
        complete = not unpriced and not stale
        total = Decimal(state.current_cash) + market_total if complete else None
        return PortfolioValuation(
            state.account_binding,
            valued_at,
            source_timestamp,
            acquired_at,
            portfolio_snapshot_identity,
            market_data_identity,
            state.current_cash,
            tuple(positions),
            None if total is None else _text(total),
            tuple(unpriced),
            tuple(stale),
            CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
        )

    def unrealized(
        self, state: PortfolioAccountingState, valuation: PortfolioValuation
    ) -> tuple[UnrealizedGainLossRecord, ...]:
        if valuation.completeness_status is not CompletenessStatus.COMPLETE:
            raise PortfolioAccountingUnavailable("unrealized gain requires complete fresh valuation")
        basis = dict(state.cost_basis_by_symbol)
        result: list[UnrealizedGainLossRecord] = []
        for item in valuation.positions:
            if item.price is None or item.market_value is None or item.symbol not in basis:
                raise PortfolioAccountingUnavailable("unrealized gain input is incomplete")
            gain = Decimal(item.market_value) - Decimal(basis[item.symbol])
            result.append(
                UnrealizedGainLossRecord(
                    item.symbol,
                    basis[item.symbol],
                    item.price,
                    item.market_value,
                    _text(gain),
                    valuation.valuation_identity,
                    valuation.valuation_timestamp,
                    CompletenessStatus.COMPLETE,
                )
            )
        return tuple(result)
