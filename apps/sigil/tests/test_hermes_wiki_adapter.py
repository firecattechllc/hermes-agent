from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai.registry import canonical_digest
from sigil.hermes_wiki_adapter import (
    HERMES_WIKI_ADAPTER_SCHEMA_VERSION,
    HermesWikiConfig,
    HermesWikiValidationError,
    WikiCitationRef,
    WikiDocument,
    WikiDocumentKind,
    WikiIndexEvidence,
    WikiIndexState,
    WikiLinkRef,
    WikiNamespaceRef,
    WikiRetrievalEvidence,
    WikiRetrievalState,
    WikiRevisionRef,
    WikiWorkState,
    evaluate_retrieval_status,
    lifecycle_projection,
    project_worker_job,
    validate_hermes_wiki_registry_entry,
)
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    ApprovalRequirements,
    EvidenceRequirements,
    GovernedWorkerJob,
    JobBudget,
    JobState,
)


NOW = "2026-08-02T00:10:00Z"
LATER = "2026-08-02T00:11:00Z"
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64
QUERY_DIGEST = "sha256:" + "c" * 64
EXCERPT_DIGEST = "sha256:" + "d" * 64


def make_job(
    *,
    state: JobState = JobState.PROPOSED,
    integration_id: str = "hermes-wiki",
) -> GovernedWorkerJob:
    payload = {
        "namespace_id": "sigil",
        "document_id": "stage7-wiki",
    }

    return GovernedWorkerJob(
        job_id="job-wiki-007",
        correlation_id="corr-wiki-007",
        idempotency_key="idem-wiki-007",
        integration_id=integration_id,
        requested_capability="knowledge_retrieval",
        requesting_actor_identity="hermes-control-plane",
        target_machine="hermes-titan",
        target_profile="governed-worker",
        created_at=NOW,
        deadline_at="2026-08-02T01:10:00Z",
        input_payload=payload,
        input_digest=f"sha256:{canonical_digest(payload)}",
        budget=JobBudget(
            maximum_cost_usd="5.00",
            maximum_runtime_seconds=3600,
            maximum_attempts=3,
            maximum_input_bytes=100000,
            maximum_output_bytes=100000,
        ),
        evidence_requirements=EvidenceRequirements(
            required=True,
            minimum_references=1,
            required_kinds=("citation",),
            require_content_digests=True,
            require_provenance=True,
        ),
        approval_requirements=ApprovalRequirements(
            required=False,
            policy_revision="wiki-stage7",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
        state=state,
    )


def namespace() -> WikiNamespaceRef:
    return WikiNamespaceRef(
        namespace_id="sigil",
        display_name="Sigil",
        classification="internal",
    )


def revision(
    *,
    revision_id: str = "revision-007",
) -> WikiRevisionRef:
    return WikiRevisionRef(
        revision_id=revision_id,
        content_digest=DIGEST,
        created_at=NOW,
        author_identity="hermes-control-plane",
        source_reference="wiki/sigil/stage7.md",
    )


def link() -> WikiLinkRef:
    return WikiLinkRef(
        link_id="link-stage6",
        target_document_id="stage6-buzznode",
        relation="depends_on",
        anchor="authority-boundary",
    )


def citation() -> WikiCitationRef:
    return WikiCitationRef(
        citation_id="citation-stage7-tests",
        source_kind="test_result",
        source_identity="stage7-focused-suite",
        content_digest=DIGEST,
        provenance="pytest focused Stage 7 suite",
        reference="evidence/stage7-tests.json",
    )


def document() -> WikiDocument:
    return WikiDocument(
        document_id="stage7-wiki",
        title="Hermes Wiki Integration",
        kind=WikiDocumentKind.DESIGN,
        namespace=namespace(),
        current_revision=revision(),
        links=(link(),),
        citations=(citation(),),
        tags=("hermes", "wiki", "stage7"),
        created_at=NOW,
        updated_at=LATER,
    )


def index_evidence(
    *,
    revision_id: str = "revision-007",
    state: WikiIndexState = WikiIndexState.INDEXED,
    chunk_count: int = 4,
) -> WikiIndexEvidence:
    return WikiIndexEvidence(
        document_id="stage7-wiki",
        revision_id=revision_id,
        observed_at=LATER,
        state=state,
        index_schema_version=1,
        embedding_model_identity="embeddinggemma",
        chunk_count=chunk_count,
        evidence_digest=DIGEST,
    )


def retrieval_evidence(
    *,
    document_id: str = "stage7-wiki",
    revision_id: str = "revision-007",
) -> WikiRetrievalEvidence:
    return WikiRetrievalEvidence(
        retrieval_id="retrieval-007",
        query_digest=QUERY_DIGEST,
        retrieved_at=LATER,
        document_id=document_id,
        revision_id=revision_id,
        rank=1,
        score_basis_points=9500,
        excerpt_digest=EXCERPT_DIGEST,
        provenance="governed retrieval evaluation",
    )


def make_registry_entry(
    *,
    integration_id: str = "hermes-wiki",
    category: IntegrationCategory = IntegrationCategory.KNOWLEDGE,
    lifecycle: LifecycleState = LifecycleState.DISCOVERED,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id=integration_id,
        canonical_project_name="Hermes Wiki",
        category=category,
        repository_url="https://github.com/firecattechllc/hermes-agent",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="firecattechllc/hermes-agent",
        maintainer_identity="firecattechllc",
        maturity="internal foundation",
        license_classification="repository license",
        license_evidence_source="upstream repository",
        activity_evidence="repository activity inspected",
        activity_observed_at=NOW,
        credential_requirements=(),
        authentication_requirements=(),
        declared_network_access=(),
        declared_egress_destinations=(),
        declared_filesystem_access=(),
        declared_tool_permissions=(),
        declared_shell_process_authority=(),
        declared_browser_authority=(),
        declared_execution_model="descriptive knowledge adapter only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan", "hermes-mac"),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("knowledge_retrieval",),
        integration_overlap=("hermes-control-plane",),
        known_risks=("stale knowledge", "citation drift"),
        threat_model_references=("docs/threat-models/hermes-wiki.md",),
        evaluation_evidence_references=("docs/evidence/hermes-wiki.md",),
        rollback_instructions="Remove the disabled Wiki projection.",
        disable_instructions="Keep the adapter disabled.",
        quarantine_instructions="Reject all Wiki projections.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 7 contract evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def test_config_is_disabled_and_has_no_authority() -> None:
    config = HermesWikiConfig()

    assert config.schema_version == HERMES_WIKI_ADAPTER_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_crawl is False
    assert config.can_publish is False
    assert config.can_edit is False
    assert config.can_authenticate is False
    assert config.can_index is False
    assert config.can_access_filesystem is False
    assert config.can_dispatch is False
    assert config.authority == AuthorityDenials()


def test_config_rejects_worker_schema_mismatch() -> None:
    with pytest.raises(HermesWikiValidationError, match="incompatible"):
        HermesWikiConfig(expected_worker_contract_schema=999)


def test_registry_entry_validation_accepts_knowledge_entry() -> None:
    validate_hermes_wiki_registry_entry(
        HermesWikiConfig(),
        make_registry_entry(),
    )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            make_registry_entry(integration_id="buzznode"),
            "identity mismatch",
        ),
        (
            make_registry_entry(
                category=IntegrationCategory.WORKER
            ),
            "knowledge integration",
        ),
        (
            make_registry_entry(
                lifecycle=LifecycleState.QUARANTINED
            ),
            "not eligible",
        ),
    ],
)
def test_registry_entry_validation_fails_closed(
    entry: IntegrationRegistryEntry,
    message: str,
) -> None:
    with pytest.raises(HermesWikiValidationError, match=message):
        validate_hermes_wiki_registry_entry(
            HermesWikiConfig(),
            entry,
        )


