from __future__ import annotations

import pytest

from hermes_cli.prime.titan_scheduler import (
    ClampedWakeDecision,
    ParkedReason,
    SchedulerConfigError,
    SchedulerPolicy,
    SchedulerState,
    SchedulerStateStore,
    WakeProposal,
    WakeTrigger,
    clamp_wake_proposal,
    record_cycle_outcome,
    should_wake,
)


# ── SchedulerPolicy validation ───────────────────────────────────────────────


def test_default_policy_matches_required_defaults() -> None:
    policy = SchedulerPolicy()
    assert policy.min_wake_interval_seconds == 300
    assert policy.max_wake_interval_seconds == 21_600
    assert policy.max_reflection_runtime_seconds == 600
    assert policy.max_consecutive_failed_cycles == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(min_wake_interval_seconds=1),
        dict(max_wake_interval_seconds=100),  # below min
        dict(max_reflection_runtime_seconds=1),
        dict(max_consecutive_failed_cycles=0),
        dict(daily_cycle_budget=0),
        dict(daily_external_provider_budget_micros=-5),
        dict(cooldown_after_pressure_seconds=-1),
    ],
)
def test_policy_rejects_invalid_bounds(kwargs) -> None:
    with pytest.raises(SchedulerConfigError):
        SchedulerPolicy(**kwargs)


def test_policy_from_env_overrides() -> None:
    policy = SchedulerPolicy.from_env({
        "HERMES_TITAN_SCHEDULER_MIN_WAKE_SECONDS": "600",
        "HERMES_TITAN_SCHEDULER_DAILY_CYCLE_BUDGET": "10",
    })
    assert policy.min_wake_interval_seconds == 600
    assert policy.daily_cycle_budget == 10


# ── clamp_wake_proposal: minimum/maximum wake interval clamping ─────────────


def test_clamp_rejects_proposal_below_minimum() -> None:
    policy = SchedulerPolicy()
    decision = clamp_wake_proposal(
        WakeProposal(
            next_wake_interval_seconds=5, reason="too eager", preferred_mode="city"
        ),
        policy,
    )
    assert decision.next_wake_interval_seconds == policy.min_wake_interval_seconds
    assert decision.clamped is True
    assert "below_minimum_wake_interval" in decision.clamp_reasons


def test_clamp_rejects_proposal_above_maximum() -> None:
    policy = SchedulerPolicy()
    decision = clamp_wake_proposal(
        WakeProposal(
            next_wake_interval_seconds=999_999, reason="lazy", preferred_mode="parked"
        ),
        policy,
    )
    assert decision.next_wake_interval_seconds == policy.max_wake_interval_seconds
    assert "above_maximum_wake_interval" in decision.clamp_reasons


def test_clamp_accepts_in_range_proposal_unmodified() -> None:
    policy = SchedulerPolicy()
    decision = clamp_wake_proposal(
        WakeProposal(
            next_wake_interval_seconds=1_800, reason="fine", preferred_mode="city"
        ),
        policy,
    )
    assert decision.next_wake_interval_seconds == 1_800
    assert decision.clamped is False
    assert decision.clamp_reasons == ()


def test_clamp_rejects_unknown_mode_proposal() -> None:
    # A model may only propose timing/mode -- an unrecognized mode is never
    # trusted, and is deterministically defaulted to parked.
    policy = SchedulerPolicy()
    decision = clamp_wake_proposal(
        WakeProposal(
            next_wake_interval_seconds=600, reason="x", preferred_mode="turbo_mode"
        ),
        policy,
    )
    assert decision.accepted_mode == "parked"
    assert decision.accepted_model_alias is None
    assert "unknown_preferred_mode_defaulted_to_parked" in decision.clamp_reasons


def test_clamp_drops_model_alias_when_mode_is_parked() -> None:
    policy = SchedulerPolicy()
    decision = clamp_wake_proposal(
        WakeProposal(
            next_wake_interval_seconds=600,
            reason="x",
            preferred_mode="parked",
            preferred_model_alias="large",
        ),
        policy,
    )
    assert decision.accepted_model_alias is None


# ── should_wake: parked / wake decision matrix ──────────────────────────────


def _state(**overrides) -> SchedulerState:
    fields = dict(next_wake_at=1_000)
    fields.update(overrides)
    return SchedulerState(**fields)


