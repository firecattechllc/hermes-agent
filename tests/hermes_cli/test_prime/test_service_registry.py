from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.service_registry import (
    KNOWN_ECOSYSTEM_SERVICES,
    EcosystemServiceRegistry,
    EcosystemServiceRegistryStore,
    ServiceDiscoveryOutcome,
    ServiceInstallationStatus,
    ServiceRegistrationOutcome,
    ServiceRegistrationRejectionCode,
    VerifiedExternalSource,
    discover_service,
    validate_external_source,
)


def _now() -> int:
    return int(time.time())


def _registry(tmp_path) -> EcosystemServiceRegistry:
    return EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))


# ── Real (non-mocked) discovery ─────────────────────────────────────────────

@pytest.mark.parametrize("descriptor", KNOWN_ECOSYSTEM_SERVICES, ids=lambda d: d.service_key)
def test_every_known_service_is_verified_present_and_disabled(descriptor) -> None:
    """This is the 'do not claim installation based on documentation or
    stubs' proof: each result comes from a real importlib import and real
    attribute reads, not a hardcoded belief."""
    result = discover_service(descriptor, now=_now())
    assert result.outcome == ServiceDiscoveryOutcome.VERIFIED_PRESENT_DISABLED
    assert result.installation_status == ServiceInstallationStatus.PRESENT_DISABLED
    assert result.enabled_default_confirmed_false is True
    assert len(result.capability_denials_confirmed) > 0


def test_discovery_of_nonexistent_module_is_not_found() -> None:
    from hermes_cli.prime.service_registry import EcosystemServiceDescriptor, EcosystemServiceCategory

    fake = EcosystemServiceDescriptor(
        service_key="ghost_service",
        display_name="Ghost Service",
        category=EcosystemServiceCategory.DISCOVERY_CATALOG,
        module_path="sigil.this_module_does_not_exist_anywhere",
        config_class_name="GhostConfig",
    )
    result = discover_service(fake, now=_now())
    assert result.outcome == ServiceDiscoveryOutcome.IMPORT_FAILED
    assert result.installation_status == ServiceInstallationStatus.NOT_FOUND


def test_discovery_with_no_module_path_is_not_found() -> None:
    from hermes_cli.prime.service_registry import EcosystemServiceDescriptor, EcosystemServiceCategory

    fake = EcosystemServiceDescriptor(
        service_key="no_module",
        display_name="No Module",
        category=EcosystemServiceCategory.DISCOVERY_CATALOG,
    )
    result = discover_service(fake, now=_now())
    assert result.installation_status == ServiceInstallationStatus.NOT_FOUND


def test_discovery_with_wrong_class_name_is_ambiguous() -> None:
    from hermes_cli.prime.service_registry import EcosystemServiceDescriptor, EcosystemServiceCategory

    fake = EcosystemServiceDescriptor(
        service_key="paperclip_wrong_class",
        display_name="Paperclip (wrong class)",
        category=EcosystemServiceCategory.BUILDER_WORKER,
        module_path="sigil.paperclip_adapter",
        config_class_name="ThisClassDoesNotExist",
    )
    result = discover_service(fake, now=_now())
    assert result.installation_status == ServiceInstallationStatus.AMBIGUOUS


def test_discovery_flags_unexpectedly_enabled_config_as_unsafe(monkeypatch) -> None:
    """Simulates a future drift where a module's default flips to enabled —
    discovery must not silently trust the historical classification."""
    import sigil.paperclip_adapter as paperclip_module
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class FakeEnabledConfig:
        enabled: bool = True

    monkeypatch.setattr(paperclip_module, "PaperclipAdapterConfig", FakeEnabledConfig)

    from hermes_cli.prime.service_registry import EcosystemServiceDescriptor, EcosystemServiceCategory

    descriptor = EcosystemServiceDescriptor(
        service_key="paperclip_drift_test",
        display_name="Paperclip (drift test)",
        category=EcosystemServiceCategory.BUILDER_WORKER,
        module_path="sigil.paperclip_adapter",
        config_class_name="PaperclipAdapterConfig",
    )
    result = discover_service(descriptor, now=_now())
    assert result.outcome == ServiceDiscoveryOutcome.UNEXPECTEDLY_ENABLED
    assert result.installation_status == ServiceInstallationStatus.UNSAFE


# ── Registration ─────────────────────────────────────────────────────────────

def test_register_known_service_succeeds(tmp_path) -> None:
    registry = _registry(tmp_path)
    outcome, record, rejection = registry.register_known_service("paperclip", now=_now())
    assert outcome == ServiceRegistrationOutcome.REGISTERED
    assert rejection is None
    assert record.installation_status == ServiceInstallationStatus.PRESENT_DISABLED
    assert record.certification_gate_met is False
    assert record.is_dispatchable() is False


def test_register_all_known_services(tmp_path) -> None:
    registry = _registry(tmp_path)
    records = registry.register_all_known_services(now=_now())
    assert len(records) == len(KNOWN_ECOSYSTEM_SERVICES)
    assert all(r.installation_status == ServiceInstallationStatus.PRESENT_DISABLED for r in records)
    assert all(r.is_dispatchable() is False for r in records)


def test_unknown_service_key_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    outcome, record, rejection = registry.register_known_service("totally-made-up-service", now=_now())
    assert outcome == ServiceRegistrationOutcome.REJECTED
    assert record is None
    assert rejection == ServiceRegistrationRejectionCode.UNKNOWN_SERVICE_KEY


