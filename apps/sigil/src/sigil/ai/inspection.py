"""Bounded, credential-free inspection of governed AI state."""

from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sigil.integration_registry import integration_registry_status

from .artifact_store import AnalysisArtifactStoreError, DurableAnalysisArtifactStore
from .finbert import FinBERTConfig, GovernedSentimentArtifact, LocalFinBERTProvider
from .fleet import DurableFleetStore, FleetStoreError
from .gemma import LocalGemmaConfig, LocalGemmaProvider
from .kronos import (
    GovernedForecastArtifact,
    GovernedForecastEvaluationArtifact,
    KronosConfig,
    LocalKronosProvider,
)
from .ledger import AIEvidenceLedgerError, DurableAIEvidenceLedger
from .mac_ollama import MacOllamaInspector, MacOllamaProfileConfig
from .orchestration import (
    DurableOrchestrationStore,
    GovernedOrchestrationArtifact,
    OrchestrationState,
    OrchestrationStoreError,
)
from .retrieval import (
    DurableRetrievalStore,
    EmbeddingGemmaConfig,
    GovernedRetrievalArtifact,
    LocalEmbeddingGemmaProvider,
    RetrievalStoreError,
)

INSPECTION_SCHEMA_VERSION = 1
MAX_INSPECTION_LIMIT = 50
_ARTIFACT_ID = re.compile(r"^(?:analysis|evaluation)-artifact-[0-9a-f]{64}$")


class AIInspectionValidationError(ValueError):
    """Inspection input is malformed or outside its read-only bound."""


def _bounded_limit(value: object, default: int = 10) -> int:
    if value is None:
        return default
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_INSPECTION_LIMIT
    ):
        raise AIInspectionValidationError("inspection limit must be an integer from 1 to 50")
    return value


def _state_root(environment: dict[str, str]) -> Path | None:
    value = environment.get("SIGIL_DESKTOP_STATE_DIR")
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.exists() or not path.is_dir():
        return None
    return path


def _read_evidence(root: Path | None) -> tuple[tuple[object, ...], str, bool]:
    directory = None if root is None else root / "governed-ai-evidence-v1"
    if directory is None or not (directory / "ledger.jsonl").exists():
        return (), "empty", False
    if not (directory / "ledger.lock").exists():
        return (), "corrupt", False
    try:
        ledger = DurableAIEvidenceLedger(root)
        return ledger.read_records(recover_truncated_tail=False), "healthy", False
    except AIEvidenceLedgerError as error:
        recoverable = "truncated tail" in str(error)
        return (), "recoverable_tail" if recoverable else "corrupt", recoverable


def _read_artifacts(root: Path | None) -> tuple[tuple[object, ...], str, bool]:
    directory = None if root is None else root / "governed-ai-artifacts-v1"
    if directory is None or not (directory / "artifacts.jsonl").exists():
        return (), "empty", False
    if not (directory / "artifacts.lock").exists():
        return (), "corrupt", False
    try:
        store = DurableAnalysisArtifactStore(root)
        return store.read_artifacts(recover_truncated_tail=False), "healthy", False
    except AnalysisArtifactStoreError as error:
        recoverable = "truncated tail" in str(error)
        return (), "recoverable_tail" if recoverable else "corrupt", recoverable


def _read_retrieval(
    root: Path | None,
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...], str, bool]:
    directory = None if root is None else root / "governed-ai-retrieval-v1"
    if directory is None or not (directory / "index.jsonl").exists():
        return (), (), (), "empty", False
    if not (directory / "index.lock").exists():
        return (), (), (), "corrupt", False
    try:
        sources, chunks, embeddings = DurableRetrievalStore(root).read_index(
            recover_truncated_tail=False
        )
        return sources, chunks, embeddings, "healthy", False
    except RetrievalStoreError as error:
        recoverable = "truncated tail" in str(error)
        return (), (), (), "recoverable_tail" if recoverable else "corrupt", recoverable


def _read_orchestrations(
    root: Path | None,
) -> tuple[tuple[object, ...], str, bool]:
    directory = None if root is None else root / "governed-ai-orchestration-v1"
    if directory is None or not (directory / "orchestrations.jsonl").exists():
        return (), "empty", False
    if not (directory / "orchestrations.lock").exists():
        return (), "corrupt", False
    try:
        records = DurableOrchestrationStore(root).latest_all()
        return records, "healthy", False
    except OrchestrationStoreError as error:
        recoverable = "truncated tail" in str(error)
        return (), "recoverable_tail" if recoverable else "corrupt", recoverable


