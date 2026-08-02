from dataclasses import replace

import pytest

from sigil.desktop_bridge.bridge import (
    BridgeComponentBinding,
    BridgeComponentKind,
    BridgeEvidenceReference,
    BridgeGateKind,
    BridgeGateState,
    BridgeIntegrationProjection,
    BridgeIntegrationState,
    BridgeLifecycleState,
    SigilBridgeSnapshot,
    SigilBridgeValidationError,
    build_activation_gates,
    build_default_bridge_snapshot,
    build_integration_projection,
)
from sigil.integration_registry import AuthorityDenials


OBSERVED_AT = "2026-08-01T20:45:00Z"
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"


def binding(
    component_id: str,
    kind: BridgeComponentKind,
    identity: str,
) -> BridgeComponentBinding:
    return BridgeComponentBinding(
        component_id=component_id,
        component_kind=kind,
        contract_schema_version=1,
        contract_identity=identity,
    )


def evidence(
    evidence_id: str,
    kind: str,
    identity: str,
) -> BridgeEvidenceReference:
    return BridgeEvidenceReference(
        evidence_id=evidence_id,
        evidence_kind=kind,
        evidence_identity=identity,
    )


def test_default_bridge_is_paper_only_and_fully_denied() -> None:
    snapshot = build_default_bridge_snapshot(observed_at=OBSERVED_AT)
    projection = snapshot.projection()

    assert projection["lifecycle_state"] == "disconnected"
    assert projection["paper_only"] is True
    assert projection["broker_submission"] is False
    assert projection["execution_authorized"] is False
    assert projection["approval_authority"] is False
    assert projection["capital_authority"] is False
    assert projection["portfolio_mutation"] is False
    assert projection["policy_mutation"] is False
    assert projection["credential_access"] is False
    assert projection["arbitrary_shell"] is False
    assert projection["arbitrary_filesystem"] is False
    assert projection["governance_bypass"] is False
    assert projection["activation_authorized"] is False
    assert projection["installation_authorized"] is False
    assert projection["connection_authorized"] is False
    assert projection["dispatch_authorized"] is False


def test_bridge_capability_properties_are_denied() -> None:
    snapshot = build_default_bridge_snapshot(observed_at=OBSERVED_AT)

    assert snapshot.can_connect is False
    assert snapshot.can_dispatch is False
    assert snapshot.can_activate is False
    assert snapshot.can_install is False
    assert snapshot.can_use_credentials is False
    assert snapshot.can_execute_shell is False
    assert snapshot.can_access_filesystem is False
    assert snapshot.can_submit_broker_orders is False


def test_authority_cannot_be_overridden() -> None:
    with pytest.raises(
        SigilBridgeValidationError,
        match="integration authority must remain fully denied",
    ):
        SigilBridgeSnapshot(
            bridge_id="sigil-desktop-hermes-ecosystem",
            observed_at=OBSERVED_AT,
            lifecycle_state=BridgeLifecycleState.DISCONNECTED,
            authority=AuthorityDenials(execution_authorized=True),
        )


def test_bindings_are_canonical_and_deterministic() -> None:
    first = binding(
        "fleet-routing",
        BridgeComponentKind.FLEET_ROUTING,
        DIGEST_A,
    )
    second = binding(
        "integration-registry",
        BridgeComponentKind.INTEGRATION_REGISTRY,
        DIGEST_B,
    )

    left = SigilBridgeSnapshot(
        bridge_id="sigil-desktop-hermes-ecosystem",
        observed_at=OBSERVED_AT,
        lifecycle_state=BridgeLifecycleState.DISCONNECTED,
        component_bindings=(first, second),
    )
    right = SigilBridgeSnapshot(
        bridge_id="sigil-desktop-hermes-ecosystem",
        observed_at=OBSERVED_AT,
        lifecycle_state=BridgeLifecycleState.DISCONNECTED,
        component_bindings=(second, first),
    )

    assert left.component_bindings == right.component_bindings
    assert left.snapshot_identity == right.snapshot_identity


def test_evidence_is_canonical_and_deterministic() -> None:
    first = evidence("routing-evidence", "fleet_route", DIGEST_A)
    second = evidence("registry-evidence", "registry_snapshot", DIGEST_B)

    left = SigilBridgeSnapshot(
        bridge_id="sigil-desktop-hermes-ecosystem",
        observed_at=OBSERVED_AT,
        lifecycle_state=BridgeLifecycleState.DEGRADED,
        evidence_references=(first, second),
    )
    right = SigilBridgeSnapshot(
        bridge_id="sigil-desktop-hermes-ecosystem",
        observed_at=OBSERVED_AT,
        lifecycle_state=BridgeLifecycleState.DEGRADED,
        evidence_references=(second, first),
    )

    assert left.evidence_references == right.evidence_references
    assert left.snapshot_identity == right.snapshot_identity


