"""Prime → Mission Control / unified evidence visibility.

Fleet Unification Stage 2. This is the composition layer: every pure Prime
decision module (:mod:`hermes_cli.prime.identity`,
:mod:`hermes_cli.prime.health`, :mod:`hermes_cli.prime.admission`,
:mod:`hermes_cli.prime.sigil_contract`,
:mod:`hermes_cli.prime.remote_maintenance_governance`,
:mod:`hermes_cli.prime.certification`) stays free of I/O; this module is
where their results are published as Mission Control ``TelemetryEvent``
records and unified ``EvidenceRecord`` entries.

This follows the exact repository-wide convention already used by every
other governed subsystem (e.g.
``hermes_cli.agent_roles.model_routing_visibility``): a
``*VisibilityAdapter`` builds an event from a domain object, and a
``*VisibilityService`` calls ``MissionControlService.append_event_once`` to
publish it idempotently. New event types are added to the closed
``_TELEMETRY_EVENT_TYPES`` set in ``hermes_cli.mission_control.models``
rather than accepting arbitrary event type strings.
"""

from __future__ import annotations

from typing import Optional, Tuple

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.certification import FleetCertification, FleetCertificationStatus
from hermes_cli.prime.evidence import (
    EvidenceRecord,
    ExternalEvidenceLink,
    PrimeEvidenceStore,
    SensitivityTier,
)
from hermes_cli.prime.health import HealthFinding, HealthReport, evaluate_health
from hermes_cli.prime.identity import FleetIdentity
from hermes_cli.prime.remote_maintenance_governance import (
    MaintenanceDecision,
    MaintenanceOutcome,
)
from hermes_cli.prime.sigil_contract import (
    SigilContractOutcome,
    SigilContractRequest,
    SigilContractResponse,
)


