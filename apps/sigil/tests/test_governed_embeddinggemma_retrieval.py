from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai import (
    AnalysisArtifactConflictError,
    Capability,
    DurableAIEvidenceLedger,
    DurableAnalysisArtifactStore,
    DurableRetrievalStore,
    EmbeddingGemmaConfig,
    FreshnessRequirement,
    GovernedAnalysisService,
    GovernedIndexingRequest,
    GovernedModelRegistry,
    GovernedRetrievalArtifact,
    GovernedRetrievalRequest,
    GovernedRetrievalWorkRequest,
    LocalEmbeddingGemmaProvider,
    PrivacyTier,
    ProviderInvocation,
    Responsibility,
    RetrievalSourceType,
    RetrievalStoreConflictError,
    RetrievalStoreCorruptionError,
    RetrievalValidationError,
    TrustTier,
    create_retrieval_source,
    deterministic_chunks,
    normalized_vector,
)
from sigil.ai.inspection import ai_artifact_get, ai_status
from sigil.ai.registry import canonical_digest

DIGEST = "sha256:" + "a" * 64


class FakeEmbeddingRuntime:
    def __init__(self, *, malformed=None, error: Exception | None = None) -> None:
        self.malformed = malformed
        self.error = error
        self.calls = 0

    def embed(self, *, texts):
        self.calls += 1
        if self.error:
            raise self.error
        if self.malformed is not None:
            return [self.malformed for _ in texts]
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str):
        lowered = text.lower()
        if "growth" in lowered or "revenue" in lowered:
            return [1.0, 0.0, 0.0]
        if "risk" in lowered or "decline" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


def config(**values) -> EmbeddingGemmaConfig:
    if "model_id" in values:
        values["model"] = values.pop("model_id")
    return EmbeddingGemmaConfig(
        **{
            "enabled": True,
            "model_version": "test-v1",
            "timeout_ms": 1_000,
            "max_input_chars": 2_000,
            "max_batch_size": 4,
            "vector_dimension": 3,
            "retrieval_max_results": 5,
            "retrieval_min_score": 0.0,
            **values,
        }
    )


def source(
    *,
    identity: str = "news-42",
    content: str = "Revenue growth remained strong.",
    source_type: RetrievalSourceType = RetrievalSourceType.GOVERNED_NEWS_EVIDENCE,
    corpus_id: str = "research-corpus",
    privacy: PrivacyTier = PrivacyTier.LOCAL_ONLY,
    trust: TrustTier = TrustTier.TRUSTED,
    stale_after: str | None = "2026-08-02T00:00:00Z",
    version: str = "v1",
    supersedes: str | None = None,
    chunk_size: int = 1_000,
):
    return create_retrieval_source(
        content=content,
        maximum_chunk_characters=chunk_size,
        source_type=source_type,
        source_identity=identity,
        source_version=version,
        corpus_id=corpus_id,
        created_at="2026-08-01T17:00:00Z",
        observed_at="2026-08-01T17:00:00Z",
        stale_after=stale_after,
        privacy_classification=privacy,
        trust_classification=trust,
        language="en",
        supersedes_source_id=supersedes,
    )


def service(tmp_path: Path, runtime=None, *, enabled: bool = True):
    provider = LocalEmbeddingGemmaProvider(config(), runtime or FakeEmbeddingRuntime())
    registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    artifacts = DurableAnalysisArtifactStore(tmp_path.resolve())
    vectors = DurableRetrievalStore(tmp_path.resolve())
    governed = GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=ledger,
        artifact_store=artifacts,
        retrieval_store=vectors,
        enabled=enabled,
    )
    return governed, provider, ledger, artifacts, vectors


def index(governed, item, *, request_id="index-request", chunk_size=1_000):
    return governed.index_retrieval_source(
        GovernedIndexingRequest(
            request_id=request_id,
            task_correlation_id=f"{request_id}-task",
            source=item,
            chunk_maximum_characters=chunk_size,
            requested_at="2026-08-01T18:00:00Z",
        ),
        completed_at="2026-08-01T18:00:01Z",
    )


