"""Live fleet node registry.

Fleet Unification live-runtime work. Stage 2 (``hermes_cli.prime.identity``,
``hermes_cli.prime.admission``, ``hermes_cli.prime.health``) defined the pure
decision primitives for identity, health, and admission but — per
``docs/architecture/FLEET_UNIFICATION_STAGES_2_9.md`` §8 — never persisted a
concrete node registry or wired a real fleet together. This module is that
durable node registry: it composes ``IdentityRegistry`` with an append-free,
atomically-written, keyed store so the same four intended fleet nodes
(Prime, Titan, Mac, Hydra Live) resolve to stable identities across process
restarts.

This module does not grant execution, admission, or dispatch authority.
Registering a node only makes it *resolvable* — whether it may participate in
governed activity is decided independently by
:class:`hermes_cli.prime.admission.PrimeAdmissionService`, and whether it is
currently healthy is decided independently by
:mod:`hermes_cli.prime.heartbeat`. A caller must consult all three before
routing work to a node.

Registration is fail-closed and default-deny:

- Only the four natural keys in :data:`KNOWN_FLEET_NODES` may register. Any
  other natural key is rejected as ``unknown_node`` — this registry never
  admits an arbitrary, previously-undeclared node.
- A natural key's role is fixed at its first successful registration; a
  later request declaring a different role is rejected as ``role_mismatch``.
- Re-registering an already-registered, non-revoked node without
  ``allow_reregistration=True`` is rejected as ``duplicate_registration`` —
  a caller must explicitly opt into updating an existing node's
  endpoint/capabilities/software version rather than silently overwriting it.
- A revoked node can never re-register through this path at all (even with
  ``allow_reregistration=True``); it is rejected as ``node_revoked``. Nothing
  in this module can un-revoke a node — revocation is permanent for the
  registry's purposes, matching :class:`hermes_cli.prime.identity.FleetIdentity`
  treating ``revoked`` as terminal.
- A request that claims an ``identity_id`` inconsistent with the one this
  registry deterministically derives from ``(role, natural_key)`` is rejected
  as ``identity_mismatch`` before it ever reaches the identity registry.
- Malformed requests (blank/oversized fields, non-HTTP(S) endpoints,
  credentials embedded in the endpoint) fail construction outright via
  pydantic validators, exactly like every other Stage 2 request model.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.parse
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only lock, matches evidence.py
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.prime.identity import (
    FleetIdentity,
    IdentityConflictError,
    IdentityKind,
    IdentityRegistry,
    IdentitySource,
)

FLEET_REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_FLEET_REGISTRY_SCHEMA_VERSIONS = frozenset({1})


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_FLEET_REGISTRY_SCHEMA_VERSIONS:
        raise ValueError(
            f"fleet registry schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_FLEET_REGISTRY_SCHEMA_VERSIONS)})"
        )
    return version


class FleetRegistryError(RuntimeError):
    """Durable fleet registry state failed closed."""


class FleetNodeRole(str, Enum):
    PRIME = "prime"
    TITAN = "titan"
    MAC = "mac"
    HYDRA_LIVE = "hydra_live"


# The closed, intended fleet. Registering any natural key not listed here is
# always rejected — this is deliberately not extensible at runtime; adding a
# node to the fleet is a code change, not a request parameter, mirroring the
# closed `SUPPORTED_SIGIL_OPERATIONS` / `KNOWN_FLEET_NODES` convention already
# used throughout `hermes_cli.prime`.
KNOWN_FLEET_NODES: Dict[str, FleetNodeRole] = {
    "prime": FleetNodeRole.PRIME,
    "titan": FleetNodeRole.TITAN,
    "mac": FleetNodeRole.MAC,
    "hydra-live": FleetNodeRole.HYDRA_LIVE,
}

# Closed capability vocabulary. A node may declare any subset; declaring a
# capability outside this set is malformed, not merely unusual.
KNOWN_FLEET_CAPABILITIES = frozenset({
    "control_plane",
    "standby",
    "worker_heartbeat",
    "local_model_inference",
    "embeddings",
    "sentiment_analysis",
    "desktop_use",
    "remote_maintenance_target",
    "sigil_paper_advisory",
    "governed_maintenance_environment",
    "omniroute_routing",
    "freellmapi_upstream",
})


class FleetNodeConnectionState(str, Enum):
    """Connectivity/health state as observed by :mod:`hermes_cli.prime.heartbeat`.

    ``UNKNOWN`` is the default for a freshly registered node that has not yet
    sent a heartbeat — it must never be treated as healthy or admitted.
    """

    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    REVOKED = "revoked"


class FleetRegistrationOutcome(str, Enum):
    REGISTERED = "registered"
    UPDATED = "updated"
    REJECTED = "rejected"


class FleetRegistrationRejectionCode(str, Enum):
    UNKNOWN_NODE = "unknown_node"
    ROLE_MISMATCH = "role_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    DUPLICATE_REGISTRATION = "duplicate_registration"
    NODE_REVOKED = "node_revoked"


def _validate_endpoint(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("endpoint cannot be blank")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("endpoint must be an http(s) URL")
    if not parsed.hostname:
        raise ValueError("endpoint must declare a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not embed credentials")
    if len(value) > 512:
        raise ValueError("endpoint is oversized")
    return value


class FleetNodeRegistrationRequest(BaseModel):
    """A single request to register or update a fleet node's runtime record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=FLEET_REGISTRY_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    natural_key: str = Field(..., min_length=1, max_length=64)
    role: FleetNodeRole
    display_name: Optional[str] = Field(default=None, max_length=256)
    declared_capabilities: Tuple[str, ...] = ()
    endpoint: str = Field(..., min_length=1, max_length=512)
    software_version: str = Field(..., min_length=1, max_length=64)
    protocol_version: int = Field(..., ge=1)
    claimed_identity_id: Optional[str] = Field(default=None, max_length=128)
    requested_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @field_validator("natural_key")
    @classmethod
    def _normalize_key(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("natural_key cannot be blank")
        return v

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, v: str) -> str:
        return _validate_endpoint(v)

    @field_validator("declared_capabilities")
    @classmethod
    def _check_capabilities(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        unknown = tuple(sorted(set(v) - KNOWN_FLEET_CAPABILITIES))
        if unknown:
            raise ValueError(f"unknown fleet node capabilities: {list(unknown)}")
        return v


class FleetNodeRecord(BaseModel):
    """The durable, current-state record for one fleet node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=FLEET_REGISTRY_SCHEMA_VERSION)
    identity_id: str = Field(..., min_length=1, max_length=128)
    natural_key: str = Field(..., min_length=1, max_length=64)
    role: FleetNodeRole
    display_name: Optional[str] = Field(default=None, max_length=256)
    capabilities: Tuple[str, ...] = ()
    endpoint: str = Field(..., min_length=1, max_length=512)
    software_version: str = Field(..., min_length=1, max_length=64)
    protocol_version: int = Field(..., ge=1)
    registered_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    revoked: bool = False
    revoked_at: Optional[int] = Field(default=None, ge=0)
    revocation_reason: Optional[str] = Field(default=None, max_length=512)
    connection_state: FleetNodeConnectionState = FleetNodeConnectionState.UNKNOWN
    last_seen_at: Optional[int] = Field(default=None, ge=0)
    last_health_report_id: Optional[str] = Field(default=None, max_length=160)
    model_inventory: Tuple[str, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, v: str) -> str:
        return _validate_endpoint(v)

    @model_validator(mode="after")
    def _revocation_consistency(self) -> "FleetNodeRecord":
        if self.revoked and self.revoked_at is None:
            raise ValueError("a revoked fleet node record must record revoked_at")
        if self.revoked and self.connection_state != FleetNodeConnectionState.REVOKED:
            raise ValueError("a revoked fleet node record must have REVOKED connection_state")
        if not self.revoked and self.revoked_at is not None:
            raise ValueError("a non-revoked fleet node record cannot carry revoked_at")
        return self


class FleetRegistrationDecision(BaseModel):
    """A deterministic, content-addressed registration decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(..., min_length=1, max_length=160)
    request_id: str = Field(..., min_length=1, max_length=160)
    natural_key: str = Field(..., min_length=1, max_length=64)
    outcome: FleetRegistrationOutcome
    rejection_code: Optional[FleetRegistrationRejectionCode] = None
    identity_id: Optional[str] = Field(default=None, max_length=128)
    decided_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _rejection_code_required_unless_accepted(self) -> "FleetRegistrationDecision":
        if (
            self.outcome == FleetRegistrationOutcome.REJECTED
            and self.rejection_code is None
        ):
            raise ValueError("a rejected registration decision requires a rejection_code")
        if (
            self.outcome != FleetRegistrationOutcome.REJECTED
            and self.rejection_code is not None
        ):
            raise ValueError("a non-rejected registration decision cannot carry a rejection_code")
        if (
            self.outcome != FleetRegistrationOutcome.REJECTED
            and self.identity_id is None
        ):
            raise ValueError("a successful registration decision requires an identity_id")
        return self


def _default_state_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "prime"


def derive_fleet_node_identity(
    role: FleetNodeRole, natural_key: str, *, registered_at: int
) -> FleetIdentity:
    """Deterministically derive the canonical identity for a fleet node.

    Two calls with the same ``natural_key`` always produce the same
    ``identity_id`` (see ``FleetIdentity.identity_id``), independent of
    ``registered_at`` — this is what lets the registry detect an
    identity-mismatched (spoofed) registration request.
    """
    return FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key=natural_key,
        display_name=role.value,
        source=IdentitySource.NATIVE,
        source_reference=f"prime_fleet_registry:{role.value}:{natural_key}",
        registered_at=registered_at,
    )


