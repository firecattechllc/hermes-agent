from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai.inspection import ai_status
from sigil.integration_registry import (
    AuthorityDenials,
    DurableIntegrationRegistryStore,
    GovernedIntegrationRegistry,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleDecision,
    LifecycleRequest,
    LifecycleState,
    RegistryStorageError,
    RegistryValidationError,
    apply_lifecycle_decision,
    integration_registry_status,
    validate_transition,
)

NOW = "2026-08-01T22:00:00Z"
PIN = "a" * 40


def entry(integration_id: str = "synthetic-reader", **changes: object) -> IntegrationRegistryEntry:
    values: dict[str, object] = {
        "integration_id": integration_id,
        "canonical_project_name": "Synthetic Reader",
        "category": IntegrationCategory.INTERNET_CAPABILITY,
        "repository_url": "https://github.com/example/synthetic-reader",
        "pinned_identity": PIN,
        "release_label": "v1.0.0",
        "upstream_repository_identity": "example/synthetic-reader",
        "maintainer_identity": "example-org",
        "maturity": "experimental",
        "license_classification": "permissive",
        "license_evidence_source": "LICENSE at pinned commit",
        "activity_evidence": "synthetic review evidence",
        "activity_observed_at": NOW,
        "credential_requirements": (),
        "authentication_requirements": (),
        "declared_network_access": ("public_https_read",),
        "declared_egress_destinations": ("public_web",),
        "declared_filesystem_access": (),
        "declared_tool_permissions": ("read_public_content",),
        "declared_shell_process_authority": (),
        "declared_browser_authority": (),
        "declared_execution_model": "descriptive_only",
        "declared_external_data_transmission": ("public_query_text",),
        "install_mechanism": "not_installed",
        "dependency_summary": ("synthetic-dependency@sha256:" + "b" * 64,),
        "supported_machines": ("synthetic_test_machine",),
        "approved_machines": (),
        "supported_profiles": ("sandbox",),
        "approved_profiles": (),
        "capabilities": ("public_read",),
        "integration_overlap": (),
        "known_risks": ("untrusted_content",),
        "threat_model_references": ("HERMES_ECOSYSTEM_THREAT_MODEL.md",),
        "evaluation_evidence_references": ("synthetic-evaluation",),
        "rollback_instructions": "Restore the prior registry revision.",
        "disable_instructions": "Keep registry and integration disabled.",
        "quarantine_instructions": "Mark quarantined and deny admission.",
        "lifecycle_state": LifecycleState.DISCOVERED,
        "lifecycle_reason": "Synthetic deterministic discovery fixture.",
        "created_at": NOW,
        "observed_at": NOW,
    }
    values.update(changes)
    return IntegrationRegistryEntry(**values)


def request(**changes: object) -> LifecycleRequest:
    values: dict[str, object] = {
        "request_id": "request-001",
        "integration_id": "synthetic-reader",
        "current_state": LifecycleState.DISCOVERED,
        "requested_state": LifecycleState.UNDER_REVIEW,
        "reason": "Begin governed review.",
        "requesting_actor_identity": "review-requester",
        "policy_revision": "policy-v1",
        "evidence_references": ("evidence-001",),
        "requested_at": NOW,
    }
    values.update(changes)
    return LifecycleRequest(**values)


def decision(**changes: object) -> LifecycleDecision:
    values: dict[str, object] = {
        "request_id": "request-001",
        "integration_id": "synthetic-reader",
        "deciding_actor_identity": "independent-reviewer",
        "decided_at": NOW,
        "approved": True,
        "rejection_classification": None,
        "resulting_registry_revision": "sha256:" + "c" * 64,
    }
    values.update(changes)
    return LifecycleDecision(**values)


def test_valid_discovered_entry_has_immutable_pin_digest_and_no_authority() -> None:
    value = entry()
    assert value.pinned is True
    assert value.content_digest.startswith("sha256:")
    assert value.can_activate is False
    assert value.authority == AuthorityDenials()


@pytest.mark.parametrize(
    ("pin", "message"),
    [("main", "immutable"), ("latest", "immutable"), ("", "immutable")],
)
def test_mutable_latest_and_missing_pins_are_rejected(pin: str, message: str) -> None:
    with pytest.raises(RegistryValidationError, match=message):
        entry(pinned_identity=pin)


def test_release_digest_is_valid_immutable_pin() -> None:
    assert entry(pinned_identity="sha256:" + "d" * 64).pinned


