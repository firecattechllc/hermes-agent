from __future__ import annotations

from hermes_cli.prime.health import DependencyHealth, evaluate_health
from hermes_cli.prime.omniroute_health import (
    OperationalStatus,
    build_health_snapshot,
    compute_operational_status,
)
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker


def test_both_providers_healthy_is_healthy() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.HEALTHY,
        freellmapi=DependencyHealth.HEALTHY,
        offline_local_only=False,
    )
    assert status == OperationalStatus.HEALTHY


def test_freellmapi_outage_with_ollama_up_is_degraded_not_down() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.HEALTHY,
        freellmapi=DependencyHealth.UNAVAILABLE,
        offline_local_only=False,
    )
    assert status == OperationalStatus.DEGRADED


def test_ollama_outage_with_freellmapi_up_is_degraded() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.UNAVAILABLE,
        freellmapi=DependencyHealth.HEALTHY,
        offline_local_only=False,
    )
    assert status == OperationalStatus.DEGRADED


def test_both_providers_down_is_down() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.UNAVAILABLE,
        freellmapi=DependencyHealth.UNAVAILABLE,
        offline_local_only=False,
    )
    assert status == OperationalStatus.DOWN


def test_offline_local_only_with_ollama_usable_is_local_only() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.HEALTHY,
        freellmapi=DependencyHealth.HEALTHY,
        offline_local_only=True,
    )
    assert status == OperationalStatus.LOCAL_ONLY


def test_offline_local_only_with_ollama_down_is_down() -> None:
    status = compute_operational_status(
        titan_ollama=DependencyHealth.UNAVAILABLE,
        freellmapi=DependencyHealth.HEALTHY,
        offline_local_only=True,
    )
    assert status == OperationalStatus.DOWN


# ── build_health_snapshot from live circuit-breaker state ──────────────────


def test_snapshot_reflects_disabled_provider_as_unknown_not_healthy() -> None:
    snapshot = build_health_snapshot(
        omniroute_enabled=True,
        titan_ollama_enabled=True,
        freellmapi_enabled=False,
        offline_local_only=False,
        titan_ollama_circuit=CircuitBreaker(),
        freellmapi_circuit=None,
        hermes_router_reachable=True,
        now=1000,
    )
    assert snapshot.freellmapi == DependencyHealth.UNKNOWN
    assert snapshot.internet_dependent_degradation is False  # disabled, not degraded


def test_snapshot_open_freellmapi_circuit_reports_degraded_with_ollama_healthy() -> (
    None
):
    ollama_breaker = CircuitBreaker()
    freellmapi_breaker = CircuitBreaker(failure_threshold=1)
    freellmapi_breaker.record_failure()

    snapshot = build_health_snapshot(
        omniroute_enabled=True,
        titan_ollama_enabled=True,
        freellmapi_enabled=True,
        offline_local_only=False,
        titan_ollama_circuit=ollama_breaker,
        freellmapi_circuit=freellmapi_breaker,
        hermes_router_reachable=True,
        now=1000,
    )
    assert snapshot.operational_status == OperationalStatus.DEGRADED
    assert snapshot.freellmapi == DependencyHealth.UNAVAILABLE
    assert snapshot.titan_ollama == DependencyHealth.HEALTHY
    assert snapshot.internet_dependent_degradation is True


def test_snapshot_distinguishes_hermes_router_health_from_provider_health() -> None:
    snapshot = build_health_snapshot(
        omniroute_enabled=True,
        titan_ollama_enabled=True,
        freellmapi_enabled=True,
        offline_local_only=False,
        titan_ollama_circuit=CircuitBreaker(),
        freellmapi_circuit=CircuitBreaker(),
        hermes_router_reachable=False,
        now=1000,
    )
    assert snapshot.hermes_router == DependencyHealth.UNAVAILABLE
    assert (
        snapshot.omniroute == DependencyHealth.HEALTHY
    )  # independent of hermes_router


def test_snapshot_to_health_report_is_usable_by_shared_health_protocol() -> None:
    snapshot = build_health_snapshot(
        omniroute_enabled=True,
        titan_ollama_enabled=True,
        freellmapi_enabled=True,
        offline_local_only=False,
        titan_ollama_circuit=CircuitBreaker(),
        freellmapi_circuit=CircuitBreaker(),
        hermes_router_reachable=True,
        now=1000,
    )
    report = snapshot.to_health_report(subject_identity_id="titan-omniroute")
    findings = evaluate_health(report, now=1000)
    assert findings == ()  # fresh and fully healthy per the shared health protocol


def test_snapshot_degraded_status_reflected_in_health_report_degradation() -> None:
    freellmapi_breaker = CircuitBreaker(failure_threshold=1)
    freellmapi_breaker.record_failure()
    snapshot = build_health_snapshot(
        omniroute_enabled=True,
        titan_ollama_enabled=True,
        freellmapi_enabled=True,
        offline_local_only=False,
        titan_ollama_circuit=CircuitBreaker(),
        freellmapi_circuit=freellmapi_breaker,
        hermes_router_reachable=True,
        now=1000,
    )
    report = snapshot.to_health_report(subject_identity_id="titan-omniroute")
    assert report.degradation.value == "partial"
    assert report.readiness.value == "ready"  # still ready -- Titan Ollama can serve
