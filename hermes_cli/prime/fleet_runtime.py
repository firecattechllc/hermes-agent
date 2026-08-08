"""Fleet runtime composition facade.

Fleet Unification live-runtime work. Every Stage 2 module
(:mod:`hermes_cli.prime.identity`, :mod:`hermes_cli.prime.admission`,
:mod:`hermes_cli.prime.health`) and every live-runtime addition
(:mod:`hermes_cli.prime.fleet_registry`, :mod:`hermes_cli.prime.heartbeat`,
:mod:`hermes_cli.prime.visibility`) is deliberately kept independent and
composable, matching the rest of the package's convention. This module is
the one place those pieces are wired together into "what does it mean for a
fleet node to be admitted and dispatchable right now" — every other
live-runtime consumer (governed model dispatch, Ollama node adapters,
desktop-use governance, Sigil routing, service entrypoints, certification)
should go through :class:`FleetRuntime` rather than constructing
``PrimeAdmissionService``/``FleetNodeRegistry``/``HeartbeatService`` ad hoc,
so there is exactly one fail-closed answer to that question fleet-wide.

``FleetRuntime`` performs I/O (durable registry/health stores, Mission
Control, evidence) — it is the composition layer, not a pure decision
module. Time is still always caller-supplied (``now``), so callers can
inject a deterministic clock in tests and a real one in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import (
    AdmissionDecision,
    AdmissionOutcome,
    AdmissionRequest,
    CertificationStatus,
    PrimeAdmissionService,
)
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import (
    FleetNodeRecord,
    FleetNodeRegistrationRequest,
    FleetNodeRegistry,
    FleetRegistrationDecision,
    FleetRegistryStore,
)
from hermes_cli.prime.health import HealthReport
from hermes_cli.prime.heartbeat import (
    HealthReportStore,
    HeartbeatResult,
    HeartbeatService,
    HeartbeatSubmission,
)
from hermes_cli.prime.visibility import PrimeVisibilityService

DEFAULT_POLICY_VERSION = "prime-admission-policy-v1"


class FleetRuntime:
    """The single composition point for live fleet admission decisions."""

    def __init__(
        self,
        *,
        state_root: Optional[Path] = None,
        project_id: str = "hermes-fleet",
        mission_control: Optional[MissionControlService] = None,
        evidence_store: Optional[PrimeEvidenceStore] = None,
        registry: Optional[FleetNodeRegistry] = None,
        heartbeats: Optional[HeartbeatService] = None,
        admission_service: Optional[PrimeAdmissionService] = None,
    ) -> None:
        self.project_id = project_id
        self.registry = registry or FleetNodeRegistry(
            store=FleetRegistryStore(state_root=state_root)
        )
        self.heartbeats = heartbeats or HeartbeatService(
            self.registry, health_store=HealthReportStore(state_root=state_root)
        )
        self._admission = admission_service or PrimeAdmissionService()
        # Scoped under the same explicit state_root as the registry/heartbeat/
        # evidence stores above whenever one is supplied — a locked-down
        # service account (e.g. systemd `User=hermes` with no real home
        # directory) has no writable ``get_hermes_home()`` default, so
        # falling through to that default here (unlike every sibling store)
        # crashed Prime's control-plane service on first boot in production.
        mission_control = mission_control or MissionControlService(
            store=MissionControlStore(root=state_root / "mission-control")
            if state_root is not None
            else None
        )
        evidence_store = evidence_store or PrimeEvidenceStore(state_root=state_root)
        self.visibility = PrimeVisibilityService(mission_control, evidence_store)

    # ── Registration ─────────────────────────────────────────────────────

    def register_node(
        self,
        request: FleetNodeRegistrationRequest,
        *,
        now: int,
        allow_reregistration: bool = False,
    ) -> FleetRegistrationDecision:
        decision = self.registry.register(
            request, now=now, allow_reregistration=allow_reregistration
        )
        record = self.registry.get(request.natural_key)
        self.visibility.publish_fleet_node_registration(self.project_id, decision, record)
        return decision

    def get_node(self, natural_key: str) -> Optional[FleetNodeRecord]:
        return self.registry.get(natural_key)

    def revoke_node(self, natural_key: str, *, now: int, reason: str) -> FleetNodeRecord:
        record = self.registry.revoke(natural_key, now=now, reason=reason)
        # `revoke` has no natural FleetRegistrationDecision of its own; the
        # subsequent heartbeat/admission attempts against a revoked node are
        # what actually publish visibility for the revocation taking effect.
        return record

    # ── Heartbeat ────────────────────────────────────────────────────────

    def ingest_heartbeat(
        self, submission: HeartbeatSubmission, *, now: int
    ) -> HeartbeatResult:
        result = self.heartbeats.ingest(submission, now=now)
        self.visibility.publish_fleet_node_connection_change(
            self.project_id, submission.natural_key, result
        )
        return result

    def latest_health(self, natural_key: str) -> Optional[HealthReport]:
        return self.heartbeats.latest_health(natural_key)

    # ── Admission ────────────────────────────────────────────────────────

    def evaluate_admission(
        self,
        natural_key: str,
        *,
        now: int,
        certification_status: CertificationStatus,
        certification_evidence_ref: Optional[str] = None,
        policy_version: str = DEFAULT_POLICY_VERSION,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AdmissionDecision:
        """Evaluate (and publish) an admission decision for one fleet node.

        Always produces a decision, even for an unknown node — an unknown
        subject is not an error case, it is a DENIED decision with reason
        code ``identity_unknown_or_inactive`` (see
        :class:`hermes_cli.prime.admission.PrimeAdmissionService`).
        """
        node = self.registry.get(natural_key)
        health = self.heartbeats.latest_health(natural_key)
        request = AdmissionRequest(
            request_id=request_id or f"padm_req_{natural_key}_{now}",
            subject_identity_id=(
                node.identity_id if node is not None else f"unknown:{natural_key}"[:128]
            ),
            role=node.role.value if node is not None else "unknown",
            declared_capabilities=node.capabilities if node is not None else (),
            software_version=node.software_version if node is not None else "unknown",
            protocol_version=node.protocol_version if node is not None else 1,
            health=health,
            certification_status=certification_status,
            certification_evidence_ref=certification_evidence_ref,
            policy_version=policy_version,
            identity_known_and_active=self.registry.is_admissible_node(natural_key),
            identity_revoked=bool(node.revoked) if node is not None else False,
            quarantined=False,
            requested_at=now,
            correlation_id=correlation_id,
        )
        decision = self._admission.evaluate(request, now=now)
        self.visibility.publish_admission(self.project_id, decision)
        return decision

    def is_dispatchable(
        self,
        natural_key: str,
        *,
        now: int,
        certification_status: CertificationStatus,
        certification_evidence_ref: Optional[str] = None,
        policy_version: str = DEFAULT_POLICY_VERSION,
    ) -> bool:
        """Fail-closed: True only for a currently-healthy AND currently-admitted node.

        This is the single check every dispatch path
        (:mod:`hermes_cli.prime.dispatch_gate`,
        :mod:`hermes_cli.prime.ollama_node`,
        :mod:`hermes_cli.prime.desktop_governance`) must call before routing
        any work to a node. It never returns True for a node that merely
        *was* healthy — heartbeat freshness is re-checked against ``now`` on
        every call.
        """
        if not self.heartbeats.is_usable_for_dispatch(natural_key, now=now):
            return False
        decision = self.evaluate_admission(
            natural_key,
            now=now,
            certification_status=certification_status,
            certification_evidence_ref=certification_evidence_ref,
            policy_version=policy_version,
        )
        return decision.outcome == AdmissionOutcome.ADMITTED and decision.is_current(now)
