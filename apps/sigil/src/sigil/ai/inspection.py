"""Bounded, credential-free inspection of governed AI state."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .artifact_store import AnalysisArtifactStoreError, DurableAnalysisArtifactStore
from .finbert import FinBERTConfig, GovernedSentimentArtifact, LocalFinBERTProvider
from .gemma import LocalGemmaConfig, LocalGemmaProvider
from .ledger import AIEvidenceLedgerError, DurableAIEvidenceLedger

INSPECTION_SCHEMA_VERSION = 1
MAX_INSPECTION_LIMIT = 50
_ARTIFACT_ID = re.compile(r"^analysis-artifact-[0-9a-f]{64}$")


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


def ai_status(environment: dict[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if environment is None else environment)
    root = _state_root(source)
    evidence, evidence_health, evidence_tail = _read_evidence(root)
    artifacts, artifact_health, artifact_tail = _read_artifacts(root)
    config, gemma_health = _config(source)
    finbert_config, finbert_provider, finbert_health = _finbert_config(source)
    failures = [item for item in evidence if getattr(item, "failure_classification", None)]
    providers = (1 if config.model_id else 0) + (1 if finbert_config.enabled else 0)
    available = sum(health == "healthy" for health in (gemma_health, finbert_health))
    latest = artifacts[-1] if artifacts else None
    latest_sentiment = next(
        (item for item in reversed(artifacts) if isinstance(item, GovernedSentimentArtifact)),
        None,
    )
    last_failure = failures[-1] if failures else None
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
        "last_successful_analysis_at": None if latest is None else latest.created_at,
        "latest_analysis_summary": None
        if latest is None
        else str(
            getattr(
                latest.structured_payload,
                "summary",
                getattr(latest.structured_payload, "label", "unavailable"),
            )
        ),
        "last_failure_at": None if last_failure is None else last_failure.ended_at,
        "last_failure_classification": None
        if last_failure is None
        else last_failure.failure_classification,
        "evidence_ledger_health": evidence_health,
        "artifact_store_health": artifact_health,
        "evidence_record_count": len(evidence),
        "artifact_count": len(artifacts),
        "recoverable_tail_detected": evidence_tail or artifact_tail,
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
    if isinstance(match, GovernedSentimentArtifact):
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
            "routing_evidence_id": match.routing_evidence_id,
            "invocation_evidence_id": match.invocation_evidence_id,
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
