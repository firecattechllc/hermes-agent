from __future__ import annotations

from hermes_cli.prime.live_runtime_certification import (
    desktop_use_and_operator_approval_selftest,
    dispatch_routing_and_model_configuration_selftest,
    evidence_integrity_selftest,
    fleet_registry_and_heartbeat_selftest,
    run_all_live_runtime_selftests,
    sigil_isolation_selftest,
)


def test_fleet_registry_and_heartbeat_selftest_passes() -> None:
    assert fleet_registry_and_heartbeat_selftest() is True


def test_dispatch_routing_and_model_configuration_selftest_passes() -> None:
    assert dispatch_routing_and_model_configuration_selftest() is True


def test_desktop_use_and_operator_approval_selftest_passes() -> None:
    assert desktop_use_and_operator_approval_selftest() is True


def test_sigil_isolation_selftest_passes() -> None:
    assert sigil_isolation_selftest() is True


def test_evidence_integrity_selftest_passes() -> None:
    assert evidence_integrity_selftest() is True


def test_run_all_live_runtime_selftests_all_pass() -> None:
    results = run_all_live_runtime_selftests()
    assert all(results.values()), results
    assert set(results.keys()) == {
        "fleet_registry_and_heartbeat",
        "dispatch_routing_and_model_configuration",
        "desktop_use_and_operator_approval",
        "sigil_isolation",
        "evidence_integrity",
    }
