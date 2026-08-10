"""Deterministic dynamic wake-up scheduler for Titan's governed reflection cycle.

Replaces "wake up and run something every N minutes, unconditionally" with a
governed decision: each completed cycle may *propose* a next wake interval,
mode, and model, but only :func:`clamp_wake_proposal` — a pure, deterministic
function with no model input — decides what Titan actually does with that
proposal. A model can never set its own wake timing, budget, or mode outside
the bounds :class:`SchedulerPolicy` enforces; it can only suggest, and the
suggestion is clamped or rejected, never trusted outright.

This is intentionally the same shape as ``cron/jobs.py``'s self-rescheduling
``next_run`` persistence pattern (compute once, persist, reload on restart)
and takes its "should a gate hold back a run" precedent from
``cron/scheduler.py``'s ``_parse_wake_gate()`` — there is no fixed-interval
Titan reflection loop anywhere in this repository today to migrate off of;
this is that mechanism's first implementation, built to the same governed,
fail-closed conventions as the rest of ``hermes_cli.prime``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

SCHEDULER_STATE_SCHEMA_VERSION = 1


class SchedulerConfigError(ValueError):
    """Scheduler policy configuration is invalid."""


class SchedulerStateError(RuntimeError):
    """Persistent scheduler state could not be loaded or saved safely."""


class WakeTrigger(str, Enum):
    JOB_ARRIVED = "job_arrived"
    QUEUED_TASK_ELIGIBLE = "queued_task_eligible"
    NEW_EVIDENCE = "new_evidence"
    SCHEDULED_REFLECTION_DUE = "scheduled_reflection_due"
    DEPENDENCY_AVAILABLE = "dependency_available"
    PROVIDER_RECOVERED = "provider_recovered"
    MAINTENANCE_THRESHOLD = "maintenance_threshold"


class ParkedReason(str, Enum):
    NO_TRIGGER = "no_trigger"
    NOT_YET_DUE = "not_yet_due"
    EMPTY_QUEUE = "empty_queue"
    REPETITIVE_REFLECTION = "repetitive_reflection"
    THERMAL_OR_LOAD_PRESSURE = "thermal_or_load_pressure"
    DAILY_BUDGET_EXHAUSTED = "daily_budget_exhausted"
    HERMES_PAUSED = "hermes_paused"
    TOO_MANY_CONSECUTIVE_FAILURES = "too_many_consecutive_failures"


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    """Enforced defaults for Titan's dynamic wake-up clock.

    These are hard invariants, not merely suggested starting points: the
    bounds in ``__post_init__`` prevent a misconfiguration from disabling
    them entirely (e.g. a near-zero minimum interval that amounts to a busy
    loop, or a multi-day maximum that starves scheduled reflection).
    """

    min_wake_interval_seconds: int = 300  # 5 minutes
    max_wake_interval_seconds: int = 21_600  # 6 hours
    max_reflection_runtime_seconds: int = 600  # 10 minutes
    max_consecutive_failed_cycles: int = 3
    daily_cycle_budget: int = 96
    daily_external_provider_budget_micros: Optional[int] = None
    cooldown_after_pressure_seconds: int = 900  # 15 minutes

    def __post_init__(self) -> None:
        if not 60 <= self.min_wake_interval_seconds <= 3_600:
            raise SchedulerConfigError(
                "min_wake_interval_seconds must be between 60 and 3600"
            )
        if self.max_wake_interval_seconds < self.min_wake_interval_seconds:
            raise SchedulerConfigError(
                "max_wake_interval_seconds must be >= min_wake_interval_seconds"
            )
        if self.max_wake_interval_seconds > 86_400:
            raise SchedulerConfigError(
                "max_wake_interval_seconds must not exceed 24 hours"
            )
        if not 30 <= self.max_reflection_runtime_seconds <= 3_600:
            raise SchedulerConfigError(
                "max_reflection_runtime_seconds must be between 30 and 3600"
            )
        if self.max_consecutive_failed_cycles < 1:
            raise SchedulerConfigError("max_consecutive_failed_cycles must be >= 1")
        if self.daily_cycle_budget < 1:
            raise SchedulerConfigError("daily_cycle_budget must be >= 1")
        if (
            self.daily_external_provider_budget_micros is not None
            and self.daily_external_provider_budget_micros < 0
        ):
            raise SchedulerConfigError(
                "daily_external_provider_budget_micros must not be negative"
            )
        if self.cooldown_after_pressure_seconds < 0:
            raise SchedulerConfigError(
                "cooldown_after_pressure_seconds must not be negative"
            )

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "SchedulerPolicy":
        import os as _os

        env = env if env is not None else _os.environ

        def _int(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw.strip())
            except ValueError as error:
                raise SchedulerConfigError(f"{name} must be an integer") from error

        def _optional_int(name: str) -> Optional[int]:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return None
            try:
                return int(raw.strip())
            except ValueError as error:
                raise SchedulerConfigError(f"{name} must be an integer") from error

        return cls(
            min_wake_interval_seconds=_int(
                "HERMES_TITAN_SCHEDULER_MIN_WAKE_SECONDS", 300
            ),
            max_wake_interval_seconds=_int(
                "HERMES_TITAN_SCHEDULER_MAX_WAKE_SECONDS", 21_600
            ),
            max_reflection_runtime_seconds=_int(
                "HERMES_TITAN_SCHEDULER_MAX_RUNTIME_SECONDS", 600
            ),
            max_consecutive_failed_cycles=_int(
                "HERMES_TITAN_SCHEDULER_MAX_CONSECUTIVE_FAILURES", 3
            ),
            daily_cycle_budget=_int("HERMES_TITAN_SCHEDULER_DAILY_CYCLE_BUDGET", 96),
            daily_external_provider_budget_micros=_optional_int(
                "HERMES_TITAN_SCHEDULER_DAILY_EXTERNAL_BUDGET_MICROS"
            ),
            cooldown_after_pressure_seconds=_int(
                "HERMES_TITAN_SCHEDULER_COOLDOWN_SECONDS", 900
            ),
        )


@dataclass(frozen=True, slots=True)
class WakeProposal:
    """A model-proposed next-cycle plan.

    Untrusted input: every field is advisory only until
    :func:`clamp_wake_proposal` validates and, where necessary, overrides it.
    A model may only *propose* timing/mode/model -- never set it directly.
    """

    next_wake_interval_seconds: int
    reason: str
    preferred_mode: str  # "parked" | "city" | "highway"
    preferred_model_alias: Optional[str] = None
    expected_cost_micros: int = 0
    resource_class: str = "low"


@dataclass(frozen=True, slots=True)
class ClampedWakeDecision:
    next_wake_interval_seconds: int
    clamped: bool
    clamp_reasons: Tuple[str, ...]
    accepted_mode: str
    accepted_model_alias: Optional[str]


_KNOWN_MODES = ("parked", "city", "highway")


def clamp_wake_proposal(
    proposal: WakeProposal, policy: SchedulerPolicy
) -> ClampedWakeDecision:
    """Deterministically validate and clamp a model's wake proposal.

    Never raises on a malformed proposal (a model produced bad output is an
    expected, recordable outcome, not an exceptional one) -- it always
    returns a safe, policy-compliant decision instead.
    """
    reasons = []
    interval = proposal.next_wake_interval_seconds
    if interval < policy.min_wake_interval_seconds:
        reasons.append("below_minimum_wake_interval")
        interval = policy.min_wake_interval_seconds
    if interval > policy.max_wake_interval_seconds:
        reasons.append("above_maximum_wake_interval")
        interval = policy.max_wake_interval_seconds

    mode = proposal.preferred_mode
    if mode not in _KNOWN_MODES:
        reasons.append("unknown_preferred_mode_defaulted_to_parked")
        mode = "parked"

    return ClampedWakeDecision(
        next_wake_interval_seconds=interval,
        clamped=bool(reasons),
        clamp_reasons=tuple(reasons),
        accepted_mode=mode,
        accepted_model_alias=(
            proposal.preferred_model_alias if mode != "parked" else None
        ),
    )


def should_wake(
    *,
    now: int,
    state: "SchedulerState",
    triggers: Tuple[WakeTrigger, ...],
    policy: SchedulerPolicy,
    thermal_or_load_pressure: bool = False,
    hermes_paused: bool = False,
    queue_empty: bool = True,
    last_reflection_repetitive: bool = False,
) -> Tuple[bool, str]:
    """The single, deterministic wake/parked decision point.

    Hard-blocking conditions (Hermes paused, too many consecutive failures,
    thermal/load pressure or an active cooldown, daily cycle budget
    exhausted) always force parked regardless of any trigger -- these are
    circuit-breaker-like safety rails, not preferences a trigger can
    override. Below that, any real trigger forces a wake; absent a trigger,
    a purely time-based wake additionally requires a non-empty queue and a
    non-repetitive prior reflection.
    """
    if hermes_paused:
        return False, ParkedReason.HERMES_PAUSED.value
    if state.consecutive_failed_cycles >= policy.max_consecutive_failed_cycles:
        return False, ParkedReason.TOO_MANY_CONSECUTIVE_FAILURES.value
    if thermal_or_load_pressure or (
        state.cooldown_until is not None and now < state.cooldown_until
    ):
        return False, ParkedReason.THERMAL_OR_LOAD_PRESSURE.value
    if state.daily_cycle_count >= policy.daily_cycle_budget:
        return False, ParkedReason.DAILY_BUDGET_EXHAUSTED.value

    if triggers:
        return True, triggers[0].value

    if now < state.next_wake_at:
        return False, ParkedReason.NOT_YET_DUE.value
    if queue_empty:
        return False, ParkedReason.EMPTY_QUEUE.value
    if last_reflection_repetitive:
        return False, ParkedReason.REPETITIVE_REFLECTION.value
    return True, WakeTrigger.SCHEDULED_REFLECTION_DUE.value


@dataclass(slots=True)
class SchedulerState:
    """Persistent, crash-recoverable scheduler state.

    Reloaded from disk on every process start (this scheduler is a one-shot
    process per cycle, never a permanently resident loop) -- state that
    only lived in memory would reset every cycle and make the daily budgets
    and consecutive-failure guard meaningless.
    """

    next_wake_at: int
    consecutive_failed_cycles: int = 0
    daily_cycle_count: int = 0
    daily_external_spend_micros: int = 0
    day_epoch: Optional[int] = None
    cooldown_until: Optional[int] = None
    last_reflection_repetitive: bool = False
    last_reflection_digest: Optional[str] = None
    schema_version: int = SCHEDULER_STATE_SCHEMA_VERSION

    def rolled_to_day(self, now: int) -> "SchedulerState":
        day = now // 86_400
        if self.day_epoch == day:
            return self
        return SchedulerState(
            next_wake_at=self.next_wake_at,
            consecutive_failed_cycles=self.consecutive_failed_cycles,
            daily_cycle_count=0,
            daily_external_spend_micros=0,
            day_epoch=day,
            cooldown_until=self.cooldown_until,
            last_reflection_repetitive=self.last_reflection_repetitive,
            last_reflection_digest=self.last_reflection_digest,
        )


def record_cycle_outcome(
    state: SchedulerState,
    *,
    now: int,
    succeeded: bool,
    clamped_decision: ClampedWakeDecision,
    policy: SchedulerPolicy,
    external_spend_micros: int = 0,
    pressure_detected: bool = False,
    reflection_digest: Optional[str] = None,
) -> SchedulerState:
    """Fold one completed cycle's outcome into scheduler state.

    A failed cycle increments the consecutive-failure counter (which
    ``should_wake`` uses to force parked once ``max_consecutive_failed_cycles``
    is reached); a succeeded cycle resets it. Repetition is detected by
    comparing ``reflection_digest`` against the prior cycle's -- purely a
    content-addressed comparison, never by inspecting the reflection's
    actual (potentially sensitive) content.
    """
    rolled = state.rolled_to_day(now)
    repetitive = (
        reflection_digest is not None
        and rolled.last_reflection_digest is not None
        and reflection_digest == rolled.last_reflection_digest
    )
    return SchedulerState(
        next_wake_at=now + clamped_decision.next_wake_interval_seconds,
        consecutive_failed_cycles=(
            0 if succeeded else rolled.consecutive_failed_cycles + 1
        ),
        daily_cycle_count=rolled.daily_cycle_count + 1,
        daily_external_spend_micros=rolled.daily_external_spend_micros
        + max(0, external_spend_micros),
        day_epoch=rolled.day_epoch,
        cooldown_until=(now + policy.cooldown_after_pressure_seconds)
        if pressure_detected
        else None,
        last_reflection_repetitive=repetitive,
        last_reflection_digest=reflection_digest or rolled.last_reflection_digest,
    )


class SchedulerStateStore:
    """Atomic, crash-safe persistence for :class:`SchedulerState`.

    Writes go to a temp file in the same directory and are moved into place
    with ``os.replace`` (atomic on POSIX and Windows), so a crash mid-write
    never leaves a torn/partial state file for the next cycle to load.
    """

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if not path.is_absolute():
            raise SchedulerStateError("scheduler state path must be absolute")
        self._path = path

    def load(self, *, default_next_wake_at: int) -> SchedulerState:
        if not self._path.exists():
            return SchedulerState(next_wake_at=default_next_wake_at)
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return SchedulerState(next_wake_at=default_next_wake_at)
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != SCHEDULER_STATE_SCHEMA_VERSION
        ):
            return SchedulerState(next_wake_at=default_next_wake_at)
        try:
            return SchedulerState(**{
                k: v for k, v in raw.items() if k != "schema_version"
            })
        except TypeError:
            return SchedulerState(next_wake_at=default_next_wake_at)

    def save(self, state: SchedulerState) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        tmp_path.write_text(json.dumps(asdict(state), sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, self._path)


def now_epoch() -> int:
    return int(time.time())