def test_material_change_changes_snapshot_identity() -> None:
    original = build_default_bridge_snapshot(observed_at=OBSERVED_AT)
    changed = replace(
        original,
        lifecycle_state=BridgeLifecycleState.DEGRADED,
        snapshot_identity="",
    )

    assert original.snapshot_identity != changed.snapshot_identity


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bridge_id", "../bridge", "malformed Sigil bridge ID"),
        (
            "observed_at",
            "not-a-timestamp",
            "bridge observation time must be a canonical UTC timestamp",
        ),
        (
            "snapshot_revision",
            0,
            "bridge snapshot revision must be positive",
        ),
    ],
)
def test_malformed_snapshot_input_fails_closed(
    field: str,
    value: object,
    message: str,
) -> None:
    arguments = {
        "bridge_id": "sigil-desktop-hermes-ecosystem",
        "observed_at": OBSERVED_AT,
        "lifecycle_state": BridgeLifecycleState.DISCONNECTED,
        field: value,
    }

    with pytest.raises(SigilBridgeValidationError, match=message):
        SigilBridgeSnapshot(**arguments)


def test_malformed_component_identity_fails_closed() -> None:
    with pytest.raises(
        SigilBridgeValidationError,
        match="component contract identity must be a SHA-256 identity",
    ):
        binding(
            "fleet-routing",
            BridgeComponentKind.FLEET_ROUTING,
            "not-a-digest",
        )


def test_duplicate_component_bindings_fail_closed() -> None:
    first = binding(
        "fleet-routing",
        BridgeComponentKind.FLEET_ROUTING,
        DIGEST_A,
    )
    second = binding(
        "fleet-routing",
        BridgeComponentKind.FLEET_ROUTING,
        DIGEST_B,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="duplicate bridge component binding",
    ):
        SigilBridgeSnapshot(
            bridge_id="sigil-desktop-hermes-ecosystem",
            observed_at=OBSERVED_AT,
            lifecycle_state=BridgeLifecycleState.DISCONNECTED,
            component_bindings=(first, second),
        )


@pytest.mark.parametrize(
    "prohibited",
    [
        "api_key=super-secret-value",
        "password=hunter-example",
        "/Users/example/private/file",
        "http://127.0.0.1:9000",
    ],
)
def test_sensitive_or_private_material_is_rejected(
    prohibited: str,
) -> None:
    with pytest.raises(SigilBridgeValidationError):
        BridgeComponentBinding(
            component_id=prohibited,
            component_kind=BridgeComponentKind.HERMES_WEBUI,
            contract_schema_version=1,
            contract_identity=DIGEST_A,
        )


def test_supplied_snapshot_identity_must_match_content() -> None:
    with pytest.raises(
        SigilBridgeValidationError,
        match="bridge snapshot identity mismatch",
    ):
        SigilBridgeSnapshot(
            bridge_id="sigil-desktop-hermes-ecosystem",
            observed_at=OBSERVED_AT,
            lifecycle_state=BridgeLifecycleState.DISCONNECTED,
            snapshot_identity=DIGEST_A,
        )


def test_runtime_snapshot_includes_fail_closed_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    from sigil.desktop_bridge.runtime import runtime_snapshot

    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(tmp_path / "bridge-runtime"),
    )
    snapshot = runtime_snapshot(
        now=datetime(2026, 8, 1, 21, 0, tzinfo=UTC),
    )

    aggregate = snapshot["sigil_bridge"]
    bridge = aggregate["bridge"]
    assert bridge["bridge_id"] == "sigil-desktop-hermes-ecosystem"
    assert bridge["lifecycle_state"] == "disconnected"
    assert aggregate["summary"]["integration_count"] == 0
    assert aggregate["summary"]["activated_integration_count"] == 0
    assert bridge["paper_only"] is True
    assert bridge["broker_submission"] is False
    assert bridge["execution_authorized"] is False
    assert bridge["approval_authority"] is False
    assert bridge["capital_authority"] is False
    assert bridge["portfolio_mutation"] is False
    assert bridge["policy_mutation"] is False
    assert bridge["credential_access"] is False
    assert bridge["arbitrary_shell"] is False
    assert bridge["arbitrary_filesystem"] is False
    assert bridge["governance_bypass"] is False
    assert bridge["activation_authorized"] is False
    assert bridge["installation_authorized"] is False
    assert bridge["connection_authorized"] is False
    assert bridge["dispatch_authorized"] is False
    assert snapshot["environment"] == "paper"
    assert snapshot["simulation"] is True
    assert snapshot["broker_submission_available"] is False


