"""Governed Public.com equity execution with exact, single-use authorization.

This module deliberately does not implement ``FinancialDataProvider``.  Read operations
and trading mutations share one closed Public transport, but execution requires immutable
proposal, preflight, approval, and submission-intent records.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
import json
import re
import time
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from .models import (
    FinancialDataAuthenticationError,
    FinancialDataRateLimitError,
    FinancialDataTransportError,
    FinancialDataValidationError,
)
from .transport import BoundedRateLimiter, CredentialResolver


PUBLIC_EXECUTION_PROVIDER_ID = "public_equity_execution"
PUBLIC_EXECUTION_ADAPTER_VERSION = "sigil-public-equity-execution-v1"
PUBLIC_API_SECRET_ENVIRONMENT_VARIABLE = "SIGIL_PUBLIC_API_SECRET"
PUBLIC_ALLOWED_HOSTS = ("api.public.com",)
PUBLIC_EXECUTION_SUPPORTED_OPERATIONS = (
    "account_portfolio",
    "cancel_approved_order",
    "get_order",
    "list_accounts",
    "preflight_equity_order",
    "quotes",
    "submit_approved_equity_order",
)
PUBLIC_APPROVAL_SCOPE = "submit_one_public_equity_order"
PUBLIC_CANCELLATION_APPROVAL_SCOPE = "cancel_one_public_equity_order"
PUBLIC_TOKEN_VALIDITY_MINUTES = 15
PUBLIC_MIN_TOKEN_VALIDITY_MINUTES = 5
PUBLIC_MAX_TOKEN_VALIDITY_MINUTES = 30
PUBLIC_MAX_INSTRUMENTS = 25
PUBLIC_PROPOSAL_SCHEMA_VERSION = 1
PUBLIC_FORBIDDEN_CAPABILITIES = (
    "bonds",
    "crypto",
    "extended_hours",
    "hosted_mcp",
    "margin",
    "multi_leg_orders",
    "options",
    "order_replacement",
    "recurring_orders",
    "short_selling",
    "tax_lot_instructions",
    "transfers",
    "treasuries",
)

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,8}(?:[.-][A-Z0-9]{1,5})?$", re.ASCII)
_SAFE_TEXT_RE = re.compile(r"^[\x20-\x7e]+$", re.ASCII)
_TOKEN_PATH = "/userapiauthservice/personal/access-tokens"
_LIST_ACCOUNTS_PATH = "/userapigateway/trading/account"
_PORTFOLIO_PATH_RE = re.compile(r"^/userapigateway/trading/[^/]+/portfolio/v2$")
_QUOTES_PATH_RE = re.compile(r"^/userapigateway/marketdata/[^/]+/quotes$")
_PREFLIGHT_PATH_RE = re.compile(
    r"^/userapigateway/trading/[^/]+/preflight/single-leg$"
)
_ORDER_COLLECTION_PATH_RE = re.compile(r"^/userapigateway/trading/[^/]+/order$")
_ORDER_ITEM_PATH_RE = re.compile(
    r"^/userapigateway/trading/[^/]+/order/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialDataValidationError(f"{name} must be timezone-aware")


def _require_text(value: str, name: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or _SAFE_TEXT_RE.fullmatch(value) is None
    ):
        raise FinancialDataValidationError(f"{name} must be bounded printable ASCII")
    return value


def _decimal_string(value: object, name: str, *, allow_zero: bool = False) -> str:
    if isinstance(value, (float, bool)) or not isinstance(value, (str, Decimal)):
        raise FinancialDataValidationError(f"{name} must be a canonical decimal string")
    text = str(value)
    if _DECIMAL_RE.fullmatch(text) is None:
        raise FinancialDataValidationError(f"{name} must be a canonical decimal string")
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise FinancialDataValidationError(f"{name} is invalid") from None
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise FinancialDataValidationError(f"{name} must be positive")
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return canonical


def normalize_public_account_id(value: str) -> str:
    if not isinstance(value, str) or _ACCOUNT_RE.fullmatch(value) is None:
        raise FinancialDataValidationError("Public account ID is invalid")
    return value


def normalize_public_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise FinancialDataValidationError("symbol must be text")
    normalized = value.upper()
    if _SYMBOL_RE.fullmatch(normalized) is None:
        raise FinancialDataValidationError("symbol must be a bounded ASCII equity symbol")
    return normalized


def protected_account_binding(account_id: str) -> str:
    return f"public-account-sha256:{sha256(normalize_public_account_id(account_id).encode()).hexdigest()}"


def _uuid(value: str, name: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise FinancialDataValidationError(f"{name} must be an RFC 4122 UUID") from None
    if str(parsed) != value.lower() or parsed.variant != UUID(value).variant:
        raise FinancialDataValidationError(f"{name} must be a canonical RFC 4122 UUID")
    return str(parsed)


def _public_body(proposal: "GovernedEquityTradeProposal", *, order_id: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "instrument": {"symbol": proposal.symbol, "type": "EQUITY"},
        "orderSide": proposal.side,
        "orderType": proposal.order_type,
        "expiration": {"timeInForce": "DAY"},
        "useMargin": False,
        "equityMarketSession": "CORE",
    }
    if proposal.quantity is not None:
        body["quantity"] = proposal.quantity
    else:
        body["amount"] = proposal.notional_amount
    if proposal.limit_price is not None:
        body["limitPrice"] = proposal.limit_price
    if order_id is not None:
        body["orderId"] = _uuid(order_id, "order_id")
    return body


@dataclass(frozen=True, slots=True)
class PublicExecutionPolicy:
    maximum_notional_per_order: str
    maximum_whole_share_quantity: str
    maximum_fractional_notional: str
    allowed_symbols: tuple[str, ...]
    proposal_lifetime_seconds: int
    approval_lifetime_seconds: int
    preflight_lifetime_seconds: int
    portfolio_freshness_seconds: int
    allow_buys: bool = True
    allow_sells: bool = True
    allow_market_orders: bool = True
    allow_limit_orders: bool = True
    allow_fractional_orders: bool = True
    require_preflight: bool = True
    require_explicit_approval: bool = True
    require_cash_only_execution: bool = True
    prohibit_extended_hours: bool = True
    prohibit_shorts: bool = True
    prohibit_margin: bool = True
    prohibit_options: bool = True
    prohibit_crypto: bool = True
    prohibit_bonds: bool = True
    prohibit_replacement: bool = True

    def __post_init__(self) -> None:
        for name in (
            "maximum_notional_per_order",
            "maximum_whole_share_quantity",
            "maximum_fractional_notional",
        ):
            object.__setattr__(self, name, _decimal_string(getattr(self, name), name))
        if not self.allowed_symbols:
            raise FinancialDataValidationError("execution policy requires allowed symbols")
        normalized = tuple(sorted({normalize_public_symbol(item) for item in self.allowed_symbols}))
        object.__setattr__(self, "allowed_symbols", normalized)
        for name in (
            "proposal_lifetime_seconds",
            "approval_lifetime_seconds",
            "preflight_lifetime_seconds",
            "portfolio_freshness_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86_400:
                raise FinancialDataValidationError(f"{name} must be bounded and positive")
        required = (
            self.require_preflight,
            self.require_explicit_approval,
            self.require_cash_only_execution,
            self.prohibit_extended_hours,
            self.prohibit_shorts,
            self.prohibit_margin,
            self.prohibit_options,
            self.prohibit_crypto,
            self.prohibit_bonds,
            self.prohibit_replacement,
        )
        if not all(required):
            raise FinancialDataValidationError("execution safety policy cannot be disabled")

    @property
    def policy_hash(self) -> str:
        return _digest({item.name: getattr(self, item.name) for item in fields(self)})

    def validate(self, proposal: "GovernedEquityTradeProposal") -> None:
        if proposal.symbol not in self.allowed_symbols:
            raise FinancialDataValidationError("symbol is not allowed by execution policy")
        if proposal.side == "BUY" and not self.allow_buys:
            raise FinancialDataValidationError("buy orders are disabled by execution policy")
        if proposal.side == "SELL" and not self.allow_sells:
            raise FinancialDataValidationError("sell orders are disabled by execution policy")
        if proposal.order_type == "MARKET" and not self.allow_market_orders:
            raise FinancialDataValidationError("market orders are disabled by execution policy")
        if proposal.order_type == "LIMIT" and not self.allow_limit_orders:
            raise FinancialDataValidationError("limit orders are disabled by execution policy")
        if proposal.quantity is not None:
            quantity = Decimal(proposal.quantity)
            if quantity > Decimal(self.maximum_whole_share_quantity):
                raise FinancialDataValidationError("quantity exceeds execution policy")
            if quantity != quantity.to_integral_value() and not self.allow_fractional_orders:
                raise FinancialDataValidationError("fractional orders are disabled")
        if proposal.notional_amount is not None:
            amount = Decimal(proposal.notional_amount)
            if amount > Decimal(self.maximum_fractional_notional):
                raise FinancialDataValidationError("fractional notional exceeds execution policy")
            if amount > Decimal(self.maximum_notional_per_order):
                raise FinancialDataValidationError("notional exceeds execution policy")
        if proposal.limit_price is not None and proposal.quantity is not None:
            notional = Decimal(proposal.limit_price) * Decimal(proposal.quantity)
            if notional > Decimal(self.maximum_notional_per_order):
                raise FinancialDataValidationError("estimated limit notional exceeds execution policy")


@dataclass(frozen=True, slots=True)
class GovernedEquityTradeProposal:
    schema_version: int
    proposal_id: str
    provider_id: str
    account_id: str = field(repr=False)
    account_binding: str
    symbol: str
    instrument_type: str
    side: str
    order_type: str
    time_in_force: str
    quantity: str | None
    notional_amount: str | None
    limit_price: str | None
    purpose: str
    created_at: datetime
    expires_at: datetime
    correlation_id: str
    policy_snapshot: str
    requested_by: str
    use_margin: bool
    market_session: str

    def __post_init__(self) -> None:
        material = {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "account_binding": self.account_binding,
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "side": self.side,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "quantity": self.quantity,
            "notional_amount": self.notional_amount,
            "limit_price": self.limit_price,
            "purpose": self.purpose,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "correlation_id": self.correlation_id,
            "policy_snapshot": self.policy_snapshot,
            "requested_by": self.requested_by,
            "use_margin": self.use_margin,
            "market_session": self.market_session,
        }
        if (
            self.schema_version != PUBLIC_PROPOSAL_SCHEMA_VERSION
            or self.provider_id != PUBLIC_EXECUTION_PROVIDER_ID
            or self.account_binding != protected_account_binding(self.account_id)
            or self.proposal_id != f"public-proposal-{_digest(material)}"
        ):
            raise FinancialDataValidationError("trade proposal integrity validation failed")

    @classmethod
    def create(
        cls,
        *,
        policy: PublicExecutionPolicy,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str | Decimal | None = None,
        notional_amount: str | Decimal | None = None,
        limit_price: str | Decimal | None = None,
        purpose: str,
        created_at: datetime,
        correlation_id: str,
        requested_by: str,
        instrument_type: str = "EQUITY",
        time_in_force: str = "DAY",
        use_margin: bool = False,
        market_session: str = "CORE",
    ) -> "GovernedEquityTradeProposal":
        _require_aware(created_at, "created_at")
        account_id = normalize_public_account_id(account_id)
        symbol = normalize_public_symbol(symbol)
        if instrument_type != "EQUITY" or side not in {"BUY", "SELL"}:
            raise FinancialDataValidationError("only BUY or SELL EQUITY proposals are supported")
        if order_type not in {"MARKET", "LIMIT"} or time_in_force != "DAY":
            raise FinancialDataValidationError("unsupported Public order terms")
        if use_margin is not False or market_session != "CORE":
            raise FinancialDataValidationError("margin and non-core sessions are forbidden")
        if (quantity is None) == (notional_amount is None):
            raise FinancialDataValidationError("exactly one quantity or notional amount is required")
        normalized_quantity = (
            _decimal_string(quantity, "quantity") if quantity is not None else None
        )
        normalized_amount = (
            _decimal_string(notional_amount, "notional_amount")
            if notional_amount is not None
            else None
        )
        normalized_limit = (
            _decimal_string(limit_price, "limit_price") if limit_price is not None else None
        )
        if order_type == "MARKET" and normalized_limit is not None:
            raise FinancialDataValidationError("market orders reject limit price")
        if order_type == "LIMIT" and normalized_limit is None:
            raise FinancialDataValidationError("limit orders require limit price")
        if side == "SELL" and normalized_amount is not None:
            raise FinancialDataValidationError("notional sell orders are forbidden")
        values: dict[str, object] = {
            "schema_version": PUBLIC_PROPOSAL_SCHEMA_VERSION,
            "provider_id": PUBLIC_EXECUTION_PROVIDER_ID,
            "account_binding": protected_account_binding(account_id),
            "symbol": symbol,
            "instrument_type": instrument_type,
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "quantity": normalized_quantity,
            "notional_amount": normalized_amount,
            "limit_price": normalized_limit,
            "purpose": _require_text(purpose, "purpose", 500),
            "created_at": created_at.isoformat(),
            "expires_at": (
                created_at + timedelta(seconds=policy.proposal_lifetime_seconds)
            ).isoformat(),
            "correlation_id": _require_text(correlation_id, "correlation_id", 255),
            "policy_snapshot": policy.policy_hash,
            "requested_by": _require_text(requested_by, "requested_by", 255),
            "use_margin": False,
            "market_session": "CORE",
        }
        proposal_id = f"public-proposal-{_digest(values)}"
        proposal = cls(
            proposal_id=proposal_id,
            account_id=account_id,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=policy.proposal_lifetime_seconds),
            **{key: value for key, value in values.items() if key not in {"created_at", "expires_at"}},  # type: ignore[arg-type]
        )
        policy.validate(proposal)
        return proposal

    @property
    def proposal_hash(self) -> str:
        return self.proposal_id.removeprefix("public-proposal-")

    def ensure_current(self, now: datetime) -> None:
        _require_aware(now, "now")
        if now >= self.expires_at:
            raise FinancialDataValidationError("trade proposal is expired")


@dataclass(frozen=True, slots=True)
class PublicPortfolioSnapshot:
    account_binding: str
    acquired_at: datetime
    cash_only_buying_power: str
    long_equity_positions: Mapping[str, str]
    response_hash: str

    @classmethod
    def from_payload(
        cls, account_id: str, payload: object, acquired_at: datetime
    ) -> "PublicPortfolioSnapshot":
        _require_aware(acquired_at, "acquired_at")
        if not isinstance(payload, dict) or payload.get("accountId") != account_id:
            raise FinancialDataValidationError("Public portfolio response is malformed")
        buying_power = payload.get("buyingPower")
        positions = payload.get("positions")
        if not isinstance(buying_power, dict) or not isinstance(positions, list):
            raise FinancialDataValidationError("Public portfolio response is malformed")
        cash = _decimal_string(
            buying_power.get("cashOnlyBuyingPower"),
            "cash_only_buying_power",
            allow_zero=True,
        )
        holdings: dict[str, str] = {}
        for item in positions:
            if not isinstance(item, dict):
                raise FinancialDataValidationError("Public portfolio positions are malformed")
            instrument = item.get("instrument")
            if not isinstance(instrument, dict) or instrument.get("type") != "EQUITY":
                continue
            symbol = normalize_public_symbol(instrument.get("symbol"))
            quantity = _decimal_string(item.get("quantity"), "position quantity", allow_zero=True)
            holdings[symbol] = quantity
        return cls(
            account_binding=protected_account_binding(account_id),
            acquired_at=acquired_at,
            cash_only_buying_power=cash,
            long_equity_positions=MappingProxyType(dict(sorted(holdings.items()))),
            response_hash=_digest(payload),
        )


@dataclass(frozen=True, slots=True)
class PublicPreflightRecord:
    preflight_id: str
    proposal_id: str
    proposal_hash: str
    provider_id: str
    account_binding: str
    submitted_body_hash: str
    estimated_cost: str | None
    estimated_proceeds: str | None
    buying_power_requirement: str
    regulatory_fees: object
    provider_timestamp: str | None
    acquired_at: datetime
    response_hash: str
    adapter_version: str
    expires_at: datetime
    preflight_hash: str

    def __post_init__(self) -> None:
        material = {
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "account_binding": self.account_binding,
            "submitted_body_hash": self.submitted_body_hash,
            "response_hash": self.response_hash,
            "acquired_at": self.acquired_at.isoformat(),
        }
        expected = _digest(material)
        if (
            self.provider_id != PUBLIC_EXECUTION_PROVIDER_ID
            or self.adapter_version != PUBLIC_EXECUTION_ADAPTER_VERSION
            or self.preflight_hash != expected
            or self.preflight_id != f"public-preflight-{expected}"
        ):
            raise FinancialDataValidationError("Public preflight integrity validation failed")

    @classmethod
    def create(
        cls,
        proposal: GovernedEquityTradeProposal,
        payload: object,
        acquired_at: datetime,
        policy: PublicExecutionPolicy,
    ) -> "PublicPreflightRecord":
        if not isinstance(payload, dict):
            raise FinancialDataValidationError("Public preflight response is malformed")
        if payload.get("rejectionReason") or payload.get("outcome") in {"REJECTED", "FAILURE"}:
            raise FinancialDataValidationError("Public preflight rejected")
        requirement = _decimal_string(
            payload.get("buyingPowerRequirement"),
            "buying_power_requirement",
            allow_zero=True,
        )
        estimated_cost = payload.get("estimatedCost")
        estimated_proceeds = payload.get("estimatedProceeds")
        if estimated_cost is not None:
            estimated_cost = _decimal_string(estimated_cost, "estimated_cost", allow_zero=True)
        if estimated_proceeds is not None:
            estimated_proceeds = _decimal_string(
                estimated_proceeds, "estimated_proceeds", allow_zero=True
            )
        if Decimal(requirement) > Decimal(policy.maximum_notional_per_order):
            raise FinancialDataValidationError("preflight requirement exceeds execution policy")
        body_hash = _digest(_public_body(proposal))
        response_hash = _digest(payload)
        material = {
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash,
            "account_binding": proposal.account_binding,
            "submitted_body_hash": body_hash,
            "response_hash": response_hash,
            "acquired_at": acquired_at.isoformat(),
        }
        preflight_hash = _digest(material)
        return cls(
            preflight_id=f"public-preflight-{preflight_hash}",
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.proposal_hash,
            provider_id=PUBLIC_EXECUTION_PROVIDER_ID,
            account_binding=proposal.account_binding,
            submitted_body_hash=body_hash,
            estimated_cost=estimated_cost,
            estimated_proceeds=estimated_proceeds,
            buying_power_requirement=requirement,
            regulatory_fees=payload.get("regulatoryFees"),
            provider_timestamp=payload.get("timestamp")
            if isinstance(payload.get("timestamp"), str)
            else None,
            acquired_at=acquired_at,
            response_hash=response_hash,
            adapter_version=PUBLIC_EXECUTION_ADAPTER_VERSION,
            expires_at=acquired_at
            + timedelta(seconds=policy.preflight_lifetime_seconds),
            preflight_hash=preflight_hash,
        )

    def ensure_current(self, now: datetime) -> None:
        if now >= self.expires_at:
            raise FinancialDataValidationError("Public preflight is expired")


@dataclass(frozen=True, slots=True)
class GovernedTradeApproval:
    approval_id: str
    provider_id: str
    proposal_id: str
    proposal_hash: str
    preflight_id: str
    preflight_hash: str
    account_binding: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    quantity: str | None
    notional_amount: str | None
    limit_price: str | None
    maximum_authorized_notional: str
    approved_at: datetime
    expires_at: datetime
    approved_by: str
    single_use_nonce: str = field(repr=False)
    approval_scope: str
    correlation_id: str
    market_session: str
    use_margin: bool
    approval_hash: str

    def __post_init__(self) -> None:
        values = {
            "provider_id": self.provider_id,
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "preflight_id": self.preflight_id,
            "preflight_hash": self.preflight_hash,
            "account_binding": self.account_binding,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "quantity": self.quantity,
            "notional_amount": self.notional_amount,
            "limit_price": self.limit_price,
            "maximum_authorized_notional": self.maximum_authorized_notional,
            "approved_at": self.approved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approved_by": self.approved_by,
            "single_use_nonce_hash": sha256(self.single_use_nonce.encode()).hexdigest(),
            "approval_scope": self.approval_scope,
            "correlation_id": self.correlation_id,
            "market_session": self.market_session,
            "use_margin": self.use_margin,
        }
        expected = _digest(values)
        if (
            self.provider_id != PUBLIC_EXECUTION_PROVIDER_ID
            or self.approval_scope != PUBLIC_APPROVAL_SCOPE
            or self.approval_hash != expected
            or self.approval_id != f"public-approval-{expected}"
        ):
            raise FinancialDataValidationError("trade approval integrity validation failed")

    @classmethod
    def create(
        cls,
        *,
        proposal: GovernedEquityTradeProposal,
        preflight: PublicPreflightRecord,
        maximum_authorized_notional: str | Decimal,
        approved_at: datetime,
        approved_by: str,
        single_use_nonce: str,
        policy: PublicExecutionPolicy,
        approval_scope: str = PUBLIC_APPROVAL_SCOPE,
    ) -> "GovernedTradeApproval":
        _require_aware(approved_at, "approved_at")
        if preflight.proposal_id != proposal.proposal_id:
            raise FinancialDataValidationError("approval preflight does not match proposal")
        if approval_scope != PUBLIC_APPROVAL_SCOPE:
            raise FinancialDataValidationError("approval scope must authorize one exact order")
        nonce = _require_text(single_use_nonce, "single_use_nonce", 128)
        maximum = _decimal_string(maximum_authorized_notional, "maximum_authorized_notional")
        if Decimal(maximum) > Decimal(policy.maximum_notional_per_order):
            raise FinancialDataValidationError("approval maximum exceeds execution policy")
        values: dict[str, object] = {
            "provider_id": PUBLIC_EXECUTION_PROVIDER_ID,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash,
            "preflight_id": preflight.preflight_id,
            "preflight_hash": preflight.preflight_hash,
            "account_binding": proposal.account_binding,
            "symbol": proposal.symbol,
            "side": proposal.side,
            "order_type": proposal.order_type,
            "time_in_force": proposal.time_in_force,
            "quantity": proposal.quantity,
            "notional_amount": proposal.notional_amount,
            "limit_price": proposal.limit_price,
            "maximum_authorized_notional": maximum,
            "approved_at": approved_at.isoformat(),
            "expires_at": (
                approved_at + timedelta(seconds=policy.approval_lifetime_seconds)
            ).isoformat(),
            "approved_by": _require_text(approved_by, "approved_by", 255),
            "single_use_nonce_hash": sha256(nonce.encode()).hexdigest(),
            "approval_scope": approval_scope,
            "correlation_id": proposal.correlation_id,
            "market_session": proposal.market_session,
            "use_margin": proposal.use_margin,
        }
        approval_hash = _digest(values)
        return cls(
            approval_id=f"public-approval-{approval_hash}",
            approved_at=approved_at,
            expires_at=approved_at + timedelta(seconds=policy.approval_lifetime_seconds),
            single_use_nonce=nonce,
            approval_hash=approval_hash,
            **{
                key: value
                for key, value in values.items()
                if key not in {"approved_at", "expires_at", "single_use_nonce_hash"}
            },  # type: ignore[arg-type]
        )


class PublicExecutionState(StrEnum):
    PROPOSED = "PROPOSED"
    PREFLIGHTED = "PREFLIGHTED"
    APPROVED = "APPROVED"
    SUBMISSION_INTENT_RECORDED = "SUBMISSION_INTENT_RECORDED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN_RECONCILIATION_REQUIRED = "UNKNOWN_RECONCILIATION_REQUIRED"


_ALLOWED_TRANSITIONS = {
    PublicExecutionState.APPROVED: {PublicExecutionState.SUBMISSION_INTENT_RECORDED},
    PublicExecutionState.SUBMISSION_INTENT_RECORDED: {
        PublicExecutionState.SUBMITTED,
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
    },
    PublicExecutionState.SUBMITTED: {
        PublicExecutionState.ACKNOWLEDGED,
        PublicExecutionState.PARTIALLY_FILLED,
        PublicExecutionState.FILLED,
        PublicExecutionState.CANCELLED,
        PublicExecutionState.CANCELLATION_REQUESTED,
        PublicExecutionState.REJECTED,
        PublicExecutionState.EXPIRED,
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
    },
    PublicExecutionState.ACKNOWLEDGED: {
        PublicExecutionState.PARTIALLY_FILLED,
        PublicExecutionState.FILLED,
        PublicExecutionState.CANCELLED,
        PublicExecutionState.REJECTED,
        PublicExecutionState.EXPIRED,
        PublicExecutionState.CANCELLATION_REQUESTED,
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
    },
    PublicExecutionState.PARTIALLY_FILLED: {
        PublicExecutionState.FILLED,
        PublicExecutionState.CANCELLATION_REQUESTED,
        PublicExecutionState.CANCELLED,
        PublicExecutionState.REJECTED,
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
    },
    PublicExecutionState.CANCELLATION_REQUESTED: {
        PublicExecutionState.CANCELLED,
        PublicExecutionState.FILLED,
        PublicExecutionState.PARTIALLY_FILLED,
        PublicExecutionState.REJECTED,
        PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
    },
    PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED: {
        PublicExecutionState.ACKNOWLEDGED,
        PublicExecutionState.PARTIALLY_FILLED,
        PublicExecutionState.FILLED,
        PublicExecutionState.CANCELLATION_REQUESTED,
        PublicExecutionState.CANCELLED,
        PublicExecutionState.REJECTED,
        PublicExecutionState.EXPIRED,
    },
}
_TERMINAL_STATES = {
    PublicExecutionState.FILLED,
    PublicExecutionState.CANCELLED,
    PublicExecutionState.REJECTED,
    PublicExecutionState.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class PublicSubmissionIntent:
    intent_id: str
    order_id: str
    proposal_id: str
    proposal_hash: str
    preflight_id: str
    preflight_hash: str
    approval_id: str
    approval_hash: str
    account_binding: str
    body_hash: str
    recorded_at: datetime
    correlation_id: str


@dataclass(frozen=True, slots=True)
class PublicOrderExecution:
    order_id: str
    intent_id: str
    account_binding: str
    state: PublicExecutionState
    updated_at: datetime
    response_hash: str | None = None

    def transition(
        self, state: PublicExecutionState, *, at: datetime, response_hash: str | None = None
    ) -> "PublicOrderExecution":
        if state not in _ALLOWED_TRANSITIONS.get(self.state, set()):
            raise FinancialDataValidationError("invalid Public execution state transition")
        return PublicOrderExecution(
            order_id=self.order_id,
            intent_id=self.intent_id,
            account_binding=self.account_binding,
            state=state,
            updated_at=at,
            response_hash=response_hash,
        )


@dataclass(frozen=True, slots=True)
class PublicCancellationApproval:
    approval_id: str
    order_id: str
    account_binding: str
    approved_at: datetime
    expires_at: datetime
    approved_by: str
    single_use_nonce: str = field(repr=False)
    approval_scope: str = PUBLIC_CANCELLATION_APPROVAL_SCOPE

    def __post_init__(self) -> None:
        material = {
            "order_id": self.order_id,
            "account_binding": self.account_binding,
            "approved_at": self.approved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approved_by": self.approved_by,
            "nonce_hash": sha256(self.single_use_nonce.encode()).hexdigest(),
            "scope": self.approval_scope,
        }
        if (
            self.approval_scope != PUBLIC_CANCELLATION_APPROVAL_SCOPE
            or self.approval_id != f"public-cancel-approval-{_digest(material)}"
        ):
            raise FinancialDataValidationError(
                "cancellation approval integrity validation failed"
            )

    @classmethod
    def create(
        cls,
        *,
        order_id: str,
        account_id: str,
        approved_at: datetime,
        expires_at: datetime,
        approved_by: str,
        single_use_nonce: str,
    ) -> "PublicCancellationApproval":
        _require_aware(approved_at, "approved_at")
        _require_aware(expires_at, "expires_at")
        if expires_at <= approved_at:
            raise FinancialDataValidationError("cancellation approval expiry is invalid")
        material = {
            "order_id": _uuid(order_id, "order_id"),
            "account_binding": protected_account_binding(account_id),
            "approved_at": approved_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "approved_by": _require_text(approved_by, "approved_by"),
            "nonce_hash": sha256(
                _require_text(single_use_nonce, "single_use_nonce").encode()
            ).hexdigest(),
            "scope": PUBLIC_CANCELLATION_APPROVAL_SCOPE,
        }
        return cls(
            approval_id=f"public-cancel-approval-{_digest(material)}",
            order_id=material["order_id"],  # type: ignore[arg-type]
            account_binding=material["account_binding"],  # type: ignore[arg-type]
            approved_at=approved_at,
            expires_at=expires_at,
            approved_by=material["approved_by"],  # type: ignore[arg-type]
            single_use_nonce=single_use_nonce,
        )


@dataclass(frozen=True, slots=True)
class PublicAuditEvidence:
    evidence_id: str
    event_type: str
    artifact_id: str
    artifact_hash: str
    occurred_at: datetime
    adapter_version: str
    correlation_id: str
    prior_state: str | None
    new_state: str | None
    safe_facts: tuple[tuple[str, str], ...]


class PublicExecutionJournal:
    """Injected in-memory journal with collision and single-use enforcement.

    This journal is process-lifetime only and is not crash-durable. Production callers
    must replace it with a durable governed implementation of the same methods before
    unattended or production trading. The adapter never silently creates durable storage.
    """

    def __init__(self) -> None:
        self._intents: dict[str, PublicSubmissionIntent] = {}
        self._order_to_intent: dict[str, str] = {}
        self._used_approvals: set[str] = set()
        self._used_cancellation_approvals: set[str] = set()
        self._executions: dict[str, PublicOrderExecution] = {}
        self._evidence: list[PublicAuditEvidence] = []
        self._retryable_after_reconciliation: set[str] = set()

    def record_proposal(self, proposal: GovernedEquityTradeProposal) -> None:
        del proposal

    def record_portfolio_snapshot(
        self, proposal: GovernedEquityTradeProposal, snapshot: PublicPortfolioSnapshot
    ) -> None:
        del proposal, snapshot

    def record_preflight(
        self, proposal: GovernedEquityTradeProposal, preflight: PublicPreflightRecord
    ) -> None:
        del proposal, preflight

    def record_intent(
        self, intent: PublicSubmissionIntent, approval: GovernedTradeApproval
    ) -> None:
        if approval.approval_id in self._used_approvals:
            raise FinancialDataValidationError("trade approval has already been consumed")
        existing = self._order_to_intent.get(intent.order_id)
        if existing is not None and self._intents[existing] != intent:
            raise FinancialDataValidationError("order ID is already bound to another payload")
        self._intents[intent.intent_id] = intent
        self._order_to_intent[intent.order_id] = intent.intent_id
        self._used_approvals.add(approval.approval_id)

    def intent(self, intent_id: str) -> PublicSubmissionIntent:
        try:
            return self._intents[intent_id]
        except KeyError:
            raise FinancialDataValidationError("unknown Public submission intent") from None

    def save_execution(self, execution: PublicOrderExecution) -> None:
        self._executions[execution.order_id] = execution

    def execution(self, order_id: str) -> PublicOrderExecution:
        try:
            return self._executions[order_id]
        except KeyError:
            raise FinancialDataValidationError("unknown Public order") from None

    def consume_cancellation(self, approval: PublicCancellationApproval) -> None:
        if approval.approval_id in self._used_cancellation_approvals:
            raise FinancialDataValidationError("cancellation approval has already been consumed")
        self._used_cancellation_approvals.add(approval.approval_id)

    def record_cancellation_intent(
        self,
        execution: PublicOrderExecution,
        approval: PublicCancellationApproval,
        at: datetime,
    ) -> None:
        del execution, approval, at

    def record_reconciliation_attempt(
        self, execution: PublicOrderExecution, at: datetime
    ) -> None:
        del execution, at

    def record_reconciliation_result(
        self,
        execution: PublicOrderExecution,
        *,
        broker_order_exists: bool,
        at: datetime,
    ) -> None:
        del at
        if broker_order_exists:
            self._retryable_after_reconciliation.discard(execution.intent_id)
        else:
            self._retryable_after_reconciliation.add(execution.intent_id)

    def record_submission_not_started(
        self, execution: PublicOrderExecution, *, reason: str, at: datetime
    ) -> None:
        del reason, at
        self._retryable_after_reconciliation.add(execution.intent_id)

    def submission_retry_permitted(self, intent_id: str) -> bool:
        return intent_id in self._retryable_after_reconciliation

    def append_evidence(self, evidence: PublicAuditEvidence) -> None:
        if any(item.evidence_id == evidence.evidence_id for item in self._evidence):
            return
        self._evidence.append(evidence)

    def evidence(self) -> tuple[PublicAuditEvidence, ...]:
        return tuple(self._evidence)


@dataclass(frozen=True, slots=True)
class PublicTransportResult:
    payload: object
    status: int
    response_hash: str
    response_bytes: int
    endpoint_identity: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _default_opener(request: Request, timeout: float):  # type: ignore[no-untyped-def]
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


class _PublicGovernedTransport:
    """Public-specific HTTPS transport with no arbitrary method or path API."""

    def __init__(
        self,
        *,
        opener: Callable[[Request, float], object] = _default_opener,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not 0.1 <= timeout_seconds <= 30 or not 1 <= max_response_bytes <= 10_000_000:
            raise FinancialDataValidationError("Public transport bounds are invalid")
        self._opener = opener
        self._timeout = float(timeout_seconds)
        self._max_bytes = max_response_bytes

    def authenticate(self, secret: str, validity_minutes: int) -> PublicTransportResult:
        if not PUBLIC_MIN_TOKEN_VALIDITY_MINUTES <= validity_minutes <= PUBLIC_MAX_TOKEN_VALIDITY_MINUTES:
            raise FinancialDataValidationError("Public token validity is outside the allowlist")
        return self._send(
            "POST",
            _TOKEN_PATH,
            body={"validityInMinutes": validity_minutes, "secret": secret},
            token=None,
            endpoint_identity=_TOKEN_PATH,
        )

    def list_accounts(self, token: str) -> PublicTransportResult:
        return self._send("GET", _LIST_ACCOUNTS_PATH, token=token)

    def account_portfolio(self, account_id: str, token: str) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        return self._send(
            "GET",
            f"/userapigateway/trading/{account_id}/portfolio/v2",
            token=token,
            endpoint_identity="/userapigateway/trading/{accountId}/portfolio/v2",
            expected=_PORTFOLIO_PATH_RE,
        )

    def quotes(
        self, account_id: str, instruments: tuple[tuple[str, str], ...], token: str
    ) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        body = {
            "instruments": [
                {"symbol": normalize_public_symbol(symbol), "type": instrument_type}
                for symbol, instrument_type in instruments
            ]
        }
        return self._send(
            "POST",
            f"/userapigateway/marketdata/{account_id}/quotes",
            token=token,
            body=body,
            endpoint_identity="/userapigateway/marketdata/{accountId}/quotes",
            expected=_QUOTES_PATH_RE,
        )

    def preflight(
        self, account_id: str, body: Mapping[str, object], token: str
    ) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        return self._send(
            "POST",
            f"/userapigateway/trading/{account_id}/preflight/single-leg",
            token=token,
            body=body,
            endpoint_identity="/userapigateway/trading/{accountId}/preflight/single-leg",
            expected=_PREFLIGHT_PATH_RE,
        )

    def _submit_order_json(
        self, account_id: str, body: Mapping[str, object], token: str
    ) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        return self._send(
            "POST",
            f"/userapigateway/trading/{account_id}/order",
            token=token,
            body=body,
            endpoint_identity="/userapigateway/trading/{accountId}/order",
            expected=_ORDER_COLLECTION_PATH_RE,
        )

    def get_order(self, account_id: str, order_id: str, token: str) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        order_id = _uuid(order_id, "order_id")
        return self._send(
            "GET",
            f"/userapigateway/trading/{account_id}/order/{order_id}",
            token=token,
            endpoint_identity="/userapigateway/trading/{accountId}/order/{orderId}",
            expected=_ORDER_ITEM_PATH_RE,
            allow_not_found=True,
        )

    def cancel_order(
        self, account_id: str, order_id: str, token: str
    ) -> PublicTransportResult:
        account_id = normalize_public_account_id(account_id)
        order_id = _uuid(order_id, "order_id")
        return self._send(
            "DELETE",
            f"/userapigateway/trading/{account_id}/order/{order_id}",
            token=token,
            endpoint_identity="/userapigateway/trading/{accountId}/order/{orderId}",
            expected=_ORDER_ITEM_PATH_RE,
            allow_empty=True,
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        body: Mapping[str, object] | None = None,
        endpoint_identity: str | None = None,
        expected: re.Pattern[str] | None = None,
        allow_empty: bool = False,
        allow_not_found: bool = False,
    ) -> PublicTransportResult:
        if method not in {"GET", "POST", "DELETE"}:
            raise FinancialDataValidationError("unsupported Public transport operation")
        if expected is not None and expected.fullmatch(path) is None:
            raise FinancialDataValidationError("Public endpoint path is not allowlisted")
        url = f"https://api.public.com{path}"
        split = urlsplit(url)
        if (
            split.scheme != "https"
            or split.hostname not in PUBLIC_ALLOWED_HOSTS
            or split.username
            or split.password
            or split.query
            or split.fragment
        ):
            raise FinancialDataValidationError("Public endpoint is not allowlisted")
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = _canonical_bytes(body)
            if len(data) > 64_000:
                raise FinancialDataValidationError("Public request body exceeds maximum bytes")
        request = Request(url, headers=headers, data=data, method=method)
        try:
            response = self._opener(request, self._timeout)
            status = int(response.getcode())  # type: ignore[attr-defined]
            response_headers = response.headers  # type: ignore[attr-defined]
            raw = response.read(self._max_bytes + 1)  # type: ignore[attr-defined]
        except HTTPError as exc:
            status = exc.code
            response_headers = exc.headers
            raw = b""
        except (URLError, TimeoutError, OSError):
            raise FinancialDataTransportError("Public transport failed") from None
        if status in {401, 403}:
            raise FinancialDataAuthenticationError("Public authentication rejected")
        if status == 429:
            raise FinancialDataRateLimitError("Public rate limit exceeded")
        if status == 404 and allow_not_found:
            return PublicTransportResult(
                payload={"notFound": True},
                status=404,
                response_hash=sha256(b"").hexdigest(),
                response_bytes=0,
                endpoint_identity=endpoint_identity or path,
            )
        if not 200 <= status < 300:
            raise FinancialDataTransportError(f"Public returned HTTP {status}")
        if len(raw) > self._max_bytes:
            raise FinancialDataTransportError("Public response exceeds maximum bytes")
        if not raw and allow_empty:
            payload: object = {}
        else:
            content_type = response_headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise FinancialDataValidationError("Public response must be application/json")
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise FinancialDataValidationError("Public returned malformed JSON") from None
        return PublicTransportResult(
            payload=payload,
            status=status,
            response_hash=sha256(raw).hexdigest(),
            response_bytes=len(raw),
            endpoint_identity=endpoint_identity or path,
        )


class PublicAccessTokenManager:
    """Private, in-memory Public access token lifecycle."""

    __slots__ = (
        "_resolver",
        "_transport",
        "_clock",
        "_validity",
        "_early_expiry",
        "_limiter",
        "_token",
        "_expires",
    )

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver,
        transport: _PublicGovernedTransport,
        monotonic: Callable[[], float] = time.monotonic,
        validity_minutes: int = PUBLIC_TOKEN_VALIDITY_MINUTES,
        early_expiry_seconds: float = 30.0,
        rate_limiter: BoundedRateLimiter | None = None,
    ) -> None:
        if not PUBLIC_MIN_TOKEN_VALIDITY_MINUTES <= validity_minutes <= PUBLIC_MAX_TOKEN_VALIDITY_MINUTES:
            raise FinancialDataValidationError("Public token validity is outside the allowlist")
        if not 0 <= early_expiry_seconds < validity_minutes * 60:
            raise FinancialDataValidationError("Public token early-expiry margin is invalid")
        self._resolver = credential_resolver
        self._transport = transport
        self._clock = monotonic
        self._validity = validity_minutes
        self._early_expiry = float(early_expiry_seconds)
        self._limiter = rate_limiter or BoundedRateLimiter(2, 60.0, monotonic=monotonic)
        self._token: str | None = None
        self._expires = 0.0

    def __repr__(self) -> str:
        return f"{type(self).__name__}(token_present={self.token_present})"

    @property
    def token_present(self) -> bool:
        return self._token is not None and self._clock() < self._expires

    def available(self) -> bool:
        return self._resolver.available(
            PUBLIC_EXECUTION_PROVIDER_ID, PUBLIC_API_SECRET_ENVIRONMENT_VARIABLE
        )

    def get(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._expires:
            return self._token
        secret = self._resolver.resolve(
            PUBLIC_EXECUTION_PROVIDER_ID, PUBLIC_API_SECRET_ENVIRONMENT_VARIABLE
        )
        self._limiter.acquire()
        result = self._transport.authenticate(secret, self._validity)
        payload = result.payload
        if not isinstance(payload, dict):
            raise FinancialDataAuthenticationError("Public token response is malformed")
        token = payload.get("accessToken")
        if not isinstance(token, str) or not token.strip() or len(token) > 16_384:
            raise FinancialDataAuthenticationError("Public token response is malformed")
        self._token = token
        self._expires = now + self._validity * 60 - self._early_expiry
        return token

    def invalidate(self) -> None:
        self._token = None
        self._expires = 0.0


@dataclass(frozen=True, slots=True)
class PublicExecutionHealth:
    provider_id: str
    allowed_hosts: tuple[str, ...]
    supported_operations: tuple[str, ...]
    runtime_secret_available: bool
    token_present: bool
    rate_limit_remaining: int
    policy_configured: bool
    execution_enabled: bool


class PublicEquityExecutionProvider:
    """Closed Public equity execution workflow; no autonomous scheduling."""

    def __init__(
        self,
        *,
        token_manager: PublicAccessTokenManager,
        transport: _PublicGovernedTransport,
        policy: PublicExecutionPolicy | None,
        journal: PublicExecutionJournal,
        wall_clock: Callable[[], datetime] = _utc_now,
        rate_limiter: BoundedRateLimiter | None = None,
        order_id_factory: Callable[[], object] = uuid4,
    ) -> None:
        self._tokens = token_manager
        self._transport = transport
        self._policy = policy
        self._journal = journal
        self._clock = wall_clock
        self._limiter = rate_limiter or BoundedRateLimiter(30, 60.0)
        self._order_id_factory = order_id_factory

    def health(self) -> PublicExecutionHealth:
        configured = self._policy is not None
        return PublicExecutionHealth(
            provider_id=PUBLIC_EXECUTION_PROVIDER_ID,
            allowed_hosts=PUBLIC_ALLOWED_HOSTS,
            supported_operations=PUBLIC_EXECUTION_SUPPORTED_OPERATIONS,
            runtime_secret_available=self._tokens.available(),
            token_present=self._tokens.token_present,
            rate_limit_remaining=self._limiter.remaining(),
            policy_configured=configured,
            execution_enabled=configured,
        )

    def list_accounts(self) -> object:
        result = self._read(self._transport.list_accounts)
        if not isinstance(result.payload, dict) or not isinstance(
            result.payload.get("accounts"), list
        ):
            raise FinancialDataValidationError("Public account-list response is malformed")
        return result.payload

    def account_portfolio(self, account_id: str) -> PublicPortfolioSnapshot:
        account_id = normalize_public_account_id(account_id)
        result = self._read(lambda token: self._transport.account_portfolio(account_id, token))
        return PublicPortfolioSnapshot.from_payload(account_id, result.payload, self._clock())

    def quotes(self, account_id: str, symbols: tuple[str, ...]) -> object:
        if not isinstance(symbols, tuple) or not 1 <= len(symbols) <= PUBLIC_MAX_INSTRUMENTS:
            raise FinancialDataValidationError("quotes require 1 to 25 equity symbols")
        canonical = tuple(sorted({normalize_public_symbol(item) for item in symbols}))
        if len(canonical) != len(symbols):
            raise FinancialDataValidationError("duplicate quote symbols are forbidden")
        result = self._read(
            lambda token: self._transport.quotes(
                account_id, tuple((symbol, "EQUITY") for symbol in canonical), token
            )
        )
        if not isinstance(result.payload, dict) or not isinstance(
            result.payload.get("quotes"), list
        ):
            raise FinancialDataValidationError("Public quotes response is malformed")
        return result.payload

    def preflight(self, proposal: GovernedEquityTradeProposal) -> PublicPreflightRecord:
        policy = self._require_policy()
        now = self._clock()
        proposal.ensure_current(now)
        policy.validate(proposal)
        self._journal.record_proposal(proposal)
        self._evidence(
            "proposal_created",
            proposal.proposal_id,
            proposal.proposal_hash,
            proposal.correlation_id,
            None,
            PublicExecutionState.PROPOSED.value,
        )
        self._evidence(
            "proposal_policy_validated",
            proposal.proposal_id,
            policy.policy_hash,
            proposal.correlation_id,
            PublicExecutionState.PROPOSED.value,
            PublicExecutionState.PROPOSED.value,
        )
        portfolio = self.account_portfolio(proposal.account_id)
        self._validate_portfolio_safety(proposal, portfolio, now)
        self._journal.record_portfolio_snapshot(proposal, portfolio)
        body = _public_body(proposal)
        body_hash = _digest(body)
        self._evidence(
            "preflight_requested",
            proposal.proposal_id,
            body_hash,
            proposal.correlation_id,
            PublicExecutionState.PROPOSED.value,
            PublicExecutionState.PROPOSED.value,
        )
        try:
            result = self._authenticated(
                lambda token: self._transport.preflight(proposal.account_id, body, token)
            )
        except Exception:
            self._evidence(
                "preflight_rejected",
                proposal.proposal_id,
                body_hash,
                proposal.correlation_id,
                PublicExecutionState.PROPOSED.value,
                PublicExecutionState.REJECTED.value,
            )
            raise
        record = PublicPreflightRecord.create(proposal, result.payload, self._clock(), policy)
        if Decimal(record.buying_power_requirement) > Decimal(
            portfolio.cash_only_buying_power
        ):
            raise FinancialDataValidationError("insufficient cash-only buying power")
        self._journal.record_preflight(proposal, record)
        self._evidence(
            "preflight_accepted",
            record.preflight_id,
            record.preflight_hash,
            proposal.correlation_id,
            None,
            PublicExecutionState.PREFLIGHTED.value,
        )
        return record

    def submit_approved_equity_order(
        self,
        proposal: GovernedEquityTradeProposal,
        preflight: PublicPreflightRecord,
        approval: GovernedTradeApproval,
    ) -> PublicOrderExecution:
        policy = self._require_policy()
        now = self._clock()
        self._validate_approval(proposal, preflight, approval, now, policy)
        self._evidence(
            "approval_recorded",
            approval.approval_id,
            approval.approval_hash,
            proposal.correlation_id,
            PublicExecutionState.PREFLIGHTED.value,
            PublicExecutionState.APPROVED.value,
        )
        order_id = _uuid(str(self._order_id_factory()), "order_id")
        body = _public_body(proposal, order_id=order_id)
        intent_hash = _digest(
            {
                "order_id": order_id,
                "proposal": proposal.proposal_id,
                "preflight": preflight.preflight_id,
                "approval": approval.approval_id,
                "body_hash": _digest(body),
            }
        )
        intent = PublicSubmissionIntent(
            intent_id=f"public-intent-{intent_hash}",
            order_id=order_id,
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.proposal_hash,
            preflight_id=preflight.preflight_id,
            preflight_hash=preflight.preflight_hash,
            approval_id=approval.approval_id,
            approval_hash=approval.approval_hash,
            account_binding=proposal.account_binding,
            body_hash=_digest(body),
            recorded_at=now,
            correlation_id=proposal.correlation_id,
        )
        self._journal.record_intent(intent, approval)
        execution = PublicOrderExecution(
            order_id=order_id,
            intent_id=intent.intent_id,
            account_binding=proposal.account_binding,
            state=PublicExecutionState.APPROVED,
            updated_at=now,
        ).transition(PublicExecutionState.SUBMISSION_INTENT_RECORDED, at=now)
        self._journal.save_execution(execution)
        self._evidence(
            "submission_intent_recorded",
            intent.intent_id,
            intent_hash,
            proposal.correlation_id,
            PublicExecutionState.APPROVED.value,
            execution.state.value,
        )
        return self._send_intent(proposal.account_id, intent, body, execution)

    def retry_ambiguous_submission(
        self, *, account_id: str, intent_id: str, proposal: GovernedEquityTradeProposal
    ) -> PublicOrderExecution:
        intent = self._journal.intent(intent_id)
        account_id = normalize_public_account_id(account_id)
        if not compare_digest(protected_account_binding(account_id), intent.account_binding):
            raise FinancialDataValidationError(
                "retry account binding does not match submission intent"
            )
        if intent.proposal_id != proposal.proposal_id:
            raise FinancialDataValidationError("submission intent does not match proposal")
        body = _public_body(proposal, order_id=intent.order_id)
        if _digest(body) != intent.body_hash:
            raise FinancialDataValidationError("submission intent body collision")
        execution = self._journal.execution(intent.order_id)
        if execution.state not in {
            PublicExecutionState.SUBMISSION_INTENT_RECORDED,
            PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED,
        }:
            raise FinancialDataValidationError("submission is not eligible for ambiguous retry")
        if not self._journal.submission_retry_permitted(intent_id):
            raise FinancialDataValidationError(
                "submission requires reconciliation proving no broker order exists"
            )
        return self._send_intent(account_id, intent, body, execution)

    def get_order(self, *, account_id: str, order_id: str) -> PublicOrderExecution:
        order_id = _uuid(order_id, "order_id")
        execution = self._journal.execution(order_id)
        if execution.account_binding != protected_account_binding(account_id):
            raise FinancialDataValidationError("order account binding mismatch")
        self._journal.record_reconciliation_attempt(execution, self._clock())
        result = self._read(lambda token: self._transport.get_order(account_id, order_id, token))
        if result.status == 404:
            if execution.state != PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED:
                execution = execution.transition(
                    PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=self._clock()
                )
            self._journal.record_reconciliation_result(
                execution, broker_order_exists=False, at=self._clock()
            )
        else:
            if not isinstance(result.payload, dict):
                raise FinancialDataValidationError("Public order response is malformed")
            state = self._map_order_status(result.payload.get("status"))
            if state != execution.state:
                execution = execution.transition(
                    state, at=self._clock(), response_hash=result.response_hash
                )
            self._journal.record_reconciliation_result(
                execution, broker_order_exists=True, at=self._clock()
            )
        self._journal.save_execution(execution)
        self._evidence(
            "order_status_observed",
            execution.intent_id,
            execution.response_hash or sha256(b"").hexdigest(),
            self._journal.intent(execution.intent_id).correlation_id,
            None,
            execution.state.value,
        )
        return execution

    def cancel_approved_order(
        self, *, account_id: str, order_id: str, approval: PublicCancellationApproval
    ) -> PublicOrderExecution:
        now = self._clock()
        order_id = _uuid(order_id, "order_id")
        execution = self._journal.execution(order_id)
        binding = protected_account_binding(account_id)
        if (
            approval.order_id != order_id
            or approval.account_binding != binding
            or execution.account_binding != binding
            or approval.approval_scope != PUBLIC_CANCELLATION_APPROVAL_SCOPE
            or now >= approval.expires_at
        ):
            raise FinancialDataValidationError("cancellation approval does not match order")
        if execution.state in _TERMINAL_STATES:
            raise FinancialDataValidationError("terminal Public orders cannot be cancelled")
        prior_state = execution.state
        self._journal.consume_cancellation(approval)
        self._journal.record_cancellation_intent(execution, approval, now)
        try:
            self._authenticated(
                lambda token: self._transport.cancel_order(account_id, order_id, token)
            )
        except FinancialDataTransportError:
            execution = execution.transition(
                PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=self._clock()
            )
            self._journal.save_execution(execution)
            raise
        execution = execution.transition(PublicExecutionState.CANCELLATION_REQUESTED, at=now)
        self._journal.save_execution(execution)
        intent = self._journal.intent(execution.intent_id)
        self._evidence(
            "cancellation_requested",
            approval.approval_id,
            approval.approval_id.removeprefix("public-cancel-approval-"),
            intent.correlation_id,
            prior_state.value,
            execution.state.value,
        )
        return execution

    def _send_intent(
        self,
        account_id: str,
        intent: PublicSubmissionIntent,
        body: Mapping[str, object],
        execution: PublicOrderExecution,
    ) -> PublicOrderExecution:
        try:
            result = self._authenticated(
                lambda token: self._transport._submit_order_json(account_id, body, token)
            )
        except (FinancialDataAuthenticationError, FinancialDataRateLimitError) as exc:
            self._journal.record_submission_not_started(
                execution,
                reason=type(exc).__name__,
                at=self._clock(),
            )
            raise
        except FinancialDataTransportError:
            if execution.state == PublicExecutionState.SUBMISSION_INTENT_RECORDED:
                execution = execution.transition(
                    PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=self._clock()
                )
                self._journal.save_execution(execution)
            raise
        if not isinstance(result.payload, dict) or result.payload.get("orderId") != intent.order_id:
            execution = execution.transition(
                PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED, at=self._clock()
            )
            self._journal.save_execution(execution)
            raise FinancialDataValidationError("Public submission response is malformed")
        if execution.state == PublicExecutionState.SUBMISSION_INTENT_RECORDED:
            execution = execution.transition(
                PublicExecutionState.SUBMITTED,
                at=self._clock(),
                response_hash=result.response_hash,
            )
        elif execution.state == PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED:
            execution = execution.transition(
                PublicExecutionState.ACKNOWLEDGED,
                at=self._clock(),
                response_hash=result.response_hash,
            )
        self._journal.save_execution(execution)
        self._evidence(
            "order_submitted",
            intent.intent_id,
            intent.intent_id.removeprefix("public-intent-"),
            intent.correlation_id,
            PublicExecutionState.SUBMISSION_INTENT_RECORDED.value,
            execution.state.value,
        )
        return execution

    def _read(self, operation: Callable[[str], PublicTransportResult]) -> PublicTransportResult:
        return self._authenticated(operation)

    def _authenticated(
        self, operation: Callable[[str], PublicTransportResult]
    ) -> PublicTransportResult:
        self._limiter.acquire()
        return operation(self._tokens.get())

    def _require_policy(self) -> PublicExecutionPolicy:
        if self._policy is None:
            raise FinancialDataValidationError("explicit Public execution policy is required")
        return self._policy

    def _validate_portfolio_safety(
        self,
        proposal: GovernedEquityTradeProposal,
        snapshot: PublicPortfolioSnapshot,
        now: datetime,
    ) -> None:
        policy = self._require_policy()
        if snapshot.account_binding != proposal.account_binding:
            raise FinancialDataValidationError("portfolio account binding mismatch")
        if (now - snapshot.acquired_at).total_seconds() > policy.portfolio_freshness_seconds:
            raise FinancialDataValidationError("Public portfolio snapshot is stale")
        if proposal.side == "SELL":
            held = snapshot.long_equity_positions.get(proposal.symbol)
            if held is None or proposal.quantity is None or Decimal(proposal.quantity) > Decimal(held):
                raise FinancialDataValidationError("sell exceeds observed long equity holdings")

    @staticmethod
    def _validate_approval(
        proposal: GovernedEquityTradeProposal,
        preflight: PublicPreflightRecord,
        approval: GovernedTradeApproval,
        now: datetime,
        policy: PublicExecutionPolicy,
    ) -> None:
        if not isinstance(approval, GovernedTradeApproval):
            raise FinancialDataValidationError("exact structured trade approval is required")
        proposal.__post_init__()
        preflight.__post_init__()
        approval.__post_init__()
        proposal.ensure_current(now)
        preflight.ensure_current(now)
        policy.validate(proposal)
        expected = (
            preflight.provider_id == PUBLIC_EXECUTION_PROVIDER_ID
            and preflight.proposal_id == proposal.proposal_id
            and preflight.proposal_hash == proposal.proposal_hash
            and preflight.account_binding == proposal.account_binding
            and preflight.submitted_body_hash == _digest(_public_body(proposal))
            and approval.provider_id == PUBLIC_EXECUTION_PROVIDER_ID
            and approval.proposal_id == proposal.proposal_id
            and approval.proposal_hash == proposal.proposal_hash
            and approval.preflight_id == preflight.preflight_id
            and approval.preflight_hash == preflight.preflight_hash
            and approval.account_binding == proposal.account_binding
            and approval.symbol == proposal.symbol
            and approval.side == proposal.side
            and approval.order_type == proposal.order_type
            and approval.time_in_force == proposal.time_in_force
            and approval.quantity == proposal.quantity
            and approval.notional_amount == proposal.notional_amount
            and approval.limit_price == proposal.limit_price
            and approval.market_session == proposal.market_session
            and approval.use_margin == proposal.use_margin
            and approval.approval_scope == PUBLIC_APPROVAL_SCOPE
            and approval.correlation_id == proposal.correlation_id
            and now < approval.expires_at
            and Decimal(preflight.buying_power_requirement)
            <= Decimal(approval.maximum_authorized_notional)
        )
        if not expected:
            raise FinancialDataValidationError("trade approval does not bind exact execution")

    @staticmethod
    def _map_order_status(value: object) -> PublicExecutionState:
        mapping = {
            "NEW": PublicExecutionState.ACKNOWLEDGED,
            "PENDING": PublicExecutionState.ACKNOWLEDGED,
            "PARTIALLY_FILLED": PublicExecutionState.PARTIALLY_FILLED,
            "FILLED": PublicExecutionState.FILLED,
            "CANCELLED": PublicExecutionState.CANCELLED,
            "CANCELED": PublicExecutionState.CANCELLED,
            "REJECTED": PublicExecutionState.REJECTED,
            "EXPIRED": PublicExecutionState.EXPIRED,
        }
        try:
            return mapping[value]  # type: ignore[index]
        except (KeyError, TypeError):
            raise FinancialDataValidationError("unsupported Public order status") from None

    def _evidence(
        self,
        event_type: str,
        artifact_id: str,
        artifact_hash: str,
        correlation_id: str,
        prior_state: str | None,
        new_state: str | None,
    ) -> None:
        if _DIGEST_RE.fullmatch(artifact_hash) is None:
            raise FinancialDataValidationError("audit artifact hash is invalid")
        occurred_at = self._clock()
        material = {
            "event_type": event_type,
            "artifact_id": artifact_id,
            "artifact_hash": artifact_hash,
            "occurred_at": occurred_at.isoformat(),
            "correlation_id": correlation_id,
            "prior_state": prior_state,
            "new_state": new_state,
        }
        self._journal.append_evidence(
            PublicAuditEvidence(
                evidence_id=f"public-audit-{_digest(material)}",
                event_type=event_type,
                artifact_id=artifact_id,
                artifact_hash=artifact_hash,
                occurred_at=occurred_at,
                adapter_version=PUBLIC_EXECUTION_ADAPTER_VERSION,
                correlation_id=correlation_id,
                prior_state=prior_state,
                new_state=new_state,
                safe_facts=(),
            )
        )