def test_duplicate_integration_id_and_active_pin_are_rejected() -> None:
    with pytest.raises(RegistryValidationError, match="duplicate integration ID"):
        GovernedIntegrationRegistry((entry(), entry()))
    with pytest.raises(RegistryValidationError, match="duplicate active pinned identity"):
        GovernedIntegrationRegistry((entry(), entry("synthetic-reader-two")))


def test_malformed_and_conflicting_repository_identity_are_rejected() -> None:
    with pytest.raises(RegistryValidationError, match="malformed repository"):
        entry(repository_url="git@example.invalid:repo")
    with pytest.raises(RegistryValidationError, match="conflicting repository"):
        entry(upstream_repository_identity="other/project")


def test_unknown_category_lifecycle_and_schema_fail_closed_on_decode(tmp_path: Path) -> None:
    store = DurableIntegrationRegistryStore(tmp_path.resolve())
    store.replace(GovernedIntegrationRegistry((entry(),)))
    payload = json.loads(store.registry_path.read_text())
    for field, invalid in (("category", "unknown"), ("lifecycle_state", "unknown")):
        changed = json.loads(json.dumps(payload))
        changed["entries"][0][field] = invalid
        store.registry_path.write_text(json.dumps(changed))
        with pytest.raises(RegistryStorageError):
            store.load()
        store.registry_path.write_text(json.dumps(payload))
    payload["schema_version"] = 999
    store.registry_path.write_text(json.dumps(payload))
    with pytest.raises(RegistryStorageError, match="unsupported"):
        store.load()


def test_transition_table_accepts_only_explicit_edges() -> None:
    accepted = {
        LifecycleState.DISCOVERED: {LifecycleState.UNDER_REVIEW, LifecycleState.REJECTED},
        LifecycleState.UNDER_REVIEW: {LifecycleState.REJECTED, LifecycleState.SANDBOX_APPROVED},
        LifecycleState.SANDBOX_APPROVED: {LifecycleState.PILOT, LifecycleState.REJECTED, LifecycleState.QUARANTINED},
        LifecycleState.PILOT: {LifecycleState.CERTIFIED, LifecycleState.REJECTED, LifecycleState.DEPRECATED, LifecycleState.QUARANTINED},
        LifecycleState.CERTIFIED: {LifecycleState.DEPRECATED, LifecycleState.QUARANTINED},
        LifecycleState.DEPRECATED: {LifecycleState.QUARANTINED},
        LifecycleState.REJECTED: set(),
        LifecycleState.QUARANTINED: set(),
    }
    for current in LifecycleState:
        for requested_state in LifecycleState:
            if requested_state in accepted[current]:
                validate_transition(current, requested_state)
            else:
                with pytest.raises(RegistryValidationError, match="denied"):
                    validate_transition(current, requested_state)


