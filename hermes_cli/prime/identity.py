"""Canonical Prime fleet identity.

Fleet Unification Stage 2B. Consolidates the fragmented per-subsystem identity
shapes already present in this repository — ``sigil.ai.fleet.FleetNodeIdentity``,
``hermes_cli.agent_roles.remote_maintenance.RemoteTarget``,
``hermes_cli.agent_roles.fleet_inventory.InventoryTarget``,
``hermes_cli.hermes_link.models.HermesLinkStatus.node_id`` /
``hermes_cli.hermes_link.security.SigningCredential`` node identifiers, and
``hermes_cli.agent_roles.learning_hierarchy`` node identifiers — into one
canonical, versioned, immutable reference type that events, evidence,
health, and admission records can point to.

"Prime" is the pre-existing, documented (but previously unimplemented) name
for the ecosystem identity/membership/policy authority in this repository
(see ``docs/architecture/hydra-ecosystem/CANONICAL_ARCHITECTURE.md``). This
module is that authority's identity layer.

Identity is a descriptive fact. It grants no authority, no permission, and no
execution capability of any kind. Nothing may treat the existence, presence,
or resolution of a ``FleetIdentity`` record as authorization for anything.
Health, admission, certification, and execution authority are evaluated by
separate modules (``health.py``, ``admission.py``, ``certification.py``) and
must never be inferred from identity alone.

This module intentionally does not import ``sigil`` or ``hermes_link`` types
at module scope (mirroring the existing
``hermes_cli.mission_control.adapters.context_adapter`` convention of staying
duck-typed against sibling subsystems), so registering an identity from any
legacy shape never creates a hard import dependency between packages.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENTITY_SCHEMA_VERSION = 1
SUPPORTED_IDENTITY_SCHEMA_VERSIONS = frozenset({1})


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_IDENTITY_SCHEMA_VERSIONS:
        raise ValueError(
            f"identity schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_IDENTITY_SCHEMA_VERSIONS)})"
        )
    return version


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class IdentityKind(str, Enum):
    FLEET = "fleet"
    NODE = "node"
    SERVICE = "service"
    AGENT = "agent"
    RUNTIME = "runtime"
    OPERATOR = "operator"


class IdentitySource(str, Enum):
    """Which pre-existing subsystem a canonical identity was minted from.

    Recorded so a Stage 2 identity record can always be traced back to the
    concrete legacy record it consolidates, without requiring that legacy
    record's own shape to change.
    """

    SIGIL_FLEET = "sigil_ai_fleet"
    REMOTE_MAINTENANCE = "agent_roles_remote_maintenance"
    FLEET_INVENTORY = "agent_roles_fleet_inventory"
    HERMES_LINK = "hermes_link"
    LEARNING_HIERARCHY = "agent_roles_learning_hierarchy"
    NATIVE = "prime_native"


class IdentityValidationError(ValueError):
    """A canonical identity record failed closed."""


class FleetIdentity(BaseModel):
    """A canonical, immutable identity reference.

    Two ``FleetIdentity`` records with the same ``kind``/``natural_key`` are
    guaranteed to produce the same :attr:`identity_id`, so the same physical
    fleet, node, service, or agent always resolves to the same canonical
    reference no matter which legacy subsystem minted it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: IdentityKind
    natural_key: str = Field(..., min_length=1, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=256)
    source: IdentitySource
    source_reference: str = Field(..., min_length=1, max_length=512)
    fleet_id: Optional[str] = Field(default=None, max_length=128)
    parent_identity_id: Optional[str] = Field(default=None, max_length=128)
    registered_at: int = Field(..., ge=0)
    revoked: bool = False
    revoked_at: Optional[int] = Field(default=None, ge=0)
    revocation_reason: Optional[str] = Field(default=None, max_length=512)
    schema_version: int = Field(default=IDENTITY_SCHEMA_VERSION)

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

    @model_validator(mode="after")
    def _revocation_consistency(self) -> "FleetIdentity":
        if self.revoked and self.revoked_at is None:
            raise ValueError("a revoked identity must record revoked_at")
        if not self.revoked and (self.revoked_at is not None or self.revocation_reason):
            raise ValueError(
                "an active (non-revoked) identity cannot carry revocation metadata"
            )
        return self

    @property
    def identity_id(self) -> str:
        """Deterministic, content-addressed identity ID.

        Derived only from ``kind``/``natural_key`` (never from mutable
        lifecycle fields such as ``revoked``) so the same underlying subject
        always resolves to the same ID across its whole lifecycle.
        """
        payload = {"kind": self.kind.value, "natural_key": self.natural_key}
        return f"fid_{self.kind.value}_{_digest(payload)[:24]}"

    def grants_no_authority(self) -> None:
        """Documentation no-op.

        Identity never grants execution, mutation, remote-maintenance,
        broker-submission, or production authority. Callers tempted to
        branch on "identity exists therefore is allowed to X" should call
        this instead of skipping the check, so a repository-wide grep for
        ``grants_no_authority`` documents every place identity was
        deliberately *not* treated as authorization.
        """
        return None


