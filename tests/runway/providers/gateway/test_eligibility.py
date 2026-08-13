from __future__ import annotations

from runway.budget import BudgetLedger, BudgetPolicy
from runway.capabilities import Capability
from runway.classification import DataClassification
from runway.flags import RunwayFeatureFlags
from runway.health import FakeHealthProbe, HealthStatus
from runway.providers.gateway.eligibility import EligibilityRequest, check_channel_eligibility
from runway.providers.gateway.models import BalanceObservation, BalanceObservationState, BalanceQuotaType, RailRestriction
from runway.trust import TrustTier

from .conftest import make_channel, make_provider


def _healthy_probe(channel_id: str) -> FakeHealthProbe:
    probe = FakeHealthProbe(now=0)
    probe.set_provider_health(channel_id, HealthStatus.HEALTHY)
    return probe


def _request(**overrides) -> EligibilityRequest:
    fields = dict(model_key="m@acme", data_classification=DataClassification.INTERNAL, timestamp=0)
    fields.update(overrides)
    return EligibilityRequest(**fields)


def _registry(provider=None, channel=None, authorized=True):
    from runway.providers.gateway.registry import ChannelRegistry
    provider = provider or make_provider("acme")
    channel = channel or make_channel(provider_id="acme")
    registry = ChannelRegistry(
        providers=(provider,), channels=(channel,), model_channel_map={"m@acme": channel.channel_id},
    )
    if authorized:
        registry = registry.authorize_channel(channel.channel_id)
    return registry, channel


def test_fully_eligible_channel():
    registry, channel = _registry()
    result = check_channel_eligibility(
        registry, _request(require_known_balance=False), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert result.eligible is True
    assert result.reasons == ()


def test_provider_disabled_denied():
    registry, channel = _registry(provider=make_provider("acme", enabled=False))
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "provider_disabled" in result.reasons


def test_channel_disabled_denied():
    registry, channel = _registry(channel=make_channel(provider_id="acme", enabled=False))
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "channel_disabled" in result.reasons


def test_not_authorized_denied():
    registry, channel = _registry(authorized=False)
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "channel_not_authorized" in result.reasons


def test_global_external_execution_gate_denied():
    registry, channel = _registry()
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags(),  # real default -- gate off
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "external_execution_not_permitted" in result.reasons


def test_client_class_restriction_denies_generic_request():
    channel = make_channel(provider_id="acme", allowed_client_classes=("internal_ops",))
    registry, _ = _registry(channel=channel)
    result = check_channel_eligibility(
        registry, _request(client_class="generic_hermes_request"),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "client_class_restricted" in result.reasons

    # Same channel accepts a request from the class it's scoped to.
    ok = check_channel_eligibility(
        registry, _request(client_class="internal_ops"),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "client_class_restricted" not in ok.reasons


def test_no_images_restriction_denies_vision_request():
    channel = make_channel(provider_id="acme", restrictions=(RailRestriction.NO_IMAGES,))
    registry, _ = _registry(channel=channel)
    result = check_channel_eligibility(
        registry, _request(required_capabilities=(Capability.VISION,)),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "restriction_no_images" in result.reasons


def test_no_tools_restriction_denies_tool_calling_request():
    channel = make_channel(provider_id="acme", restrictions=(RailRestriction.NO_TOOLS,))
    registry, _ = _registry(channel=channel)
    result = check_channel_eligibility(
        registry, _request(required_capabilities=(Capability.TOOL_CALLING,)),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "restriction_no_tools" in result.reasons


def test_capability_mismatch_without_restriction_is_fine():
    registry, channel = _registry()
    result = check_channel_eligibility(
        registry, _request(required_capabilities=(Capability.VISION,), require_known_balance=False),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert result.eligible is True


def test_data_classification_vs_trust_tier():
    provider = make_provider("acme", trust_tier=TrustTier.EXPERIMENTAL_UNKNOWN)
    registry, channel = _registry(provider=provider)
    denied = check_channel_eligibility(
        registry, _request(data_classification=DataClassification.RESTRICTED),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "data_classification_not_eligible_for_trust_tier" in denied.reasons

    allowed = check_channel_eligibility(
        registry, _request(data_classification=DataClassification.SYNTHETIC),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "data_classification_not_eligible_for_trust_tier" not in allowed.reasons


def test_unhealthy_channel_denied():
    registry, channel = _registry()
    probe = FakeHealthProbe(now=0)
    probe.set_provider_health(channel.channel_id, HealthStatus.UNHEALTHY)
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=probe,
    )
    assert "channel_unhealthy" in result.reasons


def test_degraded_channel_still_eligible():
    registry, channel = _registry()
    probe = FakeHealthProbe(now=0)
    probe.set_provider_health(channel.channel_id, HealthStatus.DEGRADED)
    result = check_channel_eligibility(
        registry, _request(require_known_balance=False), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=probe,
    )
    assert result.eligible is True  # degraded is not the same as unhealthy


def test_unknown_balance_required_by_default_denies():
    registry, channel = _registry()
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "balance_unknown_and_required" in result.reasons


def test_unknown_balance_permitted_under_explicit_policy():
    registry, channel = _registry()
    result = check_channel_eligibility(
        registry, _request(require_known_balance=False),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "balance_unknown_and_required" not in result.reasons


def test_exhausted_balance_denied():
    registry, channel = _registry()
    registry = registry.with_balance_observation(BalanceObservation(
        channel_id=channel.channel_id, quota_type=BalanceQuotaType.ACCOUNT_BALANCE,
        state=BalanceObservationState.KNOWN, remaining_micros=0, observed_at=0,
    ))
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert "balance_exhausted" in result.reasons


def test_known_positive_balance_eligible():
    registry, channel = _registry()
    registry = registry.with_balance_observation(BalanceObservation(
        channel_id=channel.channel_id, quota_type=BalanceQuotaType.PROMOTIONAL_CREDIT,
        state=BalanceObservationState.KNOWN, remaining_micros=5_000_000, observed_at=0,
    ))
    result = check_channel_eligibility(
        registry, _request(), flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id),
    )
    assert result.eligible is True


def test_request_budget_cap_enforced(tmp_path):
    registry, channel = _registry()
    ledger = BudgetLedger.open(BudgetPolicy(per_task_max_micros=100), path=tmp_path / "b.db")
    result = check_channel_eligibility(
        registry, _request(estimated_cost_micros=200, require_known_balance=False),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=_healthy_probe(channel.channel_id), budget_ledger=ledger,
    )
    assert "budget_exceeded" in result.reasons


def test_no_channel_for_model_is_ineligible():
    from runway.providers.gateway.registry import ChannelRegistry
    registry = ChannelRegistry()
    result = check_channel_eligibility(
        registry, _request(model_key="ghost@nowhere"),
        flags=RunwayFeatureFlags.model_construct(external_execution_enabled=True),
        health_probe=FakeHealthProbe(now=0),
    )
    assert result.eligible is False
    assert result.reasons == ("no_channel_for_model",)
