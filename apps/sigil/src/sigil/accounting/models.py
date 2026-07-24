"""Immutable domain models for governed portfolio accounting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping

from sigil.integrations.providers.models import FinancialDataValidationError


LEDGER_VERSION = 1
SUPPORTED_CURRENCIES = frozenset({"USD"})
SUPPORTED_INSTRUMENTS = frozenset({"EQUITY", "ETF"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$", re.ASCII)
_DIGEST = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", re.ASCII)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$", re.ASCII)
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "private_key",
        "secret",
        "set_cookie",
        "token",
    }
)


class PortfolioAccountingError(RuntimeError):
    """Base failure for the governed accounting boundary."""


class PortfolioLedgerCorruptionError(PortfolioAccountingError):
    """Persisted ledger history cannot be trusted."""


class PortfolioLedgerConflictError(PortfolioAccountingError):
    """A write conflicts with immutable committed history."""


class PortfolioAccountingUnavailable(PortfolioAccountingError):
    """A derived result cannot be produced from supplied complete evidence."""


class PortfolioLedgerEventType(StrEnum):
    ACCOUNT_OPENING_BALANCE = "account_opening_balance"
    CASH_DEPOSIT = "cash_deposit"
    CASH_WITHDRAWAL = "cash_withdrawal"
    BUY_FILL = "buy_fill"
    SELL_FILL = "sell_fill"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    TAX_WITHHOLDING = "tax_withholding"
    STOCK_SPLIT = "stock_split"
    REVERSE_SPLIT = "reverse_split"
    CASH_ADJUSTMENT = "cash_adjustment"
    POSITION_TRANSFER_IN = "position_transfer_in"
    POSITION_TRANSFER_OUT = "position_transfer_out"
    BROKER_CORRECTION = "broker_correction"
    VALUATION_OBSERVATION = "valuation_observation"
    RECONCILIATION_ADJUSTMENT_PROPOSED = "reconciliation_adjustment_proposed"
    RECONCILIATION_ADJUSTMENT_APPROVED = "reconciliation_adjustment_approved"
    ACCOUNTING_PERIOD_CLOSED = "accounting_period_closed"
    ACCOUNTING_PERIOD_REOPENED = "accounting_period_reopened"


class CostBasisMethod(StrEnum):
    FIFO = "FIFO"
    AVERAGE_COST = "AVERAGE_COST"


class CompletenessStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    TRUNCATED = "truncated"


class HoldingPeriod(StrEnum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    UNDETERMINED = "undetermined"


class LedgerDiscrepancyCode(StrEnum):
    DERIVED_CASH_MISMATCH = "derived_cash_mismatch"
    AVAILABLE_CASH_MISMATCH = "available_cash_mismatch"
    SETTLED_CASH_MISMATCH = "settled_cash_mismatch"
    POSITION_QUANTITY_MISMATCH = "position_quantity_mismatch"
    MISSING_LEDGER_POSITION = "missing_ledger_position"
    MISSING_BROKER_POSITION = "missing_broker_position"
    COST_BASIS_MISMATCH = "cost_basis_mismatch"
    MARKET_VALUE_MISMATCH = "market_value_mismatch"
    BROKER_EXECUTION_ABSENT = "broker_execution_absent_from_ledger"
    LEDGER_EXECUTION_ABSENT = "ledger_execution_absent_from_broker"
    DUPLICATE_BROKER_EXECUTION = "duplicate_broker_execution"
    UNJOURNALED_BROKER_ORDER = "unjournaled_broker_order_activity"
    ACCOUNT_MISMATCH = "account_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    STALE_SNAPSHOT = "stale_snapshot"
    PARTIAL_SNAPSHOT = "partial_snapshot"
    TRUNCATED_SNAPSHOT = "truncated_snapshot"
    INCOMPLETE_LEDGER_HISTORY = "incomplete_ledger_history"
    VALUATION_MISMATCH = "valuation_mismatch"
    UNEXPLAINED_EQUITY_DIFFERENCE = "unexplained_equity_difference"


def canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_text(value, "decimal", nonnegative=False)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return canonical_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): canonical_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(child) for child in value]
    return value


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            canonical_value(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise FinancialDataValidationError("value is not canonical JSON") from exc


def canonical_digest(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def identifier(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or _ID.fullmatch(value) is None
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise FinancialDataValidationError(f"{name} is invalid")
    return value


def digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise FinancialDataValidationError(f"{name} is invalid")
    return value


def timestamp(value: object, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise FinancialDataValidationError(f"{name} is invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialDataValidationError(f"{name} must be timezone-aware")
    return value


def decimal_text(
    value: object,
    name: str,
    *,
    nonnegative: bool = True,
    positive: bool = False,
    optional: bool = False,
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
    if not parsed.is_finite() or (nonnegative and parsed < 0) or (positive and parsed <= 0):
        raise FinancialDataValidationError(f"{name} is invalid")
    result = format(parsed, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in {"", "-0"} else result


def symbol(value: object) -> str:
    if not isinstance(value, str) or _SYMBOL.fullmatch(value.upper()) is None:
        raise FinancialDataValidationError("symbol is unsupported")
    return value.upper()


def reject_secret_bearing(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in _SECRET_KEYS
                or "authorization" in normalized
                or "access_token" in normalized
                or "api_key" in normalized
            ):
                raise FinancialDataValidationError("secret-bearing accounting data is forbidden")
            reject_secret_bearing(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_secret_bearing(child)


@dataclass(frozen=True, slots=True)
class PortfolioAccountingPolicy:
    version: str = "sigil-accounting-v1"
    default_cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO
    average_cost_instruments: frozenset[str] = frozenset()
    return_scale: int = 12
    money_weighted_tolerance: str = "0.000000000001"
    money_weighted_max_iterations: int = 256
    money_weighted_lower_bound: str = "-0.999999"
    money_weighted_upper_bound: str = "1000"

    def __post_init__(self) -> None:
        identifier(self.version, "policy version")
        if self.default_cost_basis_method is not CostBasisMethod.FIFO:
            raise FinancialDataValidationError("FIFO is the required default policy")
        if not self.average_cost_instruments <= SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("average-cost instrument allowlist is unsupported")
        if not 0 <= self.return_scale <= 28:
            raise FinancialDataValidationError("return_scale is invalid")
        decimal_text(self.money_weighted_tolerance, "money_weighted_tolerance", positive=True)
        decimal_text(
            self.money_weighted_lower_bound,
            "money_weighted_lower_bound",
            nonnegative=False,
        )
        decimal_text(
            self.money_weighted_upper_bound,
            "money_weighted_upper_bound",
            positive=True,
        )
        if not 1 <= self.money_weighted_max_iterations <= 10_000:
            raise FinancialDataValidationError("money_weighted_max_iterations is invalid")


@dataclass(frozen=True, slots=True)
class PortfolioLedgerEvent:
    account_binding: str
    ledger_identity: str
    event_type: PortfolioLedgerEventType
    source_identity: str
    source_provider: str
    source_record_id: str
    source_response_digest: str
    source_timestamp: datetime
    effective_at: datetime
    acquired_at: datetime
    currency: str
    payload: Mapping[str, object]
    accounting_policy_version: str
    source_complete: bool = True
    source_truncated: bool = False
    pagination_identity: str | None = None
    event_identity: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.account_binding, "account_binding"),
            (self.ledger_identity, "ledger_identity"),
            (self.source_identity, "source_identity"),
            (self.source_provider, "source_provider"),
            (self.source_record_id, "source_record_id"),
            (self.accounting_policy_version, "accounting_policy_version"),
        ):
            identifier(value, name)
        digest(self.source_response_digest, "source_response_digest")
        source_at = timestamp(self.source_timestamp, "source_timestamp")
        effective_at = timestamp(self.effective_at, "effective_at")
        acquired_at = timestamp(self.acquired_at, "acquired_at")
        if source_at > acquired_at:
            raise FinancialDataValidationError("source timestamp is after acquisition")
        if effective_at > acquired_at + timedelta(minutes=10):
            raise FinancialDataValidationError("effective timestamp is unreasonably future-dated")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("currency is unsupported")
        if not isinstance(self.payload, Mapping):
            raise FinancialDataValidationError("payload must be a mapping")
        reject_secret_bearing(self.payload)
        if self.source_truncated and self.source_complete:
            raise FinancialDataValidationError("truncated source cannot be complete")
        if self.pagination_identity is not None:
            identifier(self.pagination_identity, "pagination_identity")
        normalized = _validate_payload(self.event_type, self.payload)
        object.__setattr__(self, "payload", normalized)
        computed = canonical_digest(self.canonical_value(include_identity=False))
        if self.event_identity and self.event_identity != computed:
            raise FinancialDataValidationError("event identity mismatch")
        object.__setattr__(self, "event_identity", computed)

    def canonical_value(self, *, include_identity: bool = True) -> dict[str, object]:
        result = {
            "account_binding": self.account_binding,
            "accounting_policy_version": self.accounting_policy_version,
            "acquired_at": self.acquired_at.isoformat(),
            "currency": self.currency,
            "effective_at": self.effective_at.isoformat(),
            "event_type": self.event_type.value,
            "ledger_identity": self.ledger_identity,
            "pagination_identity": self.pagination_identity,
            "payload": canonical_value(self.payload),
            "source_complete": self.source_complete,
            "source_identity": self.source_identity,
            "source_provider": self.source_provider,
            "source_record_id": self.source_record_id,
            "source_response_digest": self.source_response_digest,
            "source_timestamp": self.source_timestamp.isoformat(),
            "source_truncated": self.source_truncated,
        }
        if include_identity:
            result["event_identity"] = self.event_identity
        return result


def _validate_payload(
    event_type: PortfolioLedgerEventType, payload: Mapping[str, object]
) -> dict[str, object]:
    allowed: dict[PortfolioLedgerEventType, frozenset[str]] = {
        PortfolioLedgerEventType.ACCOUNT_OPENING_BALANCE: frozenset({"cash"}),
        PortfolioLedgerEventType.CASH_DEPOSIT: frozenset({"amount"}),
        PortfolioLedgerEventType.CASH_WITHDRAWAL: frozenset({"amount"}),
        PortfolioLedgerEventType.BUY_FILL: frozenset(
            {
                "symbol", "instrument_type", "provider_order_id", "client_order_id",
                "provider_execution_id", "quantity", "fill_price", "gross_consideration",
                "fees", "taxes", "net_cash_impact", "settlement_date",
            }
        ),
        PortfolioLedgerEventType.SELL_FILL: frozenset(
            {
                "symbol", "instrument_type", "provider_order_id", "client_order_id",
                "provider_execution_id", "quantity", "fill_price", "gross_proceeds",
                "fees", "taxes", "net_proceeds", "settlement_date",
            }
        ),
        PortfolioLedgerEventType.DIVIDEND: frozenset({"amount", "symbol"}),
        PortfolioLedgerEventType.INTEREST: frozenset({"amount"}),
        PortfolioLedgerEventType.FEE: frozenset({"amount", "symbol"}),
        PortfolioLedgerEventType.TAX_WITHHOLDING: frozenset({"amount", "symbol"}),
        PortfolioLedgerEventType.STOCK_SPLIT: frozenset(
            {"symbol", "instrument_type", "numerator", "denominator"}
        ),
        PortfolioLedgerEventType.REVERSE_SPLIT: frozenset(
            {"symbol", "instrument_type", "numerator", "denominator"}
        ),
        PortfolioLedgerEventType.CASH_ADJUSTMENT: frozenset(
            {"amount", "reason_code", "proposal_event_identity", "approval_id", "approval_digest"}
        ),
        PortfolioLedgerEventType.POSITION_TRANSFER_IN: frozenset(
            {"symbol", "instrument_type", "quantity", "total_basis"}
        ),
        PortfolioLedgerEventType.POSITION_TRANSFER_OUT: frozenset(
            {"symbol", "instrument_type", "quantity"}
        ),
        PortfolioLedgerEventType.BROKER_CORRECTION: frozenset(
            {"amount", "reason_code", "affected_field"}
        ),
        PortfolioLedgerEventType.VALUATION_OBSERVATION: frozenset(
            {"valuation_identity"}
        ),
        PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_PROPOSED: frozenset(
            {"amount", "reason_code", "affected_fields", "evidence_digest", "proposal_identity"}
        ),
        PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_APPROVED: frozenset(
            {"proposal_identity", "approval_id", "approval_digest"}
        ),
        PortfolioLedgerEventType.ACCOUNTING_PERIOD_CLOSED: frozenset(
            {"period_start", "period_end", "close_identity"}
        ),
        PortfolioLedgerEventType.ACCOUNTING_PERIOD_REOPENED: frozenset(
            {"period_start", "period_end", "close_identity", "reason_code", "approval_id",
             "approval_digest"}
        ),
    }
    keys = frozenset(payload)
    if not keys <= allowed[event_type]:
        raise FinancialDataValidationError("payload contains fields not allowed for event type")
    result = dict(payload)
    amount_events = {
        PortfolioLedgerEventType.CASH_DEPOSIT,
        PortfolioLedgerEventType.CASH_WITHDRAWAL,
        PortfolioLedgerEventType.DIVIDEND,
        PortfolioLedgerEventType.INTEREST,
        PortfolioLedgerEventType.FEE,
        PortfolioLedgerEventType.TAX_WITHHOLDING,
    }
    if event_type is PortfolioLedgerEventType.ACCOUNT_OPENING_BALANCE:
        result["cash"] = decimal_text(result.get("cash"), "cash", nonnegative=False)
    if event_type in amount_events:
        result["amount"] = decimal_text(result.get("amount"), "amount", positive=True)
    if event_type in {PortfolioLedgerEventType.BUY_FILL, PortfolioLedgerEventType.SELL_FILL}:
        required = {
            "symbol", "instrument_type", "provider_order_id", "provider_execution_id",
            "quantity", "fill_price", "fees", "taxes",
        }
        if not required <= keys:
            raise FinancialDataValidationError("fill payload is incomplete")
        result["symbol"] = symbol(result["symbol"])
        if result["instrument_type"] not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("instrument type is unsupported")
        for name in ("provider_order_id", "provider_execution_id"):
            result[name] = identifier(result[name], name)
        if result.get("client_order_id") is not None:
            result["client_order_id"] = identifier(result["client_order_id"], "client_order_id")
        for name in ("quantity", "fill_price"):
            result[name] = decimal_text(result[name], name, positive=True)
        for name in ("fees", "taxes"):
            result[name] = decimal_text(result[name], name)
        quantity = Decimal(str(result["quantity"]))
        price = Decimal(str(result["fill_price"]))
        gross = quantity * price
        if event_type is PortfolioLedgerEventType.BUY_FILL:
            declared = decimal_text(result.get("gross_consideration"), "gross_consideration")
            net = decimal_text(result.get("net_cash_impact"), "net_cash_impact")
            expected_net = gross + Decimal(str(result["fees"])) + Decimal(str(result["taxes"]))
        else:
            declared = decimal_text(result.get("gross_proceeds"), "gross_proceeds")
            net = decimal_text(result.get("net_proceeds"), "net_proceeds")
            expected_net = gross - Decimal(str(result["fees"])) - Decimal(str(result["taxes"]))
        if Decimal(str(declared)) != gross or Decimal(str(net)) != expected_net:
            raise FinancialDataValidationError("fill cash arithmetic is inconsistent")
        if result.get("settlement_date") is not None:
            try:
                date.fromisoformat(str(result["settlement_date"]))
            except ValueError:
                raise FinancialDataValidationError("settlement_date is invalid") from None
    if event_type in {
        PortfolioLedgerEventType.STOCK_SPLIT,
        PortfolioLedgerEventType.REVERSE_SPLIT,
    }:
        result["symbol"] = symbol(result.get("symbol"))
        if result.get("instrument_type") not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("instrument type is unsupported")
        result["numerator"] = decimal_text(result.get("numerator"), "numerator", positive=True)
        result["denominator"] = decimal_text(
            result.get("denominator"), "denominator", positive=True
        )
        ratio = Decimal(str(result["numerator"])) / Decimal(str(result["denominator"]))
        if (
            event_type is PortfolioLedgerEventType.STOCK_SPLIT
            and ratio <= 1
            or event_type is PortfolioLedgerEventType.REVERSE_SPLIT
            and ratio >= 1
        ):
            raise FinancialDataValidationError("split ratio is inconsistent")
    if event_type in {
        PortfolioLedgerEventType.POSITION_TRANSFER_IN,
        PortfolioLedgerEventType.POSITION_TRANSFER_OUT,
    }:
        result["symbol"] = symbol(result.get("symbol"))
        if result.get("instrument_type") not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("instrument type is unsupported")
        result["quantity"] = decimal_text(result.get("quantity"), "quantity", positive=True)
        if event_type is PortfolioLedgerEventType.POSITION_TRANSFER_IN:
            result["total_basis"] = decimal_text(result.get("total_basis"), "total_basis")
    if event_type in {
        PortfolioLedgerEventType.CASH_ADJUSTMENT,
        PortfolioLedgerEventType.BROKER_CORRECTION,
        PortfolioLedgerEventType.RECONCILIATION_ADJUSTMENT_PROPOSED,
    }:
        result["amount"] = decimal_text(
            result.get("amount"), "amount", nonnegative=False
        )
        result["reason_code"] = identifier(result.get("reason_code"), "reason_code")
    return result


@dataclass(frozen=True, slots=True)
class PortfolioLedgerEntry:
    ledger_version: int
    account_binding: str
    ledger_identity: str
    sequence: int
    event: PortfolioLedgerEvent
    previous_entry_hash: str
    entry_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioPositionLot:
    lot_identity: str
    account_binding: str
    symbol: str
    instrument_type: str
    acquisition_event_identity: str
    acquisition_timestamp: datetime
    original_quantity: str
    remaining_quantity: str
    unit_cost: str
    allocated_acquisition_fees: str
    total_original_basis: str
    remaining_basis: str
    currency: str


@dataclass(frozen=True, slots=True)
class RealizedGainLossRecord:
    record_identity: str
    sale_event_identity: str
    symbol: str
    consumed_lot_identity: str
    quantity: str
    lot_cost_basis: str
    allocated_disposal_fees: str
    gross_proceeds: str
    net_proceeds: str
    realized_gain_loss: str
    holding_period: HoldingPeriod
    accounting_policy_version: str


@dataclass(frozen=True, slots=True)
class UnrealizedGainLossRecord:
    symbol: str
    open_lot_basis: str
    current_price: str
    market_value: str
    unrealized_gain_loss: str
    valuation_identity: str
    valued_at: datetime
    completeness_status: CompletenessStatus


@dataclass(frozen=True, slots=True)
class PortfolioAccountingState:
    account_binding: str
    opening_cash: str
    current_cash: str
    settled_cash: str | None
    unsettled_cash: str | None
    total_external_contributions: str
    total_external_withdrawals: str
    net_external_cash_flow: str
    position_quantities: tuple[tuple[str, str], ...]
    open_lots: tuple[PortfolioPositionLot, ...]
    cost_basis_by_symbol: tuple[tuple[str, str], ...]
    total_portfolio_cost_basis: str
    cumulative_dividends: str
    cumulative_interest: str
    cumulative_fees: str
    cumulative_withholding: str
    cumulative_realized_gain_loss: str
    realized_records: tuple[RealizedGainLossRecord, ...]
    unresolved_activity_count: int
    history_complete: bool
    last_processed_sequence: int
    ledger_chain_head: str
    policy_version: str
    state_digest: str = ""

    def __post_init__(self) -> None:
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "state_digest"}
        )
        if self.state_digest and self.state_digest != computed:
            raise FinancialDataValidationError("accounting state digest mismatch")
        object.__setattr__(self, "state_digest", computed)


@dataclass(frozen=True, slots=True)
class PositionValuation:
    symbol: str
    quantity: str
    price: str | None
    market_value: str | None
    price_timestamp: datetime | None
    stale: bool


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    account_binding: str
    valuation_timestamp: datetime
    source_timestamp: datetime
    acquired_at: datetime
    portfolio_snapshot_identity: str
    market_data_identity: str | None
    cash_value: str
    positions: tuple[PositionValuation, ...]
    total_equity: str | None
    unpriced_positions: tuple[str, ...]
    stale_price_symbols: tuple[str, ...]
    completeness_status: CompletenessStatus
    valuation_identity: str = ""

    def __post_init__(self) -> None:
        timestamp(self.valuation_timestamp, "valuation_timestamp")
        timestamp(self.source_timestamp, "source_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        decimal_text(self.cash_value, "cash_value", nonnegative=False)
        if self.total_equity is not None:
            decimal_text(self.total_equity, "total_equity", nonnegative=False)
        if self.completeness_status is CompletenessStatus.COMPLETE and (
            self.unpriced_positions or self.stale_price_symbols or self.total_equity is None
        ):
            raise FinancialDataValidationError("complete valuation contains incomplete prices")
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "valuation_identity"}
        )
        if self.valuation_identity and self.valuation_identity != computed:
            raise FinancialDataValidationError("valuation identity mismatch")
        object.__setattr__(self, "valuation_identity", computed)


@dataclass(frozen=True, slots=True)
class PortfolioPerformancePeriod:
    period_start: datetime
    period_end: datetime
    beginning_equity: str
    ending_equity: str
    external_contributions: str
    external_withdrawals: str
    net_external_cash_flow: str
    investment_profit_loss: str
    realized_gain_loss: str
    unrealized_gain_loss: str
    dividends: str
    interest: str
    fees: str
    time_weighted_return: str | None
    money_weighted_return: str | None
    money_weighted_unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class BenchmarkPerformance:
    benchmark_identity: str
    beginning_timestamp: datetime
    ending_timestamp: datetime
    benchmark_return: str
    excess_return: str
    completeness_status: CompletenessStatus
    stale: bool


@dataclass(frozen=True, slots=True)
class PortfolioPerformanceReport:
    account_binding: str
    period: PortfolioPerformancePeriod
    benchmark: BenchmarkPerformance | None
    history_complete: bool
    lifetime_claim: bool
    completeness_status: CompletenessStatus
    report_digest: str = ""

    def __post_init__(self) -> None:
        if self.lifetime_claim and not self.history_complete:
            raise FinancialDataValidationError("incomplete history cannot be lifetime performance")
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "report_digest"}
        )
        if self.report_digest and self.report_digest != computed:
            raise FinancialDataValidationError("performance report digest mismatch")
        object.__setattr__(self, "report_digest", computed)


@dataclass(frozen=True, slots=True)
class PortfolioLedgerDiscrepancy:
    code: LedgerDiscrepancyCode
    subject: str
    ledger_value: str | None = None
    broker_value: str | None = None
    material: bool = True
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioLedgerReconciliationReport:
    account_binding: str
    snapshot_identity: str
    state_digest: str
    discrepancies: tuple[PortfolioLedgerDiscrepancy, ...]
    created_at: datetime
    report_digest: str = ""

    def __post_init__(self) -> None:
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "report_digest"}
        )
        object.__setattr__(self, "report_digest", computed)


@dataclass(frozen=True, slots=True)
class AccountingAdjustmentApproval:
    approval_id: str
    account_binding: str
    proposal_identity: str
    reason_code: str
    amount: str
    affected_fields: tuple[str, ...]
    evidence_digest: str
    operator_identity: str
    approved_at: datetime
    approval_digest: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.approval_id, "approval_id"),
            (self.account_binding, "account_binding"),
            (self.proposal_identity, "proposal_identity"),
            (self.reason_code, "reason_code"),
            (self.operator_identity, "operator_identity"),
        ):
            identifier(value, name)
        decimal_text(self.amount, "amount", nonnegative=False)
        digest(self.evidence_digest, "evidence_digest")
        timestamp(self.approved_at, "approved_at")
        if tuple(sorted(set(self.affected_fields))) != self.affected_fields:
            raise FinancialDataValidationError("affected_fields must be sorted and unique")
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "approval_digest"}
        )
        if self.approval_digest and self.approval_digest != computed:
            raise FinancialDataValidationError("approval digest mismatch")
        object.__setattr__(self, "approval_digest", computed)


@dataclass(frozen=True, slots=True)
class AccountingPeriodClose:
    account_binding: str
    period_start: datetime
    period_end: datetime
    first_sequence: int
    last_sequence: int
    chain_head: str
    opening_state_digest: str
    closing_state_digest: str
    valuation_digest: str
    performance_report_digest: str
    unresolved_discrepancy_count: int
    completeness_status: CompletenessStatus
    approval_identity: str
    closed_at: datetime
    close_identity: str = ""

    def __post_init__(self) -> None:
        if self.period_end <= self.period_start:
            raise FinancialDataValidationError("accounting period is invalid")
        if self.first_sequence < 1 or self.last_sequence < self.first_sequence:
            raise FinancialDataValidationError("accounting period sequence range is invalid")
        for value, name in (
            (self.chain_head, "chain_head"),
            (self.opening_state_digest, "opening_state_digest"),
            (self.closing_state_digest, "closing_state_digest"),
            (self.valuation_digest, "valuation_digest"),
            (self.performance_report_digest, "performance_report_digest"),
        ):
            digest(value, name)
        if self.unresolved_discrepancy_count:
            raise FinancialDataValidationError("period cannot close with discrepancies")
        if self.completeness_status is not CompletenessStatus.COMPLETE:
            raise FinancialDataValidationError("period cannot close with incomplete evidence")
        computed = canonical_digest(
            {key: value for key, value in asdict(self).items() if key != "close_identity"}
        )
        object.__setattr__(self, "close_identity", computed)
