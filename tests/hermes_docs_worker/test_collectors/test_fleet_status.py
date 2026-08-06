from __future__ import annotations

from types import SimpleNamespace

from hermes_docs_worker.collectors import fleet_status
from hermes_docs_worker.status import StatusValue


class _FakeRecord:
    def __init__(self, natural_key: str, connection_state: str, revoked: bool = False) -> None:
        self.natural_key = natural_key
        self.connection_state = SimpleNamespace(value=connection_state)
        self.revoked = revoked
        self.last_seen_at = 12345


class _FakeStore:
    def __init__(self, records) -> None:
        self._records = records

    def all(self):
        return self._records


def test_no_registry_entry_is_unknown(worker_config, monkeypatch) -> None:
    monkeypatch.setattr("hermes_cli.prime.fleet_registry.FleetRegistryStore", lambda: _FakeStore(()))
    object.__setattr__(worker_config, "fleet_node_keys", ("prime",))
    facts = {f.label: f for f in fleet_status.collect(worker_config, now=0)}
    assert facts["prime"].status == StatusValue.UNKNOWN


def test_connected_node_is_deployed(worker_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.prime.fleet_registry.FleetRegistryStore",
        lambda: _FakeStore((_FakeRecord("prime", "connected"),)),
    )
    object.__setattr__(worker_config, "fleet_node_keys", ("prime",))
    facts = {f.label: f for f in fleet_status.collect(worker_config, now=0)}
    assert facts["prime"].status == StatusValue.DEPLOYED


def test_revoked_node_is_blocked(worker_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.prime.fleet_registry.FleetRegistryStore",
        lambda: _FakeStore((_FakeRecord("mac", "connected", revoked=True),)),
    )
    object.__setattr__(worker_config, "fleet_node_keys", ("mac",))
    facts = {f.label: f for f in fleet_status.collect(worker_config, now=0)}
    assert facts["mac"].status == StatusValue.BLOCKED


def test_disconnected_node_is_degraded(worker_config, monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.prime.fleet_registry.FleetRegistryStore",
        lambda: _FakeStore((_FakeRecord("hydra_live", "disconnected"),)),
    )
    object.__setattr__(worker_config, "fleet_node_keys", ("hydra_live",))
    facts = {f.label: f for f in fleet_status.collect(worker_config, now=0)}
    assert facts["hydra_live"].status == StatusValue.DEGRADED


def test_registry_failure_is_unknown_not_a_crash(worker_config, monkeypatch) -> None:
    def _raise():
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr("hermes_cli.prime.fleet_registry.FleetRegistryStore", _raise)
    object.__setattr__(worker_config, "fleet_node_keys", ("prime",))
    facts = {f.label: f for f in fleet_status.collect(worker_config, now=0)}
    assert facts["prime"].status == StatusValue.UNKNOWN
