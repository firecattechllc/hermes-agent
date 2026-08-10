"""Deterministic parked/city/highway task classification and route selection.

Mental model: Titan is the vehicle; Gemma/Qwen/local Ollama models are city
engines for lightweight, local, low-latency work; FreeLLMAPI (and any other
Hermes-approved remote provider reachable through OmniRoute) is the highway
engine for work too large or too hard for a local model. Parked is the
default when no useful work exists (see :mod:`hermes_cli.prime.titan_scheduler`
for the parked/wake decision itself -- this module only decides *which*
engine to use once Titan has already decided to do something).

This module never talks to a provider and never bypasses governance: every
route it selects is resolved through the existing, unmodified
:meth:`hermes_cli.prime.omniroute_config.TitanRoutingConfig.resolve_alias_detailed`
— the same allowlist/denylist/offline-only/enabled checks OmniRoute's HTTP
server itself uses — so a route this module selects is, by construction, one
OmniRoute would also accept. Escalation to highway mode is justified only by
task complexity, context length, recent local failure, prior degradation, or
local-model unavailability -- never merely because a larger model exists,
and never for privacy-sensitive work or under thermal/memory pressure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker, CircuitState

# A Titan-local model's practical context ceiling. Above this, city mode is
# not merely slower -- it is not expected to produce a usable result at all,
# which is a legitimate escalation justification (not "a bigger model
# exists").
LOCAL_CONTEXT_LIMIT_TOKENS = 8_192

HIGHWAY_COMPLEXITY_TASK_TYPES = frozenset({
    "coding",
    "complex_reasoning",
    "large_repository_analysis",
    "multi_step_planning",
})


class RuntimeMode(str, Enum):
    PARKED = "parked"
    CITY = "city"
    HIGHWAY = "highway"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LatencyRequirement(str, Enum):
    INTERACTIVE = "interactive"
    STANDARD = "standard"
    BATCH = "batch"


@dataclass(frozen=True, slots=True)
class TaskClassification:
    task_type: str
    complexity: Complexity
    context_length_tokens: int
    latency_requirement: LatencyRequirement
    privacy_sensitive: bool
    cost_ceiling_micros: Optional[int] = None
    local_model_available: bool = True
    recent_local_failures: int = 0
    prior_mode_degraded: bool = False


@dataclass(frozen=True, slots=True)
class RouteSelection:
    mode: RuntimeMode
    provider: Optional[str]
    model_alias: Optional[str]
    fallback_chain: Tuple[str, ...]
    reason: str
    resource_class: str
    escalated: bool = False


def classify_task(
    *,
    task_type: str,
    context_length_tokens: int,
    latency_requirement: LatencyRequirement = LatencyRequirement.STANDARD,
    privacy_sensitive: bool = False,
    cost_ceiling_micros: Optional[int] = None,
    local_model_available: bool = True,
    recent_local_failures: int = 0,
    prior_mode_degraded: bool = False,
    complexity_hint: Optional[Complexity] = None,
) -> TaskClassification:
    """Deterministically build a :class:`TaskClassification` from raw inputs.

    ``complexity_hint`` lets an upstream caller (e.g. Hermes admission,
    which may already know a task's declared complexity) short-circuit
    inference; without it, complexity is derived purely from
    ``context_length_tokens`` and whether ``task_type`` is a known
    inherently-heavy category -- never from anything a model proposes.
    """
    if complexity_hint is not None:
        complexity = complexity_hint
    elif (
        context_length_tokens > LOCAL_CONTEXT_LIMIT_TOKENS
        or task_type in HIGHWAY_COMPLEXITY_TASK_TYPES
    ):
        complexity = Complexity.HIGH
    elif context_length_tokens > 2_000:
        complexity = Complexity.MEDIUM
    else:
        complexity = Complexity.LOW

    return TaskClassification(
        task_type=task_type,
        complexity=complexity,
        context_length_tokens=context_length_tokens,
        latency_requirement=latency_requirement,
        privacy_sensitive=privacy_sensitive,
        cost_ceiling_micros=cost_ceiling_micros,
        local_model_available=local_model_available,
        recent_local_failures=recent_local_failures,
        prior_mode_degraded=prior_mode_degraded,
    )


def _city_alias(classification: TaskClassification) -> str:
    return "embedding" if classification.task_type == "embedding" else "lightweight"


def _highway_justification(classification: TaskClassification) -> Optional[str]:
    """Returns the specific justification for highway eligibility, or
    ``None`` if none applies. Never returns a reason of "a bigger model
    exists" -- that is not a representable justification in this function."""
    if classification.privacy_sensitive:
        return None
    if classification.complexity == Complexity.HIGH:
        return "high_complexity"
    if classification.context_length_tokens > LOCAL_CONTEXT_LIMIT_TOKENS:
        return "context_limit_exceeded"
    if classification.recent_local_failures > 0:
        return "recent_local_failures"
    if classification.prior_mode_degraded:
        return "prior_mode_degraded"
    if not classification.local_model_available:
        return "no_local_model_available"
    return None


def _resolve(config: TitanRoutingConfig, alias: str, circuit: Optional[CircuitBreaker]):
    resolution = config.resolve_alias_detailed(alias)
    if not resolution.permitted:
        return None, resolution.reason
    if circuit is not None and circuit.state == CircuitState.OPEN:
        return None, "circuit_breaker_open"
    return resolution, "ok"


def select_route(
    classification: TaskClassification,
    *,
    config: TitanRoutingConfig,
    titan_ollama_circuit: Optional[CircuitBreaker] = None,
    freellmapi_circuit: Optional[CircuitBreaker] = None,
    thermal_pressure: bool = False,
    memory_pressure: bool = False,
    external_budget_remaining_micros: Optional[int] = None,
) -> RouteSelection:
    """Select parked/city/highway plus provider/model for one classified task.

    Every governance boundary (denied providers, offline-local-only mode,
    provider enable/disable, per-request/daily budget) is enforced by
    :meth:`TitanRoutingConfig.resolve_alias_detailed` -- this function never
    duplicates or second-guesses that check, so there is exactly one place
    those rules live. ``external_budget_remaining_micros`` is a second,
    independent budget gate specific to escalation: the scheduler's own
    daily external-provider budget
    (:attr:`hermes_cli.prime.titan_scheduler.SchedulerPolicy.daily_external_provider_budget_micros`),
    which caps highway spend across a whole day of cycles, not a single
    request. ``None`` means unlimited; any non-positive value blocks
    escalation to highway and falls back to city.
    """
    resource_pressure = thermal_pressure or memory_pressure
    justification = _highway_justification(classification)
    city_alias = _city_alias(classification)
    budget_exhausted = (
        external_budget_remaining_micros is not None
        and external_budget_remaining_micros <= 0
    )
    highway_wanted = justification is not None

    if highway_wanted and resource_pressure:
        fallback_reason = "resource_pressure"
    elif highway_wanted and budget_exhausted:
        fallback_reason = "daily_external_budget_exhausted"
    elif highway_wanted:
        highway_resolution, highway_reason = _resolve(
            config, "large", freellmapi_circuit
        )
        if highway_resolution is not None:
            fallback: Tuple[str, ...] = ()
            city_resolution, _ = _resolve(config, city_alias, titan_ollama_circuit)
            if city_resolution is not None:
                fallback = (f"{city_resolution.provider_id}/{city_resolution.model}",)
            return RouteSelection(
                mode=RuntimeMode.HIGHWAY,
                provider=highway_resolution.provider_id,
                model_alias="large",
                fallback_chain=fallback,
                reason=f"highway_justified:{justification}",
                resource_class="high",
                escalated=True,
            )
        fallback_reason = f"highway_unavailable:{highway_reason}"
    elif classification.privacy_sensitive:
        fallback_reason = "privacy_restricted"
    else:
        fallback_reason = "city_sufficient"

    city_resolution, city_reason = _resolve(config, city_alias, titan_ollama_circuit)
    if city_resolution is not None:
        return RouteSelection(
            mode=RuntimeMode.CITY,
            provider=city_resolution.provider_id,
            model_alias=city_alias,
            fallback_chain=(),
            reason=fallback_reason,
            resource_class="low",
            escalated=False,
        )

    return RouteSelection(
        mode=RuntimeMode.PARKED,
        provider=None,
        model_alias=None,
        fallback_chain=(),
        reason=f"no_route_available:{city_reason}",
        resource_class="none",
        escalated=False,
    )