def test_runtime_recovers_tampered_bridge_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import json
    from datetime import UTC, datetime

    from sigil.desktop_bridge.runtime import runtime_snapshot

    state_dir = tmp_path / "bridge-tamper"
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(state_dir))

    runtime_snapshot(
        now=datetime(2026, 8, 1, 21, 5, tzinfo=UTC),
    )

    state_path = state_dir / "runtime-state.json"
    envelope = json.loads(state_path.read_text())
    state = envelope["payload"]
    state["sigil_bridge"]["broker_submission"] = True
    state["sigil_bridge"]["activation_authorized"] = True

    from sigil.desktop_bridge.runtime import _digest

    envelope["sha256"] = _digest(state)
    state_path.write_text(json.dumps(envelope))

    recovered = runtime_snapshot(
        now=datetime(2026, 8, 1, 21, 6, tzinfo=UTC),
    )
    bridge = recovered["sigil_bridge"]

    assert bridge["broker_submission"] is False
    assert bridge["activation_authorized"] is False
    assert bridge["installation_authorized"] is False
    assert bridge["credential_access"] is False


def test_unregistered_integration_remains_unregistered() -> None:
    projection = build_integration_projection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        registered=False,
        enabled=False,
        configuration_present=False,
        evidence_available=False,
        schema_compatible=True,
        health_acceptable=False,
        activation_requested=False,
    )

    assert projection.state == BridgeIntegrationState.UNREGISTERED
    assert projection.registered is False
    assert projection.activated is False
    assert projection.can_activate is False


def test_registered_but_disabled_integration_is_disabled() -> None:
    projection = build_integration_projection(
        integration_id="buzz-relay",
        component_kind=BridgeComponentKind.BUZZ_RELAY,
        registered=True,
        enabled=False,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=True,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        evidence_identity=DIGEST_B,
    )

    assert projection.state == BridgeIntegrationState.DISABLED
    assert projection.activation_requested is True
    assert projection.activated is False
    assert projection.activation_authorized is False
    assert projection.installation_authorized is False


def test_schema_incompatibility_blocks_projection() -> None:
    projection = build_integration_projection(
        integration_id="hermes-webui",
        component_kind=BridgeComponentKind.HERMES_WEBUI,
        registered=True,
        enabled=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=False,
        health_acceptable=True,
        activation_requested=True,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        evidence_identity=DIGEST_B,
    )

    assert projection.state == BridgeIntegrationState.BLOCKED
    schema_gate = next(
        gate
        for gate in projection.gates
        if gate.gate_kind == BridgeGateKind.SCHEMA_COMPATIBLE
    )
    assert schema_gate.state == BridgeGateState.BLOCKED
    assert projection.activated is False


def test_missing_configuration_is_incomplete() -> None:
    projection = build_integration_projection(
        integration_id="buzznode",
        component_kind=BridgeComponentKind.BUZZNODE,
        registered=True,
        enabled=True,
        configuration_present=False,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=False,
        registry_identity=DIGEST_A,
        lifecycle_state="sandbox_approved",
        evidence_identity=DIGEST_B,
    )

    assert projection.state == BridgeIntegrationState.INCOMPLETE
    assert projection.configuration_present is False
    assert projection.activated is False


def test_all_satisfied_gates_only_reach_ready_for_configuration() -> None:
    projection = build_integration_projection(
        integration_id="hermes-wiki",
        component_kind=BridgeComponentKind.HERMES_WIKI,
        registered=True,
        enabled=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=True,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        evidence_identity=DIGEST_B,
    )

    assert (
        projection.state
        == BridgeIntegrationState.READY_FOR_CONFIGURATION
    )
    assert projection.activation_requested is True
    assert projection.activated is False
    assert projection.can_activate is False
    assert projection.can_install is False
    assert projection.can_connect is False
    assert projection.can_dispatch is False


