"""Public-specific, exact-allowlist read-only portfolio acquisition."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping

from sigil.integrations.providers.models import FinancialDataValidationError
from sigil.integrations.providers.public_execution import (
    PUBLIC_EXECUTION_PROVIDER_ID,
    PublicAccessTokenManager,
    PublicTransportResult,
    _PublicGovernedTransport,
    normalize_public_account_id,
    protected_account_binding,
)

from .models import (
    BrokerageAccountState,
    BrokerageExecution,
    BrokerageOrderState,
    BrokeragePortfolioSnapshot,
    BrokeragePosition,
    PortfolioFreshnessPolicy,
    PortfolioStateProvenance,
    TERMINAL_STATUSES,
    canonical_digest,
    reject_secret_bearing,
    timestamp,
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FinancialDataValidationError(f"{name} response is malformed")
    reject_secret_bearing(value)
    return value


def _items(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise FinancialDataValidationError(f"{key} response is malformed")
    return [_mapping(item, key) for item in value]


def _required(payload: Mapping[str, object], key: str) -> object:
    if key not in payload or payload[key] is None:
        raise FinancialDataValidationError(f"{key} is required")
    return payload[key]


def _nested(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    return _mapping(_required(payload, key), key)


class PublicPortfolioStateProvider:
    """Closed read-only account-state surface; it cannot issue broker mutations."""

    def __init__(
        self,
        *,
        token_manager: PublicAccessTokenManager,
        transport: _PublicGovernedTransport,
        wall_clock: Callable[[], datetime],
    ) -> None:
        self._tokens = token_manager
        self._transport = transport
        self._clock = wall_clock

    def acquire(
        self, account_id: str, policy: PortfolioFreshnessPolicy
    ) -> BrokeragePortfolioSnapshot:
        account_id = normalize_public_account_id(account_id)
        started = self._clock()
        results = (
            ("accounts", self._read(self._transport.list_accounts)),
            (
                "portfolio",
                self._read(lambda token: self._transport.account_portfolio(account_id, token)),
            ),
            ("history", self._read(lambda token: self._transport.get_history(account_id, token))),
        )
        completed = self._clock()
        payloads = {name: _mapping(result.payload, name) for name, result in results}
        listed = [
            item
            for item in _items(payloads["accounts"], "accounts")
            if item.get("accountId") == account_id
        ]
        if len(listed) != 1:
            raise FinancialDataValidationError("provider account binding mismatch")
        portfolio = payloads["portfolio"]
        if portfolio.get("accountId") != account_id:
            raise FinancialDataValidationError("provider account binding mismatch")
        observed_at = self._latest_provider_timestamp(portfolio, payloads["history"], completed)
        account = self._account(account_id, listed[0], portfolio, results[1][1], observed_at, completed)
        positions = tuple(self._position(item, completed) for item in _items(portfolio, "positions"))
        orders = tuple(self._order(item) for item in _items(portfolio, "orders"))
        executions = tuple(
            self._execution(item)
            for item in _items(payloads["history"], "transactions")
            if item.get("type") == "TRADE"
        )
        for values, maximum, name in (
            (positions, policy.maximum_positions, "position"),
            (orders, policy.maximum_orders, "order"),
            (executions, policy.maximum_executions, "execution"),
        ):
            if len(values) > maximum:
                raise FinancialDataValidationError(f"maximum {name} count exceeded")
        history_complete = payloads["history"].get("nextToken") in {None, ""}
        provenance = tuple(
            PortfolioStateProvenance(
                provider_id=PUBLIC_EXECUTION_PROVIDER_ID,
                account_binding=protected_account_binding(account_id),
                operation=name,
                endpoint_identity=result.endpoint_identity,
                provider_timestamp=observed_at,
                acquired_at=completed,
                response_digest=result.response_hash,
            )
            for name, result in results
        )
        return BrokeragePortfolioSnapshot(
            account_binding=protected_account_binding(account_id),
            account=account,
            positions=positions,
            orders=orders,
            executions=executions,
            provenance=provenance,
            acquired_started_at=started,
            acquired_completed_at=completed,
            positions_complete=True,
            orders_complete=True,
            executions_complete=history_complete,
            executions_truncated=not history_complete,
        )

    def _read(self, operation: Callable[[str], PublicTransportResult]) -> PublicTransportResult:
        token = self._tokens.get()
        try:
            return operation(token)
        finally:
            token = ""  # noqa: F841 - shorten runtime-only credential lifetime

    @staticmethod
    def _latest_provider_timestamp(
        portfolio: Mapping[str, object],
        history: Mapping[str, object],
        fallback: datetime,
    ) -> datetime:
        candidates: list[datetime] = []
        for item in _items(portfolio, "positions"):
            for container, key in (("lastPrice", "timestamp"), ("costBasis", "lastUpdate")):
                child = item.get(container)
                if isinstance(child, Mapping) and child.get(key) is not None:
                    candidates.append(timestamp(child[key], key))
        for item in _items(portfolio, "orders"):
            for key in ("closedAt", "createdAt"):
                if item.get(key) is not None:
                    candidates.append(timestamp(item[key], key))
        for item in _items(history, "transactions"):
            if item.get("timestamp") is not None:
                candidates.append(timestamp(item["timestamp"], "timestamp"))
        return max(candidates, default=fallback)

    @staticmethod
    def _account(
        account_id: str,
        listed: Mapping[str, object],
        portfolio: Mapping[str, object],
        result: PublicTransportResult,
        observed_at: datetime,
        acquired_at: datetime,
    ) -> BrokerageAccountState:
        buying_power = _nested(portfolio, "buyingPower")
        withdraw = _nested(portfolio, "availableToWithdraw")
        permission = str(_required(listed, "tradePermissions")).upper()
        return BrokerageAccountState(
            provider_id=PUBLIC_EXECUTION_PROVIDER_ID,
            account_binding=protected_account_binding(account_id),
            broker_account_id=account_id,
            account_type=str(_required(portfolio, "accountType")),
            account_status="AVAILABLE",
            currency="USD",
            trading_eligible=permission == "BUY_AND_SELL",
            cash_balance=_required(portfolio, "cash"),  # type: ignore[arg-type]
            available_cash=_required(withdraw, "cashOnlyAvailableToWithdraw"),  # type: ignore[arg-type]
            settled_cash=None,
            unsettled_cash=None,
            buying_power=_required(buying_power, "cashOnlyBuyingPower"),  # type: ignore[arg-type]
            equity=_required(portfolio, "totalAccountValue"),  # type: ignore[arg-type]
            provider_timestamp=observed_at,
            acquired_at=acquired_at,
            response_digest=result.response_hash,
        )

    @staticmethod
    def _position(value: Mapping[str, object], acquired_at: datetime) -> BrokeragePosition:
        instrument = _nested(value, "instrument")
        last_price = _nested(value, "lastPrice")
        cost_basis = _nested(value, "costBasis")
        gain = _nested(value, "instrumentGain")
        observed = timestamp(_required(last_price, "timestamp"), "lastPrice.timestamp")
        return BrokeragePosition(
            symbol=str(_required(instrument, "symbol")),
            instrument_type=str(_required(instrument, "type")),
            quantity=_required(value, "quantity"),  # type: ignore[arg-type]
            available_quantity=None,
            average_cost=_required(cost_basis, "unitCost"),  # type: ignore[arg-type]
            current_market_value=value.get("currentValue"),  # type: ignore[arg-type]
            last_price=last_price.get("lastPrice"),  # type: ignore[arg-type]
            unrealized_gain_loss=gain.get("gainValue"),  # type: ignore[arg-type]
            currency="USD",
            provider_timestamp=observed,
            acquired_at=acquired_at,
        )

    @staticmethod
    def _order(value: Mapping[str, object]) -> BrokerageOrderState:
        instrument = _nested(value, "instrument")
        expiration = _nested(value, "expiration")
        status = str(_required(value, "status")).upper()
        submitted = timestamp(_required(value, "createdAt"), "createdAt")
        updated = (
            timestamp(value["closedAt"], "closedAt")
            if value.get("closedAt") is not None
            else submitted
        )
        return BrokerageOrderState(
            client_order_id=str(_required(value, "orderId")),
            provider_order_id=str(_required(value, "orderId")),
            symbol=str(_required(instrument, "symbol")),
            instrument_type=str(_required(instrument, "type")),
            side=str(_required(value, "side")),
            order_type=str(_required(value, "type")),
            quantity=value.get("quantity"),  # type: ignore[arg-type]
            notional=value.get("notionalValue"),  # type: ignore[arg-type]
            limit_price=value.get("limitPrice"),  # type: ignore[arg-type]
            time_in_force=str(_required(expiration, "timeInForce")),
            broker_status=status,
            filled_quantity=value.get("filledQuantity", "0"),  # type: ignore[arg-type]
            average_fill_price=value.get("averagePrice"),  # type: ignore[arg-type]
            submitted_at=submitted,
            updated_at=updated,
            terminal=status in TERMINAL_STATUSES,
        )

    @staticmethod
    def _execution(value: Mapping[str, object]) -> BrokerageExecution:
        return BrokerageExecution(
            provider_execution_id=str(_required(value, "id")),
            provider_order_id=str(value.get("orderId") or _required(value, "id")),
            client_order_id=(
                str(value["orderId"]) if value.get("orderId") is not None else None
            ),
            symbol=str(_required(value, "symbol")),
            side=str(_required(value, "side")),
            filled_quantity=_required(value, "quantity"),  # type: ignore[arg-type]
            fill_price=_required(value, "principalAmount"),  # type: ignore[arg-type]
            fees=value.get("fees"),  # type: ignore[arg-type]
            executed_at=timestamp(_required(value, "timestamp"), "timestamp"),
            settlement_metadata=(),
        )


def provider_response_digest(payload: object) -> str:
    reject_secret_bearing(payload)
    return canonical_digest(payload)