def retrieval_request(**values):
    text = values.pop("query_text", "revenue growth")
    return GovernedRetrievalRequest(
        **{
            "request_id": "retrieve-request",
            "task_correlation_id": "retrieve-task",
            "responsibility": Responsibility.RESEARCH_RETRIEVAL,
            "query_digest": f"sha256:{canonical_digest(text)}",
            "query_text": text,
            "corpus_ids": ("research-corpus",),
            "source_type_filters": (),
            "privacy_requirement": PrivacyTier.LOCAL_ONLY,
            "minimum_trust_tier": TrustTier.TRUSTED,
            "freshness_requirement": FreshnessRequirement.ANY,
            "maximum_results": 5,
            "minimum_score": 0.0,
            "fallback_permission": False,
            "requested_at": "2026-08-01T18:01:00Z",
            "evidence_context_digests": (DIGEST,),
            **values,
        }
    )


def test_configuration_is_disabled_cpu_local_only_and_bounded_by_default() -> None:
    value = EmbeddingGemmaConfig.from_environment({})
    assert value.enabled is False
    assert value.device == "cpu"
    assert value.local_files_only is True
    assert value.retrieval_max_results <= 50
    with pytest.raises(RetrievalValidationError):
        EmbeddingGemmaConfig.from_environment(
            {"SIGIL_AI_EMBEDDING_GEMMA_LOCAL_FILES_ONLY": "false"}
        )


def test_registration_is_specialized_and_prohibitions_are_explicit() -> None:
    provider = LocalEmbeddingGemmaProvider(config(), FakeEmbeddingRuntime())
    registration = provider.registration()
    assert registration.family == "embeddinggemma"
    assert registration.capabilities == frozenset(
        {Capability.EMBEDDINGS, Capability.SEMANTIC_RETRIEVAL}
    )
    assert Responsibility.RESEARCH_RETRIEVAL in registration.allowed_responsibilities
    for prohibited in (
        Responsibility.CAPITAL_AUTHORIZATION,
        Responsibility.PROPOSAL_APPROVAL,
        Responsibility.BROKER_SUBMISSION,
        Responsibility.ORDER_EXECUTION,
        Responsibility.PORTFOLIO_MUTATION,
        Responsibility.POLICY_CHANGE,
        Responsibility.CREDENTIAL_ACCESS,
        Responsibility.UNRESTRICTED_SHELL_EXECUTION,
        Responsibility.SOURCE_DELETION_WITHOUT_GOVERNED_OPERATOR_ACTION,
    ):
        assert prohibited in registration.prohibited_responsibilities


def test_provider_is_lazy_and_unavailable_when_disabled() -> None:
    runtime = FakeEmbeddingRuntime()
    provider = LocalEmbeddingGemmaProvider(EmbeddingGemmaConfig(), runtime)
    assert provider.identity.enabled is False
    assert runtime.calls == 0


@pytest.mark.parametrize(
    "vector",
    ([1.0, 0.0], [1.0, 0.0, 0.0, 0.0], [math.nan, 0.0, 1.0], [0, 0, 0]),
)
def test_malformed_dimension_nonfinite_and_empty_vectors_fail_closed(vector) -> None:
    with pytest.raises(RetrievalValidationError):
        normalized_vector(vector, 3)