class PrimeVisibilityService:
    """Publishes Prime control-plane decisions to Mission Control + evidence."""

    def __init__(
        self,
        mission_control: MissionControlService,
        evidence_store: PrimeEvidenceStore,
    ) -> None:
        self._mission_control = mission_control
        self._evidence_store = evidence_store

    # ── Identity ─────────────────────────────────────────────────────────

    def publish_identity(
        self, project_id: str, identity: FleetIdentity
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        evidence = EvidenceRecord.build(
            kind="prime_identity_registered",
            producer_identity_id="prime",
            subject_identity_id=identity.identity_id,
            provenance=f"{identity.source.value}:{identity.source_reference}",
            timestamp=identity.registered_at,
            redacted_summary=(
                f"identity {identity.identity_id} ({identity.kind.value}) "
                f"registered from {identity.source.value}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_{identity.identity_id}",
            event_type="prime_identity_registered",
            project_id=project_id,
            timestamp=identity.registered_at,
            severity="info",
            payload={
                "source": "prime",
                "identity": identity.model_dump(mode="json"),
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_identity:{identity.identity_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence

    # ── Health ───────────────────────────────────────────────────────────

    def publish_health(
        self, project_id: str, report: HealthReport
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        findings = evaluate_health(report)
        severity = "info" if findings == () else "warning"

        evidence = EvidenceRecord.build(
            kind="prime_health_reported",
            producer_identity_id="prime",
            subject_identity_id=report.subject_identity_id,
            provenance=f"health_protocol_v{report.protocol_version}",
            timestamp=report.observed_at,
            redacted_summary=(
                f"health report {report.report_id} for {report.subject_identity_id}: "
                f"liveness={report.liveness.value} readiness={report.readiness.value} "
                f"findings={[f.value for f in findings]}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_{report.report_id}",
            event_type="prime_health_reported",
            project_id=project_id,
            timestamp=report.observed_at,
            severity=severity,
            correlation_id=report.correlation_id,
            payload={
                "source": "prime",
                "health_report": report.model_dump(mode="json"),
                "findings": [f.value for f in findings],
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_health:{report.report_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence

    # ── Admission ────────────────────────────────────────────────────────

    def publish_admission(
        self, project_id: str, decision: AdmissionDecision
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        severity = (
            "info" if decision.outcome == AdmissionOutcome.ADMITTED else "warning"
        )

        evidence = EvidenceRecord.build(
            kind="prime_admission_decided",
            producer_identity_id="prime",
            subject_identity_id=decision.subject_identity_id,
            provenance="prime_admission_service",
            timestamp=decision.decided_at,
            correlation_id=decision.correlation_id,
            decision_refs=(decision.decision_id,),
            redacted_summary=(
                f"admission decision {decision.decision_id} for "
                f"{decision.subject_identity_id}: {decision.outcome.value} "
                f"reasons={list(decision.reason_codes)}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_{decision.decision_id}",
            event_type="prime_admission_decided",
            project_id=project_id,
            timestamp=decision.decided_at,
            severity=severity,
            correlation_id=decision.correlation_id,
            payload={
                "source": "prime",
                "decision": decision.model_dump(mode="json"),
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_admission:{decision.decision_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence

    # ── Sigil contract ───────────────────────────────────────────────────

    def publish_sigil_contract(
        self,
        project_id: str,
        request: SigilContractRequest,
        response: SigilContractResponse,
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        severity = (
            "info" if response.outcome == SigilContractOutcome.ACCEPTED else "warning"
        )

        evidence = EvidenceRecord.build(
            kind="prime_sigil_contract_invoked",
            producer_identity_id=request.caller_identity_id,
            subject_identity_id=request.service_identity_id,
            provenance=f"sigil_contract_v{request.contract_version}",
            timestamp=response.completed_at,
            correlation_id=request.correlation_id,
            external_links=(
                ExternalEvidenceLink(
                    system="sigil_worker_contract",
                    reference=f"sigil_contract_request:{request.request_id}",
                ),
            ),
            redacted_summary=(
                f"Sigil contract {request.operation} request {request.request_id}: "
                f"{response.outcome.value}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_sigil_{request.request_id}",
            event_type="prime_sigil_contract_invoked",
            project_id=project_id,
            timestamp=response.completed_at,
            severity=severity,
            correlation_id=request.correlation_id,
            payload={
                "source": "prime",
                "request": request.model_dump(mode="json", exclude={"input_payload"}),
                "response": response.model_dump(
                    mode="json", exclude={"advisory_output"}
                ),
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_sigil_contract:{request.request_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence

    # ── Remote maintenance ───────────────────────────────────────────────

    def publish_maintenance_decision(
        self, project_id: str, decision: MaintenanceDecision
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        severity = (
            "info" if decision.outcome == MaintenanceOutcome.ADMITTED else "warning"
        )

        evidence = EvidenceRecord.build(
            kind="prime_remote_maintenance_decided",
            producer_identity_id="prime",
            subject_identity_id=None,
            provenance="prime_remote_maintenance_governance",
            timestamp=decision.decided_at,
            decision_refs=(decision.decision_id,),
            redacted_summary=(
                f"maintenance decision {decision.decision_id}: "
                f"{decision.outcome.value} reasons={list(decision.reason_codes)}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_{decision.decision_id}",
            event_type="prime_remote_maintenance_decided",
            project_id=project_id,
            timestamp=decision.decided_at,
            severity=severity,
            payload={
                "source": "prime",
                "decision": decision.model_dump(mode="json"),
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_maintenance:{decision.decision_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence

    # ── Fleet certification ──────────────────────────────────────────────

    def publish_certification(
        self, project_id: str, certification: FleetCertification
    ) -> Tuple[mission_models.TelemetryEvent, EvidenceRecord]:
        severity = (
            "info"
            if certification.status == FleetCertificationStatus.CERTIFIED
            else "critical"
            if certification.status == FleetCertificationStatus.FAILED
            else "warning"
        )

        evidence = EvidenceRecord.build(
            kind="prime_fleet_certified",
            producer_identity_id=certification.certifier_identity_id,
            subject_identity_id=None,
            provenance="prime_fleet_certification",
            timestamp=certification.issued_at,
            correlation_id=certification.correlation_id,
            decision_refs=(certification.certification_id,),
            redacted_summary=(
                f"fleet certification {certification.certification_id}: "
                f"{certification.status.value}"
            ),
            sensitivity=SensitivityTier.INTERNAL,
        )
        self._evidence_store.append(evidence)

        event = mission_models.TelemetryEvent(
            event_id=f"telemetry_{certification.certification_id}",
            event_type="prime_fleet_certified",
            project_id=project_id,
            timestamp=certification.issued_at,
            severity=severity,
            correlation_id=certification.correlation_id,
            payload={
                "source": "prime",
                "certification": certification.model_dump(mode="json"),
                "evidence_id": evidence.evidence_id,
                "source_idempotency_key": f"prime_certification:{certification.certification_id}",
            },
        )
        published = self._mission_control.append_event_once(event)
        return (published or event), evidence