def _read_fleet(root: Path | None) -> tuple[tuple[object, ...], str, bool]:
    directory = None if root is None else root / "governed-ai-fleet-v1"
    if directory is None or not (directory / "fleet-evidence.jsonl").exists():
        return (), "empty", False
    if not (directory / "fleet-evidence.lock").exists():
        return (), "corrupt", False
    try:
        return DurableFleetStore(root).read(recover_truncated_tail=False), "healthy", False
    except FleetStoreError as error:
        recoverable = "truncated tail" in str(error)
        return (), "recoverable_tail" if recoverable else "corrupt", recoverable


def _config(environment: dict[str, str]) -> tuple[LocalGemmaConfig, str]:
    try:
        config = LocalGemmaConfig.from_environment(environment)
        health = "configured_unverified" if config.enabled else "disabled"
        return config, health
    except ValueError:
        return LocalGemmaConfig(), "configuration_invalid"


def _finbert_config(
    environment: dict[str, str],
) -> tuple[FinBERTConfig, LocalFinBERTProvider | None, str]:
    try:
        config = FinBERTConfig.from_environment(environment)
        provider = LocalFinBERTProvider(config)
        health = (
            "disabled"
            if not config.enabled
            else "configured_unverified"
            if provider.identity.health.value == "healthy"
            else "unavailable"
        )
        return config, provider, health
    except ValueError:
        return FinBERTConfig(), None, "configuration_invalid"


def _embedding_config(
    environment: dict[str, str],
) -> tuple[EmbeddingGemmaConfig, LocalEmbeddingGemmaProvider | None, str]:
    try:
        config = EmbeddingGemmaConfig.from_environment(environment)
        provider = LocalEmbeddingGemmaProvider(config)
        health = (
            "disabled"
            if not config.enabled
            else "configured_unverified"
            if provider.identity.health.value == "healthy"
            else "unavailable"
        )
        return config, provider, health
    except ValueError:
        return EmbeddingGemmaConfig(), None, "configuration_invalid"


def _kronos_config(
    environment: dict[str, str],
) -> tuple[KronosConfig, LocalKronosProvider | None, str]:
    try:
        config = KronosConfig.from_environment(environment)
        provider = LocalKronosProvider(config)
        health = (
            "disabled"
            if not config.enabled
            else "configured_unverified"
            if provider.identity.health.value == "healthy"
            else "unavailable"
        )
        return config, provider, health
    except ValueError:
        return KronosConfig(), None, "configuration_invalid"


def _fleet_status(records, health: str, tail: bool, enabled: bool) -> dict[str, Any]:
    def latest(event_type: str, role: str | None = None):
        return next(
            (
                item
                for item in reversed(records)
                if item.event_type == event_type
                and (role is None or dict(item.details).get("role") == role)
            ),
            None,
        )

    def node(role: str) -> dict[str, Any] | None:
        item = latest("heartbeat", role)
        if item is None:
            return None
        details = dict(item.details)
        return {
            "node_id": item.node_id,
            "state": item.state,
            "capabilities": details.get("capabilities", "").split(",")
            if details.get("capabilities")
            else [],
            "load": int(details.get("load", "0")),
        }

    route = latest("routing_decision")
    failover = latest("failover")
    return {
        "enabled": enabled,
        "health": health if enabled else "disabled",
        "store_health": health,
        "recoverable_corruption": tail,
        "registered_node_count": len(
            {
                item.node_id
                for item in records
                if item.event_type == "node_registration" and item.node_id is not None
            }
        ),
        "healthy_node_count": len(
            {
                item.node_id
                for item in records
                if item.event_type == "heartbeat"
                and item.state == "healthy"
                and item.node_id is not None
            }
        ),
        "nodes": {role: node(role) for role in ("titan", "mac", "prime")},
        "active_tasks": sum(
            item.state in {"acknowledged", "running", "cancellation_requested"}
            for item in records[-100:]
        ),
        "queued_tasks": sum(
            item.event_type == "task_dispatch" and item.state == "not_started"
            for item in records[-100:]
        ),
        "completion_unknown_tasks": sum(
            item.state == "completion_unknown" for item in records[-100:]
        ),
        "clock_warnings": sum(
            item.state in {"clock_skew", "stale", "future"} for item in records[-100:]
        ),
        "latest_route": None
        if route is None
        else {"node_id": route.node_id, "state": route.state, "created_at": route.created_at},
        "latest_failover": None
        if failover is None
        else {"node_id": failover.node_id, "failure": failover.failure_classification},
        "recent_failures": sum(item.failure_classification is not None for item in records[-20:]),
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
    }


