"""Immutable safety and policy models for autonomous Alpaca paper execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def decimal_value(value: object, name: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a decimal value") from None
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "positive" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return result


@dataclass(frozen=True, slots=True)
class ExecutionEnvironmentIdentity:
    application_environment: str
    broker_environment: str
    broker_base_url: str
    credential_environment: str
    broker_submission: bool
    live_execution: bool
    order_mutations: bool
    certification_mode: bool
    paper_account_authenticated: bool

    def __post_init__(self) -> None:
        identities = (
            self.application_environment,
            self.broker_environment,
            self.credential_environment,
        )
        if any(value != "paper" for value in identities):
            raise ValueError("all execution environment identities must be paper")
        if self.broker_base_url != ALPACA_PAPER_BASE_URL:
            raise ValueError("Alpaca execution base URL must be the paper endpoint")
        if self.live_execution:
            raise ValueError("live execution is permanently disabled")
        if self.broker_submission and not self.paper_account_authenticated:
            raise ValueError(
                "broker submission requires authenticated Alpaca paper account"
            )
        if self.order_mutations != self.broker_submission:
            raise ValueError(
                "paper order mutations must exactly match submission authorization"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PaperExecutionPolicy:
    maximum_order_notional: Decimal = Decimal("25.00")
    maximum_new_positions_per_cycle: int = 1
    maximum_open_positions: int = 3
    maximum_pending_entry_orders: int = 1
    maximum_deployed_capital: Decimal = Decimal("75.00")
    maximum_symbol_exposure: Decimal = Decimal("25.00")
    minimum_cash_buffer: Decimal = Decimal("100.00")
    minimum_confidence: Decimal = Decimal("0.75")
    maximum_spread_basis_points: Decimal = Decimal("50")
    minimum_average_dollar_volume: Decimal = Decimal("1000000")
    quote_freshness_seconds: int = 30
    bars_freshness_seconds: int = 900
    catalog_freshness_seconds: int = 86_400
    portfolio_freshness_seconds: int = 60
    exit_stop_loss_percent: Decimal = Decimal("5.0")
    exit_take_profit_percent: Decimal = Decimal("10.0")

    def __post_init__(self) -> None:
        positive_decimals = (
            "maximum_order_notional",
            "maximum_deployed_capital",
            "maximum_symbol_exposure",
            "minimum_cash_buffer",
            "minimum_confidence",
            "maximum_spread_basis_points",
            "minimum_average_dollar_volume",
            "exit_stop_loss_percent",
            "exit_take_profit_percent",
        )
        if any(getattr(self, name) <= 0 for name in positive_decimals):
            raise ValueError("paper policy decimal limits must be positive")
        if self.maximum_order_notional > Decimal("25.00"):
            raise ValueError("maximum order notional cannot exceed 25.00")
        if self.maximum_open_positions > 3:
            raise ValueError("maximum open positions cannot exceed 3")
        if self.maximum_deployed_capital > Decimal("75.00"):
            raise ValueError("maximum deployed capital cannot exceed 75.00")
        if self.maximum_new_positions_per_cycle != 1:
            raise ValueError("exactly one new position per cycle is permitted")
        if self.maximum_pending_entry_orders != 1:
            raise ValueError("exactly one pending entry order is permitted")
        if not Decimal("0") < self.minimum_confidence <= Decimal("1"):
            raise ValueError("minimum confidence must be within (0, 1]")
        for name in (
            "quote_freshness_seconds",
            "bars_freshness_seconds",
            "catalog_freshness_seconds",
            "portfolio_freshness_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {key: str(value) if isinstance(value, Decimal) else value
                for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class CandidateResearch:
    symbol: str
    asset_class: str
    exchange: str
    tradable: bool
    fractionable: bool
    status: str
    name: str
    quote_bid: Decimal
    quote_ask: Decimal
    quote_age_seconds: int
    bars_age_seconds: int
    average_dollar_volume: Decimal
    strategy_score: Decimal
    confidence: Decimal
    expected_setup_positive: bool
    evidence_complete: bool
    conflicting_evidence: bool = False
    leveraged_or_inverse: bool = False

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("candidate symbol is required")
        object.__setattr__(self, "symbol", symbol)
        if self.quote_bid <= 0 or self.quote_ask <= 0:
            raise ValueError("candidate quote must be positive")
        if self.quote_ask < self.quote_bid:
            raise ValueError("candidate quote is contradictory")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("candidate confidence must be within [0, 1]")

    @property
    def midpoint(self) -> Decimal:
        return (self.quote_bid + self.quote_ask) / Decimal("2")

    @property
    def spread_basis_points(self) -> Decimal:
        return ((self.quote_ask - self.quote_bid) / self.midpoint) * Decimal(
            "10000"
        )

    def score_key(self) -> tuple[Decimal, Decimal, str]:
        return (-self.strategy_score, -self.confidence, self.symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }
