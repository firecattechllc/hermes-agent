"""Disabled-by-default governed Hermes Wiki knowledge adapter.

Stage 7 models immutable knowledge documents, revisions, namespaces, links,
citations, provenance, indexing evidence, retrieval evidence, freshness, and
worker-job lifecycle projection.

This module performs no crawling, network requests, authentication, publishing,
editing, indexing execution, filesystem access, credential resolution, job
dispatch, installation, activation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    WORKER_CONTRACT_SCHEMA_VERSION,
    GovernedWorkerJob,
    JobState,
)

HERMES_WIKI_ADAPTER_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|"
    r"[A-Za-z]:\\Users\\)"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)


class HermesWikiValidationError(ValueError):
    """Hermes Wiki adapter input failed closed."""


class WikiDocumentKind(str, Enum):
    ARTICLE = "article"
    RUNBOOK = "runbook"
    DESIGN = "design"
    POLICY = "policy"
    REFERENCE = "reference"
    INCIDENT = "incident"
    DECISION = "decision"


class WikiIndexState(str, Enum):
    NOT_INDEXED = "not_indexed"
    PENDING = "pending"
    INDEXED = "indexed"
    STALE = "stale"
    FAILED = "failed"
    INCOMPATIBLE = "incompatible"


class WikiRetrievalState(str, Enum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    STALE = "stale"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    INVALID = "invalid"


class WikiWorkState(str, Enum):
    PROPOSED = "proposed"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPLETION_UNKNOWN = "completion_unknown"


_JOB_STATE_PROJECTION: dict[JobState, WikiWorkState] = {
    JobState.PROPOSED: WikiWorkState.PROPOSED,
    JobState.ADMITTED: WikiWorkState.ADMITTED,
    JobState.REJECTED: WikiWorkState.REJECTED,
    JobState.QUEUED: WikiWorkState.QUEUED,
    JobState.RUNNING: WikiWorkState.RUNNING,
    JobState.CANCELLATION_REQUESTED: WikiWorkState.CANCELLATION_REQUESTED,
    JobState.CANCELLED: WikiWorkState.CANCELLED,
    JobState.SUCCEEDED: WikiWorkState.SUCCEEDED,
    JobState.FAILED: WikiWorkState.FAILED,
    JobState.COMPLETION_UNKNOWN: WikiWorkState.COMPLETION_UNKNOWN,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise HermesWikiValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise HermesWikiValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise HermesWikiValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise HermesWikiValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise HermesWikiValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise HermesWikiValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise HermesWikiValidationError(
            f"{label} must be a repository-relative reference"
        )


@dataclass(frozen=True, slots=True)
class HermesWikiConfig:
    integration_id: str = "hermes-wiki"
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    schema_version: int = HERMES_WIKI_ADAPTER_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != HERMES_WIKI_ADAPTER_SCHEMA_VERSION:
            raise HermesWikiValidationError(
                "unsupported Hermes Wiki adapter schema"
            )

        _require_identifier(self.integration_id, "Hermes Wiki integration ID")

        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise HermesWikiValidationError(
                "incompatible worker contract schema"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Hermes Wiki configuration")

    @property
    def can_crawl(self) -> bool:
        return False

    @property
    def can_publish(self) -> bool:
        return False

    @property
    def can_edit(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_index(self) -> bool:
        return False

    @property
    def can_access_filesystem(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class WikiNamespaceRef:
    namespace_id: str
    display_name: str
    classification: str

    def __post_init__(self) -> None:
        _require_identifier(self.namespace_id, "namespace ID")
        _require_identifier(self.classification, "namespace classification")

        if not self.display_name.strip():
            raise HermesWikiValidationError(
                "namespace display name is required"
            )

        _validate_sanitized(asdict(self), "wiki namespace")


@dataclass(frozen=True, slots=True)
class WikiRevisionRef:
    revision_id: str
    content_digest: str
    created_at: str
    author_identity: str
    source_reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.revision_id, "revision ID")
        _require_digest(self.content_digest, "revision content digest")
        _require_timestamp(self.created_at, "revision creation time")

        if not self.author_identity.strip():
            raise HermesWikiValidationError(
                "revision author identity is required"
            )

        _validate_sanitized(asdict(self), "wiki revision")
        _require_relative_reference(
            self.source_reference,
            "revision source reference",
        )


@dataclass(frozen=True, slots=True)
class WikiLinkRef:
    link_id: str
    target_document_id: str
    relation: str
    anchor: str | None

    def __post_init__(self) -> None:
        _require_identifier(self.link_id, "link ID")
        _require_identifier(self.target_document_id, "target document ID")
        _require_identifier(self.relation, "link relation")

        if self.anchor is not None and not self.anchor.strip():
            raise HermesWikiValidationError(
                "link anchor cannot be empty"
            )

        _validate_sanitized(asdict(self), "wiki link")


@dataclass(frozen=True, slots=True)
class WikiCitationRef:
    citation_id: str
    source_kind: str
    source_identity: str
    content_digest: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.citation_id, "citation ID")
        _require_identifier(self.source_kind, "citation source kind")
        _require_digest(self.content_digest, "citation content digest")

        if not self.source_identity.strip():
            raise HermesWikiValidationError(
                "citation source identity is required"
            )
        if not self.provenance.strip():
            raise HermesWikiValidationError(
                "citation provenance is required"
            )

        _validate_sanitized(asdict(self), "wiki citation")
        _require_relative_reference(
            self.reference,
            "citation reference",
        )


@dataclass(frozen=True, slots=True)
class WikiDocument:
    document_id: str
    title: str
    kind: WikiDocumentKind
    namespace: WikiNamespaceRef
    current_revision: WikiRevisionRef
    links: tuple[WikiLinkRef, ...]
    citations: tuple[WikiCitationRef, ...]
    tags: tuple[str, ...]
    created_at: str
    updated_at: str
    schema_version: int = HERMES_WIKI_ADAPTER_SCHEMA_VERSION
    document_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.document_digest and self.document_digest != expected:
            raise HermesWikiValidationError(
                "Hermes Wiki document digest mismatch"
            )

        if not self.document_digest:
            object.__setattr__(self, "document_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload.pop("document_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != HERMES_WIKI_ADAPTER_SCHEMA_VERSION:
            raise HermesWikiValidationError(
                "unsupported Hermes Wiki document schema"
            )

        _require_identifier(self.document_id, "document ID")
        _require_timestamp(self.created_at, "document creation time")
        _require_timestamp(self.updated_at, "document update time")

        if not self.title.strip():
            raise HermesWikiValidationError(
                "document title is required"
            )
        if not isinstance(self.kind, WikiDocumentKind):
            raise HermesWikiValidationError(
                "unknown wiki document kind"
            )

        for tag in self.tags:
            _require_identifier(tag, "document tag")

        if len(set(self.tags)) != len(self.tags):
            raise HermesWikiValidationError(
                "duplicate document tag"
            )
        if len({item.link_id for item in self.links}) != len(self.links):
            raise HermesWikiValidationError(
                "duplicate wiki link identity"
            )
        if len(
            {item.citation_id for item in self.citations}
        ) != len(self.citations):
            raise HermesWikiValidationError(
                "duplicate wiki citation identity"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Hermes Wiki document",
        )

    @property
    def can_publish(self) -> bool:
        return False

    @property
    def can_edit(self) -> bool:
        return False

    @property
    def can_execute(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class WikiIndexEvidence:
    document_id: str
    revision_id: str
    observed_at: str
    state: WikiIndexState
    index_schema_version: int
    embedding_model_identity: str | None
    chunk_count: int
    evidence_digest: str

    def __post_init__(self) -> None:
        _require_identifier(self.document_id, "index document ID")
        _require_identifier(self.revision_id, "index revision ID")
        _require_timestamp(self.observed_at, "index observation time")
        _require_digest(self.evidence_digest, "index evidence digest")

        if not isinstance(self.state, WikiIndexState):
            raise HermesWikiValidationError(
                "unknown wiki index state"
            )
        if self.index_schema_version < 1:
            raise HermesWikiValidationError(
                "index schema version must be positive"
            )
        if self.chunk_count < 0 or self.chunk_count > 1_000_000:
            raise HermesWikiValidationError(
                "index chunk count is outside bounds"
            )
        if (
            self.state is WikiIndexState.INDEXED
            and self.chunk_count < 1
        ):
            raise HermesWikiValidationError(
                "indexed document requires at least one chunk"
            )
        if (
            self.embedding_model_identity is not None
            and not self.embedding_model_identity.strip()
        ):
            raise HermesWikiValidationError(
                "embedding model identity cannot be empty"
            )

        _validate_sanitized(asdict(self), "wiki index evidence")


@dataclass(frozen=True, slots=True)
class WikiRetrievalEvidence:
    retrieval_id: str
    query_digest: str
    retrieved_at: str
    document_id: str
    revision_id: str
    rank: int
    score_basis_points: int
    excerpt_digest: str
    provenance: str

    def __post_init__(self) -> None:
        _require_identifier(self.retrieval_id, "retrieval ID")
        _require_digest(self.query_digest, "query digest")
        _require_timestamp(self.retrieved_at, "retrieval time")
        _require_identifier(self.document_id, "retrieval document ID")
        _require_identifier(self.revision_id, "retrieval revision ID")
        _require_digest(self.excerpt_digest, "excerpt digest")

        if not 1 <= self.rank <= 1000:
            raise HermesWikiValidationError(
                "retrieval rank is outside bounds"
            )
        if not 0 <= self.score_basis_points <= 10000:
            raise HermesWikiValidationError(
                "retrieval score is outside bounds"
            )
        if not self.provenance.strip():
            raise HermesWikiValidationError(
                "retrieval provenance is required"
            )

        _validate_sanitized(asdict(self), "wiki retrieval evidence")


@dataclass(frozen=True, slots=True)
class WikiRetrievalStatus:
    document_id: str
    state: WikiRetrievalState
    enabled: bool
    revision_current: bool
    index_current: bool
    retrieval_current: bool
    worker_contract_compatible: bool
    reason: str
    document_digest: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.document_id, "status document ID")
        _require_digest(self.document_digest, "document digest")

        if not isinstance(self.state, WikiRetrievalState):
            raise HermesWikiValidationError(
                "unknown wiki retrieval state"
            )
        if not self.reason.strip():
            raise HermesWikiValidationError(
                "wiki retrieval reason is required"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "wiki retrieval status")


@dataclass(frozen=True, slots=True)
class WikiJobProjection:
    job_id: str
    correlation_id: str
    idempotency_key: str
    requested_capability: str
    state: WikiWorkState
    worker_contract_digest: str
    worker_contract_schema: int
    created_at: str
    schema_version: int = HERMES_WIKI_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        for value, label in (
            (self.job_id, "job ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.requested_capability, "requested capability"),
        ):
            _require_identifier(value, label)

        if not isinstance(self.state, WikiWorkState):
            raise HermesWikiValidationError(
                "unknown wiki work state"
            )

        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )
        _require_timestamp(self.created_at, "job creation time")

        if self.worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise HermesWikiValidationError(
                "wiki job projection worker schema is incompatible"
            )
        if self.schema_version != HERMES_WIKI_ADAPTER_SCHEMA_VERSION:
            raise HermesWikiValidationError(
                "unsupported wiki job projection schema"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "wiki job projection",
        )

        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise HermesWikiValidationError(
                "wiki job projection digest mismatch"
            )
        if not self.projection_digest:
            object.__setattr__(self, "projection_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("projection_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"


def validate_hermes_wiki_registry_entry(
    config: HermesWikiConfig,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate that the Stage 1 registry describes Hermes Wiki safely."""

    if entry.integration_id != config.integration_id:
        raise HermesWikiValidationError(
            "Hermes Wiki registry identity mismatch"
        )
    if entry.category is not IntegrationCategory.KNOWLEDGE:
        raise HermesWikiValidationError(
            "Hermes Wiki must be registered as a knowledge integration"
        )
    if entry.lifecycle_state in {
        LifecycleState.REJECTED,
        LifecycleState.DEPRECATED,
        LifecycleState.QUARANTINED,
    }:
        raise HermesWikiValidationError(
            "Hermes Wiki registry lifecycle is not eligible"
        )

    entry.authority.validate()

    if entry.can_activate:
        raise HermesWikiValidationError(
            "Hermes Wiki registry entry unexpectedly permits activation"
        )


