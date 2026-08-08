from __future__ import annotations

from hermes_cli.prime.production_certification_selftests import (
    admission_default_deny_selftest,
    event_schema_valid_selftest,
    health_protocol_compatible_selftest,
    identity_registry_conflict_free_selftest,
    remote_maintenance_default_deny_selftest,
    run_all_production_certification_selftests,
    sigil_contract_restrictions_selftest,
)


def test_identity_registry_conflict_free_selftest_passes() -> None:
    assert identity_registry_conflict_free_selftest() is True


def test_event_schema_valid_selftest_passes() -> None:
    assert event_schema_valid_selftest() is True


def test_health_protocol_compatible_selftest_passes() -> None:
    assert health_protocol_compatible_selftest() is True


def test_admission_default_deny_selftest_passes() -> None:
    assert admission_default_deny_selftest() is True


def test_sigil_contract_restrictions_selftest_passes() -> None:
    assert sigil_contract_restrictions_selftest() is True


def test_remote_maintenance_default_deny_selftest_passes() -> None:
    assert remote_maintenance_default_deny_selftest() is True


def test_run_all_production_certification_selftests_all_pass() -> None:
    results = run_all_production_certification_selftests()
    assert all(results.values()), results
    assert set(results.keys()) == {
        "identity_registry_conflict_free",
        "event_schema_valid",
        "health_protocol_compatible",
        "admission_default_deny",
        "sigil_contract_restrictions",
        "remote_maintenance_default_deny",
    }


def test_identity_registry_conflict_free_selftest_detects_real_regression(monkeypatch) -> None:
    """A selftest that always returns True regardless of behavior is worthless.

    Prove this one actually depends on IdentityRegistry raising on conflict:
    break the conflict detection and confirm the selftest starts failing.
    """
    import hermes_cli.prime.production_certification_selftests as module

    class _NeverConflicts:
        def register(self, identity, *, allow_supersede: bool = False):
            return identity

        def resolve(self, kind, natural_key):
            return None

    monkeypatch.setattr(module, "IdentityRegistry", lambda: _NeverConflicts())
    assert module.identity_registry_conflict_free_selftest() is False


def test_admission_default_deny_selftest_detects_real_regression(monkeypatch) -> None:
    import hermes_cli.prime.production_certification_selftests as module

    class _AlwaysAdmits:
        def evaluate(self, request, *, now: int):
            from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome

            return AdmissionDecision(
                decision_id="padm_forced",
                request_id=request.request_id,
                subject_identity_id=request.subject_identity_id,
                outcome=AdmissionOutcome.ADMITTED,
                reason_codes=(),
                policy_version=request.policy_version,
                decided_at=now,
                revalidate_after=now + 3600,
            )

    monkeypatch.setattr(module, "PrimeAdmissionService", lambda: _AlwaysAdmits())
    assert module.admission_default_deny_selftest() is False