class FleetRegistryStore:
    """Durable, atomically-written, keyed store for :class:`FleetNodeRecord`.

    Unlike :class:`hermes_cli.prime.evidence.PrimeEvidenceStore` (an
    append-only hash chain — correct for immutable evidence), node records
    are mutable current-state rows, so this store keeps one JSON document
    keyed by ``natural_key`` and rewrites it atomically (temp file +
    ``os.replace``) under an ``fcntl`` exclusive lock, matching the same
    symlink-rejection and fsync discipline as ``PrimeEvidenceStore``.
    """

    def __init__(self, state_root: Optional[Path] = None) -> None:
        root = state_root if state_root is not None else _default_state_root()
        if not root.is_absolute():
            raise FleetRegistryError("fleet registry state root must be an absolute path")
        if root.is_symlink():
            raise FleetRegistryError("fleet registry state root cannot be a symlink")

        self.directory = root / "fleet-registry-v1"
        self.records_path = self.directory / "nodes.json"
        self.lock_path = self.directory / "nodes.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise FleetRegistryError("fleet registry directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise FleetRegistryError("fleet registry lock cannot be a symlink")

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _read_unlocked(self) -> Dict[str, FleetNodeRecord]:
        if not self.records_path.exists():
            return {}
        if self.records_path.is_symlink():
            raise FleetRegistryError("fleet registry records file cannot be a symlink")
        try:
            raw = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FleetRegistryError("fleet registry records file is unreadable") from error
        if not isinstance(raw, dict):
            raise FleetRegistryError("fleet registry records file shape is invalid")
        try:
            return {key: FleetNodeRecord(**value) for key, value in raw.items()}
        except Exception as error:  # noqa: BLE001 - fail closed on any malformed record
            raise FleetRegistryError("fleet registry records file contains an invalid record") from error

    def _write_unlocked(self, records: Dict[str, FleetNodeRecord]) -> None:
        payload = {key: record.model_dump(mode="json") for key, record in records.items()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.directory), prefix=".nodes.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.records_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def get(self, natural_key: str) -> Optional[FleetNodeRecord]:
        with self._lock():
            return self._read_unlocked().get(natural_key.strip().lower())

    def all(self) -> Tuple[FleetNodeRecord, ...]:
        with self._lock():
            return tuple(self._read_unlocked().values())

    def put(self, record: FleetNodeRecord) -> FleetNodeRecord:
        with self._lock():
            records = self._read_unlocked()
            records[record.natural_key] = record
            self._write_unlocked(records)
        return record


