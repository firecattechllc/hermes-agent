from __future__ import annotations

import pytest

from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_upstreams import CircuitBreaker
from hermes_cli.prime.titan_runtime_modes import (
    Complexity,
    RuntimeMode,
    classify_task,
    select_route,
)


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


# ── classify_task ────────────────────────────────────────────────────────────


def test_classify_task_low_complexity_short_context() -> None:
    classification = classify_task(task_type="summary", context_length_tokens=200)
    assert classification.complexity == Complexity.LOW


def test_classify_task_high_complexity_from_context_length() -> None:
    classification = classify_task(task_type="summary", context_length_tokens=20_000)
    assert classification.complexity == Complexity.HIGH


def test_classify_task_high_complexity_from_task_type() -> None:
    classification = classify_task(task_type="coding", context_length_tokens=100)
    assert classification.complexity == Complexity.HIGH


def test_classify_task_respects_explicit_complexity_hint() -> None:
    classification = classify_task(
        task_type="summary",
        context_length_tokens=50_000,
        complexity_hint=Complexity.LOW,
    )
    assert classification.complexity == Complexity.LOW


# ── city-mode local routing ──────────────────────────────────────────────────


def test_city_mode_routes_simple_task_to_titan_ollama() -> None:
    config = _config()
    classification = classify_task(task_type="summary", context_length_tokens=300)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.CITY
    assert selection.provider == "titan_ollama"
    assert selection.model_alias == "lightweight"
    assert selection.escalated is False


def test_city_mode_uses_embedding_alias_for_embedding_tasks() -> None:
    config = _config()
    classification = classify_task(task_type="embedding", context_length_tokens=100)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.CITY
    assert selection.model_alias == "embedding"


# ── highway-mode escalation ──────────────────────────────────────────────────


def test_highway_mode_escalates_for_high_complexity_task() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.HIGHWAY
    assert selection.provider == "freellmapi"
    assert selection.escalated is True
    assert "highway_justified:high_complexity" == selection.reason
    assert selection.fallback_chain == ("titan_ollama/hermes-llama3.2:3b-64k",)


def test_highway_mode_escalates_for_context_limit_exceeded() -> None:
    config = _config()
    # complexity_hint pins complexity below HIGH so this isolates the
    # context-length justification specifically, rather than also tripping
    # classify_task's own "very long context implies high complexity"
    # inference (which would independently justify escalation too).
    classification = classify_task(
        task_type="summary",
        context_length_tokens=50_000,
        complexity_hint=Complexity.MEDIUM,
    )
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.HIGHWAY
    assert selection.reason == "highway_justified:context_limit_exceeded"


def test_highway_mode_escalates_for_recent_local_failures() -> None:
    config = _config()
    classification = classify_task(
        task_type="summary",
        context_length_tokens=200,
        recent_local_failures=1,
    )
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.HIGHWAY
    assert selection.reason == "highway_justified:recent_local_failures"


def test_highway_mode_escalates_for_prior_degraded() -> None:
    config = _config()
    classification = classify_task(
        task_type="summary",
        context_length_tokens=200,
        prior_mode_degraded=True,
    )
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.HIGHWAY
    assert selection.reason == "highway_justified:prior_mode_degraded"


def test_does_not_escalate_merely_because_task_is_simple_and_local_unavailable_is_false() -> (
    None
):
    # A low-complexity, short-context, locally-available task must stay in
    # city mode -- there is no "escalate because a bigger model exists"
    # pathway to trigger accidentally.
    config = _config()
    classification = classify_task(task_type="summary", context_length_tokens=100)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.CITY
    assert selection.escalated is False


# ── privacy never escalates ──────────────────────────────────────────────────


def test_privacy_sensitive_never_escalates_even_when_otherwise_justified() -> None:
    config = _config()
    classification = classify_task(
        task_type="coding",
        context_length_tokens=50_000,
        privacy_sensitive=True,
    )
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.CITY
    assert selection.reason == "privacy_restricted"


# ── denied provider precedence ───────────────────────────────────────────────


def test_denied_freellmapi_provider_forces_fallback_to_city() -> None:
    config = _config(HERMES_OMNIROUTE_DENIED_PROVIDERS="freellmapi")
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.CITY
    assert "provider_denied" in selection.reason


def test_denied_titan_ollama_with_no_highway_justification_parks() -> None:
    config = _config(
        HERMES_OMNIROUTE_DENIED_PROVIDERS="titan_ollama",
        HERMES_OMNIROUTE_PROVIDER_PRIORITY="freellmapi",
    )
    classification = classify_task(task_type="summary", context_length_tokens=100)
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.PARKED
    assert selection.provider is None


# ── budget exhaustion ─────────────────────────────────────────────────────────


def test_daily_external_budget_exhausted_blocks_highway() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(
        classification, config=config, external_budget_remaining_micros=0
    )
    assert selection.mode == RuntimeMode.CITY
    assert selection.reason == "daily_external_budget_exhausted"


def test_unlimited_budget_when_none_allows_highway() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(
        classification, config=config, external_budget_remaining_micros=None
    )
    assert selection.mode == RuntimeMode.HIGHWAY


# ── circuit breaker fallback ─────────────────────────────────────────────────


def test_open_freellmapi_circuit_breaker_falls_back_to_city() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    selection = select_route(classification, config=config, freellmapi_circuit=breaker)
    assert selection.mode == RuntimeMode.CITY
    assert "circuit_breaker_open" in selection.reason


def test_open_titan_ollama_circuit_breaker_with_no_highway_justification_parks() -> (
    None
):
    config = _config()
    classification = classify_task(task_type="summary", context_length_tokens=100)
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    selection = select_route(
        classification, config=config, titan_ollama_circuit=breaker
    )
    assert selection.mode == RuntimeMode.PARKED


# ── thermal / memory pressure deferral ───────────────────────────────────────


def test_thermal_pressure_forces_city_even_when_highway_justified() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(classification, config=config, thermal_pressure=True)
    assert selection.mode == RuntimeMode.CITY
    assert selection.reason == "resource_pressure"


def test_memory_pressure_forces_city_even_when_highway_justified() -> None:
    config = _config()
    classification = classify_task(task_type="coding", context_length_tokens=200)
    selection = select_route(classification, config=config, memory_pressure=True)
    assert selection.mode == RuntimeMode.CITY
    assert selection.reason == "resource_pressure"


# ── no local model available -> must escalate ───────────────────────────────


def test_no_local_model_available_justifies_escalation() -> None:
    config = _config()
    classification = classify_task(
        task_type="summary",
        context_length_tokens=100,
        local_model_available=False,
    )
    selection = select_route(classification, config=config)
    assert selection.mode == RuntimeMode.HIGHWAY
    assert selection.reason == "highway_justified:no_local_model_available"