def test_gate_order_does_not_change_projection_identity() -> None:
    gates = build_activation_gates(
        registered=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=False,
        registry_identity=DIGEST_A,
        evidence_identity=DIGEST_B,
    )

    left = BridgeIntegrationProjection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        enabled=True,
        gates=gates,
        state=BridgeIntegrationState.READY_FOR_CONFIGURATION,
    )
    right = BridgeIntegrationProjection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        enabled=True,
        gates=tuple(reversed(gates)),
        state=BridgeIntegrationState.READY_FOR_CONFIGURATION,
    )

    assert left.gates == right.gates
    assert left.projection_identity == right.projection_identity


def test_duplicate_activation_gate_fails_closed() -> None:
    gates = build_activation_gates(
        registered=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=False,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="duplicate bridge activation gate",
    ):
        BridgeIntegrationProjection(
            integration_id="paperclip",
            component_kind=BridgeComponentKind.PAPERCLIP,
            registry_identity=None,
            lifecycle_state="discovered",
            enabled=True,
            gates=gates + (gates[0],),
            state=BridgeIntegrationState.READY_FOR_CONFIGURATION,
        )


def test_incomplete_gate_set_fails_closed() -> None:
    gates = build_activation_gates(
        registered=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=False,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="activation gate set is incomplete",
    ):
        BridgeIntegrationProjection(
            integration_id="paperclip",
            component_kind=BridgeComponentKind.PAPERCLIP,
            registry_identity=None,
            lifecycle_state="discovered",
            enabled=True,
            gates=gates[:-1],
            state=BridgeIntegrationState.READY_FOR_CONFIGURATION,
        )


def test_conflicting_derived_state_fails_closed() -> None:
    gates = build_activation_gates(
        registered=False,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=True,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="state conflicts with activation gates",
    ):
        BridgeIntegrationProjection(
            integration_id="paperclip",
            component_kind=BridgeComponentKind.PAPERCLIP,
            registry_identity=None,
            lifecycle_state=None,
            enabled=True,
            gates=gates,
            state=BridgeIntegrationState.READY_FOR_CONFIGURATION,
        )


def test_activation_projection_contains_no_authority() -> None:
    projection = build_integration_projection(
        integration_id="agent-reach",
        component_kind=BridgeComponentKind.AGENT_REACH,
        registered=True,
        enabled=True,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=True,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        evidence_identity=DIGEST_B,
    ).projection()

    assert projection["activated"] is False
    assert projection["activation_authorized"] is False
    assert projection["installation_authorized"] is False
    assert projection["connection_authorized"] is False
    assert projection["dispatch_authorized"] is False
    assert projection["credential_access"] is False
    assert projection["arbitrary_shell"] is False
    assert projection["arbitrary_filesystem"] is False
    assert projection["governance_bypass"] is False


@pytest.mark.parametrize(
    ("adapter_state", "connection_state", "health_state"),
    [
        ("healthy", "connected", "healthy"),
        ("ready", "connected", "healthy"),
        ("degraded", "degraded", "degraded"),
        ("busy", "degraded", "degraded"),
        ("stale", "stale", "blocked"),
        ("unavailable", "unavailable", "blocked"),
        ("offline", "unavailable", "blocked"),
        ("incompatible", "incompatible", "blocked"),
        ("invalid", "invalid", "blocked"),
        ("unknown-value", "unknown", "unknown"),
    ],
)
def test_adapter_health_normalization(
    adapter_state: str,
    connection_state: str,
    health_state: str,
) -> None:
    from sigil.desktop_bridge.bridge import (
        build_connection_projection,
    )

    projection = build_connection_projection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        enabled=True,
        adapter_state=adapter_state,
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_A,
    )

    assert projection.connection_state.value == connection_state
    assert projection.health_state.value == health_state
    assert projection.can_connect is False
    assert projection.can_probe is False
    assert projection.can_authenticate is False
    assert projection.can_dispatch is False


def test_disabled_adapter_remains_blocked_even_with_healthy_evidence() -> None:
    from sigil.desktop_bridge.bridge import (
        BridgeConnectionState,
        BridgeHealthState,
        build_connection_projection,
    )

    projection = build_connection_projection(
        integration_id="buzz-relay",
        component_kind=BridgeComponentKind.BUZZ_RELAY,
        enabled=False,
        adapter_state="healthy",
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_A,
    )

    assert projection.connection_state is BridgeConnectionState.DISABLED
    assert projection.health_state is BridgeHealthState.BLOCKED
    assert projection.connected is False


