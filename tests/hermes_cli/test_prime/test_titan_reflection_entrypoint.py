from __future__ import annotations

from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker
from hermes_cli.prime.titan_idle_manager import IdleModelManager
from hermes_cli.prime.titan_reflection_entrypoint import (
    DispatchOutcome,
    ProcessedTaskResult,
    run_reflection_cycle,
)
from hermes_cli.prime.titan_runtime_modes import RuntimeMode
from hermes_cli.prime.titan_scheduler import SchedulerPolicy, SchedulerStateStore
from hermes_cli.prime.titan_task_queue import PersistentTaskQueue, QueueTask


def _config(**overrides) -> TitanRoutingConfig:
    env = {
        "HERMES_OMNIROUTE_AUTH_TOKEN": "a" * 20,
        "HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES": "embedding,lightweight,large",
        "HERMES_OMNIROUTE_ALIAS_ROUTES": (
            "embedding=titan_ollama@embeddinggemma:latest,"
            "lightweight=titan_ollama@hermes-llama3.2:3b-64k,"
            "large=freellmapi@gpt-4o-mini"
        ),
    }
    env.update(overrides)
    return TitanRoutingConfig.from_env(env)


class FakeDispatcher:
    def __init__(self, *, outcomes=None, default_succeeds=True):
        self._outcomes = list(outcomes or [])
        self._default_succeeds = default_succeeds
        self.calls = []

    def dispatch(self, *, model_alias, input_text, timeout_seconds):
        self.calls.append((model_alias, input_text))
        if self._outcomes:
            return self._outcomes.pop(0)
        if self._default_succeeds:
            return DispatchOutcome(
                succeeded=True, output_text=f"reply:{input_text}", latency_ms=1
            )
        return DispatchOutcome(succeeded=False, error="unreachable", latency_ms=1)


def _harness(tmp_path):
    root = tmp_path
    return dict(
        routing_config=_config(),
        scheduler_policy=SchedulerPolicy(),
        state_store=SchedulerStateStore(root / "scheduler_state.json"),
        queue=PersistentTaskQueue(root / "queue"),
        evidence_store=PrimeEvidenceStore(state_root=root / "evidence"),
    )


# ── parked mode under empty queue ────────────────────────────────────────────


def test_parked_mode_under_empty_queue(tmp_path) -> None:
    harness = _harness(tmp_path)
    result = run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        now=1_000,
    )
    assert result.woke is False
    assert result.wake_or_parked_reason == "empty_queue"
    assert result.processed == ()


# ── wake on new queued work ──────────────────────────────────────────────────


def test_wakes_and_processes_newly_queued_task(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    result = run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    assert result.woke is True
    assert len(result.processed) == 1
    assert result.processed[0].succeeded is True
    assert harness["queue"].is_empty()


# ── city-mode local routing ───────────────────────────────────────────────────


def test_city_mode_local_routing(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    dispatcher = FakeDispatcher()
    result = run_reflection_cycle(**harness, dispatcher=dispatcher, now=1_000)
    assert result.processed[0].mode == RuntimeMode.CITY
    assert dispatcher.calls == [("lightweight", "hi")]


# ── highway-mode escalation ───────────────────────────────────────────────────


def test_highway_mode_escalation(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="coding",
            context_length_tokens=200,
            input_reference="fix it",
        )
    )
    dispatcher = FakeDispatcher()
    result = run_reflection_cycle(**harness, dispatcher=dispatcher, now=1_000)
    assert result.processed[0].mode == RuntimeMode.HIGHWAY
    assert dispatcher.calls == [("large", "fix it")]


# ── denied provider precedence ───────────────────────────────────────────────


def test_denied_provider_precedence(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["routing_config"] = _config(HERMES_OMNIROUTE_DENIED_PROVIDERS="freellmapi")
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="coding",
            context_length_tokens=200,
            input_reference="fix it",
        )
    )
    result = run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    assert (
        result.processed[0].mode == RuntimeMode.CITY
    )  # never routed to the denied provider


# ── budget exhaustion ─────────────────────────────────────────────────────────