class IdentityConflictError(IdentityValidationError):
    """Two identities claim the same natural key from incompatible sources."""


class IdentityRegistry:
    """In-memory canonical identity registry.

    Deterministic and conflict-resistant: registering the same
    ``(kind, natural_key)`` pair twice from a different source without an
    explicit ``allow_supersede=True`` is rejected rather than silently
    overwritten, so two unrelated subsystems can never quietly collide on
    the same canonical identity.
    """

    def __init__(self) -> None:
        self._by_id: Dict[str, FleetIdentity] = {}

    def register(
        self, identity: FleetIdentity, *, allow_supersede: bool = False
    ) -> FleetIdentity:
        existing = self._by_id.get(identity.identity_id)
        if existing is not None and not allow_supersede:
            if (
                existing.source != identity.source
                or existing.source_reference != identity.source_reference
            ):
                raise IdentityConflictError(
                    f"identity {identity.identity_id} is already registered from "
                    f"{existing.source.value}:{existing.source_reference}; "
                    "refusing a silent overwrite from "
                    f"{identity.source.value}:{identity.source_reference}"
                )
        self._by_id[identity.identity_id] = identity
        return identity

    def get(self, identity_id: str) -> Optional[FleetIdentity]:
        return self._by_id.get(identity_id)

    def resolve(self, kind: IdentityKind, natural_key: str) -> Optional[FleetIdentity]:
        probe = {"kind": kind.value, "natural_key": natural_key.strip().lower()}
        probe_id = f"fid_{kind.value}_{_digest(probe)[:24]}"
        return self._by_id.get(probe_id)

    def is_known_and_active(self, identity_id: str) -> bool:
        """Fail-closed membership check: unknown identities are never active."""
        identity = self._by_id.get(identity_id)
        return identity is not None and not identity.revoked

    def all(self) -> Tuple[FleetIdentity, ...]:
        return tuple(self._by_id.values())


# ── Adapters from pre-existing per-subsystem identity shapes ───────────────
#
# These accept duck-typed objects (not imported types) so this module never
# creates a hard dependency on the ``sigil`` package or on
# ``hermes_cli.hermes_link`` at import time.


def identity_from_sigil_fleet_node(
    node: object, *, registered_at: int
) -> FleetIdentity:
    """Adapt a ``sigil.ai.fleet.FleetNodeIdentity``-shaped object.

    Expects ``node_id`` and ``node_name`` attributes, matching
    ``sigil.ai.fleet.FleetNodeIdentity``.
    """
    node_id = getattr(node, "node_id")
    node_name = getattr(node, "node_name", None)
    return FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key=node_id,
        display_name=node_name,
        source=IdentitySource.SIGIL_FLEET,
        source_reference=f"sigil.ai.fleet.FleetNodeIdentity:{node_id}",
        registered_at=registered_at,
    )


def identity_from_remote_target(
    target: object, *, registered_at: int, source: IdentitySource
) -> FleetIdentity:
    """Adapt a ``RemoteTarget``/``InventoryTarget``-shaped object.

    Both existing types carry a ``target_id`` field with the same semantics
    (see ``hermes_cli.agent_roles.remote_maintenance.RemoteTarget`` and
    ``hermes_cli.agent_roles.fleet_inventory.InventoryTarget``); ``source``
    must be one of :attr:`IdentitySource.REMOTE_MAINTENANCE` or
    :attr:`IdentitySource.FLEET_INVENTORY` to record which of the two
    near-duplicate legacy shapes produced this identity.
    """
    if source not in (
        IdentitySource.REMOTE_MAINTENANCE,
        IdentitySource.FLEET_INVENTORY,
    ):
        raise IdentityValidationError(
            "identity_from_remote_target requires a remote_maintenance or "
            "fleet_inventory source"
        )
    target_id = getattr(target, "target_id")
    return FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key=target_id,
        source=source,
        source_reference=f"{source.value}:{target_id}",
        registered_at=registered_at,
    )


def identity_from_hermes_link_node(
    node_id: str, node_role: str, *, registered_at: int
) -> FleetIdentity:
    """Adapt a Hermes Link node/role pair.

    ``node_role`` is expected to be one of ``hermes_cli.hermes_link.models``'s
    ``NodeRole`` values (``big_sister``/``little_sister``) passed as a plain
    string to avoid importing the enum type directly.
    """
    return FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key=node_id,
        display_name=node_role,
        source=IdentitySource.HERMES_LINK,
        source_reference=f"hermes_link:{node_role}:{node_id}",
        registered_at=registered_at,
    )


def identity_from_learning_node(
    node_id: str, role: str, *, registered_at: int
) -> FleetIdentity:
    """Adapt a Big Sister / Little Sister learning-hierarchy node."""
    return FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key=node_id,
        display_name=role,
        source=IdentitySource.LEARNING_HIERARCHY,
        source_reference=f"learning_hierarchy:{role}:{node_id}",
        registered_at=registered_at,
    )
