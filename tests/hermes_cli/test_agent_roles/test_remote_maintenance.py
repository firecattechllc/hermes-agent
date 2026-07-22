from __future__ import annotations

import json

import pytest

from hermes_cli.agent_roles.hydra_live_playbooks import (
    duplicate_tailscale_playbook,
    heartbeat_interactive_sudo_playbook,
)
from hermes_cli.agent_roles.remote_maintenance import (
    ApprovalScope,
    CertificationObservation,
    CommandResult,
    FileSnapshot,
    FindingCode,
    GovernedMaintenanceExecutor,
    MaintenanceEvidence,
    RemoteTarget,
    RepairApproval,
    RepairProposal,
    RepairStep,
    RiskLevel,
    CommandMode,
    SecretReference,
    SSHInspectionAdapter,
    certify_hydra_live,
    diagnose_hydra_live,
    redact_evidence,
)


def target() -> RemoteTarget:
    return RemoteTarget(target_id="hydra-live-sim", host_alias="hydra-live-fixture",
                        user="hydra", credential=SecretReference(provider="runtime", key="hydra-live-ssh"),
                        private_addresses=("10.44.0.7",))


def evidence(command_id: str, output: str, *, ok: bool = True) -> MaintenanceEvidence:
    return MaintenanceEvidence.build(target=target(), command_id=command_id,
        result=CommandResult(exit_code=0 if ok else 1, stdout=output), collected_at=100)


def findings():
    return diagnose_hydra_live((
        evidence("journal_excerpt", "sudo: a terminal is required; password is required"),
        evidence("service_state", "tailscaled.service active; snap.tailscale.tailscaled.service failed"),
        evidence("listening_sockets", "hydra-live.service hydra-lived LISTEN :3130"),
    ))


def approval(proposal, *scopes: ApprovalScope) -> RepairApproval:
    return RepairApproval(approval_id="approval-1", proposal_id=proposal.proposal_id,
        proposal_checksum=proposal.checksum, scopes=scopes, actor_id="operator", approved_at=101,
        reason="approved exact simulated proposal")


class FakeAdapter:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self.rollback_count = 0

    def snapshot(self, path, timestamp):
        return FileSnapshot(path=path, snapshot_ref="fixture://snapshot/heartbeat", owner="root",
            group="root", mode="0755", checksum="a" * 64, captured_at=timestamp)

    def execute(self, command_id):
        self.executed.append(command_id)
        return self.results.pop(0)

    def rollback(self, manifest):
        self.rollback_count += 1
        return (evidence("rollback", f"restored {manifest.manifest_id}"),)


def test_remote_target_requires_reference_and_configured_destination():
    with pytest.raises(ValueError):
        SecretReference(provider="runtime", key="token=actual-secret")
    with pytest.raises(ValueError):
        RemoteTarget(target_id="x", user="hydra", credential=SecretReference(provider="runtime", key="ref"))


def test_ssh_discovery_is_closed_read_only_allow_list():
    calls = []
    adapter = SSHInspectionAdapter(target(), lambda argv, timeout: calls.append((argv, timeout)) or CommandResult(exit_code=0, stdout="active"))
    item = adapter.inspect("service_state", subject="hydra-live.service", collected_at=100)
    assert item.successful
    assert calls[0][0][-4:] == ("systemctl", "show", "--no-page", "hydra-live.service")
    with pytest.raises(PermissionError):
        adapter.inspect("systemctl_restart", collected_at=100)
    with pytest.raises(ValueError):
        adapter.inspect("service_state", subject="x; reboot", collected_at=100)


def test_redaction_covers_required_secret_classes():
    raw = """Authorization: Bearer abc123
token=hunter2 password=swordfish SECRET=value API_KEY=key
machine-id=0123456789abcdef0123456789abcdef boot-id=deadbeef-dead-beef-dead-beefdeadbeef
-----BEGIN OPENSSH PRIVATE KEY-----\nprivate material\n-----END OPENSSH PRIVATE KEY-----
peer=10.44.0.7
"""
    clean = redact_evidence(raw, private_addresses=("10.44.0.7",))
    for secret in ("abc123", "hunter2", "swordfish", "private material", "10.44.0.7", "0123456789abcdef"):
        assert secret not in clean


