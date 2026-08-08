from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ecosystem_catalog import (
    ECOSYSTEM_CATALOG_SCHEMA_VERSION,
    AdmissionReadiness,
    CatalogAssessment,
    CatalogConflict,
    CatalogEnvironment,
    CatalogRecommendation,
    CompatibilityState,
    DiscoveryEvidence,
    DiscoverySourceKind,
    DiscoveredIntegration,
    EcosystemCatalogConfig,
    EcosystemCatalogValidationError,
    assess_discovery,
    validate_against_registry,
)
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)


NOW = "2026-08-02T00:15:00Z"
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64


def evidence(
    evidence_id: str,
    source_kind: DiscoverySourceKind,
) -> DiscoveryEvidence:
    return DiscoveryEvidence(
        evidence_id=evidence_id,
        source_kind=source_kind,
        observed_at=NOW,
        source_identity="manual-stage8-review",
        content_digest=DIGEST,
        provenance="Stage 8 governed manual evidence",
        reference=f"evidence/{evidence_id}.json",
    )


def complete_evidence() -> tuple[DiscoveryEvidence, ...]:
    return (
        evidence(
            "repository-evidence",
            DiscoverySourceKind.REPOSITORY_SNAPSHOT,
        ),
        evidence(
            "license-evidence",
            DiscoverySourceKind.LICENSE_REVIEW,
        ),
        evidence(
            "activity-evidence",
            DiscoverySourceKind.ACTIVITY_REVIEW,
        ),
        evidence(
            "security-evidence",
            DiscoverySourceKind.SECURITY_REVIEW,
        ),
    )


def discovery(
    *,
    known_risks: tuple[str, ...] = ("supply-chain-risk",),
    threat_models: tuple[str, ...] = (
        "docs/threat-models/example-integration.md",
    ),
    evidence_items: tuple[DiscoveryEvidence, ...] | None = None,
) -> DiscoveredIntegration:
    return DiscoveredIntegration(
        discovery_id="discovery-stage8-example",
        integration_id="example-integration",
        canonical_project_name="Example Integration",
        category=IntegrationCategory.DISCOVERY,
        repository_url="https://github.com/example/example-integration",
        repository_identity="example/example-integration",
        pinned_identity=REVISION,
        release_label=None,
        maintainer_identity="example",
        license_classification="apache-2.0",
        maturity="under evaluation",
        capabilities=("catalog_search", "metadata_projection"),
        supported_machines=("hermes-titan", "hermes-mac"),
        supported_profiles=("governed-worker",),
        known_risks=known_risks,
        threat_model_references=threat_models,
        evidence=complete_evidence()
        if evidence_items is None
        else evidence_items,
        observed_at=NOW,
    )


def environment() -> CatalogEnvironment:
    return CatalogEnvironment(
        machines=("hermes-titan", "hermes-mac"),
        profiles=("governed-worker",),
        approved_capabilities=(
            "catalog_search",
            "metadata_projection",
        ),
    )


def registry_entry(
    *,
    lifecycle: LifecycleState = LifecycleState.UNDER_REVIEW,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id="example-integration",
        canonical_project_name="Example Integration",
        category=IntegrationCategory.DISCOVERY,
        repository_url="https://github.com/example/example-integration",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="example/example-integration",
        maintainer_identity="example",
        maturity="under evaluation",
        license_classification="apache-2.0",
        license_evidence_source="upstream repository",
        activity_evidence="repository activity inspected",
        activity_observed_at=NOW,
        credential_requirements=(),
        authentication_requirements=(),
        declared_network_access=(),
        declared_egress_destinations=(),
        declared_filesystem_access=(),
        declared_tool_permissions=(),
        declared_shell_process_authority=(),
        declared_browser_authority=(),
        declared_execution_model="descriptive discovery catalog only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan", "hermes-mac"),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("catalog_search", "metadata_projection"),
        integration_overlap=(),
        known_risks=("supply-chain-risk",),
        threat_model_references=(
            "docs/threat-models/example-integration.md",
        ),
        evaluation_evidence_references=(
            "docs/evidence/example-integration.md",
        ),
        rollback_instructions="Remove the discovery projection.",
        disable_instructions="Keep catalog disabled.",
        quarantine_instructions="Reject the integration.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 8 evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def conflict(*, severity: int = 50) -> CatalogConflict:
    return CatalogConflict(
        conflict_id="conflict-example",
        conflicting_integration_id="existing-discovery",
        overlapping_capabilities=("catalog_search",),
        severity=severity,
        reason="Capability overlap requires review.",
        evidence_reference="evidence/conflict-example.json",
    )