class FleetNodeRegistry:
    """Governed, fail-closed fleet node registration and lookup."""

    def __init__(
        self,
        store: Optional[FleetRegistryStore] = None,
        identity_registry: Optional[IdentityRegistry] = None,
    ) -> None:
        self._store = store or FleetRegistryStore()
        self._identities = identity_registry if identity_registry is not None else IdentityRegistry()

    def register(
        self,
        request: FleetNodeRegistrationRequest,
        *,
        now: int,
        allow_reregistration: bool = False,
    ) -> FleetRegistrationDecision:
        """Deterministically evaluate and (if admitted) persist one registration."""

        def _decision(
            outcome: FleetRegistrationOutcome,
            *,
            rejection_code: Optional[FleetRegistrationRejectionCode] = None,
            identity_id: Optional[str] = None,
        ) -> FleetRegistrationDecision:
            payload = {
                "request_id": request.request_id,
                "natural_key": request.natural_key,
                "outcome": outcome.value,
                "rejection_code": rejection_code.value if rejection_code else None,
                "decided_at": now,
            }
            return FleetRegistrationDecision(
                decision_id=f"pfreg_{_digest(payload)[:24]}",
                request_id=request.request_id,
                natural_key=request.natural_key,
                outcome=outcome,
                rejection_code=rejection_code,
                identity_id=identity_id,
                decided_at=now,
                correlation_id=request.correlation_id,
            )

        expected_role = KNOWN_FLEET_NODES.get(request.natural_key)
        if expected_role is None:
            return _decision(
                FleetRegistrationOutcome.REJECTED,
                rejection_code=FleetRegistrationRejectionCode.UNKNOWN_NODE,
            )
        if request.role != expected_role:
            return _decision(
                FleetRegistrationOutcome.REJECTED,
                rejection_code=FleetRegistrationRejectionCode.ROLE_MISMATCH,
            )

        identity = derive_fleet_node_identity(
            request.role, request.natural_key, registered_at=now
        )
        if (
            request.claimed_identity_id is not None
            and request.claimed_identity_id != identity.identity_id
        ):
            return _decision(
                FleetRegistrationOutcome.REJECTED,
                rejection_code=FleetRegistrationRejectionCode.IDENTITY_MISMATCH,
            )

        existing = self._store.get(request.natural_key)
        if existing is not None:
            if existing.revoked:
                return _decision(
                    FleetRegistrationOutcome.REJECTED,
                    rejection_code=FleetRegistrationRejectionCode.NODE_REVOKED,
                )
            if existing.identity_id != identity.identity_id:
                return _decision(
                    FleetRegistrationOutcome.REJECTED,
                    rejection_code=FleetRegistrationRejectionCode.IDENTITY_MISMATCH,
                )
            if not allow_reregistration:
                return _decision(
                    FleetRegistrationOutcome.REJECTED,
                    rejection_code=FleetRegistrationRejectionCode.DUPLICATE_REGISTRATION,
                )

        try:
            self._identities.register(identity, allow_supersede=True)
        except IdentityConflictError:
            return _decision(
                FleetRegistrationOutcome.REJECTED,
                rejection_code=FleetRegistrationRejectionCode.IDENTITY_MISMATCH,
            )

        record = FleetNodeRecord(
            identity_id=identity.identity_id,
            natural_key=request.natural_key,
            role=request.role,
            display_name=request.display_name,
            capabilities=request.declared_capabilities,
            endpoint=request.endpoint,
            software_version=request.software_version,
            protocol_version=request.protocol_version,
            registered_at=existing.registered_at if existing is not None else now,
            updated_at=now,
            connection_state=(
                existing.connection_state if existing is not None else FleetNodeConnectionState.UNKNOWN
            ),
            last_seen_at=existing.last_seen_at if existing is not None else None,
            last_health_report_id=existing.last_health_report_id if existing is not None else None,
            model_inventory=existing.model_inventory if existing is not None else (),
        )
        self._store.put(record)

        outcome = (
            FleetRegistrationOutcome.UPDATED
            if existing is not None
            else FleetRegistrationOutcome.REGISTERED
        )
        return _decision(outcome, identity_id=identity.identity_id)

    def revoke(
        self, natural_key: str, *, now: int, reason: str
    ) -> FleetNodeRecord:
        """Permanently revoke a fleet node. Idempotent on an already-revoked node."""
        natural_key = natural_key.strip().lower()
        existing = self._store.get(natural_key)
        if existing is None:
            raise FleetRegistryError(f"cannot revoke unknown fleet node {natural_key!r}")
        if existing.revoked:
            return existing
        record = existing.model_copy(
            update={
                "revoked": True,
                "revoked_at": now,
                "revocation_reason": reason,
                "connection_state": FleetNodeConnectionState.REVOKED,
                "updated_at": now,
            }
        )
        return self._store.put(record)

    def get(self, natural_key: str) -> Optional[FleetNodeRecord]:
        return self._store.get(natural_key.strip().lower())

    def all(self) -> Tuple[FleetNodeRecord, ...]:
        return self._store.all()

    def is_admissible_node(self, natural_key: str) -> bool:
        """Fail-closed membership check: unregistered/revoked nodes are never admissible."""
        record = self.get(natural_key)
        return record is not None and not record.revoked

    @property
    def identity_registry(self) -> IdentityRegistry:
        return self._identities