def test_provider_model_capability_timeout_and_malformed_failures() -> None:
    provider = LocalEmbeddingGemmaProvider(config(), FakeEmbeddingRuntime())
    base = ProviderInvocation(
        request_id="embedding-request",
        task_correlation_id="embedding-task",
        model_id=provider.model_id,
        registry_revision=DIGEST,
        capability=Capability.EMBEDDINGS,
        input_payload={"texts": ["revenue growth"]},
        timeout_ms=1_000,
        started_at="2026-08-01T18:00:00Z",
        ended_at="2026-08-01T18:00:01Z",
    )
    assert (
        provider.invoke(replace(base, model_id="wrong-model")).failure.classification.value
        == "model_identity_mismatch"
    )
    assert (
        provider.invoke(replace(base, capability=Capability.CODING)).failure.classification.value
        == "capability_mismatch"
    )
    timeout = LocalEmbeddingGemmaProvider(
        config(), FakeEmbeddingRuntime(error=TimeoutError())
    ).invoke(base)
    assert timeout.failure.classification.value == "timeout"
    malformed = LocalEmbeddingGemmaProvider(
        config(), FakeEmbeddingRuntime(malformed=[1.0, 0.0])
    ).invoke(base)
    assert malformed.failure.classification.value == "malformed_output"


@pytest.mark.parametrize(
    "change",
    (
        {"source_type": "web_scrape"},
        {"language": "fr"},
        {"content": "authorization: Bearer credential"},
        {"content": "#!/bin/sh\nos.system('unsafe')"},
    ),
)
def test_source_rejects_unsupported_sensitive_or_executable_content(change) -> None:
    with pytest.raises((RetrievalValidationError, TypeError)):
        source(**change)


def test_chunking_is_deterministic_bounded_and_identity_stable() -> None:
    item = source(content="growth " * 80, chunk_size=100)
    first = deterministic_chunks(item, maximum_characters=100)
    second = deterministic_chunks(item, maximum_characters=100)
    assert first == second
    assert len(first) > 1
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    assert all(len(chunk.text) <= 100 for chunk in first)


def test_source_versions_and_supersession_never_overwrite_identity() -> None:
    first = source()
    second = source(
        content="Revenue growth accelerated.",
        version="v2",
        supersedes=first.source_id,
    )
    assert first.source_id != second.source_id
    assert second.supersedes_source_id == first.source_id


def test_indexing_persists_source_chunks_embeddings_evidence_and_permissions(
    tmp_path: Path,
) -> None:
    governed, _, ledger, _, store = service(tmp_path)
    response = index(governed, source())
    assert response.succeeded
    sources, chunks, embeddings = store.read_index()
    assert len(sources) == len(chunks) == len(embeddings) == 1
    assert sources[0].content is None
    assert embeddings[0].normalized is True
    assert embeddings[0].broker_submission is False
    assert len(ledger.read_records()) == 3
    assert os.stat(store.directory).st_mode & 0o777 == 0o700
    assert os.stat(store.path).st_mode & 0o777 == 0o600


def test_restart_recovery_and_duplicate_rejection(tmp_path: Path) -> None:
    governed, _, _, _, store = service(tmp_path)
    item = source()
    assert index(governed, item).succeeded
    restarted = DurableRetrievalStore(tmp_path.resolve())
    assert restarted.read_index() == store.read_index()
    with pytest.raises(RetrievalStoreConflictError):
        restarted.append_index(
            store.read_index()[0][0], store.read_index()[1], store.read_index()[2]
        )


def test_truncated_tail_is_reported_or_recovered(tmp_path: Path) -> None:
    governed, _, _, _, store = service(tmp_path)
    assert index(governed, source()).succeeded
    with store.path.open("ab") as stream:
        stream.write(b'{"truncated":')
    with pytest.raises(RetrievalStoreCorruptionError):
        store.read_index(recover_truncated_tail=False)
    assert len(store.read_index(recover_truncated_tail=True)[0]) == 1


def test_incompatible_dimension_and_model_version_are_rejected(tmp_path: Path) -> None:
    governed, _, _, _, store = service(tmp_path)
    assert index(governed, source()).succeeded
    stored_source, _stored_chunks, stored_embeddings = store.read_index()
    next_source = source(identity="news-43", content="Risk increased.")
    next_chunks = deterministic_chunks(next_source)
    with pytest.raises(RetrievalStoreConflictError):
        store.append_index(
            next_source,
            next_chunks,
            (
                replace(
                    stored_embeddings[0],
                    embedding_id="embedding-" + "b" * 64,
                    source_id=next_source.source_id,
                    chunk_id=next_chunks[0].chunk_id,
                    chunk_digest=next_chunks[0].chunk_digest,
                    model_version="different-v2",
                ),
            ),
        )
    assert stored_source[0].source_id != next_source.source_id