def test_connection_projection_is_deterministic() -> None:
    from sigil.desktop_bridge.bridge import (
        build_connection_projection,
    )

    left = build_connection_projection(
        integration_id="hermes-webui",
        component_kind=BridgeComponentKind.HERMES_WEBUI,
        enabled=True,
        adapter_state="degraded",
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_A,
    )
    right = build_connection_projection(
        integration_id="hermes-webui",
        component_kind=BridgeComponentKind.HERMES_WEBUI,
        enabled=True,
        adapter_state="degraded",
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_A,
    )

    assert left.projection_identity == right.projection_identity


def test_connection_projection_contains_no_operational_authority() -> None:
    from sigil.desktop_bridge.bridge import (
        build_connection_projection,
    )

    projection = build_connection_projection(
        integration_id="agent-reach",
        component_kind=BridgeComponentKind.AGENT_REACH,
        enabled=True,
        adapter_state="healthy",
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_A,
    ).projection()

    assert projection["connected"] is True
    assert projection["connection_authorized"] is False
    assert projection["probe_authorized"] is False
    assert projection["authentication_authorized"] is False
    assert projection["dispatch_authorized"] is False
    assert projection["credential_access"] is False
    assert projection["arbitrary_shell"] is False
    assert projection["arbitrary_filesystem"] is False
    assert projection["governance_bypass"] is False


def test_connection_projection_rejects_bad_evidence() -> None:
    from sigil.desktop_bridge.bridge import (
        build_connection_projection,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="SHA-256 identity",
    ):
        build_connection_projection(
            integration_id="paperclip",
            component_kind=BridgeComponentKind.PAPERCLIP,
            enabled=True,
            adapter_state="healthy",
            observed_at=OBSERVED_AT,
            evidence_identity="bad-digest",
        )


def test_credential_reference_accepts_opaque_relative_reference() -> None:
    from sigil.desktop_bridge.bridge import BridgeCredentialReference

    reference = BridgeCredentialReference(
        reference_id="alpaca-paper-key",
        integration_id="agent-reach",
        credential_kind="api_key_reference",
        reference="credentials/alpaca/paper-key",
        required=True,
    )

    assert reference.reference == "credentials/alpaca/paper-key"
    assert reference.reference_identity.startswith("sha256:")
    assert reference.credential_access is False
    assert reference.credential_resolution_authorized is False
    assert reference.authentication_authorized is False


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("/Users/operator/.secrets/alpaca", "private host paths"),
        ("../credentials/alpaca", "opaque reference"),
        ("credentials//alpaca", "opaque reference"),
        ("http://127.0.0.1:9000/secret", "private endpoints"),
    ],
)
def test_credential_reference_rejects_non_opaque_locations(
    reference: str,
    message: str,
) -> None:
    from sigil.desktop_bridge.bridge import BridgeCredentialReference

    with pytest.raises(SigilBridgeValidationError, match=message):
        BridgeCredentialReference(
            reference_id="bad-reference",
            integration_id="paperclip",
            credential_kind="api_key_reference",
            reference=reference,
            required=True,
        )


@pytest.mark.parametrize(
    "reference",
    [
        "api_key=sk-secretvalue",
        "password=hunter-example",
        "access_token=ghp_secretvalue",
        "client_secret=example-secret",
    ],
)
def test_credential_reference_rejects_secret_material(
    reference: str,
) -> None:
    from sigil.desktop_bridge.bridge import BridgeCredentialReference

    with pytest.raises(
        SigilBridgeValidationError,
        match="credential material",
    ):
        BridgeCredentialReference(
            reference_id="secret-reference",
            integration_id="paperclip",
            credential_kind="api_key_reference",
            reference=reference,
            required=True,
        )


def test_credential_reference_identity_is_deterministic() -> None:
    from sigil.desktop_bridge.bridge import BridgeCredentialReference

    left = BridgeCredentialReference(
        reference_id="buzz-token-reference",
        integration_id="buzz-relay",
        credential_kind="token_reference",
        reference="credentials/buzz/operator-token",
        required=False,
    )
    right = BridgeCredentialReference(
        reference_id="buzz-token-reference",
        integration_id="buzz-relay",
        credential_kind="token_reference",
        reference="credentials/buzz/operator-token",
        required=False,
    )

    assert left.reference_identity == right.reference_identity


