"""Deterministic, offline financial-document ingestion for Sigil."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import re
from typing import Iterable
from urllib.parse import urlparse


_ALLOWED_DOCUMENT_TYPES = {
    "10-K",
    "10-Q",
    "8-K",
    "earnings-transcript",
    "press-release",
    "annual-report",
    "investor-presentation",
    "other",
}
_ALLOWED_SCHEMES = {"https"}
_MAX_CONTENT_BYTES = 5_000_000
_MAX_TITLE_CHARS = 300
_MAX_SOURCE_URL_CHARS = 2_048
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_KEY_CHARS = 80
_MAX_METADATA_VALUE_CHARS = 500
_MIN_CHUNK_CHARS = 200
_MAX_CHUNK_CHARS = 4_000
_MIN_OVERLAP_CHARS = 0
_MAX_OVERLAP_CHARS = 1_000
_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class DocumentIngestionError(ValueError):
    """Raised when a document fails governed ingestion validation."""


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Traceable source identity supplied by a governed upstream collector."""

    source_url: str
    retrieved_at: datetime
    publisher: str
    accession_number: str | None = None

    def validate(self) -> None:
        parsed = urlparse(self.source_url)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            raise DocumentIngestionError("source_url must be an absolute HTTPS URL")
        if len(self.source_url) > _MAX_SOURCE_URL_CHARS:
            raise DocumentIngestionError("source_url exceeds the maximum length")
        if not self.publisher.strip():
            raise DocumentIngestionError("publisher is required")
        if self.retrieved_at.tzinfo is None:
            raise DocumentIngestionError("retrieved_at must be timezone-aware")
        if self.retrieved_at > datetime.now(timezone.utc):
            raise DocumentIngestionError("retrieved_at cannot be in the future")
        if self.accession_number is not None and not self.accession_number.strip():
            raise DocumentIngestionError("accession_number cannot be blank")


@dataclass(frozen=True, slots=True)
class FinancialDocument:
    """Raw financial text and metadata entering the Sigil boundary."""

    issuer: str
    document_type: str
    title: str
    published_on: date
    content: str
    provenance: SourceProvenance
    metadata: tuple[tuple[str, str], ...] = ()

    def validate(self) -> None:
        if not self.issuer.strip():
            raise DocumentIngestionError("issuer is required")
        if self.document_type not in _ALLOWED_DOCUMENT_TYPES:
            raise DocumentIngestionError(f"unsupported document_type: {self.document_type}")
        if not self.title.strip():
            raise DocumentIngestionError("title is required")
        if len(self.title) > _MAX_TITLE_CHARS:
            raise DocumentIngestionError("title exceeds the maximum length")
        if self.published_on > date.today():
            raise DocumentIngestionError("published_on cannot be in the future")
        if not self.content.strip():
            raise DocumentIngestionError("content is required")
        if len(self.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise DocumentIngestionError("content exceeds the maximum size")
        if len(self.metadata) > _MAX_METADATA_ITEMS:
            raise DocumentIngestionError("too many metadata entries")
        seen: set[str] = set()
        for key, value in self.metadata:
            normalized_key = key.strip()
            if not normalized_key or not value.strip():
                raise DocumentIngestionError("metadata keys and values cannot be blank")
            if normalized_key in seen:
                raise DocumentIngestionError(f"duplicate metadata key: {normalized_key}")
            if len(normalized_key) > _MAX_METADATA_KEY_CHARS:
                raise DocumentIngestionError("metadata key exceeds the maximum length")
            if len(value) > _MAX_METADATA_VALUE_CHARS:
                raise DocumentIngestionError("metadata value exceeds the maximum length")
            seen.add(normalized_key)
        self.provenance.validate()


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """Stable chunk suitable for retrieval and downstream analysis."""

    chunk_id: str
    document_id: str
    index: int
    text: str
    start_char: int
    end_char: int
    sha256: str


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    """Validated evidence-ready representation emitted by ingestion."""

    schema_version: int
    document_id: str
    content_sha256: str
    normalized_content: str
    issuer: str
    document_type: str
    title: str
    published_on: date
    provenance: SourceProvenance
    metadata: tuple[tuple[str, str], ...]
    chunks: tuple[DocumentChunk, ...]

    def evidence_manifest(self) -> dict[str, object]:
        """Return a serializable manifest for Hermes evidence storage."""

        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "content_sha256": self.content_sha256,
            "issuer": self.issuer,
            "document_type": self.document_type,
            "title": self.title,
            "published_on": self.published_on.isoformat(),
            "source_url": self.provenance.source_url,
            "publisher": self.provenance.publisher,
            "retrieved_at": self.provenance.retrieved_at.isoformat(),
            "accession_number": self.provenance.accession_number,
            "metadata": dict(self.metadata),
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "index": chunk.index,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "sha256": chunk.sha256,
                }
                for chunk in self.chunks
            ],
        }


