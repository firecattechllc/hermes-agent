"""Health model (spec section 14): provider, model, and endpoint health are
distinct dimensions, never collapsed into one boolean. A provider can be
healthy while one endpoint has failed; a model can be unavailable while its
provider is otherwise fine.

Only fake/deterministic probes exist in this phase — nothing here contacts a
real network endpoint.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Protocol

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class EndpointHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_id: str
    status: HealthStatus
    checked_at: int = Field(..., ge=0)


class ProviderHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    status: HealthStatus
    checked_at: int = Field(..., ge=0)
    endpoints: Dict[str, EndpointHealth] = Field(default_factory=dict)

    def endpoint_status(self, endpoint_id: str) -> HealthStatus:
        record = self.endpoints.get(endpoint_id)
        return record.status if record else HealthStatus.UNKNOWN

    def best_endpoint_status(self) -> HealthStatus:
        """The best status across declared endpoints — used for failover: a
        provider with one dead endpoint and one healthy endpoint is still
        routable, it just needs to avoid the dead one.
        """
        if not self.endpoints:
            return self.status
        order = {HealthStatus.HEALTHY: 0, HealthStatus.DEGRADED: 1,
                 HealthStatus.UNHEALTHY: 2, HealthStatus.UNKNOWN: 3}
        return min((item.status for item in self.endpoints.values()), key=lambda item: order[item])


class ModelHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_key: str
    status: HealthStatus
    checked_at: int = Field(..., ge=0)


class HealthProbe(Protocol):
    """Narrow contract Runway's scorer depends on. Swap in a real probe in a
    later, separately-certified phase without touching :mod:`runway.scoring`.
    """

    def provider_health(self, provider_id: str) -> ProviderHealth: ...

    def model_health(self, model_key: str) -> ModelHealth: ...


class FakeHealthProbe:
    """Deterministic, in-memory health probe for tests and simulation. Health
    is whatever the caller injected; unknown ids report UNKNOWN (fail-closed
    posture: an unmonitored route is not assumed healthy).
    """

    def __init__(self, *, now: int) -> None:
        self._now = now
        self._provider: Dict[str, ProviderHealth] = {}
        self._model: Dict[str, ModelHealth] = {}

    def set_provider_health(
        self, provider_id: str, status: HealthStatus, *, endpoints: Dict[str, HealthStatus] | None = None
    ) -> None:
        endpoint_records = {
            endpoint_id: EndpointHealth(endpoint_id=endpoint_id, status=endpoint_status, checked_at=self._now)
            for endpoint_id, endpoint_status in (endpoints or {}).items()
        }
        self._provider[provider_id] = ProviderHealth(
            provider_id=provider_id, status=status, checked_at=self._now, endpoints=endpoint_records
        )

    def set_model_health(self, model_key: str, status: HealthStatus) -> None:
        self._model[model_key] = ModelHealth(model_key=model_key, status=status, checked_at=self._now)

    def provider_health(self, provider_id: str) -> ProviderHealth:
        return self._provider.get(provider_id) or ProviderHealth(
            provider_id=provider_id, status=HealthStatus.UNKNOWN, checked_at=self._now
        )

    def model_health(self, model_key: str) -> ModelHealth:
        return self._model.get(model_key) or ModelHealth(
            model_key=model_key, status=HealthStatus.UNKNOWN, checked_at=self._now
        )