def evaluate_retrieval_status(
    config: HermesWikiConfig,
    document: WikiDocument,
    *,
    index: WikiIndexEvidence | None,
    retrieval: WikiRetrievalEvidence | None,
    index_age_seconds: int | None,
    retrieval_age_seconds: int | None,
    stale_after_seconds: int = 3600,
) -> WikiRetrievalStatus:
    """Evaluate injected Wiki evidence without crawling or indexing."""

    if not 1 <= stale_after_seconds <= 604800:
        raise HermesWikiValidationError(
            "wiki staleness threshold is outside bounds"
        )

    if not config.enabled:
        return WikiRetrievalStatus(
            document_id=document.document_id,
            state=WikiRetrievalState.DISABLED,
            enabled=False,
            revision_current=False,
            index_current=False,
            retrieval_current=False,
            worker_contract_compatible=True,
            reason="Hermes Wiki adapter is disabled by policy.",
            document_digest=document.document_digest,
        )

    if index is None:
        return WikiRetrievalStatus(
            document_id=document.document_id,
            state=WikiRetrievalState.MISSING,
            enabled=True,
            revision_current=False,
            index_current=False,
            retrieval_current=False,
            worker_contract_compatible=True,
            reason="No governed index evidence is available.",
            document_digest=document.document_digest,
        )

    if index.document_id != document.document_id:
        raise HermesWikiValidationError(
            "index evidence does not match wiki document"
        )
    if index.revision_id != document.current_revision.revision_id:
        return WikiRetrievalStatus(
            document_id=document.document_id,
            state=WikiRetrievalState.STALE,
            enabled=True,
            revision_current=False,
            index_current=False,
            retrieval_current=False,
            worker_contract_compatible=True,
            reason="Index evidence targets an older wiki revision.",
            document_digest=document.document_digest,
        )
    if index_age_seconds is None:
        raise HermesWikiValidationError(
            "index age is required when index evidence is present"
        )
    if index_age_seconds < 0:
        raise HermesWikiValidationError(
            "wiki index evidence cannot originate in the future"
        )

    index_current = (
        index.state is WikiIndexState.INDEXED
        and index_age_seconds <= stale_after_seconds
    )

    if not index_current:
        return WikiRetrievalStatus(
            document_id=document.document_id,
            state=WikiRetrievalState.STALE,
            enabled=True,
            revision_current=True,
            index_current=False,
            retrieval_current=False,
            worker_contract_compatible=True,
            reason="Wiki index evidence is stale or not indexed.",
            document_digest=document.document_digest,
        )

    if retrieval is None:
        return WikiRetrievalStatus(
            document_id=document.document_id,
            state=WikiRetrievalState.AVAILABLE,
            enabled=True,
            revision_current=True,
            index_current=True,
            retrieval_current=False,
            worker_contract_compatible=True,
            reason="Wiki document is indexed but has no retrieval evidence.",
            document_digest=document.document_digest,
        )

    if (
        retrieval.document_id != document.document_id
        or retrieval.revision_id != document.current_revision.revision_id
    ):
        raise HermesWikiValidationError(
            "retrieval evidence does not match wiki document revision"
        )
    if retrieval_age_seconds is None:
        raise HermesWikiValidationError(
            "retrieval age is required when retrieval evidence is present"
        )
    if retrieval_age_seconds < 0:
        raise HermesWikiValidationError(
            "wiki retrieval evidence cannot originate in the future"
        )

    retrieval_current = retrieval_age_seconds <= stale_after_seconds

    return WikiRetrievalStatus(
        document_id=document.document_id,
        state=(
            WikiRetrievalState.AVAILABLE
            if retrieval_current
            else WikiRetrievalState.STALE
        ),
        enabled=True,
        revision_current=True,
        index_current=True,
        retrieval_current=retrieval_current,
        worker_contract_compatible=True,
        reason=(
            "Wiki document, index evidence, and retrieval evidence are current."
            if retrieval_current
            else "Wiki retrieval evidence exceeded the freshness window."
        ),
        document_digest=document.document_digest,
    )


def project_worker_job(
    config: HermesWikiConfig,
    job: GovernedWorkerJob,
) -> WikiJobProjection:
    """Project one Stage 2 worker job into a descriptive Wiki work state."""

    if job.integration_id != config.integration_id:
        raise HermesWikiValidationError(
            "worker job integration does not match Hermes Wiki"
        )
    if job.schema_version != config.expected_worker_contract_schema:
        raise HermesWikiValidationError(
            "worker job schema is incompatible with Hermes Wiki"
        )

    job.authority.validate()

    return WikiJobProjection(
        job_id=job.job_id,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        requested_capability=job.requested_capability,
        state=_JOB_STATE_PROJECTION[job.state],
        worker_contract_digest=job.contract_digest,
        worker_contract_schema=job.schema_version,
        created_at=job.created_at,
    )


def lifecycle_projection() -> Mapping[JobState, WikiWorkState]:
    """Expose a copy of the deterministic worker-to-Wiki state map."""

    return dict(_JOB_STATE_PROJECTION)