def test_budget_exhaustion_blocks_highway_across_cycle(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["scheduler_policy"] = SchedulerPolicy(
        daily_external_provider_budget_micros=0
    )
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="coding",
            context_length_tokens=200,
            input_reference="fix it",
        )
    )
    result = run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    assert result.processed[0].mode == RuntimeMode.CITY


# ── circuit breaker fallback ─────────────────────────────────────────────────


def test_circuit_breaker_fallback_to_city(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="coding",
            context_length_tokens=200,
            input_reference="fix it",
        )
    )
    open_breaker = CircuitBreaker(failure_threshold=1)
    open_breaker.record_failure()
    result = run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        freellmapi_circuit=open_breaker,
        now=1_000,
    )
    assert result.processed[0].mode == RuntimeMode.CITY


# ── thermal-pressure deferral ─────────────────────────────────────────────────


def test_thermal_pressure_defers_entire_cycle(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    result = run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        thermal_pressure=True,
        now=1_000,
    )
    assert result.woke is False
    assert result.wake_or_parked_reason == "thermal_or_load_pressure"
    assert not harness["queue"].is_empty()  # task was never touched, still pending


def test_memory_pressure_also_defers_entire_cycle(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    result = run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        memory_pressure=True,
        now=1_000,
    )
    assert result.woke is False
    assert result.wake_or_parked_reason == "thermal_or_load_pressure"


# ── repetitive reflection backoff ────────────────────────────────────────────


def test_repetitive_reflection_detected_and_persisted_across_cycles(tmp_path) -> None:
    # The entrypoint's own queue-driven trigger (a non-empty queue always
    # forces an immediate wake, matching "wake on new queued work") means
    # repetitive-reflection backoff cannot suppress a *queue-driven* wake in
    # this design -- that would defeat the immediate-wake requirement. What
    # the entrypoint must still get right is *detecting and persisting* the
    # repetition signal via content-addressed digests
    # (hermes_cli.prime.titan_scheduler.record_cycle_outcome), which is what
    # should_wake's own repetitive-backoff gate (proven directly in
    # test_titan_scheduler.py) consults for the purely-scheduled,
    # no-new-trigger case.
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    state_after_first = harness["state_store"].load(default_next_wake_at=0)
    assert (
        state_after_first.last_reflection_repetitive is False
    )  # nothing to compare against yet

    # Cycle 2 processes an identically-shaped task (same id, same outcome
    # mode) -- its digest matches cycle 1's exactly.
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=2_000)
    state_after_second = harness["state_store"].load(default_next_wake_at=0)
    assert state_after_second.last_reflection_repetitive is True


# ── evidence generation for every route decision ────────────────────────────


def test_evidence_generated_for_every_processed_task(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    harness["queue"].enqueue(
        QueueTask(
            task_id="t2",
            task_type="coding",
            context_length_tokens=200,
            input_reference="fix",
            submitted_at=1,
        )
    )
    run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)

    entries = harness["evidence_store"].read_all()
    kinds = [e["record"]["kind"] for e in entries]
    assert kinds.count("omniroute_route_decision") == 2
    assert kinds.count("titan_reflection_cycle") == 1
    assert harness["evidence_store"].verify_chain() is True


def test_evidence_generated_even_when_parked(tmp_path) -> None:
    harness = _harness(tmp_path)
    run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    entries = harness["evidence_store"].read_all()
    assert len(entries) == 1
    assert entries[0]["record"]["kind"] == "titan_reflection_cycle"


# ── idle model unloading integrated into the cycle ──────────────────────────


