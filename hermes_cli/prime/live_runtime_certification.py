"""Live-runtime self-tests for fleet certification.

Fleet Unification live-runtime work. ``hermes_cli.prime.certification``'s
``certify_fleet`` is (deliberately) a pure function over caller-supplied
booleans — it never performs I/O itself, so its own unit tests stay
reproducible. This module is where those booleans come from for the
live-runtime components added after Stage 2: it actually exercises real
:class:`~hermes_cli.prime.fleet_runtime.FleetRuntime`,
:class:`~hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter`,
:mod:`hermes_cli.prime.desktop_governance`,
:mod:`hermes_cli.prime.operator_approval`, and
:mod:`hermes_cli.prime.sigil_contract` against ephemeral, real (temp-dir
backed) state — registering nodes, heartbeating them, dispatching through
governed adapters, granting and replaying approvals — and asserts the
specific fail-closed outcome each is supposed to produce.

None of these functions accept a pre-computed result — each one drives the
real component from scratch and returns ``True`` only if every assertion
inside it held. A selftest that merely returns a fixed ``True`` would defeat
the entire point of certification; every function below fails (raises or
returns ``False``) the moment any assertion it makes does not hold, so a
regression in the underlying live-runtime code changes this module's output
without needing to be separately kept in sync.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager

from pydantic import ValidationError

from hermes_cli.agent_roles.model_execution import (
    GovernedModelExecutionService,
    InMemoryModelExecutionStore,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
)
from hermes_cli.agent_roles.model_routing import (
    CandidateDisposition,
    CandidateScore,
    RoutingDecision,
    RoutingPolicyOutcome,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.desktop_governance import (
    DesktopUseOutcome,
    DesktopUsePolicy,
    DesktopUseRequest,
    evaluate_desktop_use_request,
)
from hermes_cli.prime.dispatch_gate import CertificationSnapshot, PrimeGovernedProviderAdapter
from hermes_cli.prime.evidence import EvidenceStorageError, PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import (
    FleetNodeRegistrationRequest,
    FleetNodeRole,
    FleetRegistrationOutcome,
)
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import DEFAULT_MAX_REPORT_AGE_SECONDS, LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatOutcome, HeartbeatSubmission
from hermes_cli.prime.ollama_node import OllamaGenerateOutcome, OllamaNodeConfigurationError
from hermes_cli.prime.operator_approval import (
    ApprovalChannel,
    OperatorApproval,
    OperatorApprovalReplayStore,
    OperatorApprovalScope,
    validate_operator_approval,
)
from hermes_cli.prime.sigil_contract import (
    SUPPORTED_SIGIL_OPERATIONS,
    SigilContractRequest,
    evaluate_sigil_contract_request,
)


def _now() -> int:
    return int(time.time())


@contextmanager
def _ephemeral_runtime(project_id: str = "prime-certification-selftest") -> Iterator[FleetRuntime]:
    with tempfile.TemporaryDirectory(prefix="prime-certify-") as tmp:
        root = Path(tmp)
        yield FleetRuntime(
            state_root=root / "prime",
            project_id=project_id,
            mission_control=MissionControlService(store=MissionControlStore(root=root / "mc")),
            evidence_store=PrimeEvidenceStore(state_root=root / "prime-evidence"),
        )


def _register_and_heartbeat(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int) -> None:
    decision = runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id=f"selftest-{natural_key}",
            natural_key=natural_key,
            role=role,
            declared_capabilities=("worker_heartbeat", "local_model_inference", "desktop_use"),
            endpoint=f"http://{natural_key}.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    if decision.outcome == FleetRegistrationOutcome.REJECTED:
        raise AssertionError(f"selftest registration of {natural_key} was unexpectedly rejected")
    result = runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key=natural_key, liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )
    if result.outcome != HeartbeatOutcome.ACCEPTED:
        raise AssertionError(f"selftest heartbeat for {natural_key} was unexpectedly rejected")


def fleet_registry_and_heartbeat_selftest() -> bool:
    """Admitted identities, healthy heartbeat paths, stale/revoked rejection.

    Real assertions: an unknown natural key can never register; a healthy
    heartbeated node is dispatchable; the same node ages into non-dispatchable
    once its heartbeat is old enough (without any new event); a revoked node
    is never dispatchable and can never re-register.
    """
    try:
        with _ephemeral_runtime() as runtime:
            now = _now()

            unknown = runtime.register_node(
                FleetNodeRegistrationRequest(
                    request_id="selftest-unknown", natural_key="not-a-real-fleet-node",
                    role=FleetNodeRole.TITAN, endpoint="http://x.tailnet.internal",
                    software_version="1.0.0", protocol_version=1, requested_at=now,
                ),
                now=now,
            )
            if unknown.outcome != FleetRegistrationOutcome.REJECTED:
                return False

            _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
            if not runtime.is_dispatchable(
                "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest",
            ):
                return False

            stale_now = now + DEFAULT_MAX_REPORT_AGE_SECONDS + 60
            if runtime.is_dispatchable(
                "titan", now=stale_now, certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest",
            ):
                return False  # a stale node must never be dispatchable

            runtime.revoke_node("titan", now=stale_now, reason="selftest")
            if runtime.is_dispatchable(
                "titan", now=stale_now, certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest",
            ):
                return False  # a revoked node must never be dispatchable

            reregistration = runtime.register_node(
                FleetNodeRegistrationRequest(
                    request_id="selftest-titan-2", natural_key="titan", role=FleetNodeRole.TITAN,
                    endpoint="http://titan.tailnet.internal:11434", software_version="1.0.0",
                    protocol_version=1, requested_at=stale_now,
                ),
                now=stale_now, allow_reregistration=True,
            )
            if reregistration.outcome != FleetRegistrationOutcome.REJECTED:
                return False  # a revoked node must never be able to re-register
            return True
    except Exception:  # noqa: BLE001 - any unexpected exception is a failed selftest
        return False


class _FakeGenerate:
    def __init__(self, outcome: OllamaGenerateOutcome) -> None:
        self._outcome = outcome
        self.calls = 0

    def generate(self, *, alias, input_text, timeout_seconds):
        self.calls += 1
        return self._outcome


def _single_candidate_route(request_id: str, provider_id: str, model_alias: str, *, now: int) -> RoutingDecision:
    candidate = CandidateScore(
        provider_id=provider_id, model_id=model_alias, disposition=CandidateDisposition.ELIGIBLE,
        estimated_cost_micros=0, score=100, quality_factor=100, reliability_factor=100,
        latency_factor=100, cost_factor=100, preference_factor=100, trust_factor=100,
    )
    return RoutingDecision(
        decision_id=f"selftest_route_{request_id}", request_id=request_id,
        request_fingerprint="0" * 64, selected_provider_id=provider_id,
        selected_model_id=model_alias, candidates=(candidate,), estimated_cost_micros=0,
        budget_limit_micros=0, policy_outcome=RoutingPolicyOutcome.FREE, fallback_chain=(), created_at=now,
    )


def dispatch_routing_and_model_configuration_selftest() -> bool:
    """Task routing + Ollama model configuration. Fail closed on both.

    Real assertions: an empty/unconfigured model alias never reaches the
    network; dispatch to an admitted, healthy node succeeds end-to-end
    through ``GovernedModelExecutionService``; dispatch to an unregistered
    node never silently succeeds.
    """
    try:
        from hermes_cli.prime.ollama_node import OllamaNodeConfig

        try:
            OllamaNodeConfig(natural_key="titan", endpoint="http://titan.tailnet.internal:11434")
            malformed_rejected = False
        except OllamaNodeConfigurationError:
            malformed_rejected = True
        if not malformed_rejected:
            pass  # a config with no aliases at all is legal (nothing configured yet)

        config = OllamaNodeConfig(
            natural_key="titan", endpoint="http://titan.tailnet.internal:11434",
            model_aliases={"lightweight": "hermes-llama3.2:3b-64k"},
        )
        try:
            config.resolve_model("")
            return False  # must raise, never resolve a blank alias
        except OllamaNodeConfigurationError:
            pass
        try:
            config.resolve_model("unconfigured-alias")
            return False
        except OllamaNodeConfigurationError:
            pass

        with _ephemeral_runtime() as runtime:
            now = _now()
            _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

            underlying = _FakeGenerate(OllamaGenerateOutcome(succeeded=True, output_text="ok"))
            store: dict[str, str] = {"input://selftest": "selftest input"}
            adapter = PrimeGovernedProviderAdapter(
                provider_id="titan-ollama", natural_key="titan", fleet_runtime=runtime,
                underlying=underlying,
                certification_provider=lambda: CertificationSnapshot(
                    status=CertificationStatus.CERTIFIED, evidence_ref="evidence://selftest"
                ),
                input_resolver=store.get, clock=lambda: now,
            )
            route = _single_candidate_route("selftest-req", "titan-ollama", "lightweight", now=now)
            request = ModelExecutionRequest(
                execution_id="selftest-exec", idempotency_key="selftest-idem",
                project_id="selftest", task_id="selftest", request_id="selftest-req",
                routing_decision=route, selected_provider_id="titan-ollama",
                selected_model_id="lightweight", input_reference="input://selftest",
                timeout_seconds=30, maximum_attempts=1, requested_at=now,
            )
            service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
            evidence = service.execute(request, timestamp=now)
            if evidence.state != ModelExecutionState.SUCCEEDED or underlying.calls != 1:
                return False

            # Now target an unregistered node — dispatch must fail closed,
            # never silently succeed through some other path.
            ghost_underlying = _FakeGenerate(OllamaGenerateOutcome(succeeded=True, output_text="should never run"))
            ghost_adapter = PrimeGovernedProviderAdapter(
                provider_id="mac-ollama", natural_key="mac", fleet_runtime=runtime,
                underlying=ghost_underlying,
                certification_provider=lambda: CertificationSnapshot(
                    status=CertificationStatus.CERTIFIED, evidence_ref="evidence://selftest"
                ),
                input_resolver=store.get, clock=lambda: now,
            )
            ghost_route = _single_candidate_route("selftest-req-2", "mac-ollama", "primary_reasoning", now=now)
            ghost_request = ModelExecutionRequest(
                execution_id="selftest-exec-2", idempotency_key="selftest-idem-2",
                project_id="selftest", task_id="selftest", request_id="selftest-req-2",
                routing_decision=ghost_route, selected_provider_id="mac-ollama",
                selected_model_id="primary_reasoning", input_reference="input://selftest",
                timeout_seconds=30, maximum_attempts=1, requested_at=now,
            )
            ghost_service = GovernedModelExecutionService((ghost_adapter,), InMemoryModelExecutionStore())
            ghost_evidence = ghost_service.execute(ghost_request, timestamp=now)
            if (
                ghost_evidence.state == ModelExecutionState.SUCCEEDED
                or ghost_evidence.error_classification != ModelExecutionErrorClass.AUTHORIZATION_INVALID
                or ghost_underlying.calls != 0
            ):
                return False
            return True
    except Exception:  # noqa: BLE001
        return False


def desktop_use_and_operator_approval_selftest() -> bool:
    """Desktop-use governance + operator approvals + replay rejection.

    Real assertions: a safe (capture) action never needs approval; a
    non-safe action is denied without approval; the same granted approval
    cannot authorize the action twice; an expired approval is rejected.
    """
    try:
        with _ephemeral_runtime() as runtime:
            now = _now()
            _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
            replay_store = OperatorApprovalReplayStore(state_root=Path(tempfile.mkdtemp(prefix="prime-certify-approvals-")))
            policy = DesktopUsePolicy(allowed_apps=("Finder",))

            capture = evaluate_desktop_use_request(
                DesktopUseRequest(request_id="selftest-capture", action="capture", app=None, requested_at=now),
                policy, fleet_runtime=runtime, certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest", replay_store=replay_store, now=now,
            )
            if capture.outcome != DesktopUseOutcome.ADMITTED:
                return False

            click_request = DesktopUseRequest(
                request_id="selftest-click", action="click", app="Finder", requested_at=now
            )
            without_approval = evaluate_desktop_use_request(
                click_request, policy, fleet_runtime=runtime,
                certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest", replay_store=replay_store, now=now,
            )
            if without_approval.outcome != DesktopUseOutcome.DENIED:
                return False

            node = runtime.get_node("mac")
            assert node is not None  # _register_and_heartbeat guarantees this
            approval = OperatorApproval.grant(
                scope=OperatorApprovalScope.DESKTOP_USE, action_id=click_request.action_id,
                subject_identity_id=node.identity_id, operator_identity="selftest:operator",
                channel=ApprovalChannel.CLI, granted_at=now, evidence_ref="evidence://selftest",
            )
            gated_request = click_request.model_copy(update={"operator_approval": approval})
            with_approval = evaluate_desktop_use_request(
                gated_request, policy, fleet_runtime=runtime,
                certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest", replay_store=replay_store, now=now,
            )
            if with_approval.outcome != DesktopUseOutcome.ADMITTED:
                return False

            replayed = evaluate_desktop_use_request(
                gated_request, policy, fleet_runtime=runtime,
                certification_status=CertificationStatus.CERTIFIED,
                certification_evidence_ref="evidence://selftest", replay_store=replay_store, now=now + 1,
            )
            if replayed.outcome != DesktopUseOutcome.DENIED:
                return False  # replaying the same approval must be denied

            expired_approval = OperatorApproval.grant(
                scope=OperatorApprovalScope.DESKTOP_USE, action_id=click_request.action_id,
                subject_identity_id=node.identity_id, operator_identity="selftest:operator",
                channel=ApprovalChannel.CLI, granted_at=now, evidence_ref="evidence://selftest",
                max_age_seconds=1,
            )
            ok, code = validate_operator_approval(
                expired_approval, expected_scope=OperatorApprovalScope.DESKTOP_USE,
                expected_action_id=click_request.action_id, expected_subject_identity_id=node.identity_id,
                now=now + 3600, replay_store=replay_store,
            )
            if ok or code is None or code.value != "expired":
                return False
            return True
    except Exception:  # noqa: BLE001
        return False


def sigil_isolation_selftest() -> bool:
    """Sigil paper-only/broker/execution invariants are structurally locked,
    and an unadmitted caller or service is always rejected before any advisory
    output could be produced."""
    try:
        base_kwargs: dict[str, object] = dict(
            request_id="selftest", correlation_id="selftest",
            caller_identity_id="fid_a", service_identity_id="fid_b",
            operation=next(iter(SUPPORTED_SIGIL_OPERATIONS)), requested_at=_now(),
        )
        for unsafe_field in ("paper_only", "broker_submission_denied", "execution_authority_denied", "advisory"):
            try:
                SigilContractRequest.model_validate({**base_kwargs, unsafe_field: False})
                return False  # must have raised
            except ValidationError:
                pass

        request = SigilContractRequest(
            request_id="selftest-2", correlation_id="selftest-2",
            caller_identity_id="fid_a", service_identity_id="fid_b",
            operation="certification_status_query", requested_at=_now(),
        )
        admitted, code = evaluate_sigil_contract_request(
            request, caller_admission=None, service_admission=None,
            caller_health=None, service_health=None, now=_now(),
        )
        if admitted or code is None:
            return False  # a request with no admission at all must never be admitted
        return True
    except Exception:  # noqa: BLE001
        return False


def evidence_integrity_selftest() -> bool:
    """Evidence chain verification actually detects tampering, not just
    trivially returning True on an untouched store."""
    try:
        with tempfile.TemporaryDirectory(prefix="prime-certify-evidence-") as tmp:
            root = Path(tmp)
            store = PrimeEvidenceStore(state_root=root)
            from hermes_cli.prime.evidence import EvidenceRecord, SensitivityTier

            for i in range(3):
                store.append(
                    EvidenceRecord.build(
                        kind="prime_identity_registered", producer_identity_id="prime",
                        subject_identity_id=f"fid_selftest_{i}", provenance="selftest",
                        timestamp=_now(), redacted_summary=f"selftest record {i}",
                        sensitivity=SensitivityTier.INTERNAL,
                    )
                )
            if not store.verify_chain():
                return False

            # Tamper with the on-disk journal directly and confirm detection.
            raw = store.evidence_path.read_text(encoding="utf-8").splitlines()
            tampered = raw[:-1] + [raw[-1].replace('"sequence":3', '"sequence":99')]
            store.evidence_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")

            try:
                store.verify_chain()
                return False  # must have raised on the tampered chain
            except EvidenceStorageError:
                return True
    except Exception:  # noqa: BLE001
        return False


def run_all_live_runtime_selftests() -> dict[str, bool]:
    """Run every live-runtime selftest and return a name->passed mapping.

    Intended for :func:`hermes_cli.prime.certification.certify_fleet` callers
    (CLI, CI) to expand into the individual boolean parameters that function
    now requires — kept as a dict (not a single aggregate boolean) so a
    caller can report exactly which live-runtime component failed rather
    than only that "something" did.
    """
    return {
        "fleet_registry_and_heartbeat": fleet_registry_and_heartbeat_selftest(),
        "dispatch_routing_and_model_configuration": dispatch_routing_and_model_configuration_selftest(),
        "desktop_use_and_operator_approval": desktop_use_and_operator_approval_selftest(),
        "sigil_isolation": sigil_isolation_selftest(),
        "evidence_integrity": evidence_integrity_selftest(),
    }
