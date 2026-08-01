"""Governed local EmbeddingGemma indexing, persistence, and retrieval contracts."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import math
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .evidence import build_invocation_evidence
from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .provider import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)
from .registry import canonical_digest

RETRIEVAL_SCHEMA_VERSION = 1
EMBEDDING_GEMMA_PROVIDER_ID = "local-embeddinggemma"
DEFAULT_EMBEDDING_GEMMA_MODEL = "google/embeddinggemma-300m"
MAX_VECTOR_DIMENSION = 4_096
MAX_SOURCE_CHARS = 100_000
MAX_CHUNK_CHARS = 2_000
MAX_RETRIEVAL_RESULTS = 50
_ZERO_HASH = "0" * 64
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^retrieval-source-[0-9a-f]{64}$")
_CHUNK_ID = re.compile(r"^retrieval-chunk-[0-9a-f]{64}$")
_EMBEDDING_ID = re.compile(r"^embedding-[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^analysis-artifact-[0-9a-f]{64}$")
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SENSITIVE = (
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "private_key",
    "private key",
    "password=",
    "secret=",
    "token=",
)
_EXECUTABLE = ("#!/bin/", "subprocess.", "os.system(", "eval(", "exec(")


class RetrievalValidationError(ValueError):
    """Governed retrieval input or output failed closed."""


class RetrievalStoreError(RuntimeError):
    """Durable retrieval persistence failed safely."""


class RetrievalStoreCorruptionError(RetrievalStoreError):
    """The retrieval hash chain or record schema is invalid."""


class RetrievalStoreConflictError(RetrievalStoreError):
    """An immutable retrieval identity already exists."""


class RetrievalSourceType(str, Enum):
    GOVERNED_NEWS_EVIDENCE = "governed_news_evidence"
    SEC_FILING_EXCERPT = "sec_filing_excerpt"
    EARNINGS_TRANSCRIPT_EXCERPT = "earnings_transcript_excerpt"
    COMPANY_ANNOUNCEMENT = "company_announcement"
    ANALYST_NOTE_EXCERPT = "analyst_note_excerpt"
    PROPOSAL_EVIDENCE = "proposal_evidence"
    RESEARCH_ARTIFACT = "research_artifact"
    AUDIT_EVIDENCE = "audit_evidence"
    OPERATOR_APPROVED_INTERNAL_NOTE = "operator_approved_internal_note"
    SANITIZED_AI_ANALYSIS_ARTIFACT = "sanitized_ai_analysis_artifact"


class FreshnessRequirement(str, Enum):
    ANY = "any"
    CURRENT_ONLY = "current_only"


EMBEDDING_GEMMA_RESPONSIBILITIES = frozenset(
    {
        Responsibility.RESEARCH_RETRIEVAL,
        Responsibility.EVIDENCE_RETRIEVAL,
        Responsibility.PROPOSAL_CONTEXT,
        Responsibility.AUDIT_CONTEXT,
        Responsibility.MARKET_CONTEXT,
        Responsibility.ORCHESTRATION_SUPPORT,
    }
)


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise RetrievalValidationError(f"{name} must be a boolean")


def _env_int(source: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = default if source.get(name) is None else int(source[name])
    except ValueError as error:
        raise RetrievalValidationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RetrievalValidationError(f"{name} is outside its governed bound")
    return value


def _env_float(
    source: Mapping[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    try:
        value = default if source.get(name) is None else float(source[name])
    except ValueError as error:
        raise RetrievalValidationError(f"{name} must be numeric") from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise RetrievalValidationError(f"{name} is outside its governed bound")
    return value


@dataclass(frozen=True, slots=True)
class EmbeddingGemmaConfig:
    enabled: bool = False
    model: str = DEFAULT_EMBEDDING_GEMMA_MODEL
    model_version: str = "local-unverified"
    device: str = "cpu"
    timeout_ms: int = 15_000
    max_input_chars: int = 8_000
    max_batch_size: int = 8
    vector_dimension: int = 768
    local_files_only: bool = True
    retrieval_max_results: int = 10
    retrieval_min_score: float = 0.2

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 1_024
            or any(marker in self.model.lower() for marker in _SENSITIVE)
        ):
            raise RetrievalValidationError("EmbeddingGemma model source is invalid")
        validate_identifier(self.model_version, "EmbeddingGemma model_version")
        if self.device not in {"cpu", "mps", "cuda"}:
            raise RetrievalValidationError("EmbeddingGemma device is unsupported")
        if not 100 <= self.timeout_ms <= 300_000:
            raise RetrievalValidationError("EmbeddingGemma timeout is invalid")
        if not 256 <= self.max_input_chars <= MAX_SOURCE_CHARS:
            raise RetrievalValidationError("EmbeddingGemma input bound is invalid")
        if not 1 <= self.max_batch_size <= 32:
            raise RetrievalValidationError("EmbeddingGemma batch bound is invalid")
        if not 2 <= self.vector_dimension <= MAX_VECTOR_DIMENSION:
            raise RetrievalValidationError("EmbeddingGemma vector dimension is invalid")
        if self.local_files_only is not True:
            raise RetrievalValidationError("EmbeddingGemma must use local files only")
        if not 1 <= self.retrieval_max_results <= MAX_RETRIEVAL_RESULTS:
            raise RetrievalValidationError("retrieval result bound is invalid")
        if not 0 <= self.retrieval_min_score <= 1:
            raise RetrievalValidationError("retrieval minimum score is invalid")

    @property
    def model_id(self) -> str:
        if self.model == DEFAULT_EMBEDDING_GEMMA_MODEL:
            return "google.embeddinggemma-300m"
        return f"embeddinggemma-local-{canonical_digest(self.model)[:16]}"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> EmbeddingGemmaConfig:
        source = os.environ if environment is None else environment
        return cls(
            enabled=_env_bool(source, "SIGIL_AI_EMBEDDING_GEMMA_ENABLED", False),
            model=source.get("SIGIL_AI_EMBEDDING_GEMMA_MODEL", DEFAULT_EMBEDDING_GEMMA_MODEL),
            model_version=source.get("SIGIL_AI_EMBEDDING_GEMMA_MODEL_VERSION", "local-unverified"),
            device=source.get("SIGIL_AI_EMBEDDING_GEMMA_DEVICE", "cpu"),
            timeout_ms=_env_int(
                source, "SIGIL_AI_EMBEDDING_GEMMA_TIMEOUT_MS", 15_000, 100, 300_000
            ),
            max_input_chars=_env_int(
                source, "SIGIL_AI_EMBEDDING_GEMMA_MAX_INPUT_CHARS", 8_000, 256, MAX_SOURCE_CHARS
            ),
            max_batch_size=_env_int(source, "SIGIL_AI_EMBEDDING_GEMMA_MAX_BATCH_SIZE", 8, 1, 32),
            vector_dimension=_env_int(
                source,
                "SIGIL_AI_EMBEDDING_GEMMA_VECTOR_DIMENSION",
                768,
                2,
                MAX_VECTOR_DIMENSION,
            ),
            local_files_only=_env_bool(source, "SIGIL_AI_EMBEDDING_GEMMA_LOCAL_FILES_ONLY", True),
            retrieval_max_results=_env_int(
                source, "SIGIL_AI_RETRIEVAL_MAX_RESULTS", 10, 1, MAX_RETRIEVAL_RESULTS
            ),
            retrieval_min_score=_env_float(source, "SIGIL_AI_RETRIEVAL_MIN_SCORE", 0.2, 0, 1),
        )


@dataclass(frozen=True, slots=True)
class GovernedRetrievalSource:
    source_id: str
    source_type: RetrievalSourceType
    source_identity: str
    source_version: str
    source_digest: str
    corpus_id: str
    created_at: str
    observed_at: str
    stale_after: str | None
    privacy_classification: PrivacyTier
    trust_classification: TrustTier
    language: str
    content_length: int
    chunk_count: int
    supersedes_source_id: str | None
    content: str | None
    schema_version: int = RETRIEVAL_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema_version != RETRIEVAL_SCHEMA_VERSION
            or _SOURCE_ID.fullmatch(self.source_id) is None
        ):
            raise RetrievalValidationError("retrieval source identity is invalid")
        if not isinstance(self.source_type, RetrievalSourceType):
            raise RetrievalValidationError("retrieval source type is unsupported")
        if _SAFE_IDENTITY.fullmatch(self.source_identity) is None:
            raise RetrievalValidationError("retrieval source reference is invalid")
        validate_identifier(self.source_version, "source_version")
        validate_identifier(self.corpus_id, "corpus_id")
        if _SHA256.fullmatch(self.source_digest) is None:
            raise RetrievalValidationError("retrieval source digest is invalid")
        if self.language != "en":
            raise RetrievalValidationError("retrieval supports governed English sources only")
        if self.content is not None and (
            not self.content.strip() or len(self.content) > MAX_SOURCE_CHARS
        ):
            raise RetrievalValidationError("retrieval source content is invalid")
        if self.content_length < 1 or self.chunk_count < 1:
            raise RetrievalValidationError("retrieval source counts are invalid")
        if self.content is not None:
            if self.content_length != len(self.content):
                raise RetrievalValidationError("retrieval source length mismatch")
            if f"sha256:{canonical_digest(self.content)}" != self.source_digest:
                raise RetrievalValidationError("retrieval source digest mismatch")
            lowered = self.content.lower()
            if any(marker in lowered for marker in _SENSITIVE):
                raise RetrievalValidationError("retrieval source contains credential material")
            if any(marker in lowered for marker in _EXECUTABLE):
                raise RetrievalValidationError("retrieval source contains executable content")
        if (
            self.supersedes_source_id is not None
            and _SOURCE_ID.fullmatch(self.supersedes_source_id) is None
        ):
            raise RetrievalValidationError("superseded source identity is invalid")
        if self.paper_only is not True or self.broker_submission is not False:
            raise RetrievalValidationError("retrieval source cannot carry execution authority")


def build_retrieval_source(**values: object) -> GovernedRetrievalSource:
    identity = {key: value for key, value in values.items() if key not in {"source_id", "content"}}
    source_id = f"retrieval-source-{canonical_digest(identity)}"
    return GovernedRetrievalSource(source_id=source_id, **values)


def _chunk_texts(content: str, maximum_characters: int) -> tuple[str, ...]:
    words = content.split()
    texts: list[str] = []
    current: list[str] = []
    for word in words:
        if len(word) > maximum_characters:
            raise RetrievalValidationError("retrieval source contains an oversized token")
        candidate = " ".join((*current, word))
        if current and len(candidate) > maximum_characters:
            texts.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        texts.append(" ".join(current))
    return tuple(texts)


def create_retrieval_source(
    *, content: str, maximum_chunk_characters: int = 1_000, **values: object
) -> GovernedRetrievalSource:
    if not 64 <= maximum_chunk_characters <= MAX_CHUNK_CHARS:
        raise RetrievalValidationError("retrieval chunk size is invalid")
    texts = _chunk_texts(content, maximum_chunk_characters)
    return build_retrieval_source(
        **values,
        content=content,
        content_length=len(content),
        chunk_count=len(texts),
        source_digest=f"sha256:{canonical_digest(content)}",
    )


@dataclass(frozen=True, slots=True)
class GovernedRetrievalChunk:
    chunk_id: str
    source_id: str
    corpus_id: str
    chunk_index: int
    chunk_digest: str
    content_digest: str
    text: str
    character_count: int
    observed_at: str
    stale_after: str | None
    privacy_classification: PrivacyTier
    trust_classification: TrustTier
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            _CHUNK_ID.fullmatch(self.chunk_id) is None
            or _SOURCE_ID.fullmatch(self.source_id) is None
        ):
            raise RetrievalValidationError("retrieval chunk identity is invalid")
        if self.chunk_index < 0 or not self.text.strip() or len(self.text) > MAX_CHUNK_CHARS:
            raise RetrievalValidationError("retrieval chunk content is invalid")
        if self.character_count != len(self.text):
            raise RetrievalValidationError("retrieval chunk length mismatch")
        digest = f"sha256:{canonical_digest(self.text)}"
        if self.content_digest != digest or _SHA256.fullmatch(self.chunk_digest) is None:
            raise RetrievalValidationError("retrieval chunk digest mismatch")
        if self.paper_only is not True or self.broker_submission is not False:
            raise RetrievalValidationError("retrieval chunk cannot carry execution authority")


def deterministic_chunks(
    source: GovernedRetrievalSource, *, maximum_characters: int = 1_000
) -> tuple[GovernedRetrievalChunk, ...]:
    if not 64 <= maximum_characters <= MAX_CHUNK_CHARS:
        raise RetrievalValidationError("retrieval chunk size is invalid")
    if source.content is None:
        raise RetrievalValidationError("retrieval source content is unavailable for chunking")
    texts = _chunk_texts(source.content, maximum_characters)
    if not texts or len(texts) != source.chunk_count:
        raise RetrievalValidationError("retrieval source chunk count mismatch")
    chunks = []
    for index, text in enumerate(texts):
        content_digest = f"sha256:{canonical_digest(text)}"
        chunk_digest = f"sha256:{canonical_digest({'source': source.source_id, 'index': index, 'content': content_digest})}"
        chunk_id = f"retrieval-chunk-{canonical_digest({'chunk_digest': chunk_digest})}"
        chunks.append(
            GovernedRetrievalChunk(
                chunk_id=chunk_id,
                source_id=source.source_id,
                corpus_id=source.corpus_id,
                chunk_index=index,
                chunk_digest=chunk_digest,
                content_digest=content_digest,
                text=text,
                character_count=len(text),
                observed_at=source.observed_at,
                stale_after=source.stale_after,
                privacy_classification=source.privacy_classification,
                trust_classification=source.trust_classification,
            )
        )
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise RetrievalValidationError("duplicate retrieval chunk identity")
    return tuple(chunks)


def normalized_vector(value: object, dimension: int) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != dimension
    ):
        raise RetrievalValidationError("embedding vector dimension mismatch")
    vector = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise RetrievalValidationError("embedding vector must be numeric")
        number = float(item)
        if not math.isfinite(number):
            raise RetrievalValidationError("embedding vector must contain finite values")
        vector.append(number)
    magnitude = math.sqrt(sum(item * item for item in vector))
    if magnitude <= 0:
        raise RetrievalValidationError("embedding vector cannot be empty")
    normalized = tuple(item / magnitude for item in vector)
    if not math.isclose(sum(item * item for item in normalized), 1.0, abs_tol=1e-6):
        raise RetrievalValidationError("embedding normalization failed")
    return normalized


@dataclass(frozen=True, slots=True)
class GovernedEmbeddingArtifact:
    embedding_id: str
    provider_id: str
    model_id: str
    model_version: str
    source_id: str
    chunk_id: str
    source_digest: str
    chunk_digest: str
    vector_dimension: int
    vector_digest: str
    vector: tuple[float, ...]
    normalized: bool
    created_at: str
    registry_revision: str
    invocation_evidence_id: str
    schema_version: int = RETRIEVAL_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    def __post_init__(self) -> None:
        if _EMBEDDING_ID.fullmatch(self.embedding_id) is None:
            raise RetrievalValidationError("embedding identity is invalid")
        for value in (
            self.source_digest,
            self.chunk_digest,
            self.vector_digest,
            self.registry_revision,
            self.invocation_evidence_id,
        ):
            if _SHA256.fullmatch(value) is None:
                raise RetrievalValidationError("embedding evidence identity is invalid")
        vector = normalized_vector(self.vector, self.vector_dimension)
        if (
            any(
                not math.isclose(left, right, abs_tol=1e-12)
                for left, right in zip(vector, self.vector, strict=True)
            )
            or self.normalized is not True
        ):
            raise RetrievalValidationError("embedding vector must be normalized")
        if self.vector_digest != f"sha256:{canonical_digest(list(self.vector))}":
            raise RetrievalValidationError("embedding vector digest mismatch")
        if any(
            (
                self.paper_only is not True,
                self.execution_authorized is not False,
                self.broker_submission is not False,
                self.portfolio_mutation is not False,
                self.approval_authority is not False,
            )
        ):
            raise RetrievalValidationError("embedding cannot carry execution authority")


def build_embedding_artifact(**values: object) -> GovernedEmbeddingArtifact:
    identity = {
        key: value for key, value in values.items() if key not in {"embedding_id", "vector"}
    }
    return GovernedEmbeddingArtifact(
        embedding_id=f"embedding-{canonical_digest(identity)}", **values
    )


@dataclass(frozen=True, slots=True)
class GovernedRetrievalRequest:
    request_id: str
    task_correlation_id: str
    responsibility: Responsibility
    query_digest: str
    query_text: str
    corpus_ids: tuple[str, ...]
    source_type_filters: tuple[RetrievalSourceType, ...]
    privacy_requirement: PrivacyTier
    minimum_trust_tier: TrustTier
    freshness_requirement: FreshnessRequirement
    maximum_results: int
    minimum_score: float
    fallback_permission: bool
    requested_at: str
    evidence_context_digests: tuple[str, ...]
    capability: Capability = Capability.SEMANTIC_RETRIEVAL
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if self.capability != Capability.SEMANTIC_RETRIEVAL:
            raise RetrievalValidationError("retrieval capability mismatch")
        if self.responsibility not in EMBEDDING_GEMMA_RESPONSIBILITIES:
            raise RetrievalValidationError("retrieval responsibility is unsupported")
        if self.responsibility in PROHIBITED_RESPONSIBILITIES:
            raise RetrievalValidationError("retrieval responsibility is prohibited")
        if not self.query_text.strip() or len(self.query_text) > 8_000:
            raise RetrievalValidationError("retrieval query is invalid")
        lowered = self.query_text.lower()
        if any(
            marker in lowered
            for marker in (*_SENSITIVE, *_EXECUTABLE, "file://", "http://", "https://")
        ):
            raise RetrievalValidationError("retrieval query contains prohibited material")
        if self.query_digest != f"sha256:{canonical_digest(self.query_text)}":
            raise RetrievalValidationError("retrieval query digest mismatch")
        if not self.corpus_ids or len(self.corpus_ids) > 32:
            raise RetrievalValidationError("retrieval corpus filter is invalid")
        for corpus_id in self.corpus_ids:
            validate_identifier(corpus_id, "corpus_id")
        if len(self.source_type_filters) > len(RetrievalSourceType) or any(
            not isinstance(item, RetrievalSourceType) for item in self.source_type_filters
        ):
            raise RetrievalValidationError("retrieval source filter is invalid")
        if not 1 <= self.maximum_results <= MAX_RETRIEVAL_RESULTS:
            raise RetrievalValidationError("retrieval result limit is invalid")
        if not 0 <= self.minimum_score <= 1:
            raise RetrievalValidationError("retrieval minimum score is invalid")
        if not self.evidence_context_digests or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_context_digests
        ):
            raise RetrievalValidationError("retrieval evidence context is invalid")
        if self.paper_only is not True or self.broker_submission is not False:
            raise RetrievalValidationError("retrieval request cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class GovernedIndexingRequest:
    request_id: str
    task_correlation_id: str
    source: GovernedRetrievalSource
    chunk_maximum_characters: int
    requested_at: str
    fallback_permission: bool = False
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if not 64 <= self.chunk_maximum_characters <= MAX_CHUNK_CHARS:
            raise RetrievalValidationError("indexing chunk bound is invalid")
        if self.paper_only is not True or self.broker_submission is not False:
            raise RetrievalValidationError("indexing cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class GovernedIndexingResponse:
    request_id: str
    source_id: str | None
    chunk_ids: tuple[str, ...]
    embedding_ids: tuple[str, ...]
    routing_evidence_id: str | None
    invocation_evidence_ids: tuple[str, ...]
    failure_classification: str | None
    limitations: tuple[str, ...]
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    @property
    def succeeded(self) -> bool:
        return self.source_id is not None and self.failure_classification is None


@dataclass(frozen=True, slots=True)
class RetrievalResultItem:
    rank: int
    score: float
    source_id: str
    source_identity: str
    source_type: RetrievalSourceType
    source_digest: str
    chunk_id: str
    chunk_digest: str
    observed_at: str
    freshness_state: str
    privacy_classification: PrivacyTier
    trust_classification: TrustTier
    excerpt: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.rank < 1 or not math.isfinite(self.score) or not -1.0 <= self.score <= 1.0:
            raise RetrievalValidationError("retrieval result rank or score is invalid")
        if (
            _SOURCE_ID.fullmatch(self.source_id) is None
            or _SAFE_IDENTITY.fullmatch(self.source_identity) is None
            or _CHUNK_ID.fullmatch(self.chunk_id) is None
            or _SHA256.fullmatch(self.source_digest) is None
            or _SHA256.fullmatch(self.chunk_digest) is None
        ):
            raise RetrievalValidationError("retrieval result identity is invalid")
        if not isinstance(self.source_type, RetrievalSourceType):
            raise RetrievalValidationError("retrieval result source type is invalid")
        if not self.excerpt.strip() or len(self.excerpt) > MAX_CHUNK_CHARS:
            raise RetrievalValidationError("retrieval result excerpt is invalid")
        if self.freshness_state not in {"current", "stale", "unbounded"}:
            raise RetrievalValidationError("retrieval result freshness is invalid")
        if not self.evidence_references or any(
            not reference.strip() for reference in self.evidence_references
        ):
            raise RetrievalValidationError("retrieval result evidence is invalid")


@dataclass(frozen=True, slots=True)
class GovernedRetrievalArtifact:
    artifact_id: str
    request_id: str
    task_correlation_id: str
    provider_id: str
    model_id: str
    model_version: str
    capability: Capability
    responsibility: Responsibility
    created_at: str
    routing_evidence_id: str
    invocation_evidence_id: str
    input_digest: str
    output_digest: str
    corpus_ids: tuple[str, ...]
    results: tuple[RetrievalResultItem, ...]
    limitations: tuple[str, ...]
    stale_after: str | None = None
    schema_version: int = RETRIEVAL_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    @property
    def confidence(self) -> float | None:
        return None if not self.results else self.results[0].score

    def __post_init__(self) -> None:
        if _ARTIFACT_ID.fullmatch(self.artifact_id) is None:
            raise RetrievalValidationError("retrieval artifact identity is invalid")
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        validate_identifier(self.provider_id, "provider_id")
        validate_identifier(self.model_id, "model_id")
        validate_identifier(self.model_version, "model_version")
        if any(
            _SHA256.fullmatch(value) is None
            for value in (
                self.routing_evidence_id,
                self.invocation_evidence_id,
                self.input_digest,
                self.output_digest,
            )
        ):
            raise RetrievalValidationError("retrieval artifact evidence identity is invalid")
        if self.capability != Capability.SEMANTIC_RETRIEVAL:
            raise RetrievalValidationError("retrieval artifact capability mismatch")
        if not 0 <= len(self.results) <= MAX_RETRIEVAL_RESULTS:
            raise RetrievalValidationError("retrieval artifact result bound is invalid")
        if tuple(item.rank for item in self.results) != tuple(range(1, len(self.results) + 1)):
            raise RetrievalValidationError("retrieval artifact ranks are invalid")
        if any(
            (
                self.paper_only is not True,
                self.execution_authorized is not False,
                self.broker_submission is not False,
                self.portfolio_mutation is not False,
                self.approval_authority is not False,
            )
        ):
            raise RetrievalValidationError("retrieval artifact cannot carry execution authority")


def build_retrieval_artifact(**values: object) -> GovernedRetrievalArtifact:
    identity = {**values, "artifact_id": "pending", "schema_version": RETRIEVAL_SCHEMA_VERSION}
    identity_payload = {
        **identity,
        "results": [asdict(item) for item in identity["results"]],
    }
    return GovernedRetrievalArtifact(
        **{
            **identity,
            "artifact_id": f"analysis-artifact-{canonical_digest(identity_payload)}",
        }
    )


class EmbeddingRuntime(Protocol):
    def embed(self, *, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SentenceTransformersEmbeddingRuntime:
    def __init__(self, config: EmbeddingGemmaConfig) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(
            config.model,
            device=config.device,
            local_files_only=True,
        )

    def embed(self, *, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._model.encode(list(texts), normalize_embeddings=True).tolist()


class LocalEmbeddingGemmaProvider:
    input_contract = "application/json;schema=sigil.ai.input.embedding.v1"
    output_contract = "application/json;schema=sigil.ai.output.embedding.v1"
    capabilities = frozenset({Capability.EMBEDDINGS, Capability.SEMANTIC_RETRIEVAL})
    model_family = "embeddinggemma"

    def __init__(
        self, config: EmbeddingGemmaConfig, runtime: EmbeddingRuntime | None = None
    ) -> None:
        self.config = config
        self.model_id = config.model_id
        self.model_version = config.model_version
        self.request_timeout_ms = config.timeout_ms
        self._runtime = runtime
        dependencies = importlib.util.find_spec("sentence_transformers") is not None
        health = (
            ProviderHealth.HEALTHY
            if config.enabled and (runtime or dependencies)
            else ProviderHealth.UNAVAILABLE
        )
        self.identity = ProviderIdentity(
            EMBEDDING_GEMMA_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            health=health,
            enabled=config.enabled,
            metadata=(("runtime", "local-files-only"),),
        )

    def registration(self) -> ModelRegistration:
        return ModelRegistration(
            model_id=self.model_id,
            provider_id=self.identity.provider_id,
            family=self.model_family,
            version=self.model_version,
            capabilities=self.capabilities,
            execution_location=ExecutionLocation.LOCAL,
            context_limit=self.config.max_input_chars,
            supported_input_types=frozenset({InputType.TEXT}),
            structured_output=True,
            cost_class=CostClass.FREE,
            trust_tier=TrustTier.TRUSTED,
            privacy_tier=PrivacyTier.LOCAL_ONLY,
            health=self.identity.health,
            enabled=self.config.enabled,
            allowed_responsibilities=EMBEDDING_GEMMA_RESPONSIBILITIES,
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        payload = invocation.input_payload
        texts = payload.get("texts")
        failure: ProviderFailure | None = None
        output: Mapping[str, object] | None = None
        if not self.config.enabled or self.identity.health != ProviderHealth.HEALTHY:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Local EmbeddingGemma is unavailable.", True
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "EmbeddingGemma model identity mismatch.",
                False,
            )
        elif invocation.capability not in self.capabilities:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH,
                "EmbeddingGemma capability mismatch.",
                False,
            )
        elif (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= self.config.max_batch_size
            or any(
                not isinstance(text, str)
                or not text.strip()
                or len(text) > self.config.max_input_chars
                for text in texts
            )
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "EmbeddingGemma input exceeded its governed bound.",
                False,
            )
        else:
            try:
                runtime = self._runtime or SentenceTransformersEmbeddingRuntime(self.config)
                self._runtime = runtime
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(runtime.embed, texts=texts)
                    vectors = future.result(timeout=invocation.timeout_ms / 1000)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                normalized = tuple(
                    normalized_vector(vector, self.config.vector_dimension) for vector in vectors
                )
                if len(normalized) != len(texts):
                    raise RetrievalValidationError("EmbeddingGemma batch size mismatch")
                output = {
                    "schema_version": 1,
                    "model_id": self.model_id,
                    "model_version": self.model_version,
                    "vector_dimension": self.config.vector_dimension,
                    "vectors": [list(vector) for vector in normalized],
                    "normalized": True,
                    "paper_only": True,
                    "execution_authorized": False,
                    "broker_submission": False,
                    "portfolio_mutation": False,
                    "approval_authority": False,
                }
            except FutureTimeoutError:
                failure = ProviderFailure(
                    ProviderFailureClass.TIMEOUT, "Local EmbeddingGemma timed out.", True
                )
            except (ImportError, OSError, RuntimeError):
                failure = ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE,
                    "Local EmbeddingGemma is unavailable.",
                    True,
                )
            except (RetrievalValidationError, TypeError, ValueError):
                failure = ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Local EmbeddingGemma returned malformed vectors.",
                    False,
                )
        evidence_payload = {key: value for key, value in payload.items() if key != "texts"}
        evidence_output = None
        if output is not None:
            evidence_output = {key: value for key, value in output.items() if key != "vectors"}
            evidence_output["vector_digests"] = [
                f"sha256:{canonical_digest(vector)}" for vector in output["vectors"]
            ]
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=self.identity.execution_location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload=evidence_payload,
            output_payload=evidence_output,
            provider_metadata=(("runtime", "local-embeddinggemma-v1"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)


def validate_embedding_output(
    output: object,
    *,
    model_id: str,
    model_version: str,
    vector_dimension: int,
    expected_count: int,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(output, Mapping):
        raise RetrievalValidationError("embedding output must be an object")
    required = {
        "schema_version",
        "model_id",
        "model_version",
        "vector_dimension",
        "vectors",
        "normalized",
        "paper_only",
        "execution_authorized",
        "broker_submission",
        "portfolio_mutation",
        "approval_authority",
    }
    if set(output) != required or output.get("schema_version") != 1:
        raise RetrievalValidationError("embedding output schema is invalid")
    if output["model_id"] != model_id or output["model_version"] != model_version:
        raise RetrievalValidationError("embedding output model mismatch")
    if output["vector_dimension"] != vector_dimension or output["normalized"] is not True:
        raise RetrievalValidationError("embedding output dimension mismatch")
    vectors = output["vectors"]
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise RetrievalValidationError("embedding output batch mismatch")
    authority = {
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "approval_authority": False,
    }
    if any(output[name] is not expected for name, expected in authority.items()):
        raise RetrievalValidationError("embedding output cannot carry execution authority")
    return tuple(normalized_vector(vector, vector_dimension) for vector in vectors)


class DurableRetrievalStore:
    """Hash-chained local vector persistence, isolated from execution state."""

    def __init__(self, state_root: Path) -> None:
        if not isinstance(state_root, Path) or not state_root.is_absolute():
            raise RetrievalStoreError("retrieval state root must be absolute")
        if state_root.is_symlink() or not state_root.exists() or not state_root.is_dir():
            raise RetrievalStoreError("retrieval state root is unsafe")
        self.directory = state_root / "governed-ai-retrieval-v1"
        self.path = self.directory / "index.jsonl"
        self.lock_path = self.directory / "index.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise RetrievalStoreError("retrieval paths cannot use symlinks")
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def append_index(
        self,
        source: GovernedRetrievalSource,
        chunks: Sequence[GovernedRetrievalChunk],
        embeddings: Sequence[GovernedEmbeddingArtifact],
    ) -> None:
        if len(chunks) != len(embeddings) or not chunks:
            raise RetrievalStoreError("retrieval index bundle is incomplete")
        with self._locked():
            existing = self._read_unlocked(recover_truncated_tail=True)
            if any(item.source_id == source.source_id for item in existing[0]):
                raise RetrievalStoreConflictError("duplicate retrieval source identity")
            if source.supersedes_source_id is not None and not any(
                item.source_id == source.supersedes_source_id for item in existing[0]
            ):
                raise RetrievalStoreConflictError("superseded retrieval source is unavailable")
            existing_chunks = {item.chunk_id for item in existing[1]}
            existing_embeddings = {item.embedding_id for item in existing[2]}
            if existing_chunks & {item.chunk_id for item in chunks}:
                raise RetrievalStoreConflictError("duplicate retrieval chunk identity")
            if existing_embeddings & {item.embedding_id for item in embeddings}:
                raise RetrievalStoreConflictError("duplicate embedding identity")
            if any(
                item.model_id != embeddings[0].model_id
                or item.model_version != embeddings[0].model_version
                or item.vector_dimension != embeddings[0].vector_dimension
                for item in existing[2]
            ):
                raise RetrievalStoreConflictError("retrieval model or dimension is incompatible")
            previous = self._last_hash if existing[0] else _ZERO_HASH
            record = {
                "schema_version": 1,
                "sequence": len(existing[0]) + 1,
                "previous_entry_hash": previous,
                "source": self._source_payload(source),
                "chunks": [self._chunk_payload(item) for item in chunks],
                "embeddings": [asdict(item) for item in embeddings],
                "entry_hash": "",
            }
            record["entry_hash"] = canonical_digest(
                {key: value for key, value in record.items() if key != "entry_hash"}
            )
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            try:
                offset = 0
                while offset < len(encoded):
                    written = os.write(descriptor, encoded[offset:])
                    if written < 1:
                        raise RetrievalStoreError("retrieval store write did not progress")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()

    def read_index(
        self, *, recover_truncated_tail: bool = True
    ) -> tuple[
        tuple[GovernedRetrievalSource, ...],
        tuple[GovernedRetrievalChunk, ...],
        tuple[GovernedEmbeddingArtifact, ...],
    ]:
        with self._locked():
            return self._read_unlocked(recover_truncated_tail=recover_truncated_tail)

    def _read_unlocked(self, *, recover_truncated_tail: bool):
        if not self.path.exists():
            self._last_hash = _ZERO_HASH
            return (), (), ()
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover_truncated_tail:
                raise RetrievalStoreCorruptionError("retrieval store has a truncated tail")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.ftruncate(descriptor, boundary)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory()
            raw = raw[:boundary]
        sources = []
        chunks = []
        embeddings = []
        previous = _ZERO_HASH
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                record = json.loads(line)
                expected = canonical_digest(
                    {key: value for key, value in record.items() if key != "entry_hash"}
                )
                if (
                    record["schema_version"] != 1
                    or record["sequence"] != number
                    or record["previous_entry_hash"] != previous
                    or record["entry_hash"] != expected
                ):
                    raise RetrievalStoreCorruptionError("retrieval hash chain is invalid")
                source = self._decode_source(record["source"])
                record_chunks = tuple(self._decode_chunk(item) for item in record["chunks"])
                record_embeddings = tuple(
                    self._decode_embedding(item) for item in record["embeddings"]
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RetrievalStoreCorruptionError(f"corrupt retrieval record {number}") from error
            sources.append(source)
            chunks.extend(record_chunks)
            embeddings.extend(record_embeddings)
            previous = record["entry_hash"]
        if len({item.source_id for item in sources}) != len(sources):
            raise RetrievalStoreCorruptionError("duplicate source identity")
        if len({item.chunk_id for item in chunks}) != len(chunks):
            raise RetrievalStoreCorruptionError("duplicate chunk identity")
        if len({item.embedding_id for item in embeddings}) != len(embeddings):
            raise RetrievalStoreCorruptionError("duplicate embedding identity")
        self._last_hash = previous
        return tuple(sources), tuple(chunks), tuple(embeddings)

    def search(
        self, request: GovernedRetrievalRequest, query_vector: tuple[float, ...], *, now: str
    ) -> tuple[RetrievalResultItem, ...]:
        sources, chunks, embeddings = self.read_index(recover_truncated_tail=False)
        source_map = {item.source_id: item for item in sources}
        chunk_map = {item.chunk_id: item for item in chunks}
        candidates = []
        for embedding in embeddings:
            source = source_map[embedding.source_id]
            chunk = chunk_map[embedding.chunk_id]
            stale = source.stale_after is not None and source.stale_after < now
            if source.corpus_id not in request.corpus_ids:
                continue
            if (
                request.source_type_filters
                and source.source_type not in request.source_type_filters
            ):
                continue
            if source.privacy_classification < request.privacy_requirement:
                continue
            if source.trust_classification < request.minimum_trust_tier:
                continue
            if request.freshness_requirement == FreshnessRequirement.CURRENT_ONLY and stale:
                continue
            if len(query_vector) != embedding.vector_dimension:
                raise RetrievalStoreError("query and stored vector dimensions differ")
            score = max(
                0.0,
                min(1.0, sum(a * b for a, b in zip(query_vector, embedding.vector, strict=True))),
            )
            if score < request.minimum_score:
                continue
            candidates.append((score, source, chunk, stale))
        candidates.sort(key=lambda item: (-item[0], item[1].source_id, item[2].chunk_id))
        diversified = []
        seen_sources = set()
        deferred = []
        for candidate in candidates:
            if candidate[1].source_id in seen_sources:
                deferred.append(candidate)
            else:
                diversified.append(candidate)
                seen_sources.add(candidate[1].source_id)
        diversified.extend(deferred)
        return tuple(
            RetrievalResultItem(
                rank=index,
                score=score,
                source_id=source.source_id,
                source_identity=source.source_identity,
                source_type=source.source_type,
                source_digest=source.source_digest,
                chunk_id=chunk.chunk_id,
                chunk_digest=chunk.chunk_digest,
                observed_at=source.observed_at,
                freshness_state="stale" if stale else "current",
                privacy_classification=source.privacy_classification,
                trust_classification=source.trust_classification,
                excerpt=chunk.text[:512],
                evidence_references=(source.source_digest, chunk.chunk_digest),
            )
            for index, (score, source, chunk, stale) in enumerate(
                diversified[: request.maximum_results], 1
            )
        )

    @staticmethod
    def _source_payload(source: GovernedRetrievalSource) -> dict[str, object]:
        payload = asdict(source)
        payload.pop("content")
        payload["source_type"] = source.source_type.value
        payload["privacy_classification"] = source.privacy_classification.name
        payload["trust_classification"] = source.trust_classification.name
        return payload

    @staticmethod
    def _chunk_payload(chunk: GovernedRetrievalChunk) -> dict[str, object]:
        payload = asdict(chunk)
        payload["privacy_classification"] = chunk.privacy_classification.name
        payload["trust_classification"] = chunk.trust_classification.name
        return payload

    @staticmethod
    def _decode_source(payload: Mapping[str, object]) -> GovernedRetrievalSource:
        return GovernedRetrievalSource(
            **{
                **payload,
                "source_type": RetrievalSourceType(payload["source_type"]),
                "privacy_classification": PrivacyTier[payload["privacy_classification"]],
                "trust_classification": TrustTier[payload["trust_classification"]],
                "content": None,
            }
        )

    @staticmethod
    def _decode_chunk(payload: Mapping[str, object]) -> GovernedRetrievalChunk:
        return GovernedRetrievalChunk(
            **{
                **payload,
                "privacy_classification": PrivacyTier[payload["privacy_classification"]],
                "trust_classification": TrustTier[payload["trust_classification"]],
            }
        )

    @staticmethod
    def _decode_embedding(payload: Mapping[str, object]) -> GovernedEmbeddingArtifact:
        return GovernedEmbeddingArtifact(**{**payload, "vector": tuple(payload["vector"])})

    def _fsync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