def test_simulated_incident_diagnosis_and_playbooks_are_deterministic():
    diagnosed = findings()
    assert {item.code for item in diagnosed} == {
        FindingCode.INTERACTIVE_SUDO_SYSTEMD, FindingCode.DUPLICATE_TAILSCALE,
        FindingCode.HYDRA_PORT_HEALTHY,
    }
    heartbeat = heartbeat_interactive_sudo_playbook("hydra-live-sim", diagnosed)
    tailscale = duplicate_tailscale_playbook("hydra-live-sim", diagnosed)
    destructive = duplicate_tailscale_playbook("hydra-live-sim", diagnosed, remove_snap=True)
    assert heartbeat == heartbeat_interactive_sudo_playbook("hydra-live-sim", diagnosed)
    assert heartbeat.steps[0].required_approvals == (ApprovalScope.MODIFY_HYDRA,)
    assert tailscale.steps[0].command_id == "disable_snap_tailscale"
    assert destructive.steps[0].required_approvals == (ApprovalScope.REMOVE_PACKAGE,)


@pytest.mark.parametrize("scope", [
    ApprovalScope.MODIFY_HYDRA, ApprovalScope.MODIFY_SYSTEMD, ApprovalScope.MODIFY_SUDOERS,
    ApprovalScope.DISABLE_PACKAGE, ApprovalScope.REMOVE_PACKAGE, ApprovalScope.RESTART_TAILSCALE,
    ApprovalScope.RESTART_SSH, ApprovalScope.FIREWALL, ApprovalScope.REBOOT,
])
def test_each_sensitive_scope_requires_exact_approval(scope):
    commands = {
        ApprovalScope.MODIFY_HYDRA: ("atomic_patch_heartbeat_no_sudo", CommandMode.REVERSIBLE),
        ApprovalScope.MODIFY_SYSTEMD: ("atomic_replace_systemd_unit", CommandMode.REVERSIBLE),
        ApprovalScope.MODIFY_SUDOERS: ("atomic_replace_sudoers", CommandMode.REVERSIBLE),
        ApprovalScope.DISABLE_PACKAGE: ("disable_snap_tailscale", CommandMode.REVERSIBLE),
        ApprovalScope.REMOVE_PACKAGE: ("remove_snap_tailscale", CommandMode.DESTRUCTIVE),
        ApprovalScope.RESTART_TAILSCALE: ("restart_tailscale", CommandMode.CONNECTIVITY),
        ApprovalScope.RESTART_SSH: ("restart_ssh", CommandMode.CONNECTIVITY),
        ApprovalScope.FIREWALL: ("change_firewall", CommandMode.CONNECTIVITY),
        ApprovalScope.REBOOT: ("reboot", CommandMode.CONNECTIVITY),
    }
    command, mode = commands[scope]
    proposal = RepairProposal.build(target_id="hydra-live-sim", risk=RiskLevel.CRITICAL,
        expected_downtime="fixture", finding_refs=(FindingCode.DUPLICATE_TAILSCALE,),
        evidence_refs=("e1",), steps=(RepairStep(step_id="fixture", command_id=command,
            mode=mode, required_approvals=(scope,), rollback_command_id="fixture_rollback"),))
    with pytest.raises(PermissionError, match=scope.value):
        GovernedMaintenanceExecutor().execute(proposal=proposal, approvals=(),
            adapter=FakeAdapter([CommandResult(exit_code=0)]), timestamp=102)


def test_unapproved_execution_is_rejected_before_snapshot_or_mutation():
    proposal = heartbeat_interactive_sudo_playbook("hydra-live-sim", findings())
    adapter = FakeAdapter([CommandResult(exit_code=0)])
    with pytest.raises(PermissionError):
        GovernedMaintenanceExecutor().execute(proposal=proposal, approvals=(), adapter=adapter, timestamp=102)
    assert adapter.executed == []


