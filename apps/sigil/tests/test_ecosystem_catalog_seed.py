from __future__ import annotations

from sigil.ecosystem_catalog import (
    AdmissionReadiness,
    CatalogEnvironment,
    DiscoverySourceKind,
    EcosystemCatalogConfig,
    assess_discovery,
)
from sigil.ecosystem_catalog_seed import (
    hermes_paperclip_adapter_discovery,
    paperclip_discovery,
    seed_discoveries,
)

NOW = "2026-08-05T22:30:00Z"


def test_seed_discoveries_returns_paperclip_entries_only() -> None:
    discoveries = seed_discoveries(NOW)

    assert len(discoveries) == 2
    integration_ids = {d.integration_id for d in discoveries}
    assert integration_ids == {"paperclip", "hermes-paperclip-adapter-upstream"}
    # Buzz has no identified upstream; nothing is fabricated for it.
    assert not any("buzz" in d.integration_id for d in discoveries)


def test_paperclip_discovery_pins_an_immutable_commit() -> None:
    discovery = paperclip_discovery(NOW)

    assert discovery.pinned_identity == "72b509c89539b31e421086c782346635c5d0517b"
    assert discovery.repository_identity == "paperclipai/paperclip"
    assert discovery.license_classification == "MIT"


def test_hermes_paperclip_adapter_discovery_pins_an_immutable_commit() -> None:
    discovery = hermes_paperclip_adapter_discovery(NOW)

    assert discovery.pinned_identity == "937ea71a34f5efcaa3834b11fdd08cfc1c99cb2c"
    assert discovery.repository_identity == "NousResearch/hermes-paperclip-adapter"


def test_every_seed_discovery_is_internally_consistent() -> None:
    for discovery in seed_discoveries(NOW):
        # __post_init__ already validated + computed discovery_digest; assert
        # recomputing it independently still agrees (catches accidental
        # tampering between construction and use).
        assert discovery.discovery_digest == discovery.expected_digest()


def test_no_seed_evidence_claims_a_security_review() -> None:
    for discovery in seed_discoveries(NOW):
        kinds = {item.source_kind for item in discovery.evidence}
        assert DiscoverySourceKind.SECURITY_REVIEW not in kinds


def test_assessment_of_seed_entries_is_risk_blocked_pending_real_security_review() -> None:
    config = EcosystemCatalogConfig(enabled=True)
    environment = CatalogEnvironment(machines=(), profiles=(), approved_capabilities=())

    for discovery in seed_discoveries(NOW):
        assessment = assess_discovery(
            config,
            discovery,
            environment,
            registry_entry=None,
            evidence_age_seconds=60,
        )

        assert assessment.evidence_complete is False
        assert assessment.admission_readiness in {
            AdmissionReadiness.RISK_BLOCKED,
            AdmissionReadiness.EVIDENCE_INCOMPLETE,
        }
