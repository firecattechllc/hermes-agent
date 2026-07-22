"""Sanitized Mission Control visibility for Step 29 artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Tuple

from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService

from .release_readiness import ReleaseDisposition, ReleaseReadiness
from .system_integration_certification import CertificationStatus, SystemIntegrationCertification


SYSTEM_INTEGRATION_CERTIFICATION_STARTED = "system_integration_certification_started"
SYSTEM_INTEGRATION_CERTIFICATION_RECORDED = "system_integration_certification_recorded"
SYSTEM_INTEGRATION_CERTIFICATION_BLOCKED = "system_integration_certification_blocked"
RELEASE_READINESS_RECORDED = "release_readiness_recorded"
RELEASE_READINESS_BLOCKED = "release_readiness_blocked"
EVIDENCE_CHAIN_CERTIFIED = "evidence_chain_certified"
ROLLBACK_READINESS_RECORDED = "rollback_readiness_recorded"
SYSTEM_INTEGRATION_EVENT_TYPES = (
    EVIDENCE_CHAIN_CERTIFIED, RELEASE_READINESS_BLOCKED, RELEASE_READINESS_RECORDED,
    ROLLBACK_READINESS_RECORDED, SYSTEM_INTEGRATION_CERTIFICATION_BLOCKED,
    SYSTEM_INTEGRATION_CERTIFICATION_RECORDED, SYSTEM_INTEGRATION_CERTIFICATION_STARTED,
)


def _event_id(event_type: str, source_id: str) -> str:
    return f"telemetry_step29_{hashlib.sha256(f'{event_type}|{source_id}'.encode()).hexdigest()[:24]}"


def _assert_sanitized(payload: dict) -> None:
    encoded = json.dumps(payload, sort_keys=True).lower()
    forbidden = ("raw_prompt", "model_response", "api_key", "authorization:", "bearer ", "password", "private_key", "secret=", "token=")
    if any(marker in encoded for marker in forbidden):
        raise ValueError("Step 29 Mission Control payload contains sensitive content")


class SystemIntegrationCertificationVisibilityAdapter:
    def certification_events(self, certification: SystemIntegrationCertification) -> Tuple[TelemetryEvent, ...]:
        base = {"certification_id": certification.certification_id, "report_id": certification.report_id, "source_commit": certification.source_commit, "branch": certification.branch, "status": certification.status.value, "evidence_chain_hash": certification.evidence_chain.chain_hash, "finding_count": len(certification.findings), "operator_approval_required": True}
        _assert_sanitized(base)
        types = [SYSTEM_INTEGRATION_CERTIFICATION_STARTED, EVIDENCE_CHAIN_CERTIFIED, SYSTEM_INTEGRATION_CERTIFICATION_RECORDED]
        if certification.status is not CertificationStatus.CERTIFIED: types.append(SYSTEM_INTEGRATION_CERTIFICATION_BLOCKED)
        if any(kind not in certification.identity.mission_control_event_ids for kind in types):
            raise ValueError("Step 29 Mission Control event association mismatch")
        return tuple(TelemetryEvent(event_id=_event_id(kind, certification.certification_id), event_type=kind, project_id=certification.identity.project_id, task_id=certification.identity.task_id, timestamp=certification.generated_at, severity="error" if kind == SYSTEM_INTEGRATION_CERTIFICATION_BLOCKED else "info", correlation_id=certification.identity.correlation_id, causation_id=certification.identity.request_id, payload={**base, "step29_event": kind, "source_idempotency_key": f"{certification.certification_id}:{kind}"}) for kind in types)

    def readiness_events(self, readiness: ReleaseReadiness, *, project_id: str, task_id: str, correlation_id: str) -> Tuple[TelemetryEvent, ...]:
        base = {"report_id": readiness.report_id, "certification_id": readiness.certification_id, "disposition": readiness.disposition.value, "manifest_id": readiness.manifest.manifest_id, "operator_approval_required": True}
        _assert_sanitized(base)
        types = [RELEASE_READINESS_RECORDED, ROLLBACK_READINESS_RECORDED]
        if readiness.disposition in {ReleaseDisposition.BLOCKED, ReleaseDisposition.FAILED}: types.append(RELEASE_READINESS_BLOCKED)
        return tuple(TelemetryEvent(event_id=_event_id(kind, readiness.report_id), event_type=kind, project_id=project_id, task_id=task_id, timestamp=readiness.generated_at, severity="error" if kind == RELEASE_READINESS_BLOCKED else "info", correlation_id=correlation_id, causation_id=readiness.certification_id, payload={**base, "step29_event": kind, "source_idempotency_key": f"{readiness.report_id}:{kind}"}) for kind in types)


class SystemIntegrationCertificationVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control

    def publish(self, events: Iterable[TelemetryEvent]) -> Tuple[TelemetryEvent, ...]:
        published = []
        for event in events:
            existing = next((item for item in self._mission_control.get_events(event.project_id) if item.event_id == event.event_id), None)
            if existing is not None:
                if existing != event: raise ValueError("Step 29 Mission Control event identity collision")
                published.append(existing); continue
            self._mission_control.append_event_once(event); published.append(event)
        return tuple(published)
