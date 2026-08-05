"""Governed dispatch gate: connects fleet admission to workflow dispatch.

Fleet Unification live-runtime work. ``hermes_cli.agent_roles.model_execution``
already implements a governed, idempotent, retryable execution state machine
over a set of ``ModelProviderAdapter`` instances — but per
``docs/architecture/FLEET_UNIFICATION_STAGES_2_9.md`` §8.1, nothing in Stage
2 ever called Prime admission or health from that execution path. This
module is that missing connection: :class:`PrimeGovernedProviderAdapter`
implements ``ModelProviderAdapter`` by wrapping a real device adapter (e.g.
:class:`hermes_cli.prime.ollama_node.OllamaNodeProviderAdapter`) behind a
:class:`hermes_cli.prime.fleet_runtime.FleetRuntime` admission + health
check.

This module never modifies ``model_execution.py`` itself — it composes in
front of it, exactly like every other Stage 2 module composes in front of
its pre-existing subsystem rather than editing it. The only way a node
participates in governed dispatch is by being wrapped in one of these
adapters and handed to ``GovernedModelExecutionService`` as a
``ModelProviderAdapter``; a node that is not wrapped this way is simply not
reachable through governed dispatch at all, so there is no code path by
which dispatch can "fall back" to an unauthorized provider or node — the
adapter set itself is the allowlist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from hermes_cli.agent_roles.model_execution import (
    ModelExecutionErrorClass,
    ProviderExecutionResult,
    ProviderUsage,
)
from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.ollama_node import OllamaGenerateOutcome, OllamaOutputStore


class GenerationAdapter(Protocol):
    """What :class:`PrimeGovernedProviderAdapter` needs from a device adapter.

    Deliberately a ``Protocol`` (structural typing) rather than requiring
    the concrete :class:`hermes_cli.prime.ollama_node.OllamaNodeProviderAdapter`
    class — any object with a matching ``generate`` method works, which is
    exactly how the test doubles used throughout this package's own test
    suite are built.
    """

    def generate(
        self, *, alias: Optional[str], input_text: str, timeout_seconds: float
    ) -> OllamaGenerateOutcome: ...


class DispatchInputUnresolvedError(RuntimeError):
    """An execution request's input_reference could not be resolved to content."""


@dataclass(frozen=True, slots=True)
class CertificationSnapshot:
    """A point-in-time echo of the fleet's certification status.

    ``hermes_cli.prime.certification.certify_fleet`` is a pure function the
    caller runs separately (e.g. on a schedule or before each service
    restart); this is the small, explicitly-refreshable snapshot of its
    latest result that governed dispatch consults on every call, so a stale
    or never-run certification never silently reads as CERTIFIED.
    """

    status: CertificationStatus
    evidence_ref: Optional[str] = None


class PrimeGovernedProviderAdapter:
    """Gates a device adapter's execution behind live fleet admission + health.

    Implements ``hermes_cli.agent_roles.model_execution.ModelProviderAdapter``
    (``provider_id`` property + ``execute(model_id, input_reference,
    timeout_seconds)``). Every call re-checks, against the caller-injectable
    clock, whether the wrapped node is *currently* admitted, healthy, and
    certified — never trusting a decision made at construction time or on a
    previous call.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        natural_key: str,
        fleet_runtime: FleetRuntime,
        underlying: GenerationAdapter,
        certification_provider: Callable[[], CertificationSnapshot],
        input_resolver: Callable[[str], Optional[str]],
        output_store: Optional[OllamaOutputStore] = None,
        clock: Optional[Callable[[], int]] = None,
    ) -> None:
        if not provider_id or not provider_id.strip() or "/" in provider_id:
            raise ValueError("provider_id must be a non-empty string without '/'")
        if not natural_key or not natural_key.strip():
            raise ValueError("natural_key must be non-empty")
        self._provider_id = provider_id
        self._natural_key = natural_key
        self._fleet_runtime = fleet_runtime
        self._underlying = underlying
        self._certification_provider = certification_provider
        self._input_resolver = input_resolver
        self._output_store = output_store or OllamaOutputStore()
        self._clock = clock or (lambda: int(time.time()))

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def execute(
        self, *, model_id: str, input_reference: str, timeout_seconds: int
    ) -> ProviderExecutionResult:
        # Non-empty provider and model are guaranteed on every request — an
        # empty model never reaches the fleet-admission check, let alone the
        # network, matching the "model is required" prevention this module
        # exists for.
        if not model_id or not model_id.strip():
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.INVALID_REQUEST
            )
        if not self._provider_id or not self._provider_id.strip():
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.INVALID_REQUEST
            )

        now = self._clock()
        certification = self._certification_provider()
        dispatchable = self._fleet_runtime.is_dispatchable(
            self._natural_key,
            now=now,
            certification_status=certification.status,
            certification_evidence_ref=certification.evidence_ref,
        )
        if not dispatchable:
            # Fail closed. This is deliberately not PROVIDER_UNAVAILABLE
            # (which model_execution.py treats as fallback/retry-eligible):
            # an inadmissible node is a governance fact, not a transient
            # outage, so the caller's fallback chain still runs (a *different*
            # admitted node may be next) but this exact node is never retried
            # as-is.
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.AUTHORIZATION_INVALID
            )

        input_text = self._input_resolver(input_reference)
        if input_text is None:
            return ProviderExecutionResult(
                error_classification=ModelExecutionErrorClass.INVALID_REQUEST
            )

        outcome = self._underlying.generate(
            alias=model_id, input_text=input_text, timeout_seconds=timeout_seconds
        )
        if not outcome.succeeded:
            classification = (
                ModelExecutionErrorClass.PROVIDER_UNAVAILABLE
                if outcome.retryable
                else ModelExecutionErrorClass.INVALID_REQUEST
            )
            return ProviderExecutionResult(error_classification=classification)

        output_reference = self._output_store.store(self._natural_key, outcome.output_text or "")
        usage = ProviderUsage(
            input_units=len(input_text.split()),
            output_units=len((outcome.output_text or "").split()),
            actual_cost_micros=0,
        )
        return ProviderExecutionResult(output_reference=output_reference, usage=usage)


class InMemoryReferenceStore:
    """A simple, non-mocked reference->text content store for request input.

    A real deployment's caller (whatever assembled the ``ModelExecutionRequest``)
    is responsible for making input content resolvable by reference before
    dispatch; this in-memory store is a legitimate minimal implementation of
    that contract (used the same way in tests and in a single-process
    service), not a stand-in for one.
    """

    def __init__(self) -> None:
        self._by_reference: dict[str, str] = {}

    def put(self, reference: str, text: str) -> str:
        self._by_reference[reference] = text
        return reference

    def resolve(self, reference: str) -> Optional[str]:
        return self._by_reference.get(reference)