def test_credential_projection_contains_no_access_authority() -> None:
    from sigil.desktop_bridge.bridge import BridgeCredentialReference

    projection = BridgeCredentialReference(
        reference_id="wiki-reference",
        integration_id="hermes-wiki",
        credential_kind="credential_reference",
        reference="credentials/wiki/read-only",
        required=True,
    ).projection()

    assert projection["credential_access"] is False
    assert projection["credential_resolution_authorized"] is False
    assert projection["authentication_authorized"] is False
    assert projection["arbitrary_filesystem"] is False
    assert projection["arbitrary_shell"] is False
    assert projection["governance_bypass"] is False


def test_rollback_metadata_is_descriptive_and_deterministic() -> None:
    from sigil.desktop_bridge.bridge import BridgeRollbackMetadata

    first = evidence(
        "rollback-evidence-b",
        "rollback_review",
        DIGEST_B,
    )
    second = evidence(
        "rollback-evidence-a",
        "registry_snapshot",
        DIGEST_A,
    )

    left = BridgeRollbackMetadata(
        rollback_id="paperclip-rollback",
        integration_id="paperclip",
        rollback_instructions=(
            "Restore the prior reviewed integration projection."
        ),
        disable_instructions=(
            "Keep the Paperclip adapter disabled."
        ),
        quarantine_instructions=(
            "Reject all Paperclip projections."
        ),
        rollback_reference="rollback/paperclip/revision-1",
        evidence_references=(first, second),
    )
    right = BridgeRollbackMetadata(
        rollback_id="paperclip-rollback",
        integration_id="paperclip",
        rollback_instructions=(
            "Restore the prior reviewed integration projection."
        ),
        disable_instructions=(
            "Keep the Paperclip adapter disabled."
        ),
        quarantine_instructions=(
            "Reject all Paperclip projections."
        ),
        rollback_reference="rollback/paperclip/revision-1",
        evidence_references=(second, first),
    )

    assert left.evidence_references == right.evidence_references
    assert left.rollback_identity == right.rollback_identity
    assert left.rollback_execution_authorized is False
    assert left.disable_execution_authorized is False
    assert left.quarantine_execution_authorized is False


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("rollback_instructions", "rollback instructions"),
        ("disable_instructions", "disable instructions"),
        ("quarantine_instructions", "quarantine instructions"),
    ],
)
def test_rollback_metadata_requires_all_instruction_fields(
    field: str,
    message: str,
) -> None:
    from sigil.desktop_bridge.bridge import BridgeRollbackMetadata

    values = {
        "rollback_id": "buzz-rollback",
        "integration_id": "buzz-relay",
        "rollback_instructions": "Restore the prior projection.",
        "disable_instructions": "Keep the adapter disabled.",
        "quarantine_instructions": "Reject all relay projections.",
        "rollback_reference": "rollback/buzz/revision-1",
    }
    values[field] = ""

    with pytest.raises(SigilBridgeValidationError, match=message):
        BridgeRollbackMetadata(**values)


def test_automatic_rollback_is_rejected() -> None:
    from sigil.desktop_bridge.bridge import BridgeRollbackMetadata

    with pytest.raises(
        SigilBridgeValidationError,
        match="automatic rollback",
    ):
        BridgeRollbackMetadata(
            rollback_id="wiki-rollback",
            integration_id="hermes-wiki",
            rollback_instructions="Restore the prior projection.",
            disable_instructions="Keep the adapter disabled.",
            quarantine_instructions="Reject all Wiki projections.",
            rollback_reference="rollback/wiki/revision-1",
            automatic_rollback_enabled=True,
        )


@pytest.mark.parametrize(
    "value",
    [
        "api_key=sk-secretvalue",
        "/Users/operator/private/rollback",
        "http://127.0.0.1:9000/rollback",
    ],
)
def test_rollback_metadata_rejects_sensitive_material(
    value: str,
) -> None:
    from sigil.desktop_bridge.bridge import BridgeRollbackMetadata

    with pytest.raises(SigilBridgeValidationError):
        BridgeRollbackMetadata(
            rollback_id="agent-reach-rollback",
            integration_id="agent-reach",
            rollback_instructions=value,
            disable_instructions="Keep Agent Reach disabled.",
            quarantine_instructions="Reject all Agent Reach envelopes.",
            rollback_reference="rollback/agent-reach/revision-1",
        )


