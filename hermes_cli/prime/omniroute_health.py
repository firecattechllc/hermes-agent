"""Aggregated health for Titan's governed OmniRoute + FreeLLMAPI routing stack.

Reuses :mod:`hermes_cli.prime.health`'s ``DependencyHealth``/``HealthReport``
vocabulary unmodified (per that module's own goal of being the one shared
health protocol) rather than inventing a parallel health taxonomy. This
module's only job is composing four independently-observed dependencies —
Hermes router reachability, the OmniRoute service itself, Titan Ollama, and
FreeLLMAPI — into one :class:`OmniRouteHealthSnapshot` that distinguishes
"everything is fine," "a remote fallback is unavailable but local inference
still works," "operating in offline-local-only mode," and "nothing can serve
a request."

The specific behavior this module exists to guarantee: a FreeLLMAPI outage
with Titan Ollama still usable reports :attr:`OperationalStatus.DEGRADED`,
never :attr:`OperationalStatus.DOWN`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from hermes_cli.prime.health import (
    DegradationLevel,
    DependencyHealth,
    HealthReport,
    LivenessState,
    ReadinessState,
)
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker, CircuitState


class OperationalStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    LOCAL_ONLY = "local_only"
    DOWN = "down"


def dependency_health_from_circuit(state: CircuitState) -> DependencyHealth:
    return {
        CircuitState.CLOSED: DependencyHealth.HEALTHY,
        CircuitState.HALF_OPEN: DependencyHealth.DEGRADED,
        CircuitState.OPEN: DependencyHealth.UNAVAILABLE,
    }[state]


def _usable(dependency: DependencyHealth) -> bool:
    return dependency in (DependencyHealth.HEALTHY, DependencyHealth.DEGRADED)


def compute_operational_status(
    *,
    titan_ollama: DependencyHealth,
    freellmapi: DependencyHealth,
    offline_local_only: bool,
) -> OperationalStatus:
    ollama_usable = _usable(titan_ollama)
    freellmapi_usable = _usable(freellmapi) and not offline_local_only

    if not ollama_usable and not freellmapi_usable:
        return OperationalStatus.DOWN
    if offline_local_only:
        return OperationalStatus.LOCAL_ONLY
    if (
        titan_ollama == DependencyHealth.HEALTHY
        and freellmapi == DependencyHealth.HEALTHY
    ):
        return OperationalStatus.HEALTHY
    return OperationalStatus.DEGRADED


@dataclass(frozen=True, slots=True)
class OmniRouteHealthSnapshot:
    observed_at: int
    hermes_router: DependencyHealth
    omniroute: DependencyHealth
    titan_ollama: DependencyHealth
    freellmapi: DependencyHealth
    offline_local_only: bool
    operational_status: OperationalStatus
    internet_dependent_degradation: bool

    def to_health_report(
        self,
        *,
        subject_identity_id: str,
        correlation_id: Optional[str] = None,
        max_age_seconds: int = 180,
    ) -> HealthReport:
        degraded = self.operational_status in (
            OperationalStatus.DEGRADED,
            OperationalStatus.LOCAL_ONLY,
        )
        ready = self.operational_status != OperationalStatus.DOWN
        alive = self.omniroute != DependencyHealth.UNAVAILABLE
        return HealthReport(
            report_id=f"omniroute_health_{self.observed_at}_{subject_identity_id}",
            subject_identity_id=subject_identity_id,
            observed_at=self.observed_at,
            expires_at=self.observed_at + max_age_seconds,
            liveness=LivenessState.ALIVE if alive else LivenessState.DEAD,
            readiness=ReadinessState.READY if ready else ReadinessState.NOT_READY,
            dependency_health={
                "hermes_router": self.hermes_router,
                "omniroute": self.omniroute,
                "titan_ollama": self.titan_ollama,
                "freellmapi": self.freellmapi,
            },
            degradation=DegradationLevel.PARTIAL if degraded else DegradationLevel.NONE,
            reason_codes=(self.operational_status.value,),
            correlation_id=correlation_id,
        )


def build_health_snapshot(
    *,
    omniroute_enabled: bool,
    titan_ollama_enabled: bool,
    freellmapi_enabled: bool,
    offline_local_only: bool,
    titan_ollama_circuit: Optional[CircuitBreaker],
    freellmapi_circuit: Optional[CircuitBreaker],
    hermes_router_reachable: bool,
    now: int,
) -> OmniRouteHealthSnapshot:
    """Build a health snapshot from live circuit-breaker state.

    A disabled or absent upstream reports :attr:`DependencyHealth.UNKNOWN`
    (never silently ``HEALTHY``) — an operator who has turned a provider off
    must not see a green light for it.
    """
    titan_ollama_health = (
        dependency_health_from_circuit(titan_ollama_circuit.state)
        if titan_ollama_enabled and titan_ollama_circuit is not None
        else DependencyHealth.UNKNOWN
    )
    freellmapi_health = (
        dependency_health_from_circuit(freellmapi_circuit.state)
        if freellmapi_enabled
        and freellmapi_circuit is not None
        and not offline_local_only
        else DependencyHealth.UNKNOWN
    )
    omniroute_health = (
        DependencyHealth.HEALTHY if omniroute_enabled else DependencyHealth.UNAVAILABLE
    )
    hermes_router_health = (
        DependencyHealth.HEALTHY
        if hermes_router_reachable
        else DependencyHealth.UNAVAILABLE
    )

    operational_status = compute_operational_status(
        titan_ollama=titan_ollama_health,
        freellmapi=freellmapi_health,
        offline_local_only=offline_local_only,
    )
    internet_dependent_degradation = (
        not offline_local_only and freellmapi_enabled and not _usable(freellmapi_health)
    )

    return OmniRouteHealthSnapshot(
        observed_at=now,
        hermes_router=hermes_router_health,
        omniroute=omniroute_health,
        titan_ollama=titan_ollama_health,
        freellmapi=freellmapi_health,
        offline_local_only=offline_local_only,
        operational_status=operational_status,
        internet_dependent_degradation=internet_dependent_degradation,
    )