def test_document_is_immutable_and_deterministic() -> None:
    first = document()
    second = document()

    assert first == second
    assert first.document_digest == second.document_digest
    assert first.document_digest.startswith("sha256:")
    assert first.can_publish is False
    assert first.can_edit is False
    assert first.can_execute is False


def test_document_rejects_digest_tampering() -> None:
    value = document()

    with pytest.raises(HermesWikiValidationError, match="digest mismatch"):
        replace(value, title="Changed title")


def test_duplicate_tags_fail_closed() -> None:
    with pytest.raises(HermesWikiValidationError, match="duplicate"):
        replace(
            document(),
            tags=("wiki", "wiki"),
            document_digest="",
        )


def test_duplicate_links_fail_closed() -> None:
    duplicate = link()

    with pytest.raises(HermesWikiValidationError, match="duplicate"):
        replace(
            document(),
            links=(duplicate, duplicate),
            document_digest="",
        )


def test_duplicate_citations_fail_closed() -> None:
    duplicate = citation()

    with pytest.raises(HermesWikiValidationError, match="duplicate"):
        replace(
            document(),
            citations=(duplicate, duplicate),
            document_digest="",
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/wiki.md",
        "/home/operator/wiki.md",
        "../outside.md",
        "wiki/../../outside.md",
        "http://127.0.0.1:3000/wiki",
    ],
)
def test_revision_references_reject_private_or_escaping_values(
    bad_reference: str,
) -> None:
    with pytest.raises(HermesWikiValidationError):
        replace(
            revision(),
            source_reference=bad_reference,
        )


def test_citation_rejects_credentials_before_path_validation() -> None:
    with pytest.raises(HermesWikiValidationError, match="credential"):
        WikiCitationRef(
            citation_id="citation-secret",
            source_kind="test_result",
            source_identity="secret",
            content_digest=DIGEST,
            provenance="api_key=secret-value",
            reference="evidence/result.json",
        )


def test_indexed_document_requires_chunks() -> None:
    with pytest.raises(HermesWikiValidationError, match="at least one"):
        index_evidence(chunk_count=0)


