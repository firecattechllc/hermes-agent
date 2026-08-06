"""One-shot governed reflection cycle for Titan.

This process starts, evaluates whether there is anything worth doing right
now (:func:`hermes_cli.prime.titan_scheduler.should_wake`), does at most a
bounded amount of governed work if so, persists its scheduler state, and
exits -- it is never a resident loop and never performs unbounded or
continuous inference. Deployed as a systemd oneshot service
(``deploy/titan/titan-reflection.service``), triggered by a periodic timer
floor (``titan-reflection.timer``) and an event-driven path unit watching
the task queue directory (``titan-reflection.path``).

Dispatch always goes through Titan's own local OmniRoute endpoint (the same
one any other approved Hermes workload uses) via
:func:`build_omniroute_dispatcher` -- there is no separate, unaudited
execution path for reflection-cycle work. Route decisions are recorded with
the exact same :mod:`hermes_cli.prime.omniroute_evidence` schema OmniRoute's
own HTTP server uses, so Mission Control sees one consistent evidence trail
regardless of which component made the routing decision.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Tuple

from hermes_cli.prime.evidence import (
    EvidenceRecord,
    PrimeEvidenceStore,
    SensitivityTier,
)
from hermes_cli.prime.ollama_node import UrllibOllamaTransport
from hermes_cli.prime.omniroute_client_adapter import (
    OmniRouteClientTransport,
    OmniRouteClientTransportError,
    UrllibOmniRouteClientTransport,
    extract_output_text,
)
from hermes_cli.prime.omniroute_config import (
    TitanRoutingConfig,
    TitanRoutingConfigError,
)
from hermes_cli.prime.omniroute_evidence import (
    RouteDecisionEvidence,
    RouteStatus,
    build_route_decision_evidence_record,
)
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker
from hermes_cli.prime.titan_idle_manager import IdleModelManager
from hermes_cli.prime.titan_runtime_modes import (
    RuntimeMode,
    classify_task,
    select_route,
)
from hermes_cli.prime.titan_scheduler import (
    SchedulerConfigError,
    SchedulerPolicy,
    SchedulerStateStore,
    WakeProposal,
    WakeTrigger,
    clamp_wake_proposal,
    record_cycle_outcome,
    should_wake,
)
from hermes_cli.prime.titan_task_queue import PersistentTaskQueue

logger = logging.getLogger("hermes.prime.titan.reflection")


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    succeeded: bool
    output_text: Optional[str] = None
    error: Optional[str] = None
    latency_ms: int = 0
    cost_micros: int = 0


class ReflectionDispatcher(Protocol):
    def dispatch(
        self, *, model_alias: str, input_text: str, timeout_seconds: float
    ) -> DispatchOutcome: ...


def build_omniroute_dispatcher(
    *,
    base_url: str,
    auth_token: str,
    transport: Optional[OmniRouteClientTransport] = None,
) -> ReflectionDispatcher:
    transport = transport or UrllibOmniRouteClientTransport()
    base = base_url.rstrip("/")

    class _OmniRouteDispatcher:
        def dispatch(
            self, *, model_alias: str, input_text: str, timeout_seconds: float
        ) -> DispatchOutcome:
            started = time.monotonic()
            payload = {
                "model": model_alias,
                "messages": [{"role": "user", "content": input_text}],
                "stream": False,
            }
            try:
                raw = transport.post_chat_completion(
                    f"{base}/v1/chat/completions",
                    payload,
                    auth_token=auth_token,
                    timeout_seconds=timeout_seconds,
                )
            except OmniRouteClientTransportError as error:
                return DispatchOutcome(
                    succeeded=False,
                    error=str(error),
                    latency_ms=int((time.monotonic() - started) * 1_000),
                )
            text = extract_output_text(raw)
            if text is None:
                return DispatchOutcome(
                    succeeded=False,
                    error="malformed OmniRoute response",
                    latency_ms=int((time.monotonic() - started) * 1_000),
                )
            return DispatchOutcome(
                succeeded=True,
                output_text=text,
                latency_ms=int((time.monotonic() - started) * 1_000),
            )

    return _OmniRouteDispatcher()


@dataclass(frozen=True, slots=True)
class ProcessedTaskResult:
    task_id: str
    mode: RuntimeMode
    succeeded: bool
    evidence_id: Optional[str]


@dataclass(frozen=True, slots=True)
class ReflectionCycleResult:
    woke: bool
    wake_or_parked_reason: str
    processed: Tuple[ProcessedTaskResult, ...]
    next_wake_at: int
    cycle_evidence_id: Optional[str]


def _append_cycle_evidence(
    *,
    producer_identity_id: str,
    woke: bool,
    reason: str,
    processed_count: int,
    timestamp: int,
    evidence_store: Optional[PrimeEvidenceStore],
) -> Optional[str]:
    if evidence_store is None:
        return None
    record = EvidenceRecord.build(
        kind="titan_reflection_cycle",
        producer_identity_id=producer_identity_id,
        subject_identity_id="titan",
        provenance="hermes_cli.prime.titan_reflection_entrypoint",
        timestamp=timestamp,
        redacted_summary=f"woke={woke} reason={reason} processed={processed_count}",
        sensitivity=SensitivityTier.INTERNAL,
    )
    try:
        evidence_store.append(record)
    except Exception:  # noqa: BLE001 - evidence recording must never crash the cycle
        logger.exception("failed to append Titan reflection cycle evidence")
        return None
    return record.evidence_id


def _append_route_evidence(
    evidence: RouteDecisionEvidence, *, evidence_store: Optional[PrimeEvidenceStore]
) -> Optional[str]:
    if evidence_store is None:
        return None
    try:
        record = build_route_decision_evidence_record(
            evidence, producer_identity_id="titan-reflection"
        )
        evidence_store.append(record)
    except Exception:  # noqa: BLE001 - evidence recording must never crash the cycle
        logger.exception("failed to append Titan route-decision evidence")
        return None
    return record.evidence_id


def run_reflection_cycle(
    *,
    routing_config: TitanRoutingConfig,
    scheduler_policy: SchedulerPolicy,
    state_store: SchedulerStateStore,
    queue: PersistentTaskQueue,
    dispatcher: ReflectionDispatcher,
    idle_manager: Optional[IdleModelManager] = None,
    evidence_store: Optional[PrimeEvidenceStore] = None,
    titan_ollama_circuit: Optional[CircuitBreaker] = None,
    freellmapi_circuit: Optional[CircuitBreaker] = None,
    hermes_paused: bool = False,
    thermal_pressure: bool = False,
    memory_pressure: bool = False,
    max_tasks_per_cycle: int = 5,
    now: Optional[int] = None,
) -> ReflectionCycleResult:
    """Run exactly one governed reflection cycle and return its outcome.

    Bounded by ``max_tasks_per_cycle`` and, in production, by systemd's own
    ``TimeoutStartSec`` matching ``scheduler_policy.max_reflection_runtime_seconds``
    -- this function itself never loops indefinitely regardless of how much
    work is queued.
    """
    now = now if now is not None else int(time.time())
    state = state_store.load(default_next_wake_at=now)

    # Idle-model unloading is purely time-based (last-used vs. threshold),
    # independent of whether this cycle finds any work -- it must still run
    # while parked, otherwise a model loaded just before Titan goes idle for
    # a long stretch would never be unloaded.
    if idle_manager is not None:
        idle_manager.sweep(now=now)

    pending = queue.list_pending()
    triggers: Tuple[WakeTrigger, ...] = (
        (WakeTrigger.QUEUED_TASK_ELIGIBLE,) if pending else ()
    )

    woke, reason = should_wake(
        now=now,
        state=state,
        triggers=triggers,
        policy=scheduler_policy,
        thermal_or_load_pressure=(thermal_pressure or memory_pressure),
        hermes_paused=hermes_paused,
        queue_empty=(len(pending) == 0),
        last_reflection_repetitive=state.last_reflection_repetitive,
    )

    if not woke:
        cycle_evidence_id = _append_cycle_evidence(
            producer_identity_id="titan-reflection",
            woke=False,
            reason=reason,
            processed_count=0,
            timestamp=now,
            evidence_store=evidence_store,
        )
        return ReflectionCycleResult(
            woke=False,
            wake_or_parked_reason=reason,
            processed=(),
            next_wake_at=state.next_wake_at,
            cycle_evidence_id=cycle_evidence_id,
        )

    processed = []
    any_failed = False
    digest_parts = []
    external_spend_this_cycle = 0

    daily_budget = scheduler_policy.daily_external_provider_budget_micros
    for task in pending[:max_tasks_per_cycle]:
        classification = classify_task(
            task_type=task.task_type,
            context_length_tokens=task.context_length_tokens,
            privacy_sensitive=task.privacy_sensitive,
        )
        external_budget_remaining = (
            None
            if daily_budget is None
            else daily_budget
            - state.daily_external_spend_micros
            - external_spend_this_cycle
        )
        selection = select_route(
            classification,
            config=routing_config,
            titan_ollama_circuit=titan_ollama_circuit,
            freellmapi_circuit=freellmapi_circuit,
            thermal_pressure=thermal_pressure,
            memory_pressure=memory_pressure,
            external_budget_remaining_micros=external_budget_remaining,
        )

        if selection.mode == RuntimeMode.PARKED or selection.model_alias is None:
            evidence = RouteDecisionEvidence(
                correlation_id=task.task_id,
                requested_capability=task.task_type,
                reason=selection.reason,
                status=RouteStatus.POLICY_REJECTED,
                policy_rejected=True,
                latency_ms=0,
                observed_at=now,
            )
            evidence_id = _append_route_evidence(
                evidence, evidence_store=evidence_store
            )
            processed.append(
                ProcessedTaskResult(
                    task_id=task.task_id,
                    mode=selection.mode,
                    succeeded=False,
                    evidence_id=evidence_id,
                )
            )
            any_failed = True
            continue

        outcome = dispatcher.dispatch(
            model_alias=selection.model_alias,
            input_text=task.input_reference,
            timeout_seconds=scheduler_policy.max_reflection_runtime_seconds,
        )
        status = RouteStatus.SUCCEEDED if outcome.succeeded else RouteStatus.FAILED
        evidence = RouteDecisionEvidence(
            correlation_id=task.task_id,
            requested_capability=task.task_type,
            selected_provider=selection.provider,
            selected_model=selection.model_alias,
            is_local_route=(selection.mode == RuntimeMode.CITY),
            reason=selection.reason,
            fallback_attempts=selection.fallback_chain,
            provider_error=(outcome.error[:256] if outcome.error else None),
            status=status,
            latency_ms=max(0, outcome.latency_ms),
            observed_at=now,
        )
        evidence_id = _append_route_evidence(evidence, evidence_store=evidence_store)

        if outcome.succeeded:
            queue.dequeue(task.task_id)
            if idle_manager is not None:
                idle_manager.touch(selection.model_alias, now=now)
            digest_parts.append(f"{task.task_id}:{selection.mode.value}")
            if selection.mode == RuntimeMode.HIGHWAY:
                external_spend_this_cycle += max(0, outcome.cost_micros)
        else:
            any_failed = True

        processed.append(
            ProcessedTaskResult(
                task_id=task.task_id,
                mode=selection.mode,
                succeeded=outcome.succeeded,
                evidence_id=evidence_id,
            )
        )

    remaining_pending = not queue.is_empty()
    proposal = WakeProposal(
        next_wake_interval_seconds=(
            scheduler_policy.min_wake_interval_seconds
            if remaining_pending
            else scheduler_policy.max_wake_interval_seconds
        ),
        reason=("more_work_pending" if remaining_pending else "queue_drained"),
        preferred_mode=("city" if remaining_pending else "parked"),
    )
    clamped = clamp_wake_proposal(proposal, scheduler_policy)

    reflection_digest = (
        hashlib.sha256("|".join(digest_parts).encode()).hexdigest()
        if digest_parts
        else None
    )

    new_state = record_cycle_outcome(
        state,
        now=now,
        succeeded=not any_failed,
        clamped_decision=clamped,
        policy=scheduler_policy,
        external_spend_micros=external_spend_this_cycle,
        pressure_detected=(thermal_pressure or memory_pressure),
        reflection_digest=reflection_digest,
    )
    state_store.save(new_state)

    cycle_evidence_id = _append_cycle_evidence(
        producer_identity_id="titan-reflection",
        woke=True,
        reason=reason,
        processed_count=len(processed),
        timestamp=now,
        evidence_store=evidence_store,
    )

    return ReflectionCycleResult(
        woke=True,
        wake_or_parked_reason=reason,
        processed=tuple(processed),
        next_wake_at=new_state.next_wake_at,
        cycle_evidence_id=cycle_evidence_id,
    )


def main(argv: Optional[list] = None) -> int:
    """CLI entrypoint: run exactly one reflection cycle and exit."""
    del argv
    logging.basicConfig(
        level=os.environ.get("HERMES_TITAN_REFLECTION_LOG_LEVEL", "INFO")
    )

    try:
        routing_config = TitanRoutingConfig.from_env()
        scheduler_policy = SchedulerPolicy.from_env()
    except (TitanRoutingConfigError, SchedulerConfigError) as error:
        logger.error("Titan reflection configuration rejected: %s", error)
        return 2

    state_root = Path(
        os.environ.get(
            "HERMES_TITAN_REFLECTION_STATE_ROOT",
            "/var/lib/hermes-prime/titan-reflection",
        )
    )
    queue = PersistentTaskQueue(state_root / "queue")
    state_store = SchedulerStateStore(state_root / "scheduler_state.json")
    idle_threshold_raw = os.environ.get(
        "HERMES_TITAN_IDLE_MODEL_THRESHOLD_SECONDS", ""
    ).strip()
    idle_manager = IdleModelManager(
        state_path=state_root / "idle_models.json",
        ollama_endpoint=routing_config.titan_ollama_endpoint,
        idle_threshold_seconds=int(idle_threshold_raw) if idle_threshold_raw else 600,
        transport=UrllibOllamaTransport(),
    )
    evidence_store = PrimeEvidenceStore()
    dispatcher = build_omniroute_dispatcher(
        base_url=f"http://{routing_config.bind_host}:{routing_config.bind_port}",
        auth_token=routing_config.omniroute_auth_token,
    )

    result = run_reflection_cycle(
        routing_config=routing_config,
        scheduler_policy=scheduler_policy,
        state_store=state_store,
        queue=queue,
        dispatcher=dispatcher,
        idle_manager=idle_manager,
        evidence_store=evidence_store,
    )
    logger.info(
        "Titan reflection cycle: woke=%s reason=%s processed=%d next_wake_at=%d",
        result.woke,
        result.wake_or_parked_reason,
        len(result.processed),
        result.next_wake_at,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