def test_rollback_projection_has_no_execution_authority() -> None:
    from sigil.desktop_bridge.bridge import BridgeRollbackMetadata

    projection = BridgeRollbackMetadata(
        rollback_id="buzznode-rollback",
        integration_id="buzznode",
        rollback_instructions="Restore the prior projection.",
        disable_instructions="Keep Buzznode disabled.",
        quarantine_instructions="Reject all Buzznode projections.",
        rollback_reference="rollback/buzznode/revision-1",
    ).projection()

    assert projection["automatic_rollback_enabled"] is False
    assert projection["rollback_execution_authorized"] is False
    assert projection["disable_execution_authorized"] is False
    assert projection["quarantine_execution_authorized"] is False
    assert projection["credential_access"] is False
    assert projection["arbitrary_filesystem"] is False
    assert projection["arbitrary_shell"] is False
    assert projection["governance_bypass"] is False


def aggregate_fixture():
    from sigil.desktop_bridge.bridge import (
        BridgeCredentialReference,
        BridgeRollbackMetadata,
        SigilBridgeAggregateSnapshot,
        build_connection_projection,
    )

    integration = build_integration_projection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        registered=True,
        enabled=False,
        configuration_present=True,
        evidence_available=True,
        schema_compatible=True,
        health_acceptable=True,
        activation_requested=False,
        registry_identity=DIGEST_A,
        lifecycle_state="certified",
        evidence_identity=DIGEST_B,
    )
    connection = build_connection_projection(
        integration_id="paperclip",
        component_kind=BridgeComponentKind.PAPERCLIP,
        enabled=False,
        adapter_state="disabled",
        observed_at=OBSERVED_AT,
        evidence_identity=DIGEST_B,
    )
    credential = BridgeCredentialReference(
        reference_id="paperclip-token",
        integration_id="paperclip",
        credential_kind="token_reference",
        reference="credentials/paperclip/operator-token",
        required=False,
    )
    rollback = BridgeRollbackMetadata(
        rollback_id="paperclip-rollback",
        integration_id="paperclip",
        rollback_instructions="Restore the prior projection.",
        disable_instructions="Keep Paperclip disabled.",
        quarantine_instructions="Reject all Paperclip projections.",
        rollback_reference="rollback/paperclip/revision-1",
    )
    bridge = build_default_bridge_snapshot(
        observed_at=OBSERVED_AT,
    )

    return SigilBridgeAggregateSnapshot(
        bridge=bridge,
        integrations=(integration,),
        connections=(connection,),
        credential_references=(credential,),
        rollback_metadata=(rollback,),
    )


def test_default_aggregate_is_empty_and_fail_closed() -> None:
    from sigil.desktop_bridge.bridge import (
        build_default_aggregate_snapshot,
    )

    aggregate = build_default_aggregate_snapshot(
        observed_at=OBSERVED_AT,
    )
    projection = aggregate.projection()

    assert projection["summary"]["integration_count"] == 0
    assert projection["summary"]["activated_integration_count"] == 0
    assert projection["paper_only"] is True
    assert projection["broker_submission"] is False
    assert projection["activation_authorized"] is False
    assert projection["installation_authorized"] is False
    assert projection["connection_authorized"] is False
    assert projection["dispatch_authorized"] is False
    assert projection["rollback_execution_authorized"] is False


def test_aggregate_identity_is_deterministic() -> None:
    left = aggregate_fixture()
    right = aggregate_fixture()

    assert left.aggregate_identity == right.aggregate_identity
    assert left.projection() == right.projection()


def test_aggregate_ordering_is_canonical() -> None:
    from dataclasses import replace

    from sigil.desktop_bridge.bridge import (
        SigilBridgeAggregateSnapshot,
    )

    first = aggregate_fixture()
    second_integration = build_integration_projection(
        integration_id="buzz-relay",
        component_kind=BridgeComponentKind.BUZZ_RELAY,
        registered=False,
        enabled=False,
        configuration_present=False,
        evidence_available=False,
        schema_compatible=True,
        health_acceptable=False,
        activation_requested=False,
    )

    left = SigilBridgeAggregateSnapshot(
        bridge=first.bridge,
        integrations=(
            first.integrations[0],
            second_integration,
        ),
        connections=first.connections,
        credential_references=first.credential_references,
        rollback_metadata=first.rollback_metadata,
    )
    right = replace(
        left,
        integrations=tuple(reversed(left.integrations)),
        aggregate_identity="",
    )

    assert left.integrations == right.integrations
    assert left.aggregate_identity == right.aggregate_identity