@pytest.mark.parametrize("state", [LifecycleState.REJECTED, LifecycleState.QUARANTINED, LifecycleState.CERTIFIED])
def test_no_lifecycle_state_grants_activation(state: LifecycleState) -> None:
    assert entry(lifecycle_state=state).can_activate is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rollback_instructions", "", "rollback instructions"),
        ("credential_requirements", ("api_key=sk-secretvalue",), "credential material"),
        ("declared_egress_destinations", ("http://192.168.1.2:9000",), "private endpoints"),
        ("declared_filesystem_access", ("/Users/private/secret",), "private host paths"),
    ],
)
def test_missing_recovery_credentials_and_private_locations_are_rejected(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(RegistryValidationError, match=message):
        entry(**{field: value})


def test_declared_transmission_and_authority_are_visible_without_granting_authority() -> None:
    registry = GovernedIntegrationRegistry(
        (
            entry(
                declared_shell_process_authority=("bounded_command",),
                declared_filesystem_access=("sandbox_relative",),
                declared_browser_authority=("public_unauthenticated",),
            ),
        )
    )
    assert registry.entries[0].declared_external_data_transmission
    assert registry.entries[0].authority == AuthorityDenials()


def test_digests_revision_and_order_are_deterministic() -> None:
    first = entry("a-entry", repository_url="https://github.com/example/a", upstream_repository_identity="example/a")
    second = entry("b-entry", repository_url="https://github.com/example/b", upstream_repository_identity="example/b", pinned_identity="b" * 40)
    assert entry().content_digest == entry().content_digest
    assert GovernedIntegrationRegistry((second, first)).revision == GovernedIntegrationRegistry((first, second)).revision
    assert [item["integration_id"] for item in GovernedIntegrationRegistry((second, first)).payload()["entries"]] == ["a-entry", "b-entry"]


def test_atomic_store_round_trip_and_corruption_fail_closed(tmp_path: Path) -> None:
    store = DurableIntegrationRegistryStore(tmp_path.resolve())
    registry = GovernedIntegrationRegistry((entry(),))
    assert store.replace(registry) == registry.revision
    assert store.load() == registry
    store.registry_path.write_text("{corrupt")
    with pytest.raises(RegistryStorageError, match="invalid"):
        store.load()


def test_lifecycle_request_cannot_self_approve_and_rejection_does_not_mutate() -> None:
    lifecycle_request = request()
    with pytest.raises(RegistryValidationError, match="self-approve"):
        decision(deciding_actor_identity="review-requester").validate_for(lifecycle_request)
    rejected = decision(approved=False, rejection_classification="insufficient_evidence")
    assert apply_lifecycle_decision(entry(), lifecycle_request, rejected) == entry()


def test_lifecycle_evidence_inputs_reject_credentials_and_private_locations() -> None:
    with pytest.raises(RegistryValidationError, match="credential material"):
        request(reason="api_key=sk-secretvalue")
    with pytest.raises(RegistryValidationError, match="private host paths"):
        request(evidence_references=("/Users/operator/private/evidence",))


def test_approved_transition_revises_entry_but_never_authority() -> None:
    revised = apply_lifecycle_decision(entry(), request(), decision())
    assert revised.lifecycle_state == LifecycleState.UNDER_REVIEW
    assert revised.entry_revision == 2
    assert revised.content_digest != entry().content_digest
    assert revised.authority == AuthorityDenials()
    assert revised.can_activate is False


def test_lifecycle_evidence_is_append_only_hash_linked_and_sanitized(tmp_path: Path) -> None:
    store = DurableIntegrationRegistryStore(tmp_path.resolve())
    first = store.append_decision(request(), decision())
    second_request = request(request_id="request-002")
    second_decision = decision(request_id="request-002")
    second = store.append_decision(second_request, second_decision)
    records = store.read_evidence()
    assert records == (first, second)
    assert second["previous_record_hash"] == first["entry_hash"]
    serialized = json.dumps(records)
    assert "activation_authorized\": false" in serialized
    assert "installation_authorized\": false" in serialized


def test_empty_and_populated_inspection_come_from_actual_storage(tmp_path: Path) -> None:
    environment = {"SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve())}
    empty = integration_registry_status(environment)
    assert empty["state"] == "disabled"
    assert empty["store_health"] == "empty"
    assert empty["entry_count"] == 0
    assert not (tmp_path / "governed-integration-registry-v1").exists()

    registry_entry = entry(
        credential_requirements=("operator_supplied_reference",),
        declared_shell_process_authority=("bounded_command",),
        declared_filesystem_access=("sandbox_relative",),
        declared_browser_authority=("public_unauthenticated",),
    )
    store = DurableIntegrationRegistryStore(tmp_path.resolve())
    store.replace(GovernedIntegrationRegistry((registry_entry,)))
    status = integration_registry_status(environment)
    assert status["entry_count"] == 1
    assert status["pinned_count"] == 1
    assert status["external_transmission_count"] == 1
    assert status["credential_required_count"] == 1
    assert status["declared_authority_counts"] == {
        "shell_process": 1,
        "filesystem": 1,
        "browser": 1,
        "network": 1,
    }
    for denial in as_denials():
        assert status[denial] is (denial == "paper_only")

    projected = ai_status(environment)["integration_registry"]
    assert projected["registry_revision"] == status["registry_revision"]
    assert projected["entry_count"] == 1


def as_denials() -> tuple[str, ...]:
    return tuple(AuthorityDenials.__dataclass_fields__)


def test_invalid_storage_inspection_is_sanitized_and_fail_closed(tmp_path: Path) -> None:
    store = DurableIntegrationRegistryStore(tmp_path.resolve())
    store.replace(GovernedIntegrationRegistry())
    store.registry_path.write_text("private token=secret at /Users/owner/private")
    status = integration_registry_status({"SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve())})
    assert status["state"] == "invalid"
    assert status["entry_count"] == 0
    assert status["reason"] == "registry storage failed integrity validation"
    assert "private" not in json.dumps(status)


def test_authority_claims_fail_closed() -> None:
    with pytest.raises(RegistryValidationError, match="authority"):
        entry(authority=replace(AuthorityDenials(), installation_authorized=True))
