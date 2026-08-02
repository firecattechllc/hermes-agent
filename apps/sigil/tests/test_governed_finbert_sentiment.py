from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai import (
    AnalysisArtifactConflictError,
    Capability,
    DurableAIEvidenceLedger,
    DurableAnalysisArtifactStore,
    FinBERTConfig,
    FinBERTValidationError,
    GovernedAnalysisService,
    GovernedModelRegistry,
    GovernedSentimentArtifact,
    GovernedSentimentRequest,
    GovernedSentimentWorkRequest,
    LocalFinBERTProvider,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    SentimentLabel,
    SentimentSourceType,
    aggregate_sentiment,
    validate_finbert_output,
)
from sigil.ai.inspection import ai_artifact_get, ai_status
from sigil.ai.registry import canonical_digest

DIGEST = "sha256:" + "a" * 64
TEXT = "Revenue and margins improved while management maintained guidance."


class FakeRuntime:
    def __init__(self, scores=None, error: Exception | None = None) -> None:
        self.scores = scores or {"positive": 0.8, "neutral": 0.15, "negative": 0.05}
        self.error = error
        self.calls = 0

    def predict(self, *, text: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.scores


def config(**values) -> FinBERTConfig:
    if "model_id" in values:
        values["model"] = values.pop("model_id")
    return FinBERTConfig(
        **{
            "enabled": True,
            "model_version": "test-v1",
            "timeout_ms": 1_000,
            "max_input_chars": 2_000,
            **values,
        }
    )


def request(**values) -> GovernedSentimentRequest:
    text = values.pop("source_text", TEXT)
    return GovernedSentimentRequest(
        **{
            "request_id": "finbert-request",
            "task_correlation_id": "finbert-task",
            "responsibility": Responsibility.NEWS_SENTIMENT,
            "input_digest": f"sha256:{canonical_digest(text)}",
            "evidence_context_digests": (DIGEST,),
            "source_type": SentimentSourceType.NEWS_EXCERPT,
            "source_identity": "news-42",
            "source_text": text,
            "language": "en",
            "requested_at": "2026-08-01T18:00:00Z",
            "timeout_ms": 1_000,
            **values,
        }
    )


def service(tmp_path: Path, runtime: FakeRuntime, *, enabled: bool = True):
    provider = LocalFinBERTProvider(config(), runtime)
    registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    return (
        GovernedAnalysisService(
            registry=registry,
            providers={provider.identity.provider_id: provider},
            evidence_ledger=ledger,
            artifact_store=store,
            enabled=enabled,
        ),
        ledger,
        store,
    )


def successful_artifact(tmp_path: Path, scores=None) -> GovernedSentimentArtifact:
    tmp_path.mkdir(parents=True, exist_ok=True)
    governed, _, _ = service(tmp_path, FakeRuntime(scores))
    response = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert response.succeeded
    assert isinstance(response.artifact, GovernedSentimentArtifact)
    return response.artifact


def test_finbert_configuration_is_disabled_local_cpu_and_bounded_by_default() -> None:
    value = FinBERTConfig.from_environment({})
    assert value.enabled is False
    assert value.device == "cpu"
    assert value.local_files_only is True
    with pytest.raises(FinBERTValidationError):
        FinBERTConfig.from_environment({"SIGIL_AI_FINBERT_LOCAL_FILES_ONLY": "false"})


def test_finbert_registration_is_specialized_and_explicitly_prohibited() -> None:
    provider = LocalFinBERTProvider(config(), FakeRuntime())
    model = provider.registration()
    assert model.family == "finbert"
    assert model.capabilities == frozenset({Capability.FINANCIAL_SENTIMENT})
    assert Responsibility.NEWS_SENTIMENT in model.allowed_responsibilities
    for responsibility in (
        Responsibility.CAPITAL_AUTHORIZATION,
        Responsibility.PROPOSAL_APPROVAL,
        Responsibility.BROKER_SUBMISSION,
        Responsibility.ORDER_EXECUTION,
        Responsibility.PORTFOLIO_MUTATION,
        Responsibility.POLICY_CHANGE,
        Responsibility.CREDENTIAL_ACCESS,
        Responsibility.UNRESTRICTED_SHELL_EXECUTION,
    ):
        assert responsibility in model.prohibited_responsibilities


@pytest.mark.parametrize(
    ("scores", "label"),
    [
        ({"positive": 8, "neutral": 1, "negative": 1}, SentimentLabel.POSITIVE),
        ({"positive": 1, "neutral": 8, "negative": 1}, SentimentLabel.NEUTRAL),
        ({"positive": 1, "neutral": 1, "negative": 8}, SentimentLabel.NEGATIVE),
    ],
)
def test_deterministic_sentiment_classification_and_persistence(
    tmp_path: Path, scores, label
) -> None:
    artifact = successful_artifact(tmp_path, scores)
    assert artifact.structured_payload.label == label
    assert artifact.confidence == pytest.approx(0.8)
    assert artifact.paper_only is True
    assert artifact.broker_submission is False
    assert artifact.execution_authorized is False
    assert artifact.portfolio_mutation is False
    assert artifact.approval_authority is False


def test_success_persists_attempt_result_and_restart_safe_artifact(tmp_path: Path) -> None:
    governed, ledger, store = service(tmp_path, FakeRuntime())
    result = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert result.succeeded
    records = ledger.read_records()
    assert len(records) == 3
    assert all(item.broker_submission is False for item in records)
    restarted = DurableAnalysisArtifactStore(tmp_path.resolve()).read_artifacts()
    assert restarted == store.read_artifacts()
    assert isinstance(restarted[0], GovernedSentimentArtifact)


def test_unavailable_provider_fails_closed_with_evidence_and_no_artifact(tmp_path: Path) -> None:
    provider = LocalFinBERTProvider(FinBERTConfig())
    registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    governed = GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=ledger,
        artifact_store=store,
        enabled=True,
    )
    result = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert result.succeeded is False
    assert result.failure_classification == "no_suitable_model"
    assert len(ledger.read_records()) == 1
    assert store.read_artifacts() == ()