def test_similarity_ranking_filters_freshness_and_no_results(tmp_path: Path) -> None:
    governed, _, _, artifacts, _ = service(tmp_path)
    assert index(governed, source(identity="growth", content="Revenue growth strong.")).succeeded
    assert index(
        governed,
        source(
            identity="risk",
            content="Risk and decline increased.",
            privacy=PrivacyTier.EXTERNAL_APPROVED,
            trust=TrustTier.RESTRICTED,
            stale_after="2026-08-01T17:30:00Z",
        ),
        request_id="index-risk",
    ).succeeded
    response = governed.retrieve(retrieval_request(), completed_at="2026-08-01T18:02:00Z")
    assert response.succeeded
    assert isinstance(response.artifact, GovernedRetrievalArtifact)
    assert [item.source_identity for item in response.artifact.results] == ["growth"]
    assert response.artifact.results[0].score == pytest.approx(1.0)
    assert response.artifact.results[0].freshness_state == "current"
    assert (
        DurableAnalysisArtifactStore(tmp_path.resolve()).read_artifacts()
        == artifacts.read_artifacts()
    )

    none = governed.retrieve(
        retrieval_request(
            request_id="retrieve-none",
            task_correlation_id="retrieve-none-task",
            minimum_score=1.0,
            query_text="unrelated context",
        ),
        completed_at="2026-08-01T18:03:00Z",
    )
    assert none.succeeded
    assert none.artifact.results == ()


def test_corpus_source_type_and_stale_filters_apply_before_ranking(tmp_path: Path) -> None:
    governed, _, _, _, _ = service(tmp_path)
    assert index(
        governed,
        source(
            identity="filing",
            source_type=RetrievalSourceType.SEC_FILING_EXCERPT,
            stale_after="2026-08-01T17:30:00Z",
        ),
    ).succeeded
    response = governed.retrieve(
        retrieval_request(
            source_type_filters=(RetrievalSourceType.GOVERNED_NEWS_EVIDENCE,),
            freshness_requirement=FreshnessRequirement.CURRENT_ONLY,
        ),
        completed_at="2026-08-01T18:02:00Z",
    )
    assert response.artifact.results == ()


def test_duplicate_source_diversification_and_bounded_result_count(tmp_path: Path) -> None:
    governed, _, _, _, _ = service(tmp_path)
    long_source = source(identity="multi", content="revenue growth " * 80, chunk_size=100)
    assert index(governed, long_source, chunk_size=100).succeeded
    assert index(
        governed,
        source(identity="single", content="Revenue growth continued."),
        request_id="index-single",
    ).succeeded
    result = governed.retrieve(
        retrieval_request(maximum_results=2), completed_at="2026-08-01T18:02:00Z"
    ).artifact.results
    assert len(result) == 2
    assert result[0].source_id != result[1].source_id


def test_retrieval_artifact_excludes_raw_query_vectors_and_unbounded_content(
    tmp_path: Path,
) -> None:
    governed, _, _, artifact_store, _ = service(tmp_path)
    assert index(governed, source()).succeeded
    query = "growth outlook"
    response = governed.retrieve(
        retrieval_request(query_text=query), completed_at="2026-08-01T18:02:00Z"
    )
    serialized = str(response.artifact).lower()
    assert query not in serialized
    assert "vector" not in serialized
    assert response.artifact.broker_submission is False
    assert response.artifact.execution_authorized is False
    assert response.artifact.portfolio_mutation is False
    assert response.artifact.approval_authority is False
    with pytest.raises(AnalysisArtifactConflictError):
        artifact_store.append(response.artifact)


