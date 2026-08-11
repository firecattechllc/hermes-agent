from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.fleet_registry import (
    FleetNodeConnectionState,
    FleetNodeRecord,
    FleetNodeRegistrationRequest,
    FleetNodeRegistry,
    FleetNodeRole,
    FleetRegistrationOutcome,
    FleetRegistrationRejectionCode,
    FleetRegistryStore,
    derive_fleet_node_identity,
)


def _now() -> int:
    return int(time.time())


def _registry(tmp_path) -> FleetNodeRegistry:
    return FleetNodeRegistry(store=FleetRegistryStore(state_root=tmp_path / "prime"))


def _request(**overrides) -> FleetNodeRegistrationRequest:
    fields = dict(
        request_id="req-1",
        natural_key="titan",
        role=FleetNodeRole.TITAN,
        display_name="Titan",
        declared_capabilities=("worker_heartbeat", "local_model_inference"),
        endpoint="http://titan.tailnet.internal:11434",
        software_version="1.0.0",
        protocol_version=1,
        requested_at=_now(),
    )
    fields.update(overrides)
    return FleetNodeRegistrationRequest(**fields)


def test_identity_derivation_is_deterministic() -> None:
    a = derive_fleet_node_identity(FleetNodeRole.TITAN, "titan", registered_at=100)
    b = derive_fleet_node_identity(FleetNodeRole.TITAN, "titan", registered_at=999)
    assert a.identity_id == b.identity_id


def test_register_new_node_succeeds(tmp_path) -> None:
    registry = _registry(tmp_path)
    decision = registry.register(_request(), now=_now())
    assert decision.outcome == FleetRegistrationOutcome.REGISTERED
    assert decision.rejection_code is None
    record = registry.get("titan")
    assert record is not None
    assert record.role == FleetNodeRole.TITAN
    assert record.connection_state == FleetNodeConnectionState.UNKNOWN
    assert not record.revoked


def test_register_all_three_active_fleet_nodes(tmp_path) -> None:
    registry = _registry(tmp_path)
    for natural_key, role in (
        ("prime", FleetNodeRole.PRIME),
        ("titan", FleetNodeRole.TITAN),
        ("mac", FleetNodeRole.MAC),
    ):
        decision = registry.register(
            _request(natural_key=natural_key, role=role, request_id=f"req-{natural_key}"),
            now=_now(),
        )
        assert decision.outcome == FleetRegistrationOutcome.REGISTERED, natural_key
    assert {r.natural_key for r in registry.all()} == {"prime", "titan", "mac"}


def test_retired_hydra_live_cannot_register_or_be_admitted(tmp_path) -> None:
    registry = _registry(tmp_path)
    decision = registry.register(
        _request(natural_key="hydra-live", role=FleetNodeRole.HYDRA_LIVE),
        now=_now(),
    )
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.UNKNOWN_NODE
    assert registry.is_admissible_node("hydra-live") is False


def test_historical_hydra_live_record_remains_parseable_but_inactive(tmp_path) -> None:
    store = FleetRegistryStore(state_root=tmp_path / "prime")
    store.put(
        FleetNodeRecord(
            identity_id="fid_node_hydra_live_historical",
            natural_key="hydra-live",
            role=FleetNodeRole.HYDRA_LIVE,
            endpoint="http://hydra-live.invalid:3130",
            software_version="historical",
            protocol_version=1,
            registered_at=1,
            updated_at=1,
        )
    )
    registry = FleetNodeRegistry(store=store)

    assert registry.get("hydra-live").role == FleetNodeRole.HYDRA_LIVE
    assert {record.natural_key for record in registry.historical_records()} == {"hydra-live"}
    assert registry.all() == ()
    assert registry.is_admissible_node("hydra-live") is False


def test_unknown_node_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    request = _request(natural_key="attacker-node", role=FleetNodeRole.TITAN)
    decision = registry.register(request, now=_now())
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.UNKNOWN_NODE
    assert registry.get("attacker-node") is None


def test_role_mismatch_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    request = _request(natural_key="titan", role=FleetNodeRole.MAC)
    decision = registry.register(request, now=_now())
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.ROLE_MISMATCH


def test_duplicate_registration_is_rejected_without_explicit_reregistration(tmp_path) -> None:
    registry = _registry(tmp_path)
    first = registry.register(_request(), now=_now())
    assert first.outcome == FleetRegistrationOutcome.REGISTERED

    second = registry.register(_request(request_id="req-2"), now=_now())
    assert second.outcome == FleetRegistrationOutcome.REJECTED
    assert second.rejection_code == FleetRegistrationRejectionCode.DUPLICATE_REGISTRATION


def test_explicit_reregistration_updates_the_existing_node(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.register(_request(), now=_now())
    updated = registry.register(
        _request(request_id="req-2", endpoint="http://titan-new.tailnet.internal:11434"),
        now=_now() + 10,
        allow_reregistration=True,
    )
    assert updated.outcome == FleetRegistrationOutcome.UPDATED
    record = registry.get("titan")
    assert record.endpoint == "http://titan-new.tailnet.internal:11434"


def test_revoked_node_can_never_reregister(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.register(_request(), now=_now())
    registry.revoke("titan", now=_now(), reason="compromised credential")

    decision = registry.register(
        _request(request_id="req-2"), now=_now(), allow_reregistration=True
    )
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.NODE_REVOKED
    assert registry.get("titan").revoked is True


def test_identity_mismatched_claim_is_rejected(tmp_path) -> None:
    registry = _registry(tmp_path)
    request = _request(claimed_identity_id="fid_node_deadbeefdeadbeefdeadbeef")
    decision = registry.register(request, now=_now())
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.IDENTITY_MISMATCH


@pytest.mark.parametrize(
    "overrides",
    [
        {"natural_key": ""},
        {"endpoint": "not-a-url"},
        {"endpoint": "http://user:pass@titan.tailnet.internal"},
        {"endpoint": "ftp://titan.tailnet.internal"},
        {"software_version": ""},
        {"declared_capabilities": ("arbitrary_shell_access",)},
    ],
)
def test_malformed_requests_are_rejected_at_construction(overrides) -> None:
    with pytest.raises(ValidationError):
        _request(**overrides)


def test_is_admissible_node_is_fail_closed(tmp_path) -> None:
    registry = _registry(tmp_path)
    assert registry.is_admissible_node("titan") is False
    registry.register(_request(), now=_now())
    assert registry.is_admissible_node("titan") is True
    registry.revoke("titan", now=_now(), reason="rotation")
    assert registry.is_admissible_node("titan") is False


def test_registry_state_persists_across_instances(tmp_path) -> None:
    state_root = tmp_path / "prime"
    first = FleetNodeRegistry(store=FleetRegistryStore(state_root=state_root))
    first.register(_request(), now=_now())

    second = FleetNodeRegistry(store=FleetRegistryStore(state_root=state_root))
    record = second.get("titan")
    assert record is not None
    assert record.natural_key == "titan"


def test_registry_records_file_cannot_be_a_symlink(tmp_path) -> None:
    state_root = tmp_path / "prime"
    store = FleetRegistryStore(state_root=state_root)
    store.directory.mkdir(parents=True)
    real_target = tmp_path / "elsewhere.json"
    real_target.write_text("{}", encoding="utf-8")
    store.records_path.symlink_to(real_target)

    with pytest.raises(Exception):
        store.get("titan")
