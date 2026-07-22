from hermes_cli.agent_roles.hydra_live_playbooks import heartbeat_interactive_sudo_playbook
from hermes_cli.agent_roles.remote_maintenance import (
    ApprovalScope,
    FindingCode,
    FleetFinding,
    RepairApproval,
    RepairCertification,
)
from hermes_cli.agent_roles.remote_maintenance_visibility import RemoteMaintenanceVisibilityService
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def test_proposal_is_idempotently_visible_in_mission_control(tmp_path):
    mission = MissionControlService(store=MissionControlStore(tmp_path))
    visibility = RemoteMaintenanceVisibilityService(mission)
    proposal = heartbeat_interactive_sudo_playbook("hydra-live-sim", (
        FleetFinding(code=FindingCode.INTERACTIVE_SUDO_SYSTEMD, summary="fixture", evidence_refs=("e1",)),
    ))
    visibility.publish_proposal("project-hydra", proposal, "operator", 100)
    visibility.publish_proposal("project-hydra", proposal, "operator", 100)
    records = visibility.list_records("project-hydra")
    assert len(records) == 1
    assert records[0]["record"]["proposal_id"] == proposal.proposal_id


def test_approval_and_certification_are_visible(tmp_path):
    mission = MissionControlService(store=MissionControlStore(tmp_path))
    visibility = RemoteMaintenanceVisibilityService(mission)
    approval = RepairApproval(approval_id="approval-1", proposal_id="proposal-1",
        proposal_checksum="a" * 64, scopes=(ApprovalScope.MODIFY_HYDRA,),
        actor_id="operator", approved_at=100, reason="fixture")
    certification = RepairCertification(certified=True,
        checks=(("hydra_live_active", True),), evidence_refs=("e1",))
    visibility.publish_approval("project-hydra", approval)
    visibility.publish_certification("project-hydra", "proposal-1", certification, "certifier", 101)
    assert [item["kind"] for item in visibility.list_records("project-hydra")] == ["approval", "certification"]
