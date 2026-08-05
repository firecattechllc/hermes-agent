"""Governed routing from the Sigil service contract to admitted fleet providers.

Fleet Unification live-runtime work. ``hermes_cli.prime.sigil_contract``
already defines the typed, locked-safe (advisory/paper-only/broker-denied/
execution-denied) contract and its precondition gate
(``evaluate_sigil_contract_request``) — but per that module's own docstring
it "never reaches into Sigil's execution path" and never calls anything.
This module is the live-runtime completion of that contract: given a
``SigilContractRequest`` and the caller's/service's already-evaluated
:class:`~hermes_cli.prime.admission.AdmissionDecision` objects, it actually
produces the advisory output by dispatching to an admitted, healthy Mac or
Titan fleet node through :class:`~hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter`
— the same governed adapter used for all other model dispatch — and returns
a fully valid, locked-safe :class:`~hermes_cli.prime.sigil_contract.SigilContractResponse`.

Two independent, non-bypassable gates stand between a Sigil request and any
advisory output:

1. :func:`hermes_cli.prime.sigil_contract.evaluate_sigil_contract_request`
   checks the caller's and service's *supplied* admission/health objects.
2. :class:`hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter.execute`
   independently re-checks the target node's admission and health against
   the live :class:`hermes_cli.prime.fleet_runtime.FleetRuntime` state —
   not the objects a caller supplied — before ever calling into a real
   provider.

A caller cannot skip gate 2 by fabricating an ``AdmissionDecision`` object
for gate 1: even if gate 1 is satisfied, dispatch still fails closed unless
the target node is *actually* registered, non-revoked, and heartbeating
healthy right now. This module also never imports
``hermes_cli.prime.remote_maintenance_governance`` or anything under
``hermes_cli.agent_roles.remote_maintenance`` — there is no code path here
by which a Sigil request could reach the remote-maintenance path at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from hermes_cli.agent_roles.model_execution import (
    GovernedModelExecutionService,
    InMemoryModelExecutionStore,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
    ModelExecutionStoreProtocol,
)
from hermes_cli.agent_roles.model_routing import (
    CandidateDisposition,
    CandidateScore,
    RoutingDecision,
    RoutingPolicyOutcome,
)
from hermes_cli.prime.admission import AdmissionDecision
from hermes_cli.prime.dispatch_gate import InMemoryReferenceStore, PrimeGovernedProviderAdapter
from hermes_cli.prime.health import HealthReport
from hermes_cli.prime.sigil_contract import (
    SigilContractOutcome,
    SigilContractRequest,
    SigilContractResponse,
    SigilRejectionCode,
    evaluate_sigil_contract_request,
)

@dataclass(frozen=True, slots=True)
class SigilOperationRoute:
    natural_key: str
    model_alias: str


# Closed routing table: which fleet node/alias serves each supported Sigil
# operation. A deliberate governance decision, not a runtime-configurable
# default — matches ``SUPPORTED_SIGIL_OPERATIONS`` being a closed allowlist.
# ``certification_status_query`` is deliberately absent — it never dispatches
# to a node at all (see ``SigilRoutingService.route``).
DEFAULT_OPERATION_ROUTES: Dict[str, SigilOperationRoute] = {
    "advisory_financial_sentiment": SigilOperationRoute("titan", "sentiment"),
    "advisory_valuation": SigilOperationRoute("mac", "primary_reasoning"),
    "advisory_risk_assessment": SigilOperationRoute("mac", "primary_reasoning"),
    "advisory_portfolio_construction": SigilOperationRoute("mac", "primary_reasoning"),
    "advisory_research_summary": SigilOperationRoute("mac", "primary_reasoning"),
}


class SigilRoutingService:
    """Dispatches admitted Sigil contract requests to governed fleet adapters.

    ``certification_status_query`` is handled without dispatching to any
    node — it only echoes whether the two Prime admission preconditions
    held, since that is the entire question it asks.
    """

    def __init__(
        self,
        *,
        adapters: Dict[str, PrimeGovernedProviderAdapter],
        input_store: Optional[InMemoryReferenceStore] = None,
        execution_store: Optional[ModelExecutionStoreProtocol] = None,
        routes: Optional[Dict[str, SigilOperationRoute]] = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._input_store = input_store or InMemoryReferenceStore()
        self._execution_service = GovernedModelExecutionService(
            tuple(self._adapters.values()), execution_store or InMemoryModelExecutionStore()
        )
        self._routes = dict(routes) if routes is not None else dict(DEFAULT_OPERATION_ROUTES)

    def route(
        self,
        request: SigilContractRequest,
        *,
        caller_admission: Optional[AdmissionDecision],
        service_admission: Optional[AdmissionDecision],
        caller_health: Optional[HealthReport],
        service_health: Optional[HealthReport],
        now: int,
    ) -> SigilContractResponse:
        admitted, rejection_code = evaluate_sigil_contract_request(
            request,
            caller_admission=caller_admission,
            service_admission=service_admission,
            caller_health=caller_health,
            service_health=service_health,
            now=now,
        )
        if not admitted:
            return SigilContractResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                outcome=SigilContractOutcome.REJECTED,
                rejection_code=rejection_code,
                completed_at=now,
            )

        if request.operation == "certification_status_query":
            # `evaluate_sigil_contract_request` only returns admitted=True when
            # both admission decisions are present and ADMITTED (see its
            # docstring/implementation) — this assertion documents and
            # enforces that invariant rather than silently trusting it.
            assert service_admission is not None
            return SigilContractResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                outcome=SigilContractOutcome.ACCEPTED,
                advisory_output={
                    "caller_admitted": True,
                    "service_admitted": True,
                },
                evidence_refs=(f"prime_admission:{service_admission.decision_id}",),
                completed_at=now,
            )

        route = self._routes.get(request.operation)
        if route is None:
            return SigilContractResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                outcome=SigilContractOutcome.REJECTED,
                rejection_code=SigilRejectionCode.UNSUPPORTED_OPERATION,
                completed_at=now,
            )

        adapter = self._adapters.get(route.natural_key)
        if adapter is None:
            return SigilContractResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                outcome=SigilContractOutcome.REJECTED,
                rejection_code=SigilRejectionCode.SERVICE_NOT_ADMITTED,
                completed_at=now,
            )

        input_text = _summarize_input(request.input_payload)
        input_reference = f"input://sigil-contract/{request.request_id}"
        self._input_store.put(input_reference, input_text)

        routing_decision = _single_candidate_route(request, adapter.provider_id, route.model_alias)
        execution_request = ModelExecutionRequest(
            execution_id=f"sigil_exec_{request.request_id}",
            idempotency_key=f"sigil_idem_{request.request_id}",
            project_id="sigil-advisory",
            task_id=request.operation,
            request_id=request.request_id,
            routing_decision=routing_decision,
            selected_provider_id=adapter.provider_id,
            selected_model_id=route.model_alias,
            input_reference=input_reference,
            timeout_seconds=min(request.timeout_seconds, 300),
            maximum_attempts=1,
            requested_at=request.requested_at,
        )
        evidence = self._execution_service.execute(execution_request, timestamp=now)

        if evidence.state != ModelExecutionState.SUCCEEDED:
            rejection = (
                SigilRejectionCode.SERVICE_HEALTH_NOT_USABLE
                if evidence.error_classification
                in (
                    ModelExecutionErrorClass.AUTHORIZATION_INVALID,
                    ModelExecutionErrorClass.PROVIDER_UNAVAILABLE,
                )
                else SigilRejectionCode.INTERNAL_ERROR
            )
            return SigilContractResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                outcome=SigilContractOutcome.REJECTED,
                rejection_code=rejection,
                completed_at=now,
            )

        return SigilContractResponse(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            outcome=SigilContractOutcome.ACCEPTED,
            advisory_output={
                "operation": request.operation,
                "routed_to": route.natural_key,
                "model_alias": route.model_alias,
                "output_reference": evidence.output_reference,
            },
            evidence_refs=(evidence.evidence_id,),
            completed_at=now,
        )


def _summarize_input(payload: Dict[str, object]) -> str:
    keys = sorted(str(key) for key in payload.keys())
    return "advisory_request_keys:" + ",".join(keys) if keys else "advisory_request:empty"


def _single_candidate_route(
    request: SigilContractRequest, provider_id: str, model_alias: str
) -> RoutingDecision:
    """A minimal, single-candidate RoutingDecision for one Sigil dispatch.

    Sigil advisory dispatch does not need the full multi-provider scoring
    routing layer (``hermes_cli.agent_roles.model_routing``) — there is
    exactly one governed destination per operation (see
    ``DEFAULT_OPERATION_ROUTES``) — so this builds the minimal valid
    ``RoutingDecision`` that satisfies ``ModelExecutionRequest``'s
    consistency validator instead of routing through the general scorer.
    """
    candidate = CandidateScore(
        provider_id=provider_id,
        model_id=model_alias,
        disposition=CandidateDisposition.ELIGIBLE,
        estimated_cost_micros=0,
        score=100,
        quality_factor=100,
        reliability_factor=100,
        latency_factor=100,
        cost_factor=100,
        preference_factor=100,
        trust_factor=100,
    )
    return RoutingDecision(
        decision_id=f"sigil_route_{request.request_id}",
        request_id=request.request_id,
        request_fingerprint="0" * 64,
        selected_provider_id=provider_id,
        selected_model_id=model_alias,
        candidates=(candidate,),
        estimated_cost_micros=0,
        budget_limit_micros=0,
        policy_outcome=RoutingPolicyOutcome.FREE,
        fallback_chain=(),
        created_at=request.requested_at,
    )
