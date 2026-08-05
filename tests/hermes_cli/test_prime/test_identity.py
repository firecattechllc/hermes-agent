from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.identity import (
    FleetIdentity,
    IdentityConflictError,
    IdentityKind,
    IdentityRegistry,
    IdentitySource,
    identity_from_hermes_link_node,
    identity_from_learning_node,
    identity_from_remote_target,
    identity_from_sigil_fleet_node,
)


def _now() -> int:
    return int(time.time())


def test_identity_id_is_deterministic_for_same_kind_and_key() -> None:
    a = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="titan-01",
        source=IdentitySource.NATIVE,
        source_reference="native:titan-01",
        registered_at=_now(),
    )
    b = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="TITAN-01",  # case should normalize identically
        source=IdentitySource.HERMES_LINK,
        source_reference="hermes_link:little_sister:titan-01",
        registered_at=_now(),
    )
    assert a.identity_id == b.identity_id


def test_identity_id_differs_by_kind() -> None:
    a = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="prime",
        source=IdentitySource.NATIVE,
        source_reference="native:prime",
        registered_at=_now(),
    )
    b = FleetIdentity(
        kind=IdentityKind.SERVICE,
        natural_key="prime",
        source=IdentitySource.NATIVE,
        source_reference="native:prime",
        registered_at=_now(),
    )
    assert a.identity_id != b.identity_id


def test_revoked_identity_requires_revoked_at() -> None:
    with pytest.raises(ValidationError):
        FleetIdentity(
            kind=IdentityKind.NODE,
            natural_key="x",
            source=IdentitySource.NATIVE,
            source_reference="native:x",
            registered_at=_now(),
            revoked=True,
        )


def test_active_identity_cannot_carry_revocation_metadata() -> None:
    with pytest.raises(ValidationError):
        FleetIdentity(
            kind=IdentityKind.NODE,
            natural_key="x",
            source=IdentitySource.NATIVE,
            source_reference="native:x",
            registered_at=_now(),
            revoked=False,
            revoked_at=_now(),
        )


def test_registry_unknown_identity_is_not_active() -> None:
    registry = IdentityRegistry()
    assert registry.is_known_and_active("fid_node_doesnotexist") is False
    assert registry.get("fid_node_doesnotexist") is None
    assert registry.resolve(IdentityKind.NODE, "nonexistent") is None


def test_registry_rejects_conflicting_registration() -> None:
    registry = IdentityRegistry()
    now = _now()
    first = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="mac-01",
        source=IdentitySource.HERMES_LINK,
        source_reference="hermes_link:big_sister:mac-01",
        registered_at=now,
    )
    conflicting = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="mac-01",
        source=IdentitySource.SIGIL_FLEET,
        source_reference="sigil.ai.fleet.FleetNodeIdentity:mac-01",
        registered_at=now,
    )
    registry.register(first)
    with pytest.raises(IdentityConflictError):
        registry.register(conflicting)


def test_registry_allows_explicit_supersede() -> None:
    registry = IdentityRegistry()
    now = _now()
    first = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="mac-01",
        source=IdentitySource.HERMES_LINK,
        source_reference="hermes_link:big_sister:mac-01",
        registered_at=now,
    )
    conflicting = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="mac-01",
        source=IdentitySource.SIGIL_FLEET,
        source_reference="sigil.ai.fleet.FleetNodeIdentity:mac-01",
        registered_at=now,
    )
    registry.register(first)
    registry.register(conflicting, allow_supersede=True)
    assert registry.get(first.identity_id).source == IdentitySource.SIGIL_FLEET


def test_identity_revoked_identity_is_not_active() -> None:
    registry = IdentityRegistry()
    now = _now()
    identity = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="x",
        source=IdentitySource.NATIVE,
        source_reference="native:x",
        registered_at=now,
    )
    registry.register(identity)
    revoked = identity.model_copy(
        update={"revoked": True, "revoked_at": now, "revocation_reason": "compromised"}
    )
    registry.register(revoked, allow_supersede=True)
    assert registry.is_known_and_active(identity.identity_id) is False


def test_identity_alone_grants_no_authority_marker() -> None:
    identity = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="x",
        source=IdentitySource.NATIVE,
        source_reference="native:x",
        registered_at=_now(),
    )
    # documentation no-op: exists purely so callers cannot accidentally treat
    # identity resolution as authorization without a visible marker call
    assert identity.grants_no_authority() is None
    assert not hasattr(identity, "execution_authorized")
    assert not hasattr(identity, "authorized")


class _FakeSigilNode:
    node_id = "titan-01"
    node_name = "Titan"


class _FakeRemoteTarget:
    target_id = "hydra-live-01"


def test_adapter_from_sigil_fleet_node() -> None:
    identity = identity_from_sigil_fleet_node(_FakeSigilNode(), registered_at=_now())
    assert identity.source == IdentitySource.SIGIL_FLEET
    assert identity.natural_key == "titan-01"


def test_adapter_from_remote_target_requires_valid_source() -> None:
    with pytest.raises(Exception):
        identity_from_remote_target(
            _FakeRemoteTarget(), registered_at=_now(), source=IdentitySource.NATIVE
        )


def test_adapter_from_hermes_link_and_learning_node() -> None:
    link_identity = identity_from_hermes_link_node(
        "mac-01", "big_sister", registered_at=_now()
    )
    assert link_identity.source == IdentitySource.HERMES_LINK

    learning_identity = identity_from_learning_node(
        "titan-01", "little_sister", registered_at=_now()
    )
    assert learning_identity.source == IdentitySource.LEARNING_HIERARCHY