def test_arbitrary_mutation_command_and_tampered_proposal_fail_closed():
    with pytest.raises(ValueError, match="integrity"):
        heartbeat_interactive_sudo_playbook("hydra-live-sim", findings()).model_copy(
            update={"checksum": "0" * 64}
        ).__class__.model_validate(
            heartbeat_interactive_sudo_playbook("hydra-live-sim", findings()).model_dump() | {"checksum": "0" * 64}
        )
    proposal = RepairProposal.build(target_id="hydra-live-sim", risk=RiskLevel.HIGH,
        expected_downtime="fixture", finding_refs=(FindingCode.DUPLICATE_TAILSCALE,), evidence_refs=("e1",),
        steps=(RepairStep(step_id="bad", command_id="arbitrary_shell", mode=CommandMode.DESTRUCTIVE,
            required_approvals=(ApprovalScope.REMOVE_PACKAGE,), rollback_command_id="rollback"),))
    with pytest.raises(PermissionError, match="not allow-listed"):
        GovernedMaintenanceExecutor().execute(proposal=proposal,
            approvals=(approval(proposal, ApprovalScope.REMOVE_PACKAGE),),
            adapter=FakeAdapter([CommandResult(exit_code=0)]), timestamp=102)


def test_execution_snapshots_before_mutation_and_rolls_back_on_failure():
    proposal = heartbeat_interactive_sudo_playbook("hydra-live-sim", findings())
    adapter = FakeAdapter([CommandResult(exit_code=1, stderr="fixture failure")])
    receipt = GovernedMaintenanceExecutor().execute(proposal=proposal,
        approvals=(approval(proposal, ApprovalScope.MODIFY_HYDRA),), adapter=adapter, timestamp=102)
    assert receipt.state == "rolled_back"
    assert receipt.rolled_back
    assert adapter.rollback_count == 1
    assert receipt.rollback_manifest.manifest_id.startswith("rollback_")


def test_timeout_is_failure_and_triggers_single_governed_rollback():
    proposal = heartbeat_interactive_sudo_playbook("hydra-live-sim", findings())
    adapter = FakeAdapter([CommandResult(exit_code=0, timed_out=True)])
    receipt = GovernedMaintenanceExecutor().execute(proposal=proposal,
        approvals=(approval(proposal, ApprovalScope.MODIFY_HYDRA),), adapter=adapter, timestamp=102)
    assert receipt.state == "rolled_back" and adapter.rollback_count == 1


def passing_observation(**updates):
    values = dict(hydra_service_active=True,
        port_3130_owner="hydra-live.service /usr/bin/python3 hydra-lived",
        heartbeat_timer_results=(True, True, True), apt_tailscale_active=True,
        apt_tailscale_connected=True, snap_tailscale_absent_or_disabled=True,
        ssh_connectivity_preserved=True, unexpected_failed_services=(), evidence=(evidence("certification", "sanitized"),))
    values.update(updates)
    return CertificationObservation(**values)


def test_certification_requires_every_acceptance_condition():
    assert certify_hydra_live(passing_observation()).certified
    failures = (
        {"hydra_service_active": False}, {"port_3130_owner": "unexpected"},
        {"heartbeat_timer_results": (True, True)}, {"heartbeat_timer_results": (True, False, True)},
        {"apt_tailscale_active": False}, {"apt_tailscale_connected": False},
        {"snap_tailscale_absent_or_disabled": False}, {"ssh_connectivity_preserved": False},
        {"unexpected_failed_services": ("surprise.service",)},
    )
    for update in failures:
        assert not certify_hydra_live(passing_observation(**update)).certified


def test_persisted_evidence_is_json_serializable_and_secret_free():
    item = evidence("journal_excerpt", "token=actual-secret peer=10.44.0.7")
    encoded = json.dumps(item.model_dump(mode="json"))
    assert "actual-secret" not in encoded and "10.44.0.7" not in encoded
