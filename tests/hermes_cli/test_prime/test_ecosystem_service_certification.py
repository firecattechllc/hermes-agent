from __future__ import annotations

from hermes_cli.prime.ecosystem_service_certification import (
    duplicate_and_revoked_service_rejection_selftest,
    ecosystem_evidence_integrity_selftest,
    ecosystem_services_availability_selftest,
    ecosystem_services_no_unsafe_drift_selftest,
    run_all_ecosystem_service_selftests,
    self_evolution_self_approval_guard_selftest,
    unverified_service_rejection_selftest,
)


def test_ecosystem_services_no_unsafe_drift_selftest_passes() -> None:
    assert ecosystem_services_no_unsafe_drift_selftest() is True


def test_ecosystem_services_availability_selftest_passes() -> None:
    assert ecosystem_services_availability_selftest() is True


def test_unverified_service_rejection_selftest_passes() -> None:
    assert unverified_service_rejection_selftest() is True


def test_duplicate_and_revoked_service_rejection_selftest_passes() -> None:
    assert duplicate_and_revoked_service_rejection_selftest() is True


def test_self_evolution_self_approval_guard_selftest_passes() -> None:
    assert self_evolution_self_approval_guard_selftest() is True


def test_ecosystem_evidence_integrity_selftest_passes() -> None:
    assert ecosystem_evidence_integrity_selftest() is True


def test_run_all_ecosystem_service_selftests_all_pass() -> None:
    results = run_all_ecosystem_service_selftests()
    assert all(results.values()), results
    assert set(results.keys()) == {
        "ecosystem_services_no_unsafe_drift",
        "ecosystem_services_availability",
        "unverified_service_rejection",
        "duplicate_and_revoked_service_rejection",
        "self_evolution_self_approval_guard",
        "ecosystem_evidence_integrity",
    }