def ai_status(environment: dict[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if environment is None else environment)
    root = _state_root(source)
    evidence, evidence_health, evidence_tail = _read_evidence(root)
    artifacts, artifact_health, artifact_tail = _read_artifacts(root)
    sources, chunks, embeddings, retrieval_health, retrieval_tail = _read_retrieval(root)
    orchestrations, orchestration_health, orchestration_tail = _read_orchestrations(root)
    fleet_records, fleet_health, fleet_tail = _read_fleet(root)
    latest_by_orchestration = {item.orchestration_id: item for item in orchestrations}
    latest_orchestrations = tuple(latest_by_orchestration.values())
    config, gemma_health = _config(source)
    finbert_config, finbert_provider, finbert_health = _finbert_config(source)
    embedding_config, embedding_provider, embedding_health = _embedding_config(source)
    kronos_config, kronos_provider, kronos_health = _kronos_config(source)
    failures = [item for item in evidence if getattr(item, "failure_classification", None)]
    providers = (
        (1 if config.model_id else 0)
        + (1 if finbert_config.enabled else 0)
        + (1 if embedding_config.enabled else 0)
        + (1 if kronos_config.enabled else 0)
    )
    available = sum(
        health == "healthy"
        for health in (gemma_health, finbert_health, embedding_health, kronos_health)
    )
    fleet_enabled = source.get("SIGIL_AI_FLEET_ENABLED", "").lower() in {"1", "true", "yes"}
    latest = artifacts[-1] if artifacts else None
    latest_sentiment = next(
        (item for item in reversed(artifacts) if isinstance(item, GovernedSentimentArtifact)),
        None,
    )
    latest_retrieval = next(
        (item for item in reversed(artifacts) if isinstance(item, GovernedRetrievalArtifact)),
        None,
    )
    latest_forecast = next(
        (item for item in reversed(artifacts) if isinstance(item, GovernedForecastArtifact)),
        None,
    )
    latest_evaluation = next(
        (
            item
            for item in reversed(artifacts)
            if isinstance(item, GovernedForecastEvaluationArtifact)
        ),
        None,
    )
    latest_orchestration_artifact = next(
        (item for item in reversed(artifacts) if isinstance(item, GovernedOrchestrationArtifact)),
        None,
    )
    if isinstance(latest, GovernedOrchestrationArtifact):
        latest_summary = f"{len(latest.completed_step_ids)} orchestration steps completed"
    elif isinstance(latest, GovernedForecastEvaluationArtifact):
        latest_summary = f"forecast evaluation MAE {latest.mae:.6g}"
    elif isinstance(latest, GovernedForecastArtifact):
        latest_summary = f"{latest.symbol} {latest.forecast_horizon}-point forecast"
    elif isinstance(latest, GovernedRetrievalArtifact):
        latest_summary = f"{len(latest.results)} retrieval results"
    elif isinstance(latest, GovernedSentimentArtifact):
        latest_summary = latest.structured_payload.label.value
    else:
        latest_summary = None if latest is None else latest.structured_payload.summary
    last_failure = failures[-1] if failures else None
    try:
        mac_ollama_config = MacOllamaProfileConfig.from_environment(source)
        mac_ollama = MacOllamaInspector(mac_ollama_config).status()
    except ValueError:
        mac_ollama = {
            "enabled": False,
            "health": "configuration_invalid",
            "paper_only": True,
            "broker_submission": False,
            "execution_authorized": False,
            "approval_authority": False,
        }
    return {
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "enabled": source.get("SIGIL_AI_SERVICE_ENABLED", "").lower() in {"1", "true", "yes"},
        "service_state": "enabled"
        if source.get("SIGIL_AI_SERVICE_ENABLED", "").lower() in {"1", "true", "yes"}
        else "disabled",
        "registry_revision": "unconfigured" if not config.model_id else "configured-local-gemma",
        "configured_provider_count": providers,
        "available_provider_count": available,
        "configured_model_count": providers,
        "available_model_count": available,
        "preferred_model_family": "gemma",
        "configured_local_gemma_model": config.model_id,
        "local_gemma_health": gemma_health,
        "mac_ollama": mac_ollama,
        "last_successful_analysis_at": None if latest is None else latest.created_at,
        "latest_analysis_summary": latest_summary,
        "last_failure_at": None if last_failure is None else last_failure.ended_at,
        "last_failure_classification": None
        if last_failure is None
        else last_failure.failure_classification,
        "evidence_ledger_health": evidence_health,
        "artifact_store_health": artifact_health,
        "evidence_record_count": len(evidence),
        "artifact_count": len(artifacts),
        "recoverable_tail_detected": evidence_tail
        or artifact_tail
        or retrieval_tail
        or orchestration_tail,
        "finbert": {
            "enabled": finbert_config.enabled,
            "model_id": finbert_config.model_id,
            "model_version": finbert_config.model_version,
            "health": finbert_health,
            "device_class": finbert_config.device,
            "last_successful_sentiment_at": next(
                (
                    item.created_at
                    for item in reversed(artifacts)
                    if isinstance(item, GovernedSentimentArtifact)
                ),
                None,
            ),
            "latest_sentiment": None
            if latest_sentiment is None
            else {
                "label": latest_sentiment.structured_payload.label.value,
                "confidence": latest_sentiment.confidence,
                "source_identity": latest_sentiment.structured_payload.source_identity,
                "freshness": latest_sentiment.stale_after,
                "limitations": list(latest_sentiment.limitations[:5]),
            },
            "sentiment_artifact_count": sum(
                isinstance(item, GovernedSentimentArtifact) for item in artifacts
            ),
            "available": bool(finbert_provider and finbert_health == "healthy"),
        },
        "embeddinggemma": {
            "enabled": embedding_config.enabled,
            "model_id": embedding_config.model_id,
            "model_version": embedding_config.model_version,
            "health": embedding_health,
            "available": bool(embedding_provider and embedding_health == "healthy"),
            "vector_dimension": embedding_config.vector_dimension,
            "corpus_count": len({item.corpus_id for item in sources}),
            "source_count": len(sources),
            "chunk_count": len(chunks),
            "embedding_count": len(embeddings),
            "vector_store_health": retrieval_health,
            "recoverable_corruption": retrieval_tail,
            "last_successful_indexing": None if not embeddings else embeddings[-1].created_at,
            "last_successful_retrieval": None
            if latest_retrieval is None
            else latest_retrieval.created_at,
            "latest_retrieval": None
            if latest_retrieval is None
            else {
                "result_count": len(latest_retrieval.results),
                "freshness": sorted({item.freshness_state for item in latest_retrieval.results}),
                "limitations": list(latest_retrieval.limitations[:5]),
            },
            "last_failure_classification": None
            if last_failure is None
            else last_failure.failure_classification,
        },
        "kronos": {
            "enabled": kronos_config.enabled,
            "model_id": kronos_config.model_id,
            "model_version": kronos_config.model_version,
            "tokenizer_id": kronos_config.tokenizer_id,
            "tokenizer_version": kronos_config.tokenizer_version,
            "health": kronos_health,
            "available": bool(kronos_provider and kronos_health == "healthy"),
            "device_class": kronos_config.device,
            "supported_intervals": list(kronos_config.allowed_intervals),
            "minimum_sequence_length": kronos_config.min_sequence_length,
            "maximum_sequence_length": kronos_config.max_sequence_length,
            "maximum_horizon": kronos_config.max_horizon,
            "forecast_artifact_count": sum(
                isinstance(item, GovernedForecastArtifact) for item in artifacts
            ),
            "evaluation_artifact_count": sum(
                isinstance(item, GovernedForecastEvaluationArtifact) for item in artifacts
            ),
            "last_successful_forecast": None
            if latest_forecast is None
            else {
                "symbol": latest_forecast.symbol,
                "interval": latest_forecast.interval,
                "forecast_horizon": latest_forecast.forecast_horizon,
                "created_at": latest_forecast.created_at,
                "uncertainty_available": latest_forecast.structured_payload.uncertainty_mode.value
                != "none",
                "freshness": latest_forecast.freshness_state,
                "limitations": list(latest_forecast.limitations[:5]),
            },
            "last_evaluation_summary": None
            if latest_evaluation is None
            else {
                "mae": latest_evaluation.mae,
                "rmse": latest_evaluation.rmse,
                "sample_count": latest_evaluation.sample_count,
                "evaluated_at": latest_evaluation.created_at,
                "limitations": list(latest_evaluation.limitations[:5]),
            },
            "last_failure_classification": None
            if last_failure is None
            else last_failure.failure_classification,
        },
        "orchestration": {
            "enabled": source.get("SIGIL_AI_ORCHESTRATION_ENABLED", "").lower()
            in {"1", "true", "yes"},
            "health": "disabled"
            if source.get("SIGIL_AI_ORCHESTRATION_ENABLED", "").lower() not in {"1", "true", "yes"}
            else orchestration_health,
            "store_health": orchestration_health,
            "recoverable_corruption": orchestration_tail,
            "active_count": sum(
                item.state in {OrchestrationState.PLANNED, OrchestrationState.RUNNING}
                for item in latest_orchestrations
            ),
            "completed_count": sum(
                item.state == OrchestrationState.COMPLETED for item in latest_orchestrations
            ),
            "partial_count": sum(
                item.state == OrchestrationState.PARTIAL for item in latest_orchestrations
            ),
            "failed_count": sum(
                item.state in {OrchestrationState.FAILED, OrchestrationState.CANCELLED}
                for item in latest_orchestrations
            ),
            "paused_count": sum(
                item.state == OrchestrationState.PAUSED for item in latest_orchestrations
            ),
            "pending_human_interactions": sum(
                item.response is None
                for record in latest_orchestrations
                for item in record.interactions
            ),
            "buzz": "available"
            if source.get("SIGIL_AI_BUZZ_ENABLED", "").lower() in {"1", "true", "yes"}
            else "unavailable",
            "atlas": "available"
            if source.get("SIGIL_AI_ATLAS_ENABLED", "").lower() in {"1", "true", "yes"}
            else "unavailable",
            "openworker": "available"
            if source.get("SIGIL_AI_OPENWORKER_ENABLED", "").lower() in {"1", "true", "yes"}
            else "unavailable",
            "worker_count": 1
            if source.get("SIGIL_AI_OPENWORKER_ENABLED", "").lower() in {"1", "true", "yes"}
            else 0,
            "latest": None
            if not latest_orchestrations
            else {
                "orchestration_id": latest_orchestrations[-1].orchestration_id,
                "plan_id": latest_orchestrations[-1].plan.plan_id,
                "state": latest_orchestrations[-1].state.value,
                "capabilities": [
                    item.capability.value for item in latest_orchestrations[-1].plan.steps
                ],
                "completed_steps": sum(
                    item.status.value == "succeeded"
                    for item in latest_orchestrations[-1].step_results
                ),
                "failed_steps": sum(
                    item.status.value == "failed" for item in latest_orchestrations[-1].step_results
                ),
                "artifact_id": latest_orchestrations[-1].final_artifact_id,
                "evidence_identities": [
                    item.evidence_id for item in latest_orchestrations[-1].evidence[-10:]
                ],
                "failure_classification": latest_orchestrations[-1].failure_classification,
                "limitations": [
                    item
                    for result in latest_orchestrations[-1].step_results
                    for item in result.limitations
                ][:5],
                "updated_at": latest_orchestrations[-1].updated_at,
            },
            "latest_artifact": None
            if latest_orchestration_artifact is None
            else latest_orchestration_artifact.artifact_id,
            "paper_only": True,
            "execution_authorized": False,
            "broker_submission": False,
        },
        "fleet": _fleet_status(fleet_records, fleet_health, fleet_tail, fleet_enabled),
        "integration_registry": integration_registry_status(source),
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "approval_authority": False,
        "secrets_exposed": False,
    }


def ai_registry_status(environment: dict[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if environment is None else environment)
    config, health = _config(source)
    models = []
    if config.model_id is not None:
        models.append((LocalGemmaProvider(config).registration(), health))
    finbert_config, finbert_provider, finbert_health = _finbert_config(source)
    if finbert_config.enabled and finbert_provider is not None:
        models.append((finbert_provider.registration(), finbert_health))
    embedding_config, embedding_provider, embedding_health = _embedding_config(source)
    if embedding_config.enabled and embedding_provider is not None:
        models.append((embedding_provider.registration(), embedding_health))
    kronos_config, kronos_provider, kronos_health = _kronos_config(source)
    if kronos_config.enabled and kronos_provider is not None:
        models.append((kronos_provider.registration(), kronos_health))
    return {
        "schema_version": 1,
        "entries": [
            {
                "provider_id": model.provider_id,
                "model_id": model.model_id,
                "model_family": model.family,
                "model_version": model.version,
                "execution_location": model.execution_location.value,
                "declared_capabilities": sorted(item.value for item in model.capabilities),
                "health_state": model_health,
                "enabled_state": model.enabled,
                "trust_tier": model.trust_tier.name.lower(),
                "privacy_tier": model.privacy_tier.name.lower(),
                "cost_class": model.cost_class.name.lower(),
                "allowed_responsibilities": sorted(
                    item.value for item in model.allowed_responsibilities
                ),
                "prohibited_responsibilities": sorted(
                    item.value for item in model.prohibited_responsibilities
                ),
            }
            for model, model_health in models[:MAX_INSPECTION_LIMIT]
        ],
        "bounded": True,
    }


def _artifact_summary(artifact) -> dict[str, Any]:
    if isinstance(artifact, GovernedOrchestrationArtifact):
        return {
            "artifact_id": artifact.artifact_id,
            "orchestration_id": artifact.orchestration_id,
            "task_correlation_id": artifact.task_correlation_id,
            "plan_id": artifact.plan_id,
            "capability": artifact.capability.value,
            "responsibility": artifact.responsibility.value,
            "created_at": artifact.created_at,
            "completed_step_count": len(artifact.completed_step_ids),
            "failed_step_count": len(artifact.failed_step_ids),
            "skipped_step_count": len(artifact.skipped_step_ids),
            "evidence_identities": list(artifact.evidence_identities[:20]),
            "limitations": list(artifact.limitations[:5]),
            "paper_only": True,
            "execution_authorized": False,
            "broker_submission": False,
        }
    if isinstance(artifact, GovernedForecastEvaluationArtifact):
        return {
            "artifact_id": artifact.artifact_id,
            "request_id": artifact.request_id,
            "task_correlation_id": artifact.task_correlation_id,
            "capability": artifact.capability.value,
            "responsibility": artifact.responsibility.value,
            "created_at": artifact.created_at,
            "forecast_evaluation": {
                "forecast_artifact_id": artifact.forecast_artifact_id,
                "observed_series_id": artifact.observed_series_id,
                "mae": artifact.mae,
                "rmse": artifact.rmse,
                "sample_count": artifact.sample_count,
            },
            "limitations": list(artifact.limitations[:5]),
            "paper_only": True,
            "execution_authorized": False,
            "broker_submission": False,
        }
    summary = {
        "artifact_id": artifact.artifact_id,
        "request_id": artifact.request_id,
        "task_correlation_id": artifact.task_correlation_id,
        "provider_id": artifact.provider_id,
        "model_id": artifact.model_id,
        "capability": artifact.capability.value,
        "responsibility": artifact.responsibility.value,
        "created_at": artifact.created_at,
        "confidence": artifact.confidence,
        "limitations": list(artifact.limitations[:5]),
        "stale_after": artifact.stale_after,
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
    }
    if isinstance(artifact, GovernedSentimentArtifact):
        summary["sentiment"] = {
            "label": artifact.structured_payload.label.value,
            "positive_score": artifact.structured_payload.positive_score,
            "neutral_score": artifact.structured_payload.neutral_score,
            "negative_score": artifact.structured_payload.negative_score,
            "source_identity": artifact.structured_payload.source_identity,
            "freshness": artifact.stale_after,
        }
    if isinstance(artifact, GovernedRetrievalArtifact):
        summary["retrieval"] = {
            "result_count": len(artifact.results),
            "corpus_ids": list(artifact.corpus_ids),
            "freshness": sorted({item.freshness_state for item in artifact.results}),
        }
    if isinstance(artifact, GovernedForecastArtifact):
        summary["forecast"] = {
            "symbol": artifact.symbol,
            "interval": artifact.interval,
            "forecast_horizon": artifact.forecast_horizon,
            "uncertainty_mode": artifact.structured_payload.uncertainty_mode.value,
            "freshness": artifact.freshness_state,
        }
    return summary


def ai_recent_artifacts(
    payload: object, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    if payload is not None and not isinstance(payload, dict):
        raise AIInspectionValidationError("artifact inspection payload must be an object")
    limit = _bounded_limit(None if payload is None else payload.get("limit"))
    artifacts, health, tail = _read_artifacts(
        _state_root(dict(os.environ if environment is None else environment))
    )
    return {
        "schema_version": 1,
        "health": health,
        "recoverable_tail_detected": tail,
        "artifacts": [_artifact_summary(item) for item in artifacts[-limit:]],
    }


def ai_artifact_status(environment: dict[str, str] | None = None) -> dict[str, Any]:
    artifacts, health, tail = _read_artifacts(
        _state_root(dict(os.environ if environment is None else environment))
    )
    return {
        "schema_version": 1,
        "health": health,
        "artifact_count": len(artifacts),
        "recoverable_tail_detected": tail,
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
    }


def ai_artifact_get(payload: object, environment: dict[str, str] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"artifact_id"}:
        raise AIInspectionValidationError("exact artifact identity is required")
    artifact_id = payload["artifact_id"]
    if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise AIInspectionValidationError("artifact identity is malformed")
    artifacts, health, _ = _read_artifacts(
        _state_root(dict(os.environ if environment is None else environment))
    )
    match = next((item for item in artifacts if item.artifact_id == artifact_id), None)
    if match is None:
        return {"schema_version": 1, "found": False, "artifact": None, "health": health}
    if isinstance(match, GovernedOrchestrationArtifact):
        structured_payload = {
            "plan_id": match.plan_id,
            "completed_step_ids": list(match.completed_step_ids),
            "failed_step_ids": list(match.failed_step_ids),
            "skipped_step_ids": list(match.skipped_step_ids),
            "evidence_identities": list(match.evidence_identities),
            "retrieval_artifact_ids": list(match.retrieval_artifact_ids),
            "sentiment_artifact_ids": list(match.sentiment_artifact_ids),
            "forecast_artifact_ids": list(match.forecast_artifact_ids),
            "synthesis_artifact_id": match.synthesis_artifact_id,
            "findings": list(match.findings),
            "risks": list(match.risks),
            "disagreements": list(match.disagreements),
            "missing_evidence": list(match.missing_evidence),
            "limitations": list(match.limitations),
            "confidence": match.confidence,
            "freshness": list(match.freshness),
        }
    elif isinstance(match, GovernedForecastEvaluationArtifact):
        structured_payload = {
            "forecast_artifact_id": match.forecast_artifact_id,
            "observed_series_id": match.observed_series_id,
            "observed_series_digest": match.observed_series_digest,
            "evaluation_start_at": match.evaluation_start_at,
            "evaluation_end_at": match.evaluation_end_at,
            "mae": match.mae,
            "rmse": match.rmse,
            "mape": match.mape,
            "directional_accuracy": match.directional_accuracy,
            "interval_coverage": match.interval_coverage,
            "sample_count": match.sample_count,
            "horizon_metrics": [
                {
                    "horizon_index": item.horizon_index,
                    "mae": item.mae,
                    "rmse": item.rmse,
                    "directional_accuracy": item.directional_accuracy,
                    "interval_coverage": item.interval_coverage,
                    "sample_count": item.sample_count,
                }
                for item in match.horizon_metrics
            ],
            "limitations": list(match.limitations),
        }
    elif isinstance(match, GovernedForecastArtifact):
        structured_payload = {
            "series_id": match.series_id,
            "series_digest": match.series_digest,
            "symbol": match.symbol,
            "interval": match.interval,
            "forecast_horizon": match.forecast_horizon,
            "forecast_points": [asdict(item) for item in match.structured_payload.forecast_points],
            "uncertainty_mode": match.structured_payload.uncertainty_mode.value,
            "calibration": match.structured_payload.calibration,
            "freshness": match.freshness_state,
            "limitations": list(match.limitations),
        }
    elif isinstance(match, GovernedSentimentArtifact):
        structured_payload = {
            "label": match.structured_payload.label.value,
            "positive_score": match.structured_payload.positive_score,
            "neutral_score": match.structured_payload.neutral_score,
            "negative_score": match.structured_payload.negative_score,
            "confidence": match.structured_payload.confidence,
            "source_identity": match.structured_payload.source_identity,
            "source_digest": match.structured_payload.source_digest,
            "analyzed_at": match.structured_payload.analyzed_at,
            "limitations": list(match.structured_payload.limitations),
        }
    elif isinstance(match, GovernedRetrievalArtifact):
        structured_payload = {
            "corpus_ids": list(match.corpus_ids),
            "result_count": len(match.results),
            "results": [
                {
                    "rank": item.rank,
                    "score": item.score,
                    "source_id": item.source_id,
                    "source_identity": item.source_identity,
                    "source_type": item.source_type.value,
                    "source_digest": item.source_digest,
                    "chunk_id": item.chunk_id,
                    "chunk_digest": item.chunk_digest,
                    "observed_at": item.observed_at,
                    "freshness_state": item.freshness_state,
                    "privacy_classification": item.privacy_classification.name.lower(),
                    "trust_classification": item.trust_classification.name.lower(),
                    "excerpt": item.excerpt,
                    "evidence_references": list(item.evidence_references),
                }
                for item in match.results
            ],
            "limitations": list(match.limitations),
        }
    else:
        structured_payload = {
            "summary": match.structured_payload.summary,
            "findings": list(match.structured_payload.findings),
            "risks": list(match.structured_payload.risks),
            "evidence_references": list(match.structured_payload.evidence_references),
            "limitations": list(match.structured_payload.limitations),
            "confidence": match.structured_payload.confidence,
        }
    return {
        "schema_version": 1,
        "found": True,
        "health": health,
        "artifact": {
            **_artifact_summary(match),
            "structured_payload": structured_payload,
            "routing_evidence_id": getattr(match, "routing_evidence_id", None),
            "invocation_evidence_id": getattr(match, "invocation_evidence_id", None),
        },
    }


def ai_evidence_status(
    payload: object, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    if payload is not None and not isinstance(payload, dict):
        raise AIInspectionValidationError("evidence inspection payload must be an object")
    limit = _bounded_limit(None if payload is None else payload.get("limit"))
    records, health, tail = _read_evidence(
        _state_root(dict(os.environ if environment is None else environment))
    )
    summaries = [
        {
            "evidence_id": item.evidence_identity,
            "record_type": item.record_type.value,
            "request_id": item.request_id,
            "task_correlation_id": item.task_correlation_id,
            "provider_id": item.provider_id,
            "model_id": item.model_id,
            "capability": item.capability.value,
            "execution_location": None
            if item.execution_location is None
            else item.execution_location.value,
            "success": item.succeeded,
            "failure_classification": item.failure_classification,
            "fallback_used": item.fallback,
            "created_at": item.ended_at,
            "input_digest": item.input_digest,
            "output_digest": item.output_digest,
            "paper_only": item.paper_only,
            "broker_submission": item.broker_submission,
        }
        for item in records[-limit:]
    ]
    return {
        "schema_version": 1,
        "health": health,
        "recoverable_tail_detected": tail,
        "record_count": len(records),
        "records": summaries,
    }


def ai_recent_failures(
    payload: object, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    result = ai_evidence_status(payload, environment)
    failures = [item for item in result["records"] if item["failure_classification"]]
    return {
        "schema_version": 1,
        "health": result["health"],
        "failures": [
            {
                "failure_classification": item["failure_classification"],
                "request_id": item["request_id"],
                "provider_id": item["provider_id"],
                "model_id": item["model_id"],
                "capability": item["capability"],
                "stage": item["record_type"],
                "timestamp": item["created_at"],
                "message": "Governed AI operation failed safely.",
                "evidence_identity": item["evidence_id"],
                "fallback_status": item["fallback_used"],
            }
            for item in failures
        ],
    }