def test_material_aggregate_change_changes_identity() -> None:
    from dataclasses import replace

    original = aggregate_fixture()
    changed_bridge = replace(
        original.bridge,
        snapshot_revision=2,
        snapshot_identity="",
    )
    changed = replace(
        original,
        bridge=changed_bridge,
        aggregate_identity="",
    )

    assert original.aggregate_identity != changed.aggregate_identity


def test_aggregate_rejects_orphan_connection() -> None:
    from sigil.desktop_bridge.bridge import (
        SigilBridgeAggregateSnapshot,
        build_connection_projection,
    )

    orphan = build_connection_projection(
        integration_id="buzznode",
        component_kind=BridgeComponentKind.BUZZNODE,
        enabled=False,
        adapter_state="disabled",
        observed_at=OBSERVED_AT,
    )

    with pytest.raises(
        SigilBridgeValidationError,
        match="lacks integration projection",
    ):
        SigilBridgeAggregateSnapshot(
            bridge=build_default_bridge_snapshot(
                observed_at=OBSERVED_AT,
            ),
            connections=(orphan,),
        )


def test_aggregate_rejects_duplicate_integrations() -> None:
    from sigil.desktop_bridge.bridge import (
        SigilBridgeAggregateSnapshot,
    )

    aggregate = aggregate_fixture()

    with pytest.raises(
        SigilBridgeValidationError,
        match="duplicate aggregate integration",
    ):
        SigilBridgeAggregateSnapshot(
            bridge=aggregate.bridge,
            integrations=(
                aggregate.integrations[0],
                aggregate.integrations[0],
            ),
        )


def test_aggregate_summary_never_reports_activation() -> None:
    aggregate = aggregate_fixture()
    projection = aggregate.projection()

    assert projection["summary"]["integration_count"] == 1
    assert projection["summary"]["activated_integration_count"] == 0
    assert projection["activation_authorized"] is False
    assert projection["credential_access"] is False
    assert projection["governance_bypass"] is False


def test_mission_control_exposes_aggregate_bridge_without_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    from sigil.desktop_bridge.runtime import (
        runtime_mission_control_status,
        runtime_snapshot,
    )

    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(tmp_path / "mission-control-bridge"),
    )

    runtime_snapshot(
        now=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
    )
    status = runtime_mission_control_status()

    aggregate = status["sigil_bridge"]
    ecosystem = status["ecosystem"]

    assert aggregate["bridge"]["lifecycle_state"] == "disconnected"
    assert aggregate["summary"]["integration_count"] == 0
    assert aggregate["summary"]["activated_integration_count"] == 0
    assert aggregate["paper_only"] is True
    assert aggregate["broker_submission"] is False
    assert aggregate["execution_authorized"] is False
    assert aggregate["credential_access"] is False
    assert aggregate["activation_authorized"] is False
    assert aggregate["installation_authorized"] is False
    assert aggregate["connection_authorized"] is False
    assert aggregate["dispatch_authorized"] is False
    assert aggregate["rollback_execution_authorized"] is False

    assert ecosystem["state"] == "disconnected"
    assert ecosystem["health"] == "blocked"
    assert ecosystem["integration_count"] == 0
    assert ecosystem["connected_integration_count"] == 0
    assert ecosystem["activated_integration_count"] == 0
    assert ecosystem["paper_only"] is True
    assert ecosystem["broker_submission"] is False
    assert ecosystem["credential_access"] is False


def test_mission_control_bridge_is_backward_compatible_with_paper_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    from sigil.desktop_bridge.runtime import (
        runtime_mission_control_status,
        runtime_snapshot,
    )

    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(tmp_path / "mission-control-paper"),
    )

    runtime_snapshot(
        now=datetime(2026, 8, 1, 22, 5, tzinfo=UTC),
    )
    status = runtime_mission_control_status()

    assert status["environment"] == "paper"
    assert status["simulation"] is True
    assert status["execution_authorized"] is False
    assert status["broker_submission_available"] is False
    assert "sigil_bridge" in status
    assert "ecosystem" in status


def test_runner_command_surface_is_unchanged() -> None:
    from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS

    assert "sigil_bridge_activate" not in SUPPORTED_COMMANDS
    assert "sigil_bridge_install" not in SUPPORTED_COMMANDS
    assert "sigil_bridge_connect" not in SUPPORTED_COMMANDS
    assert "sigil_bridge_dispatch" not in SUPPORTED_COMMANDS
    assert "sigil_bridge_credentials" not in SUPPORTED_COMMANDS
    assert "sigil_bridge_rollback" not in SUPPORTED_COMMANDS