def test_parked_under_empty_queue_when_due_and_no_trigger() -> None:
    policy = SchedulerPolicy()
    woke, reason = should_wake(
        now=1_000,
        state=_state(),
        triggers=(),
        policy=policy,
        queue_empty=True,
    )
    assert woke is False
    assert reason == ParkedReason.EMPTY_QUEUE.value


def test_parked_when_not_yet_due() -> None:
    policy = SchedulerPolicy()
    woke, reason = should_wake(
        now=500,
        state=_state(next_wake_at=1_000),
        triggers=(),
        policy=policy,
        queue_empty=False,
    )
    assert woke is False
    assert reason == ParkedReason.NOT_YET_DUE.value


def test_wakes_on_new_queued_work_regardless_of_timer() -> None:
    policy = SchedulerPolicy()
    woke, reason = should_wake(
        now=1,
        state=_state(next_wake_at=999_999),
        triggers=(WakeTrigger.QUEUED_TASK_ELIGIBLE,),
        policy=policy,
        queue_empty=False,
    )
    assert woke is True
    assert reason == WakeTrigger.QUEUED_TASK_ELIGIBLE.value


def test_thermal_pressure_defers_even_with_a_real_trigger() -> None:
    policy = SchedulerPolicy()
    woke, reason = should_wake(
        now=1_000,
        state=_state(),
        triggers=(WakeTrigger.JOB_ARRIVED,),
        policy=policy,
        thermal_or_load_pressure=True,
    )
    assert woke is False
    assert reason == ParkedReason.THERMAL_OR_LOAD_PRESSURE.value


def test_active_cooldown_defers_wake() -> None:
    policy = SchedulerPolicy()
    state = _state(cooldown_until=2_000)
    woke, reason = should_wake(
        now=1_500, state=state, triggers=(), policy=policy, queue_empty=False
    )
    assert woke is False
    assert reason == ParkedReason.THERMAL_OR_LOAD_PRESSURE.value


def test_repetitive_reflection_backoff_on_purely_scheduled_wake() -> None:
    policy = SchedulerPolicy()
    state = _state(next_wake_at=500)
    woke, reason = should_wake(
        now=1_000,
        state=state,
        triggers=(),
        policy=policy,
        queue_empty=False,
        last_reflection_repetitive=True,
    )
    assert woke is False
    assert reason == ParkedReason.REPETITIVE_REFLECTION.value


def test_repetitive_reflection_does_not_block_a_real_trigger() -> None:
    policy = SchedulerPolicy()
    state = _state(next_wake_at=500)
    woke, reason = should_wake(
        now=1_000,
        state=state,
        triggers=(WakeTrigger.NEW_EVIDENCE,),
        policy=policy,
        last_reflection_repetitive=True,
    )
    assert woke is True
    assert reason == WakeTrigger.NEW_EVIDENCE.value


def test_daily_budget_exhausted_blocks_wake() -> None:
    policy = SchedulerPolicy(daily_cycle_budget=2)
    state = _state(daily_cycle_count=2)
    woke, reason = should_wake(
        now=1_000,
        state=state,
        triggers=(WakeTrigger.JOB_ARRIVED,),
        policy=policy,
    )
    assert woke is False
    assert reason == ParkedReason.DAILY_BUDGET_EXHAUSTED.value


def test_hermes_paused_blocks_everything() -> None:
    policy = SchedulerPolicy()
    woke, reason = should_wake(
        now=1_000,
        state=_state(),
        triggers=(WakeTrigger.MAINTENANCE_THRESHOLD,),
        policy=policy,
        hermes_paused=True,
    )
    assert woke is False
    assert reason == ParkedReason.HERMES_PAUSED.value


def test_too_many_consecutive_failures_blocks_wake() -> None:
    policy = SchedulerPolicy(max_consecutive_failed_cycles=3)
    state = _state(consecutive_failed_cycles=3)
    woke, reason = should_wake(
        now=1_000,
        state=state,
        triggers=(WakeTrigger.JOB_ARRIVED,),
        policy=policy,
    )
    assert woke is False
    assert reason == ParkedReason.TOO_MANY_CONSECUTIVE_FAILURES.value


# ── record_cycle_outcome ─────────────────────────────────────────────────────


def test_record_cycle_outcome_resets_failures_on_success() -> None:
    policy = SchedulerPolicy()
    state = _state(consecutive_failed_cycles=2)
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="city",
        accepted_model_alias="lightweight",
    )
    new_state = record_cycle_outcome(
        state,
        now=1_000,
        succeeded=True,
        clamped_decision=clamped,
        policy=policy,
    )
    assert new_state.consecutive_failed_cycles == 0
    assert new_state.next_wake_at == 1_300


