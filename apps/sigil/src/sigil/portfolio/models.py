"""Immutable normalized brokerage account and portfolio state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping

from sigil.integrations.providers.models import FinancialDataValidationError
from sigil.integrations.providers.public_execution import (
    PUBLIC_EXECUTION_PROVIDER_ID,
    normalize_public_account_id,
    normalize_public_symbol,
    protected_account_binding,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$", re.ASCII)
_DIGEST = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", re.ASCII)
_SECRET_KEYS = frozenset(
    {"access_token", "api_key", "authorization", "cookie", "private_key", "secret", "token"}
)
SUPPORTED_CURRENCIES = frozenset({"USD"})
SUPPORTED_INSTRUMENTS = frozenset({"EQUITY", "ETF"})
SUPPORTED_SIDES = frozenset({"BUY", "SELL"})
SUPPORTED_ORDER_TYPES = frozenset({"MARKET", "LIMIT"})
TERMINAL_STATUSES = frozenset({"CANCELLED", "EXPIRED", "FILLED", "REJECTED"})
NONTERMINAL_STATUSES = frozenset(
    {
        "ACCEPTED",
        "ACKNOWLEDGED",
        "CANCELLATION_REQUESTED",
        "NEW",
        "PARTIALLY_FILLED",
        "PENDING",
        "PENDING_CANCEL",
    }
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def canonical_digest(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise FinancialDataValidationError(f"{name} is invalid")
    return value


def _choice(value: object, name: str, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value.upper() not in choices:
        raise FinancialDataValidationError(f"{name} is unsupported")
    return value.upper()


def decimal_text(
    value: object, name: str, *, nonnegative: bool = True, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, Decimal)):
        raise FinancialDataValidationError(f"{name} must be an exact decimal")
    text = str(value)
    if _DECIMAL.fullmatch(text) is None:
        raise FinancialDataValidationError(f"{name} must be a canonical decimal")
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise FinancialDataValidationError(f"{name} is invalid") from None
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise FinancialDataValidationError(f"{name} is invalid")
    result = format(parsed, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in {"-0", ""} else result


def timestamp(value: object, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise FinancialDataValidationError(f"{name} is invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialDataValidationError(f"{name} must be timezone-aware")
    return value


def _digest_field(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise FinancialDataValidationError(f"{name} is invalid")
    return value


def reject_secret_bearing(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SECRET_KEYS or "authorization" in normalized:
                raise FinancialDataValidationError("secret-bearing portfolio state is forbidden")
            reject_secret_bearing(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_secret_bearing(child)


@dataclass(frozen=True, slots=True)
class PortfolioStateProvenance:
    provider_id: str
    account_binding: str
    operation: str
    endpoint_identity: str
    provider_timestamp: datetime
    acquired_at: datetime
    response_digest: str

    def __post_init__(self) -> None:
        if self.provider_id != PUBLIC_EXECUTION_PROVIDER_ID:
            raise FinancialDataValidationError("portfolio provider identity is unsupported")
        if not self.account_binding.startswith("public-account-sha256:"):
            raise FinancialDataValidationError("protected account binding is invalid")
        _identifier(self.operation, "operation")
        if not self.endpoint_identity.startswith("/userapigateway/"):
            raise FinancialDataValidationError("endpoint identity is invalid")
        timestamp(self.provider_timestamp, "provider_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        _digest_field(self.response_digest, "response_digest")


@dataclass(frozen=True, slots=True)
class BrokerageAccountState:
    provider_id: str
    account_binding: str
    broker_account_id: str = field(repr=False)
    account_type: str
    account_status: str
    currency: str
    trading_eligible: bool
    cash_balance: str
    available_cash: str
    settled_cash: str | None
    unsettled_cash: str | None
    buying_power: str
    equity: str
    provider_timestamp: datetime
    acquired_at: datetime
    response_digest: str

    def __post_init__(self) -> None:
        account_id = normalize_public_account_id(self.broker_account_id)
        if self.provider_id != PUBLIC_EXECUTION_PROVIDER_ID:
            raise FinancialDataValidationError("account provider identity is unsupported")
        if self.account_binding != protected_account_binding(account_id):
            raise FinancialDataValidationError("account binding mismatch")
        _identifier(self.account_type, "account_type")
        _identifier(self.account_status, "account_status")
        _choice(self.currency, "currency", SUPPORTED_CURRENCIES)
        if not isinstance(self.trading_eligible, bool):
            raise FinancialDataValidationError("trading_eligible must be boolean")
        for name in ("cash_balance", "available_cash", "buying_power", "equity"):
            object.__setattr__(self, name, decimal_text(getattr(self, name), name))
        for name in ("settled_cash", "unsettled_cash"):
            object.__setattr__(
                self, name, decimal_text(getattr(self, name), name, optional=True)
            )
        timestamp(self.provider_timestamp, "provider_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        _digest_field(self.response_digest, "response_digest")


@dataclass(frozen=True, slots=True)
class BrokeragePosition:
    symbol: str
    instrument_type: str
    quantity: str
    available_quantity: str | None
    average_cost: str
    current_market_value: str | None
    last_price: str | None
    unrealized_gain_loss: str | None
    currency: str
    provider_timestamp: datetime
    acquired_at: datetime
    position_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_public_symbol(self.symbol))
        object.__setattr__(
            self,
            "instrument_type",
            _choice(self.instrument_type, "instrument_type", SUPPORTED_INSTRUMENTS),
        )
        for name in ("quantity", "average_cost"):
            object.__setattr__(self, name, decimal_text(getattr(self, name), name))
        for name in ("available_quantity", "current_market_value", "last_price"):
            object.__setattr__(
                self, name, decimal_text(getattr(self, name), name, optional=True)
            )
        object.__setattr__(
            self,
            "unrealized_gain_loss",
            decimal_text(
                self.unrealized_gain_loss,
                "unrealized_gain_loss",
                nonnegative=False,
                optional=True,
            ),
        )
        object.__setattr__(self, "currency", _choice(self.currency, "currency", SUPPORTED_CURRENCIES))
        timestamp(self.provider_timestamp, "provider_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        material = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in asdict(self).items()
            if key != "position_digest"
        }
        computed = canonical_digest(material)
        if self.position_digest and self.position_digest != computed:
            raise FinancialDataValidationError("position digest mismatch")
        object.__setattr__(self, "position_digest", computed)


@dataclass(frozen=True, slots=True)
class BrokerageOrderState:
    client_order_id: str
    provider_order_id: str
    symbol: str
    instrument_type: str
    side: str
    order_type: str
    quantity: str | None
    notional: str | None
    limit_price: str | None
    time_in_force: str
    broker_status: str
    filled_quantity: str
    average_fill_price: str | None
    submitted_at: datetime
    updated_at: datetime
    terminal: bool

    def __post_init__(self) -> None:
        _identifier(self.client_order_id, "client_order_id")
        _identifier(self.provider_order_id, "provider_order_id")
        object.__setattr__(self, "symbol", normalize_public_symbol(self.symbol))
        object.__setattr__(
            self,
            "instrument_type",
            _choice(self.instrument_type, "instrument_type", SUPPORTED_INSTRUMENTS),
        )
        object.__setattr__(self, "side", _choice(self.side, "side", SUPPORTED_SIDES))
        object.__setattr__(
            self, "order_type", _choice(self.order_type, "order_type", SUPPORTED_ORDER_TYPES)
        )
        object.__setattr__(self, "quantity", decimal_text(self.quantity, "quantity", optional=True))
        object.__setattr__(self, "notional", decimal_text(self.notional, "notional", optional=True))
        if (self.quantity is None) == (self.notional is None):
            raise FinancialDataValidationError("order requires exactly one of quantity or notional")
        object.__setattr__(
            self, "limit_price", decimal_text(self.limit_price, "limit_price", optional=True)
        )
        if (self.order_type == "LIMIT") != (self.limit_price is not None):
            raise FinancialDataValidationError("limit price is inconsistent with order type")
        object.__setattr__(self, "time_in_force", _identifier(self.time_in_force, "time_in_force").upper())
        statuses = TERMINAL_STATUSES | NONTERMINAL_STATUSES
        object.__setattr__(
            self, "broker_status", _choice(self.broker_status, "broker_status", statuses)
        )
        object.__setattr__(
            self, "filled_quantity", decimal_text(self.filled_quantity, "filled_quantity")
        )
        object.__setattr__(
            self,
            "average_fill_price",
            decimal_text(self.average_fill_price, "average_fill_price", optional=True),
        )
        submitted = timestamp(self.submitted_at, "submitted_at")
        updated = timestamp(self.updated_at, "updated_at")
        if updated < submitted:
            raise FinancialDataValidationError("order updated_at precedes submitted_at")
        if self.terminal != (self.broker_status in TERMINAL_STATUSES):
            raise FinancialDataValidationError("terminal classification is inconsistent")


@dataclass(frozen=True, slots=True)
class BrokerageExecution:
    provider_execution_id: str
    provider_order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    filled_quantity: str
    fill_price: str
    fees: str | None
    executed_at: datetime
    settlement_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.provider_execution_id, "provider_execution_id")
        _identifier(self.provider_order_id, "provider_order_id")
        if self.client_order_id is not None:
            _identifier(self.client_order_id, "client_order_id")
        object.__setattr__(self, "symbol", normalize_public_symbol(self.symbol))
        object.__setattr__(self, "side", _choice(self.side, "side", SUPPORTED_SIDES))
        object.__setattr__(
            self, "filled_quantity", decimal_text(self.filled_quantity, "filled_quantity")
        )
        object.__setattr__(self, "fill_price", decimal_text(self.fill_price, "fill_price"))
        object.__setattr__(self, "fees", decimal_text(self.fees, "fees", optional=True))
        timestamp(self.executed_at, "executed_at")
        if tuple(sorted(self.settlement_metadata)) != self.settlement_metadata:
            raise FinancialDataValidationError("settlement metadata must be deterministic")
        for key, value in self.settlement_metadata:
            _identifier(key, "settlement key")
            _identifier(value, "settlement value")


@dataclass(frozen=True, slots=True)
class PortfolioFreshnessPolicy:
    maximum_account_state_age: timedelta
    maximum_position_state_age: timedelta
    maximum_open_order_age: timedelta
    allowed_future_clock_skew: timedelta
    maximum_acquisition_duration: timedelta
    maximum_positions: int = 1_000
    maximum_orders: int = 1_000
    maximum_executions: int = 2_000

    def __post_init__(self) -> None:
        for name in (
            "maximum_account_state_age",
            "maximum_position_state_age",
            "maximum_open_order_age",
            "maximum_acquisition_duration",
        ):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or not timedelta(0) < value <= timedelta(days=7):
                raise FinancialDataValidationError(f"{name} is invalid")
        if not timedelta(0) <= self.allowed_future_clock_skew <= timedelta(minutes=10):
            raise FinancialDataValidationError("allowed_future_clock_skew is invalid")
        for name in ("maximum_positions", "maximum_orders", "maximum_executions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
                raise FinancialDataValidationError(f"{name} is invalid")


@dataclass(frozen=True, slots=True)
class BrokeragePortfolioSnapshot:
    account_binding: str
    account: BrokerageAccountState
    positions: tuple[BrokeragePosition, ...]
    orders: tuple[BrokerageOrderState, ...]
    executions: tuple[BrokerageExecution, ...]
    provenance: tuple[PortfolioStateProvenance, ...]
    acquired_started_at: datetime
    acquired_completed_at: datetime
    positions_complete: bool
    orders_complete: bool
    executions_complete: bool
    positions_truncated: bool = False
    orders_truncated: bool = False
    executions_truncated: bool = False
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        if self.account_binding != self.account.account_binding:
            raise FinancialDataValidationError("snapshot account binding mismatch")
        started = timestamp(self.acquired_started_at, "acquired_started_at")
        completed = timestamp(self.acquired_completed_at, "acquired_completed_at")
        if completed < started:
            raise FinancialDataValidationError("snapshot acquisition duration is invalid")
        positions = tuple(sorted(self.positions, key=lambda item: item.symbol))
        orders = tuple(sorted(self.orders, key=lambda item: (item.client_order_id, item.provider_order_id)))
        executions = tuple(sorted(self.executions, key=lambda item: item.provider_execution_id))
        provenance = tuple(sorted(self.provenance, key=lambda item: item.operation))
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "executions", executions)
        object.__setattr__(self, "provenance", provenance)
        if len({item.symbol for item in positions}) != len(positions):
            raise FinancialDataValidationError("duplicate positions are forbidden")
        if len({item.provider_order_id for item in orders}) != len(orders):
            raise FinancialDataValidationError("duplicate provider order IDs are forbidden")
        if len({item.client_order_id for item in orders}) != len(orders):
            raise FinancialDataValidationError("duplicate client order IDs are forbidden")
        if len({item.provider_execution_id for item in executions}) != len(executions):
            raise FinancialDataValidationError("duplicate execution IDs are forbidden")
        if self.positions_truncated and self.positions_complete:
            raise FinancialDataValidationError("truncated positions cannot be complete")
        if self.orders_truncated and self.orders_complete:
            raise FinancialDataValidationError("truncated orders cannot be complete")
        if self.executions_truncated and self.executions_complete:
            raise FinancialDataValidationError("truncated executions cannot be complete")
        computed = canonical_digest(self.canonical_value(include_identity=False))
        if self.snapshot_id and self.snapshot_id != computed:
            raise FinancialDataValidationError("snapshot identity mismatch")
        object.__setattr__(self, "snapshot_id", computed)

    def canonical_value(self, *, include_identity: bool = True) -> dict[str, object]:
        def encode(value: object) -> object:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Mapping):
                return {str(key): encode(item) for key, item in value.items()}
            if hasattr(value, "__dataclass_fields__"):
                return {key: encode(item) for key, item in asdict(value).items()}
            if isinstance(value, (list, tuple)):
                return [encode(item) for item in value]
            return value

        result = {key: encode(value) for key, value in asdict(self).items()}
        if not include_identity:
            result.pop("snapshot_id", None)
        return result

    @property
    def complete(self) -> bool:
        return (
            self.positions_complete
            and self.orders_complete
            and self.executions_complete
            and not any(
                (self.positions_truncated, self.orders_truncated, self.executions_truncated)
            )
        )

    def pre_trade_eligibility(
        self, policy: PortfolioFreshnessPolicy, *, now: datetime, expected_account_id: str
    ) -> tuple[bool, tuple[str, ...]]:
        now = timestamp(now, "now")
        reasons: list[str] = []
        if self.account_binding != protected_account_binding(expected_account_id):
            reasons.append("wrong_account")
        if not self.complete:
            reasons.append("partial_or_truncated")
        if len(self.positions) > policy.maximum_positions:
            reasons.append("positions_limit_exceeded")
        if len(self.orders) > policy.maximum_orders:
            reasons.append("orders_limit_exceeded")
        if len(self.executions) > policy.maximum_executions:
            reasons.append("executions_limit_exceeded")
        if self.acquired_completed_at - self.acquired_started_at > policy.maximum_acquisition_duration:
            reasons.append("acquisition_too_slow")
        checks = [(self.account.provider_timestamp, policy.maximum_account_state_age, "stale_account")]
        checks.extend(
            (item.provider_timestamp, policy.maximum_position_state_age, "stale_positions")
            for item in self.positions
        )
        checks.extend(
            (item.updated_at, policy.maximum_open_order_age, "stale_orders")
            for item in self.orders
            if not item.terminal
        )
        for observed, maximum_age, reason in checks:
            if observed > now + policy.allowed_future_clock_skew:
                reasons.append("future_timestamp")
            elif now - observed > maximum_age:
                reasons.append(reason)
        return not reasons, tuple(sorted(set(reasons)))


class PortfolioStateDiscrepancyCode(StrEnum):
    MATCHED = "journal_intent_matched"
    ACKNOWLEDGED = "broker_order_acknowledged"
    AMBIGUOUS_RESOLVED = "ambiguous_submission_resolved"
    JOURNAL_ORDER_ABSENT = "journal_order_absent"
    UNJOURNALED_BROKER_ORDER = "unjournaled_broker_order"
    MISMATCHED_ACCOUNT = "mismatched_account"
    MISMATCHED_SYMBOL = "mismatched_symbol"
    MISMATCHED_SIDE = "mismatched_side"
    MISMATCHED_QUANTITY = "mismatched_quantity"
    MISMATCHED_NOTIONAL = "mismatched_notional"
    MISMATCHED_ORDER_TYPE = "mismatched_order_type"
    MISMATCHED_LIMIT_PRICE = "mismatched_limit_price"
    MISMATCHED_CLIENT_ORDER_ID = "mismatched_client_order_id"
    MISMATCHED_PROVIDER_ORDER_ID = "mismatched_provider_order_id"
    TERMINAL_STATE_CONFLICT = "terminal_state_conflict"
    BROKER_TERMINAL_NOT_RECORDED = "broker_terminal_not_recorded"
    CANCELLATION_RESOLVED = "cancellation_ambiguity_resolved"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CORRUPT_JOURNAL = "corrupt_journal"


@dataclass(frozen=True, slots=True, order=True)
class PortfolioStateDiscrepancy:
    code: PortfolioStateDiscrepancyCode
    execution_id: str
    client_order_id: str | None = None
    provider_order_id: str | None = None
    field: str | None = None
    journal_value: str | None = None
    broker_value: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioReconciliationReport:
    account_binding: str
    snapshot_id: str
    discrepancies: tuple[PortfolioStateDiscrepancy, ...]
    journal_execution_count: int
    broker_order_count: int
    report_id: str = ""

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.discrepancies))
        object.__setattr__(self, "discrepancies", ordered)
        material = {
            "account_binding": self.account_binding,
            "snapshot_id": self.snapshot_id,
            "discrepancies": [
                {key: (value.value if isinstance(value, StrEnum) else value) for key, value in asdict(item).items()}
                for item in ordered
            ],
            "journal_execution_count": self.journal_execution_count,
            "broker_order_count": self.broker_order_count,
        }
        computed = canonical_digest(material)
        if self.report_id and self.report_id != computed:
            raise FinancialDataValidationError("reconciliation report identity mismatch")
        object.__setattr__(self, "report_id", computed)