def test_disabled_retrieval_status_fails_closed() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(),
        document(),
        index=index_evidence(),
        retrieval=retrieval_evidence(),
        index_age_seconds=1,
        retrieval_age_seconds=1,
    )

    assert status.state is WikiRetrievalState.DISABLED
    assert status.enabled is False
    assert status.index_current is False
    assert status.retrieval_current is False


def test_missing_index_evidence_is_missing() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=None,
        retrieval=None,
        index_age_seconds=None,
        retrieval_age_seconds=None,
    )

    assert status.state is WikiRetrievalState.MISSING


def test_old_revision_index_is_stale() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=index_evidence(revision_id="revision-006"),
        retrieval=None,
        index_age_seconds=10,
        retrieval_age_seconds=None,
    )

    assert status.state is WikiRetrievalState.STALE
    assert status.revision_current is False


def test_current_index_without_retrieval_is_available() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=index_evidence(),
        retrieval=None,
        index_age_seconds=10,
        retrieval_age_seconds=None,
    )

    assert status.state is WikiRetrievalState.AVAILABLE
    assert status.index_current is True
    assert status.retrieval_current is False


def test_current_retrieval_is_available() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=index_evidence(),
        retrieval=retrieval_evidence(),
        index_age_seconds=10,
        retrieval_age_seconds=10,
    )

    assert status.state is WikiRetrievalState.AVAILABLE
    assert status.revision_current is True
    assert status.index_current is True
    assert status.retrieval_current is True


def test_stale_index_is_stale() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=index_evidence(),
        retrieval=None,
        index_age_seconds=3601,
        retrieval_age_seconds=None,
        stale_after_seconds=3600,
    )

    assert status.state is WikiRetrievalState.STALE


def test_stale_retrieval_is_stale() -> None:
    status = evaluate_retrieval_status(
        HermesWikiConfig(enabled=True),
        document(),
        index=index_evidence(),
        retrieval=retrieval_evidence(),
        index_age_seconds=10,
        retrieval_age_seconds=3601,
        stale_after_seconds=3600,
    )

    assert status.state is WikiRetrievalState.STALE
    assert status.retrieval_current is False


def test_future_index_evidence_fails_closed() -> None:
    with pytest.raises(HermesWikiValidationError, match="future"):
        evaluate_retrieval_status(
            HermesWikiConfig(enabled=True),
            document(),
            index=index_evidence(),
            retrieval=None,
            index_age_seconds=-1,
            retrieval_age_seconds=None,
        )


def test_mismatched_index_document_fails_closed() -> None:
    bad_index = replace(
        index_evidence(),
        document_id="different-document",
    )

    with pytest.raises(HermesWikiValidationError, match="does not match"):
        evaluate_retrieval_status(
            HermesWikiConfig(enabled=True),
            document(),
            index=bad_index,
            retrieval=None,
            index_age_seconds=1,
            retrieval_age_seconds=None,
        )


def test_mismatched_retrieval_revision_fails_closed() -> None:
    bad_retrieval = retrieval_evidence(
        revision_id="revision-006"
    )

    with pytest.raises(HermesWikiValidationError, match="does not match"):
        evaluate_retrieval_status(
            HermesWikiConfig(enabled=True),
            document(),
            index=index_evidence(),
            retrieval=bad_retrieval,
            index_age_seconds=1,
            retrieval_age_seconds=1,
        )


@pytest.mark.parametrize(
    ("job_state", "wiki_state"),
    [
        (JobState.PROPOSED, WikiWorkState.PROPOSED),
        (JobState.ADMITTED, WikiWorkState.ADMITTED),
        (JobState.REJECTED, WikiWorkState.REJECTED),
        (JobState.QUEUED, WikiWorkState.QUEUED),
        (JobState.RUNNING, WikiWorkState.RUNNING),
        (
            JobState.CANCELLATION_REQUESTED,
            WikiWorkState.CANCELLATION_REQUESTED,
        ),
        (JobState.CANCELLED, WikiWorkState.CANCELLED),
        (JobState.SUCCEEDED, WikiWorkState.SUCCEEDED),
        (JobState.FAILED, WikiWorkState.FAILED),
        (
            JobState.COMPLETION_UNKNOWN,
            WikiWorkState.COMPLETION_UNKNOWN,
        ),
    ],
)
def test_worker_lifecycle_projection(
    job_state: JobState,
    wiki_state: WikiWorkState,
) -> None:
    result = project_worker_job(
        HermesWikiConfig(),
        make_job(state=job_state),
    )

    assert result.state is wiki_state
    assert lifecycle_projection()[job_state] is wiki_state


def test_projection_preserves_worker_identity() -> None:
    job = make_job()
    result = project_worker_job(HermesWikiConfig(), job)

    assert result.job_id == job.job_id
    assert result.correlation_id == job.correlation_id
    assert result.idempotency_key == job.idempotency_key
    assert result.requested_capability == job.requested_capability
    assert result.worker_contract_digest == job.contract_digest


def test_projection_rejects_wrong_integration() -> None:
    with pytest.raises(HermesWikiValidationError, match="does not match"):
        project_worker_job(
            HermesWikiConfig(),
            make_job(integration_id="buzznode"),
        )