class FinancialDocumentIngestor:
    """Validate, normalize, fingerprint, deduplicate, and chunk documents."""

    def __init__(self, *, chunk_chars: int = 1_500, overlap_chars: int = 200) -> None:
        if not _MIN_CHUNK_CHARS <= chunk_chars <= _MAX_CHUNK_CHARS:
            raise DocumentIngestionError("chunk_chars is outside the governed range")
        if not _MIN_OVERLAP_CHARS <= overlap_chars <= _MAX_OVERLAP_CHARS:
            raise DocumentIngestionError("overlap_chars is outside the governed range")
        if overlap_chars >= chunk_chars:
            raise DocumentIngestionError("overlap_chars must be smaller than chunk_chars")
        self._chunk_chars = chunk_chars
        self._overlap_chars = overlap_chars
        self._seen_hashes: set[str] = set()

    @staticmethod
    def normalize_content(content: str) -> str:
        """Normalize line endings and incidental whitespace without rewriting meaning."""

        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in normalized.splitlines())
        return _BLANK_LINES_RE.sub("\n\n", normalized).strip()

    def ingest(self, document: FinancialDocument) -> IngestedDocument:
        document.validate()
        normalized = self.normalize_content(document.content)
        content_hash = sha256(normalized.encode("utf-8")).hexdigest()

        if content_hash in self._seen_hashes:
            raise DocumentIngestionError(f"duplicate document content: {content_hash}")

        identity_material = "|".join(
            (
                document.issuer.strip(),
                document.document_type,
                document.published_on.isoformat(),
                document.provenance.source_url,
                content_hash,
            )
        )
        document_id = f"sigil-doc-{sha256(identity_material.encode('utf-8')).hexdigest()[:24]}"
        chunks = tuple(self._chunk(document_id, normalized))

        self._seen_hashes.add(content_hash)
        return IngestedDocument(
            schema_version=1,
            document_id=document_id,
            content_sha256=content_hash,
            normalized_content=normalized,
            issuer=document.issuer.strip(),
            document_type=document.document_type,
            title=document.title.strip(),
            published_on=document.published_on,
            provenance=document.provenance,
            metadata=tuple((key.strip(), value.strip()) for key, value in document.metadata),
            chunks=chunks,
        )

    def ingest_many(self, documents: Iterable[FinancialDocument]) -> tuple[IngestedDocument, ...]:
        return tuple(self.ingest(document) for document in documents)

    def _chunk(self, document_id: str, content: str) -> Iterable[DocumentChunk]:
        start = 0
        index = 0

        while start < len(content):
            hard_end = min(start + self._chunk_chars, len(content))
            end = hard_end

            if hard_end < len(content):
                paragraph_break = content.rfind("\n\n", start, hard_end)
                sentence_break = max(
                    content.rfind(". ", start, hard_end),
                    content.rfind("? ", start, hard_end),
                    content.rfind("! ", start, hard_end),
                )
                candidate = max(paragraph_break + 2, sentence_break + 2)
                minimum_useful_end = start + max(1, self._chunk_chars // 2)
                if candidate >= minimum_useful_end:
                    end = candidate

            text = content[start:end].strip()
            if not text:
                break

            chunk_hash = sha256(text.encode("utf-8")).hexdigest()
            chunk_id = f"{document_id}-chunk-{index:04d}-{chunk_hash[:12]}"
            yield DocumentChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                index=index,
                text=text,
                start_char=start,
                end_char=end,
                sha256=chunk_hash,
            )

            if end >= len(content):
                break
            start = max(end - self._overlap_chars, start + 1)
            index += 1