def test_idle_model_unload_triggered_by_cycle(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )

    class FakeUnloadTransport:
        def __init__(self):
            self.calls = []

        def post(self, url, payload, *, timeout_seconds):
            self.calls.append(payload)
            return {}

    transport = FakeUnloadTransport()
    idle_manager = IdleModelManager(
        state_path=tmp_path / "idle.json",
        ollama_endpoint="http://127.0.0.1:11434",
        idle_threshold_seconds=60,
        transport=transport,
    )
    run_reflection_cycle(
        **harness, dispatcher=FakeDispatcher(), idle_manager=idle_manager, now=1_000
    )
    # Not yet idle long enough to unload.
    assert transport.calls == []
    assert idle_manager.last_used_models() == {"lightweight": 1_000}

    # A later cycle (empty queue -> parked) does not call touch/sweep at
    # all when parked, so simulate a second processing cycle far later.
    harness["queue"].enqueue(
        QueueTask(
            task_id="t2",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi2",
        )
    )
    run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        idle_manager=idle_manager,
        now=1_000 + 120,
    )
    assert transport.calls == [{"model": "lightweight", "keep_alive": 0}]


# ── restart / persistent scheduler recovery ─────────────────────────────────


def test_restart_recovery_persists_across_fresh_state_store_instances(tmp_path) -> None:
    harness = _harness(tmp_path)
    harness["queue"].enqueue(
        QueueTask(
            task_id="t1",
            task_type="summary",
            context_length_tokens=100,
            input_reference="hi",
        )
    )
    result1 = run_reflection_cycle(**harness, dispatcher=FakeDispatcher(), now=1_000)
    assert result1.woke is True

    # Simulate a process restart: brand-new SchedulerStateStore instance
    # pointed at the same path, as a fresh one-shot process would create.
    fresh_state_store = SchedulerStateStore(tmp_path / "scheduler_state.json")
    reloaded_state = fresh_state_store.load(default_next_wake_at=0)
    assert reloaded_state.next_wake_at == result1.next_wake_at
    assert reloaded_state.daily_cycle_count == 1

    # A cycle run against the reloaded store, before its next_wake_at and
    # with an empty queue, correctly stays parked -- state truly persisted.
    result2 = run_reflection_cycle(
        routing_config=harness["routing_config"],
        scheduler_policy=harness["scheduler_policy"],
        state_store=fresh_state_store,
        queue=harness["queue"],
        dispatcher=FakeDispatcher(),
        evidence_store=harness["evidence_store"],
        now=1_001,
    )
    assert result2.woke is False
    assert result2.wake_or_parked_reason == "not_yet_due"


# ── no Mac dependency ─────────────────────────────────────────────────────────


def test_routing_config_used_by_reflection_cycle_rejects_mac_dependency() -> None:
    from hermes_cli.prime.omniroute_config import TitanRoutingConfigError

    env = {
        "HERMES_OMNIROUTE_AUTH_TOKEN": "a" * 20,
        "HERMES_TITAN_OLLAMA_ENDPOINT": "http://matthews-macbook-air:11434",
    }
    try:
        TitanRoutingConfig.from_env(env)
        raised = False
    except TitanRoutingConfigError:
        raised = True
    assert raised is True


# ── no uncontrolled continuous inference ────────────────────────────────────


def test_cycle_processes_at_most_max_tasks_per_cycle_never_unbounded(tmp_path) -> None:
    harness = _harness(tmp_path)
    for i in range(10):
        harness["queue"].enqueue(
            QueueTask(
                task_id=f"t{i}",
                task_type="summary",
                context_length_tokens=100,
                input_reference=f"hi {i}",
                submitted_at=i,
            )
        )
    result = run_reflection_cycle(
        **harness,
        dispatcher=FakeDispatcher(),
        max_tasks_per_cycle=3,
        now=1_000,
    )
    assert len(result.processed) == 3  # bounded, not all 10 in one cycle
    assert (
        len(harness["queue"].list_pending()) == 7
    )  # remainder waits for a later cycle


def test_cycle_is_a_single_bounded_call_not_a_loop(tmp_path) -> None:
    # run_reflection_cycle is a plain function call that returns -- there is
    # no internal while-True, no thread, no timer started by this call.
    # Calling it twice explicitly is required to run it twice; nothing about
    # the function itself keeps running afterward.
    import inspect

    from hermes_cli.prime import titan_reflection_entrypoint as module

    source = inspect.getsource(module.run_reflection_cycle)
    assert "while True" not in source
    assert "serve_forever" not in source
