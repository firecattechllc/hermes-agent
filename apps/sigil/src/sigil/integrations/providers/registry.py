"""Deterministic dependency-injected provider registry."""

from __future__ import annotations

from typing import Protocol

from .models import (
    FinancialDataProviderError,
    FinancialDataProviderHealth,
    FinancialDataProviderMetadata,
    FinancialDataRequest,
    FinancialDataResponse,
)


class FinancialDataProvider(Protocol):
    @property
    def metadata(self) -> FinancialDataProviderMetadata: ...

    def acquire(self, request: FinancialDataRequest) -> FinancialDataResponse: ...

    def health(self) -> FinancialDataProviderHealth: ...


class FinancialDataProviderRegistry:
    """Explicit registry with no discovery or process-global state."""

    def __init__(self, providers: tuple[FinancialDataProvider, ...] = ()) -> None:
        self._providers: dict[str, FinancialDataProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: FinancialDataProvider) -> None:
        provider_id = provider.metadata.provider_id
        if provider_id in self._providers:
            raise FinancialDataProviderError(f"duplicate provider_id: {provider_id}")
        self._providers[provider_id] = provider

    def resolve(self, provider_id: str) -> FinancialDataProvider:
        try:
            return self._providers[provider_id]
        except (KeyError, TypeError) as exc:
            raise FinancialDataProviderError("unknown provider_id") from exc

    def list_metadata(self) -> tuple[FinancialDataProviderMetadata, ...]:
        return tuple(self._providers[key].metadata for key in sorted(self._providers))