def test_duplicate_registration_is_rejected_without_explicit_reregistration(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.register_known_service("buzz_relay", now=_now())
    outcome, record, rejection = registry.register_known_service("buzz_relay", now=_now())
    assert outcome == ServiceRegistrationOutcome.REJECTED
    assert rejection == ServiceRegistrationRejectionCode.DUPLICATE_REGISTRATION


def test_explicit_reregistration_refreshes_the_record(tmp_path) -> None:
    registry = _registry(tmp_path)
    now = _now()
    registry.register_known_service("buzznode", now=now)
    outcome, record, rejection = registry.register_known_service(
        "buzznode", now=now + 10, allow_reregistration=True
    )
    assert outcome == ServiceRegistrationOutcome.UPDATED
    assert record.last_checked_at == now + 10


def test_revoked_service_can_never_reregister(tmp_path) -> None:
    registry = _registry(tmp_path)
    now = _now()
    registry.register_known_service("hermes_wiki", now=now)
    registry.revoke("hermes_wiki", now=now, reason="superseded")
    outcome, record, rejection = registry.register_known_service(
        "hermes_wiki", now=now + 1, allow_reregistration=True
    )
    assert outcome == ServiceRegistrationOutcome.REJECTED
    assert rejection == ServiceRegistrationRejectionCode.REVOKED


def test_registry_persists_across_instances(tmp_path) -> None:
    state_root = tmp_path / "prime"
    first = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=state_root))
    first.register_known_service("agent_reach", now=_now())

    second = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=state_root))
    record = second.get("agent_reach")
    assert record is not None
    assert record.service_key == "agent_reach"


# ── Unverified-repository / external-source rejection ──────────────────────

def test_external_registration_without_a_source_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    outcome, record, rejection = registry.register_external_service(
        "paperclip", external_source=None, now=_now()
    )
    assert outcome == ServiceRegistrationOutcome.REJECTED
    assert rejection == ServiceRegistrationRejectionCode.UNVERIFIED_EXTERNAL_SOURCE
    assert record is None


def test_external_source_rejects_non_github_gitlab_url() -> None:
    with pytest.raises(ValidationError):
        VerifiedExternalSource(
            repository_url="https://example.com/someone/paperclip",
            revision="a" * 40,
            license_spdx_id="MIT",
            integrity_sha256="a" * 64,
            verified_by="reviewer",
            verified_at=_now(),
        )


def test_external_source_rejects_unpinned_revision() -> None:
    with pytest.raises(ValidationError):
        VerifiedExternalSource(
            repository_url="https://github.com/someorg/paperclip",
            revision="main",
            license_spdx_id="MIT",
            integrity_sha256="a" * 64,
            verified_by="reviewer",
            verified_at=_now(),
        )


def test_external_source_rejects_unclear_license() -> None:
    source = VerifiedExternalSource(
        repository_url="https://github.com/someorg/paperclip",
        revision="a" * 40,
        license_spdx_id="unknown",
        integrity_sha256="a" * 64,
        verified_by="reviewer",
        verified_at=_now(),
    )
    ok, reason = validate_external_source(source)
    assert ok is False
    assert reason == "unclear_or_incompatible_license"


def test_fully_valid_external_source_is_admissible(tmp_path) -> None:
    source = VerifiedExternalSource(
        repository_url="https://github.com/someorg/paperclip",
        revision="a" * 40,
        license_spdx_id="MIT",
        integrity_sha256="b" * 64,
        verified_by="reviewer",
        verified_at=_now(),
    )
    ok, reason = validate_external_source(source)
    assert ok is True
    assert reason is None

    registry = _registry(tmp_path)
    outcome, record, rejection = registry.register_external_service(
        "paperclip", external_source=source, now=_now()
    )
    assert outcome == ServiceRegistrationOutcome.REGISTERED
    assert record.installation_status == ServiceInstallationStatus.AVAILABLE_VERIFIED
    # Even a verified external source never becomes dispatchable until the
    # certification gate is separately met.
    assert record.is_dispatchable() is False


def test_external_registration_for_unknown_service_key_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    source = VerifiedExternalSource(
        repository_url="https://github.com/someorg/whatever",
        revision="a" * 40,
        license_spdx_id="MIT",
        integrity_sha256="c" * 64,
        verified_by="reviewer",
        verified_at=_now(),
    )
    outcome, record, rejection = registry.register_external_service(
        "not-a-known-service", external_source=source, now=_now()
    )
    assert outcome == ServiceRegistrationOutcome.REJECTED
    assert rejection == ServiceRegistrationRejectionCode.UNKNOWN_SERVICE_KEY


# ── Malformed records ────────────────────────────────────────────────────────

def test_service_record_rejects_inconsistent_revocation_fields() -> None:
    from hermes_cli.prime.service_registry import ServiceRecord, EcosystemServiceCategory, ServiceInstallationStatus as SIS, ServiceDiscoveryOutcome as SDO

    with pytest.raises(ValidationError):
        ServiceRecord(
            identity_id="fid_service_x",
            service_key="paperclip",
            display_name="Paperclip",
            category=EcosystemServiceCategory.BUILDER_WORKER,
            installation_status=SIS.PRESENT_DISABLED,
            discovery_outcome=SDO.VERIFIED_PRESENT_DISABLED,
            certification_gate="phase9_live_node_certification",
            revoked=True,
            revoked_at=None,  # inconsistent: revoked but no revoked_at
            registered_at=_now(),
            updated_at=_now(),
            last_checked_at=_now(),
        )
