"""Deterministic, offline financial-evidence extraction for Sigil."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import math
import re
from urllib.parse import urlparse

from .ingestion import DocumentChunk, IngestedDocument, SourceProvenance


SUPPORTED_EXTRACTOR_VERSION = "sigil-financial-evidence-v1"
SUPPORTED_EVIDENCE_TYPES = frozenset(
    {
        "revenue",
        "profit",
        "loss",
        "guidance",
        "risk",
        "liquidity",
        "debt",
        "cash_flow",
        "dividend",
        "capital_expenditure",
    }
)

_EVIDENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues", "net sales"),
    "profit": ("profit", "profits", "net income", "operating income"),
    "loss": ("loss", "losses", "net loss"),
    "guidance": ("guidance", "outlook", "forecast"),
    "risk": ("risk", "risks", "uncertainty", "uncertainties"),
    "liquidity": ("liquidity", "liquid assets"),
    "debt": ("debt", "borrowings", "indebtedness"),
    "cash_flow": ("cash flow", "cash flows"),
    "dividend": ("dividend", "dividends"),
    "capital_expenditure": ("capital expenditure", "capital expenditures", "capex"),
}
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SENTENCE_RE = re.compile(r"\S(?:.*?\S)?(?:[.!?](?=\s|$)|(?=$))", re.DOTALL)
_SPACE_RE = re.compile(r"\s+")
_MAX_CONFIGURED_CHUNKS = 10_000
_MAX_CONFIGURED_RECORDS_PER_CHUNK = 100
_MAX_CONFIGURED_CLAIM_LENGTH = 4_000


class FinancialEvidenceExtractionError(ValueError):
    """Raised when governed financial-evidence extraction fails closed."""


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FinancialEvidenceExtractionError(f"{field_name} must be non-empty")


def _require_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _HEX_DIGEST_RE.fullmatch(value) is None:
        raise FinancialEvidenceExtractionError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """An exact, document-relative source span."""

    start_char: int
    end_char: int
    exact_text: str

    def __post_init__(self) -> None:
        if isinstance(self.start_char, bool) or not isinstance(self.start_char, int):
            raise FinancialEvidenceExtractionError("start_char must be an integer")
        if isinstance(self.end_char, bool) or not isinstance(self.end_char, int):
            raise FinancialEvidenceExtractionError("end_char must be an integer")
        if self.start_char < 0:
            raise FinancialEvidenceExtractionError("start_char must be non-negative")
        if self.end_char <= self.start_char:
            raise FinancialEvidenceExtractionError("end_char must be greater than start_char")
        if not isinstance(self.exact_text, str) or not self.exact_text:
            raise FinancialEvidenceExtractionError("exact_text must be non-empty")
        if self.end_char - self.start_char != len(self.exact_text):
            raise FinancialEvidenceExtractionError("span length must equal len(exact_text)")


@dataclass(frozen=True, slots=True)
class FinancialEvidenceRecord:
    """A bounded financial-domain observation with exact source evidence."""

    evidence_id: str
    document_id: str
    chunk_id: str
    issuer: str
    evidence_type: str
    claim: str
    span: EvidenceSpan
    source_url: str
    published_on: date
    content_hash: str
    confidence: float
    extractor_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "document_id",
            "chunk_id",
            "issuer",
            "evidence_type",
            "claim",
            "source_url",
            "content_hash",
            "extractor_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.evidence_type not in SUPPORTED_EVIDENCE_TYPES:
            raise FinancialEvidenceExtractionError(
                f"unsupported evidence_type: {self.evidence_type}"
            )
        if not isinstance(self.span, EvidenceSpan):
            raise FinancialEvidenceExtractionError("span must be an EvidenceSpan")
        parsed_url = urlparse(self.source_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise FinancialEvidenceExtractionError("source_url must be an absolute HTTPS URL")
        if not isinstance(self.published_on, date):
            raise FinancialEvidenceExtractionError("published_on must be a date")
        _require_digest(self.content_hash, "content_hash")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise FinancialEvidenceExtractionError("confidence must be a finite number")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise FinancialEvidenceExtractionError("confidence must be between 0 and 1")
        if self.extractor_version != SUPPORTED_EXTRACTOR_VERSION:
            raise FinancialEvidenceExtractionError(
                f"extractor_version must equal {SUPPORTED_EXTRACTOR_VERSION}"
            )


@dataclass(frozen=True, slots=True)
class EvidenceExtractionManifest:
    """Deterministic extraction result ready for governed Hermes persistence."""

    document_id: str
    extractor_version: str
    evidence_records: tuple[FinancialEvidenceRecord, ...]
    evidence_count: int
    deterministic_hash: str

    def __post_init__(self) -> None:
        _require_text(self.document_id, "document_id")
        if self.extractor_version != SUPPORTED_EXTRACTOR_VERSION:
            raise FinancialEvidenceExtractionError(
                f"extractor_version must equal {SUPPORTED_EXTRACTOR_VERSION}"
            )
        if not isinstance(self.evidence_records, tuple):
            raise FinancialEvidenceExtractionError("evidence_records must be a tuple")
        if self.evidence_count != len(self.evidence_records):
            raise FinancialEvidenceExtractionError(
                "evidence_count must match evidence_records length"
            )
        evidence_ids: set[str] = set()
        for record in self.evidence_records:
            if not isinstance(record, FinancialEvidenceRecord):
                raise FinancialEvidenceExtractionError(
                    "evidence_records must contain FinancialEvidenceRecord values"
                )
            if record.document_id != self.document_id:
                raise FinancialEvidenceExtractionError(
                    "every evidence record must belong to manifest document_id"
                )
            if record.extractor_version != self.extractor_version:
                raise FinancialEvidenceExtractionError(
                    "every evidence record must use the manifest extractor_version"
                )
            if record.evidence_id in evidence_ids:
                raise FinancialEvidenceExtractionError("duplicate evidence_id")
            evidence_ids.add(record.evidence_id)
        _require_digest(self.deterministic_hash, "deterministic_hash")


class GovernedFinancialEvidenceExtractor:
    """Extract transparent keyword evidence from validated Step 6 documents."""

    def __init__(
        self,
        *,
        max_chunks_per_document: int = 1_000,
        max_evidence_records_per_chunk: int = 25,
        max_claim_length: int = 1_000,
    ) -> None:
        self._max_chunks = self._bounded_integer(
            max_chunks_per_document,
            "max_chunks_per_document",
            _MAX_CONFIGURED_CHUNKS,
        )
        self._max_records_per_chunk = self._bounded_integer(
            max_evidence_records_per_chunk,
            "max_evidence_records_per_chunk",
            _MAX_CONFIGURED_RECORDS_PER_CHUNK,
        )
        self._max_claim_length = self._bounded_integer(
            max_claim_length,
            "max_claim_length",
            _MAX_CONFIGURED_CLAIM_LENGTH,
        )

    @staticmethod
    def _bounded_integer(value: int, name: str, upper_bound: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper_bound:
            raise FinancialEvidenceExtractionError(
                f"{name} must be an integer between 1 and {upper_bound}"
            )
        return value

    def extract(self, document: IngestedDocument) -> EvidenceExtractionManifest:
        """Extract a stable manifest from one validated Step 6 document."""

        self._validate_document(document)
        records_by_identity: dict[tuple[str, int, int], FinancialEvidenceRecord] = {}

        for chunk in sorted(document.chunks, key=lambda item: (item.index, item.chunk_id)):
            chunk_records = self._extract_chunk(document, chunk)
            if len(chunk_records) > self._max_records_per_chunk:
                raise FinancialEvidenceExtractionError(
                    f"chunk {chunk.chunk_id} exceeds max_evidence_records_per_chunk"
                )
            for record in chunk_records:
                identity = (
                    record.evidence_type,
                    record.span.start_char,
                    record.span.end_char,
                )
                existing = records_by_identity.get(identity)
                if existing is None:
                    records_by_identity[identity] = record
                elif (
                    existing.claim != record.claim
                    or existing.span.exact_text != record.span.exact_text
                    or existing.evidence_id != record.evidence_id
                ):
                    raise FinancialEvidenceExtractionError("conflicting duplicate evidence")

        records = tuple(
            sorted(
                records_by_identity.values(),
                key=lambda item: (
                    item.span.start_char,
                    item.span.end_char,
                    item.evidence_type,
                    item.evidence_id,
                ),
            )
        )
        manifest_hash = self._manifest_hash(document.document_id, records)
        return EvidenceExtractionManifest(
            document_id=document.document_id,
            extractor_version=SUPPORTED_EXTRACTOR_VERSION,
            evidence_records=records,
            evidence_count=len(records),
            deterministic_hash=manifest_hash,
        )

    def _validate_document(self, document: IngestedDocument) -> None:
        if not isinstance(document, IngestedDocument):
            raise FinancialEvidenceExtractionError(
                "extract requires a Step 6 IngestedDocument"
            )
        if document.schema_version != 1:
            raise FinancialEvidenceExtractionError("unsupported Step 6 schema_version")
        _require_text(document.document_id, "document_id")
        _require_text(document.issuer, "issuer")
        _require_text(document.normalized_content, "normalized_content")
        _require_digest(document.content_sha256, "content_sha256")
        if sha256(document.normalized_content.encode("utf-8")).hexdigest() != (
            document.content_sha256
        ):
            raise FinancialEvidenceExtractionError("content_sha256 does not match normalized_content")
        if not isinstance(document.provenance, SourceProvenance):
            raise FinancialEvidenceExtractionError(
                "provenance must be a Step 6 SourceProvenance"
            )
        parsed_url = urlparse(document.provenance.source_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise FinancialEvidenceExtractionError("source_url must be an absolute HTTPS URL")
        if not isinstance(document.chunks, tuple):
            raise FinancialEvidenceExtractionError("chunks must be a tuple")
        if len(document.chunks) > self._max_chunks:
            raise FinancialEvidenceExtractionError("document exceeds max_chunks_per_document")
        if not document.chunks:
            raise FinancialEvidenceExtractionError("document must contain at least one chunk")

        seen_chunk_ids: set[str] = set()
        for expected_index, chunk in enumerate(document.chunks):
            if not isinstance(chunk, DocumentChunk):
                raise FinancialEvidenceExtractionError(
                    "chunks must contain Step 6 DocumentChunk values"
                )
            if chunk.document_id != document.document_id:
                raise FinancialEvidenceExtractionError("chunk document_id does not match document")
            if chunk.index != expected_index:
                raise FinancialEvidenceExtractionError("chunk indexes must be contiguous and ordered")
            if chunk.chunk_id in seen_chunk_ids:
                raise FinancialEvidenceExtractionError("duplicate chunk_id")
            seen_chunk_ids.add(chunk.chunk_id)
            if (
                isinstance(chunk.start_char, bool)
                or isinstance(chunk.end_char, bool)
                or not isinstance(chunk.start_char, int)
                or not isinstance(chunk.end_char, int)
                or chunk.start_char < 0
                or chunk.end_char <= chunk.start_char
                or chunk.end_char > len(document.normalized_content)
            ):
                raise FinancialEvidenceExtractionError("chunk has invalid source span")
            source_slice = document.normalized_content[chunk.start_char : chunk.end_char]
            if source_slice.strip() != chunk.text:
                raise FinancialEvidenceExtractionError("chunk text does not match source span")
            _require_digest(chunk.sha256, "chunk sha256")
            if sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.sha256:
                raise FinancialEvidenceExtractionError("chunk sha256 does not match chunk text")
            expected_chunk_id = (
                f"{document.document_id}-chunk-{chunk.index:04d}-{chunk.sha256[:12]}"
            )
            if chunk.chunk_id != expected_chunk_id:
                raise FinancialEvidenceExtractionError("chunk_id is inconsistent with document and index")

    def _extract_chunk(
        self,
        document: IngestedDocument,
        chunk: DocumentChunk,
    ) -> tuple[FinancialEvidenceRecord, ...]:
        source_slice = document.normalized_content[chunk.start_char : chunk.end_char]
        leading_trim = len(source_slice) - len(source_slice.lstrip())
        chunk_base = chunk.start_char + leading_trim
        records: list[FinancialEvidenceRecord] = []

        for match in _SENTENCE_RE.finditer(chunk.text):
            exact_text = match.group(0)
            sentence_start = chunk_base + match.start()
            sentence_end = chunk_base + match.end()
            if document.normalized_content[sentence_start:sentence_end] != exact_text:
                raise FinancialEvidenceExtractionError("extracted span does not match source text")
            claim = _SPACE_RE.sub(" ", exact_text).strip()
            if len(claim) > self._max_claim_length:
                raise FinancialEvidenceExtractionError(
                    f"claim exceeds max_claim_length in chunk {chunk.chunk_id}"
                )
            lowered = claim.casefold()
            for evidence_type in sorted(SUPPORTED_EVIDENCE_TYPES):
                if not any(
                    re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", lowered)
                    for keyword in _EVIDENCE_KEYWORDS[evidence_type]
                ):
                    continue
                span = EvidenceSpan(sentence_start, sentence_end, exact_text)
                identity_material = {
                    "document_id": document.document_id,
                    "evidence_type": evidence_type,
                    "span_end": sentence_end,
                    "span_start": sentence_start,
                    "text_sha256": sha256(exact_text.encode("utf-8")).hexdigest(),
                    "version": SUPPORTED_EXTRACTOR_VERSION,
                }
                identity_hash = sha256(
                    json.dumps(
                        identity_material,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                records.append(
                    FinancialEvidenceRecord(
                        evidence_id=f"sigil-evidence-{identity_hash}",
                        document_id=document.document_id,
                        chunk_id=chunk.chunk_id,
                        issuer=document.issuer,
                        evidence_type=evidence_type,
                        claim=claim,
                        span=span,
                        source_url=document.provenance.source_url,
                        published_on=document.published_on,
                        content_hash=document.content_sha256,
                        confidence=1.0,
                        extractor_version=SUPPORTED_EXTRACTOR_VERSION,
                    )
                )
        return tuple(records)

    @staticmethod
    def _manifest_hash(
        document_id: str,
        records: tuple[FinancialEvidenceRecord, ...],
    ) -> str:
        material = {
            "document_id": document_id,
            "evidence": [
                {
                    "chunk_id": record.chunk_id,
                    "claim": record.claim,
                    "content_hash": record.content_hash,
                    "evidence_id": record.evidence_id,
                    "evidence_type": record.evidence_type,
                    "span": {
                        "end_char": record.span.end_char,
                        "exact_text": record.span.exact_text,
                        "start_char": record.span.start_char,
                    },
                }
                for record in records
            ],
            "extractor_version": SUPPORTED_EXTRACTOR_VERSION,
        }
        return sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
