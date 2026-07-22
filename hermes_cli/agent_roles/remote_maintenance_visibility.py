"""Read-only Mission Control projection for governed remote maintenance."""

from __future__ import annotations

from typing import Tuple

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .remote_maintenance import (
    RepairApproval,
    RepairCertification,
    RepairExecution,
    RepairProposal,
)


REMOTE_MAINTENANCE_EVENT = "remote_maintenance_recorded"


class RemoteMaintenanceVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control

    def publish_proposal(self, project_id: str, proposal: RepairProposal, actor_id: str, timestamp: int) -> None:
        self._publish(project_id, proposal.proposal_id, "proposal", proposal.model_dump(mode="json"), actor_id, timestamp, "warning")

    def publish_execution(self, project_id: str, execution: RepairExecution, actor_id: str, timestamp: int) -> None:
        severity = "info" if execution.state == "completed" else "error"
        self._publish(project_id, execution.execution_id, "execution", execution.model_dump(mode="json"), actor_id, timestamp, severity)

    def publish_approval(self, project_id: str, approval: RepairApproval) -> None:
        self._publish(project_id, approval.approval_id, "approval",
                      approval.model_dump(mode="json"), approval.actor_id,
                      approval.approved_at, "info")

    def publish_certification(self, project_id: str, proposal_id: str,
                              certification: RepairCertification,
                              actor_id: str, timestamp: int) -> None:
        record_id = f"{proposal_id}:{'certified' if certification.certified else 'rejected'}"
        self._publish(project_id, record_id, "certification",
                      certification.model_dump(mode="json"), actor_id, timestamp,
                      "info" if certification.certified else "error")

    def _publish(self, project_id: str, record_id: str, kind: str, record: dict, actor_id: str, timestamp: int, severity: str) -> None:
        self._mission_control.append_event_once(mission_models.TelemetryEvent(
            event_id=f"telemetry_remote_maintenance_{record_id}", event_type=REMOTE_MAINTENANCE_EVENT,
            project_id=project_id, actor_id=actor_id, timestamp=timestamp, severity=severity,
            payload={"source": "remote_maintenance", "kind": kind, "record": record,
                     "source_idempotency_key": f"remote_maintenance:{kind}:{record_id}"},
        ))

    def list_records(self, project_id: str) -> Tuple[dict, ...]:
        return tuple(event.payload for event in self._mission_control.get_events(project_id)
                     if event.event_type == REMOTE_MAINTENANCE_EVENT)