def test_failed_indexing_and_retrieval_are_evidenced_without_authoritative_artifacts(
    tmp_path: Path,
) -> None:
    governed, _, ledger, artifacts, _ = service(
        tmp_path, FakeEmbeddingRuntime(malformed=[1.0, 0.0])
    )
    failed = index(governed, source())
    assert failed.failure_classification == "malformed_output"
    assert ledger.read_records()[-1].failure_classification == "malformed_output"
    assert artifacts.read_artifacts() == ()


def test_hermes_handoff_success_and_digest_failure(tmp_path: Path) -> None:
    governed, _, _, _, _ = service(tmp_path)
    assert index(governed, source()).succeeded
    query = "revenue growth"
    work = GovernedRetrievalWorkRequest(
        request_id="hermes-retrieval",
        task_correlation_id="hermes-task",
        query_digest=f"sha256:{canonical_digest(query)}",
        corpus_ids=("research-corpus",),
        source_type_filters=(),
        privacy_requirement=PrivacyTier.LOCAL_ONLY,
        minimum_trust_tier=TrustTier.TRUSTED,
        freshness_requirement="any",
        maximum_results=3,
        minimum_score=0.0,
        evidence_context_digests=(DIGEST,),
        responsibility=Responsibility.RESEARCH_RETRIEVAL,
    )
    response = governed.retrieve_hermes(
        work,
        query_text=query,
        requested_at="2026-08-01T18:01:00Z",
        completed_at="2026-08-01T18:02:00Z",
    )
    assert response.succeeded
    assert response.routing_evidence_id
    assert response.invocation_evidence_id
    with pytest.raises(RetrievalValidationError):
        governed.retrieve_hermes(
            replace(work, request_id="hermes-retrieval-2"),
            query_text="different query",
            requested_at="2026-08-01T18:01:00Z",
            completed_at="2026-08-01T18:02:00Z",
        )


def test_inspection_and_startup_are_safe_without_embeddinggemma(tmp_path: Path) -> None:
    status = ai_status({"SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve())})
    assert status["embeddinggemma"]["enabled"] is False
    assert status["embeddinggemma"]["vector_store_health"] == "empty"
    assert status["paper_only"] is True
    assert status["broker_submission"] is False
    governed, _, _, _, _ = service(tmp_path, enabled=False)
    result = governed.retrieve(retrieval_request(), completed_at="2026-08-01T18:02:00Z")
    assert result.failure_classification == "service_disabled"


def test_inspection_exposes_counts_without_query_vectors_paths_or_credentials(
    tmp_path: Path,
) -> None:
    governed, _, _, _, _ = service(tmp_path)
    assert index(governed, source()).succeeded
    response = governed.retrieve(
        retrieval_request(query_text="growth outlook"),
        completed_at="2026-08-01T18:02:00Z",
    )
    environment = {
        "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
        "SIGIL_AI_EMBEDDING_GEMMA_ENABLED": "true",
        "SIGIL_AI_EMBEDDING_GEMMA_MODEL": "/private/models/embeddinggemma",
        "SIGIL_AI_EMBEDDING_GEMMA_MODEL_VERSION": "test-v1",
        "SIGIL_AI_EMBEDDING_GEMMA_VECTOR_DIMENSION": "3",
    }
    status = ai_status(environment)
    retrieval = status["embeddinggemma"]
    assert retrieval["source_count"] == 1
    assert retrieval["embedding_count"] == 1
    assert retrieval["latest_retrieval"]["result_count"] == 1
    serialized_status = str(status).lower()
    assert "/private/models/embeddinggemma" not in serialized_status
    assert "vector" not in str(retrieval["latest_retrieval"]).lower()
    exact = ai_artifact_get({"artifact_id": response.artifact.artifact_id}, environment)
    serialized = str(exact).lower()
    assert "growth outlook" not in serialized
    assert "authorization" not in serialized
    assert "vector" not in serialized
