from __future__ import annotations

from runway.health import FakeHealthProbe, HealthStatus


def test_provider_can_be_healthy_while_one_endpoint_is_down():
    probe = FakeHealthProbe(now=0)
    probe.set_provider_health(
        "vendor-x", HealthStatus.HEALTHY,
        endpoints={"primary": HealthStatus.UNHEALTHY, "secondary": HealthStatus.HEALTHY},
    )
    health = probe.provider_health("vendor-x")
    assert health.endpoint_status("primary") == HealthStatus.UNHEALTHY
    assert health.endpoint_status("secondary") == HealthStatus.HEALTHY
    # Failover: the provider as a whole is still routable via its healthy endpoint.
    assert health.best_endpoint_status() == HealthStatus.HEALTHY


def test_model_health_is_independent_of_provider_health():
    probe = FakeHealthProbe(now=0)
    probe.set_provider_health("vendor-x", HealthStatus.HEALTHY)
    probe.set_model_health("broken-model@vendor-x", HealthStatus.UNHEALTHY)
    assert probe.provider_health("vendor-x").status == HealthStatus.HEALTHY
    assert probe.model_health("broken-model@vendor-x").status == HealthStatus.UNHEALTHY


def test_unmonitored_ids_report_unknown_not_healthy():
    probe = FakeHealthProbe(now=0)
    assert probe.provider_health("never-configured").status == HealthStatus.UNKNOWN
    assert probe.model_health("never-configured@x").status == HealthStatus.UNKNOWN
