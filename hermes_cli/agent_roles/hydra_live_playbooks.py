"""Deterministic Hydra Live repair proposals; these functions never execute."""

from __future__ import annotations

from typing import Iterable

from .remote_maintenance import (
    ApprovalScope, CommandMode, FindingCode, FleetFinding, RepairProposal,
    RepairStep, RiskLevel,
)


def hermes_hydra_live_restart_playbook(target_id: str) -> RepairProposal:
    """Routine, harmless, reversible restart of the Hermes Hydra Live fleet worker.

    Not tied to a detected finding (``finding_refs=()``): this is a routine
    validation/restart operation, the one explicitly requested as the
    minimum real proof that ``RemoteMaintenanceVisibilityService`` and
    ``SSHMaintenanceAdapter`` can execute a genuine mutating step end to
    end, not a repair for a diagnosed problem. ``systemctl restart`` on an
    already-healthy ``Restart=on-failure`` unit is the least disruptive
    real mutation available: worst case is a few seconds of heartbeat gap
    while systemd restarts the process, with the unit's own restart policy
    already proven (Hermes add-on Phase D report) to recover cleanly.
    """

    return RepairProposal.build(
        target_id=target_id, risk=RiskLevel.LOW,
        expected_downtime="a few seconds while systemd restarts the process",
        finding_refs=(),
        evidence_refs=(),
        steps=(RepairStep(
            step_id="restart-hermes-hydra-live-worker", command_id="restart_hermes_hydra_live",
            mode=CommandMode.CONNECTIVITY, affected_services=("hermes-hydra-live.service",),
            required_approvals=(ApprovalScope.RESTART_HERMES_HYDRA_LIVE,),
            validation_command_ids=("service_state", "journal_excerpt"),
            rollback_command_id="restart_hermes_hydra_live",
            # Not a literal file: SSHMaintenanceAdapter.snapshot() treats this
            # as the systemd unit name to capture pre-mutation state for.
            changed_files=("hermes-hydra-live.service",),
        ),),
    )


def heartbeat_interactive_sudo_playbook(target_id: str, findings: Iterable[FleetFinding]) -> RepairProposal:
    findings = tuple(findings)
    if FindingCode.INTERACTIVE_SUDO_SYSTEMD not in {item.code for item in findings}:
        raise ValueError("heartbeat interactive-sudo finding is required")
    return RepairProposal.build(
        target_id=target_id, risk=RiskLevel.MEDIUM, expected_downtime="one heartbeat interval",
        finding_refs=(FindingCode.INTERACTIVE_SUDO_SYSTEMD,),
        evidence_refs=tuple(sorted({ref for item in findings for ref in item.evidence_refs})),
        steps=(RepairStep(step_id="patch-heartbeat-daemon", command_id="atomic_patch_heartbeat_no_sudo",
            mode=CommandMode.REVERSIBLE, affected_services=("hydra-fleet-heartbeat.service",),
            required_approvals=(ApprovalScope.MODIFY_HYDRA,),
            validation_command_ids=("timer_state", "journal_excerpt"),
            rollback_command_id="restore_heartbeat_snapshot",
            changed_files=("/opt/hydra-os/scripts/hydra-fleet-heartbeat-daemon",)),),
    )


def duplicate_tailscale_playbook(target_id: str, findings: Iterable[FleetFinding], *, remove_snap: bool = False) -> RepairProposal:
    findings = tuple(findings)
    if FindingCode.DUPLICATE_TAILSCALE not in {item.code for item in findings}:
        raise ValueError("duplicate Tailscale finding is required")
    command = "remove_snap_tailscale" if remove_snap else "disable_snap_tailscale"
    scope = ApprovalScope.REMOVE_PACKAGE if remove_snap else ApprovalScope.DISABLE_PACKAGE
    mode = CommandMode.DESTRUCTIVE if remove_snap else CommandMode.REVERSIBLE
    return RepairProposal.build(
        target_id=target_id, risk=RiskLevel.HIGH if remove_snap else RiskLevel.MEDIUM,
        expected_downtime="none for the active APT Tailscale service",
        finding_refs=(FindingCode.DUPLICATE_TAILSCALE,),
        evidence_refs=tuple(sorted({ref for item in findings for ref in item.evidence_refs})),
        steps=(RepairStep(step_id="stabilize-stale-snap-tailscale", command_id=command,
            mode=mode, affected_services=("snap.tailscale.tailscaled.service",),
            required_approvals=(scope,), validation_command_ids=("apt_tailscale", "snap_tailscale", "tailscale_status", "ssh_probe"),
            rollback_command_id="restore_snap_tailscale_state"),),
    )