def test_config_is_disabled_and_has_no_authority() -> None:
    config = EcosystemCatalogConfig()

    assert config.schema_version == ECOSYSTEM_CATALOG_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_discover is False
    assert config.can_crawl is False
    assert config.can_install is False
    assert config.can_activate is False
    assert config.can_authenticate is False
    assert config.can_mutate_registry is False
    assert config.authority == AuthorityDenials()


def test_config_rejects_registry_schema_mismatch() -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match="incompatible",
    ):
        EcosystemCatalogConfig(expected_registry_schema=999)


def test_discovery_is_immutable_and_deterministic() -> None:
    first = discovery()
    second = discovery()

    assert first == second
    assert first.discovery_digest == second.discovery_digest
    assert first.discovery_digest.startswith("sha256:")
    assert first.can_install is False
    assert first.can_activate is False
    assert first.can_escalate_authority is False


def test_discovery_rejects_digest_tampering() -> None:
    value = discovery()

    with pytest.raises(
        EcosystemCatalogValidationError,
        match="digest mismatch",
    ):
        replace(value, maturity="changed")


def test_discovery_rejects_mutable_identity() -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match="immutable",
    ):
        replace(
            discovery(),
            pinned_identity="latest",
            discovery_digest="",
        )


def test_discovery_rejects_repository_conflict() -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match="conflict",
    ):
        replace(
            discovery(),
            repository_identity="other/project",
            discovery_digest="",
        )


def test_discovery_evidence_rejects_credentials() -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match="credential",
    ):
        DiscoveryEvidence(
            evidence_id="secret-evidence",
            source_kind=DiscoverySourceKind.SECURITY_REVIEW,
            observed_at=NOW,
            source_identity="manual",
            content_digest=DIGEST,
            provenance="api_key=secret-value",
            reference="evidence/security.json",
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/evidence.json",
        "/home/operator/evidence.json",
        "../outside.json",
        "evidence/../../outside.json",
        "http://127.0.0.1:3000/result",
    ],
)
def test_evidence_references_fail_closed(
    bad_reference: str,
) -> None:
    with pytest.raises(EcosystemCatalogValidationError):
        replace(
            evidence(
                "security-evidence",
                DiscoverySourceKind.SECURITY_REVIEW,
            ),
            reference=bad_reference,
        )


def test_registry_validation_accepts_exact_match() -> None:
    validate_against_registry(discovery(), registry_entry())


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            replace(
                registry_entry(),
                integration_id="different-integration",
                content_digest="",
            ),
            "identity mismatch",
        ),
        (
            replace(
                registry_entry(),
                category=IntegrationCategory.WORKER,
                content_digest="",
            ),
            "category mismatch",
        ),
        (
            replace(
                registry_entry(),
                upstream_repository_identity="other/project",
                repository_url="https://github.com/other/project",
                content_digest="",
            ),
            "repository mismatch",
        ),
        (
            replace(
                registry_entry(),
                pinned_identity="c" * 40,
                content_digest="",
            ),
            "pinned identity mismatch",
        ),
    ],
)
def test_registry_validation_fails_closed(
    entry: IntegrationRegistryEntry,
    message: str,
) -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match=message,
    ):
        validate_against_registry(discovery(), entry)


def test_disabled_catalog_holds_discovery() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(),
        discovery(),
        environment(),
        registry_entry=registry_entry(),
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.UNKNOWN
    assert assessment.recommendation is CatalogRecommendation.HOLD
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.NOT_READY
    )
    assert assessment.can_admit is False
    assert assessment.can_install is False
    assert assessment.can_activate is False


def test_compatible_discovery_is_ready_for_review() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=None,
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.COMPATIBLE
    assert assessment.recommendation is CatalogRecommendation.REVIEW
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.READY_FOR_REVIEW
    )
    assert assessment.evidence_complete is True
    assert assessment.evidence_current is True
    assert assessment.registry_match is False


