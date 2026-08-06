"""Governed Sigil ecosystem bridge contract.

Stage 11 binds the existing Sigil desktop runtime to the governed Hermes
ecosystem through deterministic, descriptive-only projections.

This module performs no networking, dispatch, installation, activation,
credential resolution, shell execution, filesystem access, policy mutation,
portfolio mutation, approval, capital authorization, or broker submission.

Non-authoritative / historical, per Hermes add-on Phase A consolidation
(``docs/roadmap/HERMES_ADDON_ROADMAP.md``): ``tools/computer_use/`` is the
sole authoritative desktop/computer-use system for real, live desktop
actions. This module remains in place only because
``ecosystem_certification.py`` and ``ecosystem_boundary_certification.py``
depend on it as a paper-reference evidence source; no second competing
runtime is built here.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    RegistryValidationError,
)

SIGIL_BRIDGE_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?<![A-Za-z0-9])(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9]{8,}"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|"
    r"[A-Za-z]:\\Users\\)"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)


class SigilBridgeValidationError(ValueError):
    """Sigil bridge input failed closed."""


class BridgeLifecycleState(str, Enum):
    DECLARED = "declared"
    UNAVAILABLE = "unavailable"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    READY_FOR_CONFIGURATION = "ready_for_configuration"


class BridgeComponentKind(str, Enum):
    INTEGRATION_REGISTRY = "integration_registry"
    WORKER_CONTRACT = "worker_contract"
    HERMES_WEBUI = "hermes_webui"
    PAPERCLIP = "paperclip"
    BUZZ_RELAY = "buzz_relay"
    BUZZNODE = "buzznode"
    HERMES_WIKI = "hermes_wiki"
    ECOSYSTEM_CATALOG = "ecosystem_catalog"
    AGENT_REACH = "agent_reach"
    SELF_EVOLUTION = "self_evolution"
    FLEET_ROUTING = "fleet_routing"


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise SigilBridgeValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise SigilBridgeValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise SigilBridgeValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise SigilBridgeValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise SigilBridgeValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise SigilBridgeValidationError(
            f"{label} must be a SHA-256 identity"
        )


@dataclass(frozen=True, slots=True)
class BridgeComponentBinding:
    component_id: str
    component_kind: BridgeComponentKind
    contract_schema_version: int
    contract_identity: str
    lifecycle_state: BridgeLifecycleState = BridgeLifecycleState.DECLARED

    def __post_init__(self) -> None:
        _require_identifier(self.component_id, "bridge component ID")

        if not isinstance(self.component_kind, BridgeComponentKind):
            raise SigilBridgeValidationError(
                "unknown bridge component kind"
            )
        if not isinstance(self.lifecycle_state, BridgeLifecycleState):
            raise SigilBridgeValidationError(
                "unknown bridge component lifecycle state"
            )
        if self.contract_schema_version < 1:
            raise SigilBridgeValidationError(
                "component contract schema version must be positive"
            )

        _require_digest(
            self.contract_identity,
            "component contract identity",
        )
        _validate_sanitized(asdict(self), "bridge component binding")


@dataclass(frozen=True, slots=True)
class BridgeEvidenceReference:
    evidence_id: str
    evidence_kind: str
    evidence_identity: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "bridge evidence ID")
        _require_identifier(self.evidence_kind, "bridge evidence kind")
        _require_digest(
            self.evidence_identity,
            "bridge evidence identity",
        )
        _validate_sanitized(asdict(self), "bridge evidence reference")


@dataclass(frozen=True, slots=True)
class SigilBridgeSnapshot:
    bridge_id: str
    observed_at: str
    lifecycle_state: BridgeLifecycleState
    component_bindings: tuple[BridgeComponentBinding, ...] = ()
    evidence_references: tuple[BridgeEvidenceReference, ...] = ()
    schema_version: int = SIGIL_BRIDGE_SCHEMA_VERSION
    snapshot_revision: int = 1
    snapshot_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()

        canonical_bindings = tuple(
            sorted(
                self.component_bindings,
                key=lambda item: (
                    item.component_kind.value,
                    item.component_id,
                    item.contract_identity,
                ),
            )
        )
        canonical_evidence = tuple(
            sorted(
                self.evidence_references,
                key=lambda item: (
                    item.evidence_kind,
                    item.evidence_id,
                    item.evidence_identity,
                ),
            )
        )

        object.__setattr__(
            self,
            "component_bindings",
            canonical_bindings,
        )
        object.__setattr__(
            self,
            "evidence_references",
            canonical_evidence,
        )

        expected = self.expected_identity()
        if self.snapshot_identity and self.snapshot_identity != expected:
            raise SigilBridgeValidationError(
                "bridge snapshot identity mismatch"
            )
        if not self.snapshot_identity:
            object.__setattr__(
                self,
                "snapshot_identity",
                expected,
            )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["lifecycle_state"] = self.lifecycle_state.value

        payload["component_bindings"] = [
            {
                **item,
                "component_kind": item["component_kind"].value,
                "lifecycle_state": item["lifecycle_state"].value,
            }
            for item in payload["component_bindings"]
        ]

        payload.pop("snapshot_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != SIGIL_BRIDGE_SCHEMA_VERSION:
            raise SigilBridgeValidationError(
                "unsupported Sigil bridge schema"
            )
        if not isinstance(self.lifecycle_state, BridgeLifecycleState):
            raise SigilBridgeValidationError(
                "unknown Sigil bridge lifecycle state"
            )
        if self.lifecycle_state not in {
            BridgeLifecycleState.DECLARED,
            BridgeLifecycleState.UNAVAILABLE,
            BridgeLifecycleState.DISCONNECTED,
            BridgeLifecycleState.DEGRADED,
            BridgeLifecycleState.READY_FOR_CONFIGURATION,
        }:
            raise SigilBridgeValidationError(
                "operational bridge lifecycle states are prohibited"
            )

        _require_identifier(self.bridge_id, "Sigil bridge ID")
        _require_timestamp(self.observed_at, "bridge observation time")

        if self.snapshot_revision < 1:
            raise SigilBridgeValidationError(
                "bridge snapshot revision must be positive"
            )

        component_keys = [
            (binding.component_kind, binding.component_id)
            for binding in self.component_bindings
        ]
        if len(component_keys) != len(set(component_keys)):
            raise SigilBridgeValidationError(
                "duplicate bridge component binding"
            )

        evidence_keys = [
            (reference.evidence_kind, reference.evidence_id)
            for reference in self.evidence_references
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise SigilBridgeValidationError(
                "duplicate bridge evidence reference"
            )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(self.digest_payload(), "Sigil bridge snapshot")

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_activate(self) -> bool:
        return False

    @property
    def can_install(self) -> bool:
        return False

    @property
    def can_use_credentials(self) -> bool:
        return False

    @property
    def can_execute_shell(self) -> bool:
        return False

    @property
    def can_access_filesystem(self) -> bool:
        return False

    @property
    def can_submit_broker_orders(self) -> bool:
        return False

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["snapshot_identity"] = self.snapshot_identity
        payload["paper_only"] = True
        payload["broker_submission"] = False
        payload["execution_authorized"] = False
        payload["approval_authority"] = False
        payload["capital_authority"] = False
        payload["portfolio_mutation"] = False
        payload["policy_mutation"] = False
        payload["credential_access"] = False
        payload["arbitrary_shell"] = False
        payload["arbitrary_filesystem"] = False
        payload["governance_bypass"] = False
        payload["activation_authorized"] = False
        payload["installation_authorized"] = False
        payload["connection_authorized"] = False
        payload["dispatch_authorized"] = False
        return payload


def build_default_bridge_snapshot(
    *,
    observed_at: str,
    snapshot_revision: int = 1,
) -> SigilBridgeSnapshot:
    """Build the default disconnected, descriptive-only bridge snapshot."""

    return SigilBridgeSnapshot(
        bridge_id="sigil-desktop-hermes-ecosystem",
        observed_at=observed_at,
        lifecycle_state=BridgeLifecycleState.DISCONNECTED,
        snapshot_revision=snapshot_revision,
    )


class BridgeGateState(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class BridgeGateKind(str, Enum):
    REGISTERED = "registered"
    CONFIGURATION_PRESENT = "configuration_present"
    EVIDENCE_AVAILABLE = "evidence_available"
    SCHEMA_COMPATIBLE = "schema_compatible"
    HEALTH_ACCEPTABLE = "health_acceptable"
    ACTIVATION_REQUESTED = "activation_requested"


class BridgeIntegrationState(str, Enum):
    UNREGISTERED = "unregistered"
    DISABLED = "disabled"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    READY_FOR_CONFIGURATION = "ready_for_configuration"


@dataclass(frozen=True, slots=True)
class BridgeActivationGate:
    gate_kind: BridgeGateKind
    state: BridgeGateState
    reason: str
    evidence_identity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gate_kind, BridgeGateKind):
            raise SigilBridgeValidationError(
                "unknown bridge activation gate kind"
            )
        if not isinstance(self.state, BridgeGateState):
            raise SigilBridgeValidationError(
                "unknown bridge activation gate state"
            )
        if not self.reason.strip():
            raise SigilBridgeValidationError(
                "bridge activation gate reason is required"
            )
        if self.evidence_identity is not None:
            _require_digest(
                self.evidence_identity,
                "bridge activation gate evidence identity",
            )

        _validate_sanitized(
            asdict(self),
            "bridge activation gate",
        )


@dataclass(frozen=True, slots=True)
class BridgeIntegrationProjection:
    integration_id: str
    component_kind: BridgeComponentKind
    registry_identity: str | None
    lifecycle_state: str | None
    enabled: bool
    gates: tuple[BridgeActivationGate, ...]
    state: BridgeIntegrationState
    projection_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(
            self.integration_id,
            "bridge integration ID",
        )

        if not isinstance(self.component_kind, BridgeComponentKind):
            raise SigilBridgeValidationError(
                "unknown bridge integration component kind"
            )
        if not isinstance(self.state, BridgeIntegrationState):
            raise SigilBridgeValidationError(
                "unknown bridge integration state"
            )
        if self.registry_identity is not None:
            _require_digest(
                self.registry_identity,
                "bridge registry identity",
            )
        if self.lifecycle_state is not None:
            _require_identifier(
                self.lifecycle_state,
                "bridge registry lifecycle state",
            )

        canonical_gates = tuple(
            sorted(
                self.gates,
                key=lambda gate: gate.gate_kind.value,
            )
        )
        object.__setattr__(self, "gates", canonical_gates)

        gate_kinds = [gate.gate_kind for gate in self.gates]
        if len(gate_kinds) != len(set(gate_kinds)):
            raise SigilBridgeValidationError(
                "duplicate bridge activation gate"
            )

        required_gate_kinds = set(BridgeGateKind)
        if set(gate_kinds) != required_gate_kinds:
            raise SigilBridgeValidationError(
                "bridge activation gate set is incomplete"
            )

        expected_state = self.expected_state()
        if self.state != expected_state:
            raise SigilBridgeValidationError(
                "bridge integration state conflicts with activation gates"
            )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(
            self.digest_payload(),
            "bridge integration projection",
        )

        expected_identity = self.expected_identity()
        if (
            self.projection_identity
            and self.projection_identity != expected_identity
        ):
            raise SigilBridgeValidationError(
                "bridge integration projection identity mismatch"
            )
        if not self.projection_identity:
            object.__setattr__(
                self,
                "projection_identity",
                expected_identity,
            )

    def expected_state(self) -> BridgeIntegrationState:
        return _derive_integration_state(
            enabled=self.enabled,
            gates=self.gates,
        )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["component_kind"] = self.component_kind.value
        payload["state"] = self.state.value
        payload["gates"] = [
            {
                **gate,
                "gate_kind": gate["gate_kind"].value,
                "state": gate["state"].value,
            }
            for gate in payload["gates"]
        ]
        payload.pop("projection_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    @property
    def registered(self) -> bool:
        return self._gate_satisfied(BridgeGateKind.REGISTERED)

    @property
    def configuration_present(self) -> bool:
        return self._gate_satisfied(
            BridgeGateKind.CONFIGURATION_PRESENT
        )

    @property
    def evidence_available(self) -> bool:
        return self._gate_satisfied(
            BridgeGateKind.EVIDENCE_AVAILABLE
        )

    @property
    def schema_compatible(self) -> bool:
        return self._gate_satisfied(
            BridgeGateKind.SCHEMA_COMPATIBLE
        )

    @property
    def health_acceptable(self) -> bool:
        return self._gate_satisfied(
            BridgeGateKind.HEALTH_ACCEPTABLE
        )

    @property
    def activation_requested(self) -> bool:
        return self._gate_satisfied(
            BridgeGateKind.ACTIVATION_REQUESTED
        )

    @property
    def activated(self) -> bool:
        return False

    @property
    def activation_authorized(self) -> bool:
        return False

    @property
    def installation_authorized(self) -> bool:
        return False

    @property
    def can_activate(self) -> bool:
        return False

    @property
    def can_install(self) -> bool:
        return False

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    def _gate_satisfied(
        self,
        gate_kind: BridgeGateKind,
    ) -> bool:
        return any(
            gate.gate_kind == gate_kind
            and gate.state == BridgeGateState.SATISFIED
            for gate in self.gates
        )

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["projection_identity"] = self.projection_identity
        payload["registered"] = self.registered
        payload["configuration_present"] = (
            self.configuration_present
        )
        payload["evidence_available"] = self.evidence_available
        payload["schema_compatible"] = self.schema_compatible
        payload["health_acceptable"] = self.health_acceptable
        payload["activation_requested"] = (
            self.activation_requested
        )
        payload["activated"] = False
        payload["activation_authorized"] = False
        payload["installation_authorized"] = False
        payload["connection_authorized"] = False
        payload["dispatch_authorized"] = False
        payload["credential_access"] = False
        payload["arbitrary_shell"] = False
        payload["arbitrary_filesystem"] = False
        payload["governance_bypass"] = False
        return payload


def _derive_integration_state(
    *,
    enabled: bool,
    gates: tuple[BridgeActivationGate, ...],
) -> BridgeIntegrationState:
    by_kind = {
        gate.gate_kind: gate
        for gate in gates
    }

    if (
        by_kind[BridgeGateKind.REGISTERED].state
        != BridgeGateState.SATISFIED
    ):
        return BridgeIntegrationState.UNREGISTERED

    if not enabled:
        return BridgeIntegrationState.DISABLED

    if any(
        gate.state == BridgeGateState.BLOCKED
        for gate in gates
    ):
        return BridgeIntegrationState.BLOCKED

    required_configuration_gates = (
        BridgeGateKind.CONFIGURATION_PRESENT,
        BridgeGateKind.EVIDENCE_AVAILABLE,
        BridgeGateKind.SCHEMA_COMPATIBLE,
        BridgeGateKind.HEALTH_ACCEPTABLE,
    )
    if any(
        by_kind[kind].state != BridgeGateState.SATISFIED
        for kind in required_configuration_gates
    ):
        return BridgeIntegrationState.INCOMPLETE

    return BridgeIntegrationState.READY_FOR_CONFIGURATION


def build_activation_gates(
    *,
    registered: bool,
    configuration_present: bool,
    evidence_available: bool,
    schema_compatible: bool,
    health_acceptable: bool,
    activation_requested: bool,
    registry_identity: str | None = None,
    evidence_identity: str | None = None,
) -> tuple[BridgeActivationGate, ...]:
    """Build a deterministic, descriptive-only activation gate set."""

    return (
        BridgeActivationGate(
            gate_kind=BridgeGateKind.REGISTERED,
            state=(
                BridgeGateState.SATISFIED
                if registered
                else BridgeGateState.UNSATISFIED
            ),
            reason=(
                "Integration is present in the governed registry."
                if registered
                else "Integration is absent from the governed registry."
            ),
            evidence_identity=registry_identity,
        ),
        BridgeActivationGate(
            gate_kind=BridgeGateKind.CONFIGURATION_PRESENT,
            state=(
                BridgeGateState.SATISFIED
                if configuration_present
                else BridgeGateState.UNSATISFIED
            ),
            reason=(
                "A sanitized configuration projection is present."
                if configuration_present
                else "No sanitized configuration projection is present."
            ),
        ),
        BridgeActivationGate(
            gate_kind=BridgeGateKind.EVIDENCE_AVAILABLE,
            state=(
                BridgeGateState.SATISFIED
                if evidence_available
                else BridgeGateState.UNKNOWN
            ),
            reason=(
                "Governed integration evidence is available."
                if evidence_available
                else "Governed integration evidence is unavailable."
            ),
            evidence_identity=evidence_identity,
        ),
        BridgeActivationGate(
            gate_kind=BridgeGateKind.SCHEMA_COMPATIBLE,
            state=(
                BridgeGateState.SATISFIED
                if schema_compatible
                else BridgeGateState.BLOCKED
            ),
            reason=(
                "Integration contract schema is compatible."
                if schema_compatible
                else "Integration contract schema is incompatible."
            ),
        ),
        BridgeActivationGate(
            gate_kind=BridgeGateKind.HEALTH_ACCEPTABLE,
            state=(
                BridgeGateState.SATISFIED
                if health_acceptable
                else BridgeGateState.UNKNOWN
            ),
            reason=(
                "Injected health evidence is acceptable."
                if health_acceptable
                else "Acceptable health evidence is unavailable."
            ),
        ),
        BridgeActivationGate(
            gate_kind=BridgeGateKind.ACTIVATION_REQUESTED,
            state=(
                BridgeGateState.SATISFIED
                if activation_requested
                else BridgeGateState.UNSATISFIED
            ),
            reason=(
                "An activation request is recorded descriptively."
                if activation_requested
                else "No activation request is recorded."
            ),
        ),
    )


def build_integration_projection(
    *,
    integration_id: str,
    component_kind: BridgeComponentKind,
    registered: bool,
    enabled: bool,
    configuration_present: bool,
    evidence_available: bool,
    schema_compatible: bool,
    health_acceptable: bool,
    activation_requested: bool,
    registry_identity: str | None = None,
    lifecycle_state: str | None = None,
    evidence_identity: str | None = None,
) -> BridgeIntegrationProjection:
    """Build a deterministic integration projection without activation."""

    gates = build_activation_gates(
        registered=registered,
        configuration_present=configuration_present,
        evidence_available=evidence_available,
        schema_compatible=schema_compatible,
        health_acceptable=health_acceptable,
        activation_requested=activation_requested,
        registry_identity=registry_identity,
        evidence_identity=evidence_identity,
    )

    derived_state = _derive_integration_state(
        enabled=enabled,
        gates=gates,
    )

    return BridgeIntegrationProjection(
        integration_id=integration_id,
        component_kind=component_kind,
        registry_identity=registry_identity,
        lifecycle_state=lifecycle_state,
        enabled=enabled,
        gates=gates,
        state=derived_state,
    )


class BridgeConnectionState(str, Enum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class BridgeHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BridgeConnectionProjection:
    integration_id: str
    component_kind: BridgeComponentKind
    adapter_state: str
    observed_at: str | None
    evidence_identity: str | None
    connection_state: BridgeConnectionState
    health_state: BridgeHealthState
    reason: str
    projection_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(
            self.integration_id,
            "bridge connection integration ID",
        )

        if not isinstance(self.component_kind, BridgeComponentKind):
            raise SigilBridgeValidationError(
                "unknown bridge connection component kind"
            )
        if not isinstance(self.connection_state, BridgeConnectionState):
            raise SigilBridgeValidationError(
                "unknown bridge connection state"
            )
        if not isinstance(self.health_state, BridgeHealthState):
            raise SigilBridgeValidationError(
                "unknown bridge health state"
            )
        if not self.adapter_state.strip():
            raise SigilBridgeValidationError(
                "bridge adapter state is required"
            )
        if not self.reason.strip():
            raise SigilBridgeValidationError(
                "bridge connection reason is required"
            )
        if self.observed_at is not None:
            _require_timestamp(
                self.observed_at,
                "bridge connection observation time",
            )
        if self.evidence_identity is not None:
            _require_digest(
                self.evidence_identity,
                "bridge connection evidence identity",
            )

        expected_connection, expected_health = (
            normalize_bridge_health(
                enabled=self.connection_state
                != BridgeConnectionState.DISABLED,
                adapter_state=self.adapter_state,
            )
        )
        if (
            self.connection_state != expected_connection
            or self.health_state != expected_health
        ):
            raise SigilBridgeValidationError(
                "bridge connection projection conflicts with adapter state"
            )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(
            self.digest_payload(),
            "bridge connection projection",
        )

        expected_identity = self.expected_identity()
        if (
            self.projection_identity
            and self.projection_identity != expected_identity
        ):
            raise SigilBridgeValidationError(
                "bridge connection projection identity mismatch"
            )
        if not self.projection_identity:
            object.__setattr__(
                self,
                "projection_identity",
                expected_identity,
            )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["component_kind"] = self.component_kind.value
        payload["connection_state"] = self.connection_state.value
        payload["health_state"] = self.health_state.value
        payload.pop("projection_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    @property
    def connected(self) -> bool:
        return self.connection_state == BridgeConnectionState.CONNECTED

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_probe(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["projection_identity"] = self.projection_identity
        payload["connected"] = self.connected
        payload["connection_authorized"] = False
        payload["probe_authorized"] = False
        payload["authentication_authorized"] = False
        payload["dispatch_authorized"] = False
        payload["credential_access"] = False
        payload["arbitrary_shell"] = False
        payload["arbitrary_filesystem"] = False
        payload["governance_bypass"] = False
        return payload


def normalize_bridge_health(
    *,
    enabled: bool,
    adapter_state: str,
) -> tuple[BridgeConnectionState, BridgeHealthState]:
    """Normalize injected adapter status without probing or connecting."""

    normalized = adapter_state.strip().lower()

    if not enabled or normalized == "disabled":
        return (
            BridgeConnectionState.DISABLED,
            BridgeHealthState.BLOCKED,
        )

    if normalized in {
        "healthy",
        "ready",
        "available",
        "accepted",
        "eligible",
    }:
        return (
            BridgeConnectionState.CONNECTED,
            BridgeHealthState.HEALTHY,
        )

    if normalized in {
        "degraded",
        "busy",
        "partial",
        "expiring",
    }:
        return (
            BridgeConnectionState.DEGRADED,
            BridgeHealthState.DEGRADED,
        )

    if normalized == "stale":
        return (
            BridgeConnectionState.STALE,
            BridgeHealthState.BLOCKED,
        )

    if normalized in {
        "unavailable",
        "offline",
        "disconnected",
        "expired",
    }:
        return (
            BridgeConnectionState.UNAVAILABLE,
            BridgeHealthState.BLOCKED,
        )

    if normalized in {
        "incompatible",
        "schema_incompatible",
    }:
        return (
            BridgeConnectionState.INCOMPATIBLE,
            BridgeHealthState.BLOCKED,
        )

    if normalized in {
        "invalid",
        "quarantined",
        "corrupt",
    }:
        return (
            BridgeConnectionState.INVALID,
            BridgeHealthState.BLOCKED,
        )

    return (
        BridgeConnectionState.UNKNOWN,
        BridgeHealthState.UNKNOWN,
    )


def build_connection_projection(
    *,
    integration_id: str,
    component_kind: BridgeComponentKind,
    enabled: bool,
    adapter_state: str,
    observed_at: str | None = None,
    evidence_identity: str | None = None,
    reason: str | None = None,
) -> BridgeConnectionProjection:
    """Build a deterministic health projection from injected evidence."""

    connection_state, health_state = normalize_bridge_health(
        enabled=enabled,
        adapter_state=adapter_state,
    )

    return BridgeConnectionProjection(
        integration_id=integration_id,
        component_kind=component_kind,
        adapter_state=adapter_state,
        observed_at=observed_at,
        evidence_identity=evidence_identity,
        connection_state=connection_state,
        health_state=health_state,
        reason=reason or (
            f"Injected adapter state normalized as "
            f"{connection_state.value}."
        ),
    )


_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise SigilBridgeValidationError(
            f"{label} must be a repository-relative opaque reference"
        )


@dataclass(frozen=True, slots=True)
class BridgeCredentialReference:
    reference_id: str
    integration_id: str
    credential_kind: str
    reference: str
    required: bool
    reference_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(
            self.reference_id,
            "bridge credential reference ID",
        )
        _require_identifier(
            self.integration_id,
            "bridge credential integration ID",
        )
        _require_identifier(
            self.credential_kind,
            "bridge credential kind",
        )
        _validate_sanitized(
            {"reference": self.reference},
            "bridge credential reference",
        )
        _require_relative_reference(
            self.reference,
            "bridge credential reference",
        )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(
            self.digest_payload(),
            "bridge credential reference",
        )

        expected = self.expected_identity()
        if self.reference_identity and self.reference_identity != expected:
            raise SigilBridgeValidationError(
                "bridge credential reference identity mismatch"
            )
        if not self.reference_identity:
            object.__setattr__(
                self,
                "reference_identity",
                expected,
            )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("reference_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    @property
    def credential_access(self) -> bool:
        return False

    @property
    def credential_resolution_authorized(self) -> bool:
        return False

    @property
    def authentication_authorized(self) -> bool:
        return False

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["reference_identity"] = self.reference_identity
        payload["credential_access"] = False
        payload["credential_resolution_authorized"] = False
        payload["authentication_authorized"] = False
        payload["arbitrary_filesystem"] = False
        payload["arbitrary_shell"] = False
        payload["governance_bypass"] = False
        return payload


@dataclass(frozen=True, slots=True)
class BridgeRollbackMetadata:
    rollback_id: str
    integration_id: str
    rollback_instructions: str
    disable_instructions: str
    quarantine_instructions: str
    rollback_reference: str
    evidence_references: tuple[BridgeEvidenceReference, ...] = ()
    automatic_rollback_enabled: bool = False
    rollback_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(
            self.rollback_id,
            "bridge rollback ID",
        )
        _require_identifier(
            self.integration_id,
            "bridge rollback integration ID",
        )
        _require_relative_reference(
            self.rollback_reference,
            "bridge rollback reference",
        )

        required = {
            "rollback instructions": self.rollback_instructions,
            "disable instructions": self.disable_instructions,
            "quarantine instructions": self.quarantine_instructions,
        }
        missing = [
            label
            for label, value in required.items()
            if not value.strip()
        ]
        if missing:
            raise SigilBridgeValidationError(
                f"missing required fields: {', '.join(missing)}"
            )

        if self.automatic_rollback_enabled:
            raise SigilBridgeValidationError(
                "Stage 11 cannot enable automatic rollback execution"
            )

        canonical_evidence = tuple(
            sorted(
                self.evidence_references,
                key=lambda item: (
                    item.evidence_kind,
                    item.evidence_id,
                    item.evidence_identity,
                ),
            )
        )
        object.__setattr__(
            self,
            "evidence_references",
            canonical_evidence,
        )

        evidence_keys = [
            (item.evidence_kind, item.evidence_id)
            for item in self.evidence_references
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise SigilBridgeValidationError(
                "duplicate bridge rollback evidence reference"
            )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(
            self.digest_payload(),
            "bridge rollback metadata",
        )

        expected = self.expected_identity()
        if self.rollback_identity and self.rollback_identity != expected:
            raise SigilBridgeValidationError(
                "bridge rollback identity mismatch"
            )
        if not self.rollback_identity:
            object.__setattr__(
                self,
                "rollback_identity",
                expected,
            )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("rollback_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    @property
    def rollback_execution_authorized(self) -> bool:
        return False

    @property
    def disable_execution_authorized(self) -> bool:
        return False

    @property
    def quarantine_execution_authorized(self) -> bool:
        return False

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["rollback_identity"] = self.rollback_identity
        payload["rollback_execution_authorized"] = False
        payload["disable_execution_authorized"] = False
        payload["quarantine_execution_authorized"] = False
        payload["credential_access"] = False
        payload["arbitrary_filesystem"] = False
        payload["arbitrary_shell"] = False
        payload["governance_bypass"] = False
        return payload


@dataclass(frozen=True, slots=True)
class SigilBridgeAggregateSnapshot:
    bridge: SigilBridgeSnapshot
    integrations: tuple[BridgeIntegrationProjection, ...] = ()
    connections: tuple[BridgeConnectionProjection, ...] = ()
    credential_references: tuple[BridgeCredentialReference, ...] = ()
    rollback_metadata: tuple[BridgeRollbackMetadata, ...] = ()
    aggregate_identity: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        canonical_integrations = tuple(
            sorted(
                self.integrations,
                key=lambda item: (
                    item.component_kind.value,
                    item.integration_id,
                    item.projection_identity,
                ),
            )
        )
        canonical_connections = tuple(
            sorted(
                self.connections,
                key=lambda item: (
                    item.component_kind.value,
                    item.integration_id,
                    item.projection_identity,
                ),
            )
        )
        canonical_credentials = tuple(
            sorted(
                self.credential_references,
                key=lambda item: (
                    item.integration_id,
                    item.credential_kind,
                    item.reference_id,
                    item.reference_identity,
                ),
            )
        )
        canonical_rollbacks = tuple(
            sorted(
                self.rollback_metadata,
                key=lambda item: (
                    item.integration_id,
                    item.rollback_id,
                    item.rollback_identity,
                ),
            )
        )

        object.__setattr__(
            self,
            "integrations",
            canonical_integrations,
        )
        object.__setattr__(
            self,
            "connections",
            canonical_connections,
        )
        object.__setattr__(
            self,
            "credential_references",
            canonical_credentials,
        )
        object.__setattr__(
            self,
            "rollback_metadata",
            canonical_rollbacks,
        )

        self.validate()

        expected = self.expected_identity()
        if self.aggregate_identity and self.aggregate_identity != expected:
            raise SigilBridgeValidationError(
                "Sigil bridge aggregate identity mismatch"
            )
        if not self.aggregate_identity:
            object.__setattr__(
                self,
                "aggregate_identity",
                expected,
            )

    def validate(self) -> None:
        integration_keys = [
            (item.component_kind, item.integration_id)
            for item in self.integrations
        ]
        if len(integration_keys) != len(set(integration_keys)):
            raise SigilBridgeValidationError(
                "duplicate aggregate integration projection"
            )

        connection_keys = [
            (item.component_kind, item.integration_id)
            for item in self.connections
        ]
        if len(connection_keys) != len(set(connection_keys)):
            raise SigilBridgeValidationError(
                "duplicate aggregate connection projection"
            )

        credential_keys = [
            (item.integration_id, item.reference_id)
            for item in self.credential_references
        ]
        if len(credential_keys) != len(set(credential_keys)):
            raise SigilBridgeValidationError(
                "duplicate aggregate credential reference"
            )

        rollback_keys = [
            (item.integration_id, item.rollback_id)
            for item in self.rollback_metadata
        ]
        if len(rollback_keys) != len(set(rollback_keys)):
            raise SigilBridgeValidationError(
                "duplicate aggregate rollback metadata"
            )

        integration_ids = {
            item.integration_id
            for item in self.integrations
        }
        if any(
            item.integration_id not in integration_ids
            for item in self.connections
        ):
            raise SigilBridgeValidationError(
                "connection projection lacks integration projection"
            )
        if any(
            item.integration_id not in integration_ids
            for item in self.credential_references
        ):
            raise SigilBridgeValidationError(
                "credential reference lacks integration projection"
            )
        if any(
            item.integration_id not in integration_ids
            for item in self.rollback_metadata
        ):
            raise SigilBridgeValidationError(
                "rollback metadata lacks integration projection"
            )

        try:
            self.authority.validate()
        except RegistryValidationError as error:
            raise SigilBridgeValidationError(str(error)) from error

        _validate_sanitized(
            self.digest_payload(),
            "Sigil bridge aggregate snapshot",
        )

    def digest_payload(self) -> dict[str, object]:
        return {
            "bridge": self.bridge.projection(),
            "integrations": [
                item.projection()
                for item in self.integrations
            ],
            "connections": [
                item.projection()
                for item in self.connections
            ],
            "credential_references": [
                item.projection()
                for item in self.credential_references
            ],
            "rollback_metadata": [
                item.projection()
                for item in self.rollback_metadata
            ],
            "authority": asdict(self.authority),
        }

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    @property
    def activated_integration_count(self) -> int:
        return 0

    @property
    def connected_integration_count(self) -> int:
        return sum(
            item.connected
            for item in self.connections
        )

    @property
    def blocked_integration_count(self) -> int:
        return sum(
            item.state == BridgeIntegrationState.BLOCKED
            for item in self.integrations
        )

    def projection(self) -> dict[str, object]:
        payload = self.digest_payload()
        payload["aggregate_identity"] = self.aggregate_identity
        payload["summary"] = {
            "integration_count": len(self.integrations),
            "connection_count": len(self.connections),
            "credential_reference_count": len(
                self.credential_references
            ),
            "rollback_metadata_count": len(
                self.rollback_metadata
            ),
            "activated_integration_count": 0,
            "connected_integration_count": (
                self.connected_integration_count
            ),
            "blocked_integration_count": (
                self.blocked_integration_count
            ),
        }
        payload["paper_only"] = True
        payload["broker_submission"] = False
        payload["execution_authorized"] = False
        payload["approval_authority"] = False
        payload["capital_authority"] = False
        payload["portfolio_mutation"] = False
        payload["policy_mutation"] = False
        payload["credential_access"] = False
        payload["arbitrary_shell"] = False
        payload["arbitrary_filesystem"] = False
        payload["governance_bypass"] = False
        payload["activation_authorized"] = False
        payload["installation_authorized"] = False
        payload["connection_authorized"] = False
        payload["dispatch_authorized"] = False
        payload["rollback_execution_authorized"] = False
        return payload


def build_default_aggregate_snapshot(
    *,
    observed_at: str,
    snapshot_revision: int = 1,
) -> SigilBridgeAggregateSnapshot:
    """Build the default empty, disconnected ecosystem aggregate."""

    return SigilBridgeAggregateSnapshot(
        bridge=build_default_bridge_snapshot(
            observed_at=observed_at,
            snapshot_revision=snapshot_revision,
        ),
    )