def test_timeout_is_sanitized_evidenced_and_creates_no_artifact(tmp_path: Path) -> None:
    governed, ledger, store = service(tmp_path, FakeRuntime(error=TimeoutError()))
    result = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert result.failure_classification == "timeout"
    assert ledger.read_records()[-1].failure_classification == "timeout"
    assert store.read_artifacts() == ()


def test_malformed_score_distribution_is_evidenced_without_artifact(tmp_path: Path) -> None:
    governed, ledger, store = service(
        tmp_path,
        FakeRuntime({"positive": float("nan"), "neutral": 0.5, "negative": 0.5}),
    )
    result = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert result.failure_classification == "output_validation_failed"
    assert ledger.read_records()[-1].record_type.value == "analysis_output_rejected"
    assert store.read_artifacts() == ()


@pytest.mark.parametrize(
    "change",
    [
        {"language": "fr"},
        {"source_text": "authorization: Bearer credential"},
        {"source_text": "#!/bin/sh\nexecute arbitrary code"},
    ],
)
def test_request_rejects_unsupported_or_sensitive_input(change) -> None:
    with pytest.raises(FinBERTValidationError):
        request(**change)


def test_request_rejects_unsupported_source_type() -> None:
    with pytest.raises(FinBERTValidationError):
        request(source_type="web_scrape")


def test_provider_rejects_input_beyond_configured_bound_without_inference() -> None:
    runtime = FakeRuntime()
    provider = LocalFinBERTProvider(config(max_input_chars=256), runtime)
    invocation_request = request(source_text="A" * 300)
    from sigil.ai import ProviderInvocation

    result = provider.invoke(
        ProviderInvocation(
            request_id=invocation_request.request_id,
            task_correlation_id=invocation_request.task_correlation_id,
            model_id=provider.model_id,
            registry_revision=DIGEST,
            capability=Capability.FINANCIAL_SENTIMENT,
            input_payload={
                "source_text": invocation_request.source_text,
                "source_identity": invocation_request.source_identity,
                "source_digest": invocation_request.input_digest,
            },
            timeout_ms=1_000,
            started_at=invocation_request.requested_at,
            ended_at="2026-08-01T18:00:01Z",
        )
    )
    assert result.failure.classification.value == "malformed_output"
    assert runtime.calls == 0


def test_output_validation_rejects_sum_label_model_and_digest_mismatch() -> None:
    governed_request = request()
    base = {
        "schema_version": 1,
        "label": "positive",
        "positive_score": 0.8,
        "neutral_score": 0.1,
        "negative_score": 0.1,
        "confidence": 0.8,
        "model_id": "prosusai.finbert",
        "model_version": "test-v1",
        "source_identity": "news-42",
        "source_digest": governed_request.input_digest,
        "analyzed_at": "2026-08-01T18:00:01Z",
        "limitations": ["Advisory only"],
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "approval_authority": False,
    }
    for mutated in (
        {**base, "positive_score": 0.9},
        {**base, "label": "negative"},
        {**base, "model_id": "wrong-model"},
        {**base, "source_digest": DIGEST},
        {**base, "broker_submission": True},
    ):
        with pytest.raises(FinBERTValidationError):
            validate_finbert_output(
                mutated,
                request=governed_request,
                expected_model_id="prosusai.finbert",
                expected_model_version="test-v1",
            )