def test_matching_sandbox_registry_is_ready_for_sandbox() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=registry_entry(
            lifecycle=LifecycleState.SANDBOX_APPROVED
        ),
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.COMPATIBLE
    assert (
        assessment.recommendation
        is CatalogRecommendation.SANDBOX_CANDIDATE
    )
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.READY_FOR_SANDBOX
    )


def test_incomplete_evidence_is_held() -> None:
    partial = discovery(
        evidence_items=(
            evidence(
                "repository-evidence",
                DiscoverySourceKind.REPOSITORY_SNAPSHOT,
            ),
        )
    )

    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        partial,
        environment(),
        registry_entry=None,
        evidence_age_seconds=10,
    )

    assert assessment.evidence_complete is False
    assert assessment.recommendation is CatalogRecommendation.HOLD
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.EVIDENCE_INCOMPLETE
    )


def test_stale_evidence_is_held() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=None,
        evidence_age_seconds=604801,
        stale_after_seconds=604800,
    )

    assert assessment.evidence_current is False
    assert assessment.recommendation is CatalogRecommendation.HOLD


def test_future_evidence_fails_closed() -> None:
    with pytest.raises(
        EcosystemCatalogValidationError,
        match="future",
    ):
        assess_discovery(
            EcosystemCatalogConfig(enabled=True),
            discovery(),
            environment(),
            registry_entry=None,
            evidence_age_seconds=-1,
        )


def test_environment_mismatch_is_rejected() -> None:
    incompatible_environment = CatalogEnvironment(
        machines=("unknown-machine",),
        profiles=("unknown-profile",),
        approved_capabilities=("unknown-capability",),
    )

    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        incompatible_environment,
        registry_entry=None,
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.INCOMPATIBLE
    assert assessment.recommendation is CatalogRecommendation.REJECT
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.NOT_READY
    )


def test_moderate_conflict_requires_review() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=None,
        conflicts=(conflict(severity=50),),
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.PARTIAL
    assert assessment.recommendation is CatalogRecommendation.REVIEW
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.CONFLICTED
    )


def test_high_severity_conflict_quarantines() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=None,
        conflicts=(conflict(severity=90),),
        evidence_age_seconds=10,
    )

    assert assessment.compatibility is CompatibilityState.INCOMPATIBLE
    assert assessment.recommendation is CatalogRecommendation.QUARANTINE
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.RISK_BLOCKED
    )


def test_known_risk_without_threat_model_is_blocked() -> None:
    assessment = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(threat_models=()),
        environment(),
        registry_entry=None,
        evidence_age_seconds=10,
    )

    assert assessment.recommendation is CatalogRecommendation.REJECT
    assert (
        assessment.admission_readiness
        is AdmissionReadiness.RISK_BLOCKED
    )


def test_assessment_is_deterministic() -> None:
    first = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=registry_entry(),
        evidence_age_seconds=10,
    )
    second = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=registry_entry(),
        evidence_age_seconds=10,
    )

    assert first == second
    assert first.assessment_digest == second.assessment_digest


def test_assessment_rejects_digest_tampering() -> None:
    value = assess_discovery(
        EcosystemCatalogConfig(enabled=True),
        discovery(),
        environment(),
        registry_entry=registry_entry(),
        evidence_age_seconds=10,
    )

    with pytest.raises(
        EcosystemCatalogValidationError,
        match="digest mismatch",
    ):
        replace(value, reason="changed")


def test_duplicate_conflicts_fail_closed() -> None:
    duplicate = conflict()

    with pytest.raises(
        EcosystemCatalogValidationError,
        match="duplicate conflict",
    ):
        CatalogAssessment(
            discovery_id="discovery-stage8-example",
            integration_id="example-integration",
            compatibility=CompatibilityState.PARTIAL,
            machine_matches=("hermes-titan",),
            profile_matches=("governed-worker",),
            capability_matches=("catalog_search",),
            conflicts=(duplicate, duplicate),
            evidence_complete=True,
            evidence_current=True,
            registry_match=False,
            recommendation=CatalogRecommendation.REVIEW,
            admission_readiness=AdmissionReadiness.CONFLICTED,
            reason="Conflicted.",
            discovery_digest=discovery().discovery_digest,
        )