def test_record_cycle_outcome_increments_failures_on_failure() -> None:
    policy = SchedulerPolicy()
    state = _state(consecutive_failed_cycles=1)
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="city",
        accepted_model_alias="lightweight",
    )
    new_state = record_cycle_outcome(
        state,
        now=1_000,
        succeeded=False,
        clamped_decision=clamped,
        policy=policy,
    )
    assert new_state.consecutive_failed_cycles == 2


def test_record_cycle_outcome_sets_cooldown_on_pressure() -> None:
    policy = SchedulerPolicy(cooldown_after_pressure_seconds=500)
    state = _state()
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="parked",
        accepted_model_alias=None,
    )
    new_state = record_cycle_outcome(
        state,
        now=1_000,
        succeeded=True,
        clamped_decision=clamped,
        policy=policy,
        pressure_detected=True,
    )
    assert new_state.cooldown_until == 1_500


def test_record_cycle_outcome_detects_repetition_via_digest() -> None:
    policy = SchedulerPolicy()
    state = _state(last_reflection_digest="abc123")
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="city",
        accepted_model_alias="lightweight",
    )
    new_state = record_cycle_outcome(
        state,
        now=1_000,
        succeeded=True,
        clamped_decision=clamped,
        policy=policy,
        reflection_digest="abc123",
    )
    assert new_state.last_reflection_repetitive is True

    different = record_cycle_outcome(
        state,
        now=1_000,
        succeeded=True,
        clamped_decision=clamped,
        policy=policy,
        reflection_digest="different",
    )
    assert different.last_reflection_repetitive is False


def test_record_cycle_outcome_rolls_over_daily_counters_on_new_day() -> None:
    policy = SchedulerPolicy()
    state = SchedulerState(next_wake_at=0, daily_cycle_count=50, day_epoch=0)
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="city",
        accepted_model_alias="lightweight",
    )
    next_day = 86_400 + 10
    new_state = record_cycle_outcome(
        state,
        now=next_day,
        succeeded=True,
        clamped_decision=clamped,
        policy=policy,
    )
    assert new_state.daily_cycle_count == 1  # reset then incremented for this cycle
    assert new_state.day_epoch == next_day // 86_400


# ── SchedulerStateStore: restart / persistent recovery ──────────────────────


def test_state_store_round_trips(tmp_path) -> None:
    store = SchedulerStateStore(tmp_path / "scheduler_state.json")
    state = SchedulerState(
        next_wake_at=12_345, consecutive_failed_cycles=1, daily_cycle_count=4
    )
    store.save(state)
    reloaded = store.load(default_next_wake_at=0)
    assert reloaded == state


def test_state_store_returns_default_when_missing(tmp_path) -> None:
    store = SchedulerStateStore(tmp_path / "missing.json")
    state = store.load(default_next_wake_at=555)
    assert state.next_wake_at == 555
    assert state.consecutive_failed_cycles == 0


def test_state_store_recovers_from_corrupt_file_without_crashing(tmp_path) -> None:
    path = tmp_path / "scheduler_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = SchedulerStateStore(path)
    state = store.load(default_next_wake_at=777)
    assert state.next_wake_at == 777


def test_state_store_rejects_relative_path() -> None:
    from hermes_cli.prime.titan_scheduler import SchedulerStateError

    with pytest.raises(SchedulerStateError):
        SchedulerStateStore("relative/path.json")  # type: ignore[arg-type]


def test_state_store_survives_process_restart_simulation(tmp_path) -> None:
    # Simulate: cycle 1 runs and saves state, "process exits" (nothing kept
    # in memory), cycle 2 is a fresh process that reloads the same state.
    path = tmp_path / "scheduler_state.json"
    store1 = SchedulerStateStore(path)
    state1 = store1.load(default_next_wake_at=100)
    policy = SchedulerPolicy()
    clamped = ClampedWakeDecision(
        next_wake_interval_seconds=300,
        clamped=False,
        clamp_reasons=(),
        accepted_mode="city",
        accepted_model_alias="lightweight",
    )
    updated = record_cycle_outcome(
        state1, now=100, succeeded=True, clamped_decision=clamped, policy=policy
    )
    store1.save(updated)

    store2 = SchedulerStateStore(
        path
    )  # a brand-new store instance, as a new process would create
    state2 = store2.load(default_next_wake_at=0)
    assert state2.next_wake_at == 400
    assert state2.daily_cycle_count == 1