def test_aggregation_preserves_traceability_disagreement_and_confidence(tmp_path: Path) -> None:
    first = successful_artifact(
        tmp_path / "first", {"positive": 0.8, "neutral": 0.1, "negative": 0.1}
    )
    second = successful_artifact(
        tmp_path / "second", {"positive": 0.1, "neutral": 0.1, "negative": 0.8}
    )
    second = replace(
        second,
        structured_payload=replace(second.structured_payload, source_identity="news-43"),
    )
    aggregate = aggregate_sentiment(
        (first, second),
        weights=(1.0, 1.0),
        window_start="2026-08-01T17:00:00Z",
        window_end="2026-08-01T19:00:00Z",
        freshness="current",
    )
    assert aggregate.source_count == 2
    assert aggregate.positive_count == 1
    assert aggregate.negative_count == 1
    assert aggregate.source_identities == ("news-42", "news-43")
    assert aggregate.confidence <= max(first.confidence, second.confidence)
    assert aggregate.broker_submission is False


def test_duplicate_sentiment_artifact_is_rejected(tmp_path: Path) -> None:
    artifact = successful_artifact(tmp_path)
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    with pytest.raises(AnalysisArtifactConflictError):
        store.append(artifact)


def test_explicit_fallback_policy_controls_nonpreferred_sentiment_model(
    tmp_path: Path,
) -> None:
    class FallbackProvider(LocalFinBERTProvider):
        model_family = "sentiment-fallback"

        def __init__(self) -> None:
            super().__init__(
                config(model_id="sentiment-fallback"),
                FakeRuntime(),
            )
            self.identity = ProviderIdentity(
                "fallback-finbert",
                self.identity.execution_location,
                ProviderHealth.HEALTHY,
            )

    provider = FallbackProvider()
    registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    governed = GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=ledger,
        artifact_store=store,
        enabled=True,
    )
    rejected = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert rejected.failure_classification == "preferred_route_unavailable"

    second_root = tmp_path / "allowed"
    second_root.mkdir()
    allowed = GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=DurableAIEvidenceLedger(second_root.resolve()),
        artifact_store=DurableAnalysisArtifactStore(second_root.resolve()),
        enabled=True,
    ).analyze_sentiment(request(fallback_permission=True), completed_at="2026-08-01T18:00:01Z")
    assert allowed.succeeded
    assert allowed.routing_summary == "fallback selected"


def test_hermes_handoff_success_and_digest_failure(tmp_path: Path) -> None:
    governed, _, _ = service(tmp_path, FakeRuntime())
    handoff = GovernedSentimentWorkRequest(
        request_id="finbert-request",
        task_correlation_id="finbert-task",
        source_identity="news-42",
        source_digest=f"sha256:{canonical_digest(TEXT)}",
        source_type="news_excerpt",
        privacy_requirement=PrivacyTier.LOCAL_ONLY,
        evidence_references=(DIGEST,),
        responsibility=Responsibility.NEWS_SENTIMENT,
    )
    result = governed.analyze_hermes_sentiment(
        handoff,
        source_text=TEXT,
        language="en",
        requested_at="2026-08-01T18:00:00Z",
        completed_at="2026-08-01T18:00:01Z",
    )
    assert result.succeeded
    assert result.artifact.artifact_id.startswith("analysis-artifact-")
    with pytest.raises(FinBERTValidationError):
        governed.analyze_hermes_sentiment(
            replace(handoff, request_id="finbert-request-2"),
            source_text="different text",
            language="en",
            requested_at="2026-08-01T18:00:00Z",
            completed_at="2026-08-01T18:00:01Z",
        )


def test_inspection_is_sanitized_and_exposes_finbert_status(tmp_path: Path) -> None:
    artifact = successful_artifact(tmp_path)
    env = {
        "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
        "SIGIL_AI_FINBERT_ENABLED": "true",
        "SIGIL_AI_FINBERT_MODEL": "/private/models/finbert",
        "SIGIL_AI_FINBERT_MODEL_VERSION": "test-v1",
    }
    status = ai_status(env)
    assert status["finbert"]["enabled"] is True
    assert status["finbert"]["sentiment_artifact_count"] == 1
    assert status["finbert"]["latest_sentiment"]["label"] == "positive"
    exact = ai_artifact_get({"artifact_id": artifact.artifact_id}, env)
    serialized = str(exact).lower()
    assert exact["artifact"]["structured_payload"]["source_identity"] == "news-42"
    assert TEXT.lower() not in serialized
    assert "model path" not in serialized
    assert "/private/models/finbert" not in str(status)
    assert exact["artifact"]["broker_submission"] is False


def test_service_disabled_never_invokes_finbert(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    governed, _, _ = service(tmp_path, runtime, enabled=False)
    result = governed.analyze_sentiment(request(), completed_at="2026-08-01T18:00:01Z")
    assert result.failure_classification == "service_disabled"
    assert runtime.calls == 0
