"""Append-safe local persistence for validated financial evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .evidence import (
    SUPPORTED_EVIDENCE_TYPES,
    SUPPORTED_EXTRACTOR_VERSION,
    EvidenceExtractionManifest,
    EvidenceSpan,
    FinancialEvidenceExtractionError,
    FinancialEvidenceRecord,
)


REPOSITORY_SCHEMA_VERSION = 1
DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_LIMIT = 1_000

_EVIDENCE_ID_RE = re.compile(r"^sigil-evidence-[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_RECORD_KEYS = frozenset({"record_hash", "schema_version", "evidence"})
_EVIDENCE_KEYS = frozenset(
    {
        "chunk_id",
        "claim",
        "confidence",
        "content_hash",
        "document_id",
        "evidence_id",
        "evidence_type",
        "extractor_version",
        "issuer",
        "published_on",
        "source_url",
        "span",
    }
)
_SPAN_KEYS = frozenset({"end_char", "exact_text", "start_char"})
_MANIFEST_KEYS = frozenset(
    {
        "document_id",
        "evidence_ids",
        "extraction_id",
        "extractor_version",
        "schema_version",
    }
)


class FinancialEvidenceRepositoryError(RuntimeError):
    """Base error for the governed evidence repository."""


class FinancialEvidenceRepositoryIntegrityError(FinancialEvidenceRepositoryError):
    """Raised when stored or supplied evidence fails integrity validation."""


class FinancialEvidenceConflictError(FinancialEvidenceRepositoryError):
    """Raised when an immutable identifier already has different content."""


class FinancialEvidenceNotFoundError(FinancialEvidenceRepositoryError):
    """Raised when exact evidence lookup cannot find a committed record."""


@dataclass(frozen=True, slots=True)
class FinancialEvidenceRepositoryRecord:
    """One validated repository record and its content-derived hash."""

    evidence: FinancialEvidenceRecord
    record_hash: str


@dataclass(frozen=True, slots=True)
class FinancialEvidenceRepositoryManifest:
    """Reproducible metadata derived from all committed evidence records."""

    schema_version: int
    record_count: int
    extraction_count: int
    evidence_ids: tuple[str, ...]
    extraction_ids: tuple[str, ...]
    deterministic_hash: str


@dataclass(frozen=True, slots=True)
class FinancialEvidenceWriteResult:
    """Result of storing one complete Step 7 extraction manifest."""

    extraction_id: str
    evidence_ids: tuple[str, ...]
    records_created: int
    records_existing: int
    manifest_created: bool


@dataclass(frozen=True, slots=True)
class FinancialEvidenceQuery:
    """Bounded exact-match repository query."""

    evidence_id: str | None = None
    document_id: str | None = None
    extraction_id: str | None = None
    evidence_type: str | None = None
    source_start_char: int | None = None
    source_end_char: int | None = None
    limit: int = DEFAULT_QUERY_LIMIT

    def __post_init__(self) -> None:
        if self.evidence_id is not None:
            _validate_evidence_id(self.evidence_id)
        if self.document_id is not None:
            _validate_safe_id(self.document_id, "document_id")
        if self.extraction_id is not None:
            _validate_digest(self.extraction_id, "extraction_id")
        if self.evidence_type is not None and self.evidence_type not in SUPPORTED_EVIDENCE_TYPES:
            raise FinancialEvidenceRepositoryError(
                f"unsupported evidence_type: {self.evidence_type}"
            )
        for value, name in (
            (self.source_start_char, "source_start_char"),
            (self.source_end_char, "source_end_char"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise FinancialEvidenceRepositoryError(
                    f"{name} must be a non-negative integer"
                )
        if (
            self.source_start_char is not None
            and self.source_end_char is not None
            and self.source_end_char <= self.source_start_char
        ):
            raise FinancialEvidenceRepositoryError(
                "source_end_char must be greater than source_start_char"
            )
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= MAX_QUERY_LIMIT
        ):
            raise FinancialEvidenceRepositoryError(
                f"limit must be an integer between 1 and {MAX_QUERY_LIMIT}"
            )


@dataclass(frozen=True, slots=True)
class FinancialEvidenceQueryResult:
    """Immutable query results with explicit truncation metadata."""

    records: tuple[FinancialEvidenceRepositoryRecord, ...]
    total_matches: int
    truncated: bool
    limit: int


@dataclass(frozen=True, slots=True)
class FinancialEvidenceAuditIssue:
    """One deterministic repository corruption finding."""

    relative_path: str
    message: str


@dataclass(frozen=True, slots=True)
class FinancialEvidenceAuditResult:
    """Read-only validation result for the complete repository."""

    valid: bool
    committed_record_count: int
    committed_extraction_count: int
    valid_record_count: int
    valid_extraction_count: int
    issues: tuple[FinancialEvidenceAuditIssue, ...]
    manifest: FinancialEvidenceRepositoryManifest | None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise FinancialEvidenceRepositoryError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )


def _validate_evidence_id(value: str) -> None:
    if not isinstance(value, str) or _EVIDENCE_ID_RE.fullmatch(value) is None:
        raise FinancialEvidenceRepositoryError(
            "evidence_id must be sigil-evidence- followed by a lowercase SHA-256 digest"
        )


def _validate_safe_id(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or _SAFE_ID_RE.fullmatch(value) is None
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise FinancialEvidenceRepositoryError(f"{field_name} is not a safe identifier")


def _evidence_payload(record: FinancialEvidenceRecord) -> dict[str, object]:
    return {
        "chunk_id": record.chunk_id,
        "claim": record.claim,
        "confidence": record.confidence,
        "content_hash": record.content_hash,
        "document_id": record.document_id,
        "evidence_id": record.evidence_id,
        "evidence_type": record.evidence_type,
        "extractor_version": record.extractor_version,
        "issuer": record.issuer,
        "published_on": record.published_on.isoformat(),
        "source_url": record.source_url,
        "span": {
            "end_char": record.span.end_char,
            "exact_text": record.span.exact_text,
            "start_char": record.span.start_char,
        },
    }


def _expected_evidence_id(record: FinancialEvidenceRecord) -> str:
    material = {
        "document_id": record.document_id,
        "evidence_type": record.evidence_type,
        "span_end": record.span.end_char,
        "span_start": record.span.start_char,
        "text_sha256": sha256(record.span.exact_text.encode("utf-8")).hexdigest(),
        "version": record.extractor_version,
    }
    return f"sigil-evidence-{_hash_json(material)}"


def _expected_extraction_id(
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
    return _hash_json(material)


def _validate_record_identity(record: FinancialEvidenceRecord) -> None:
    _validate_evidence_id(record.evidence_id)
    _validate_safe_id(record.document_id, "document_id")
    _validate_safe_id(record.chunk_id, "chunk_id")
    if record.evidence_id != _expected_evidence_id(record):
        raise FinancialEvidenceRepositoryIntegrityError(
            f"evidence identity mismatch: {record.evidence_id}"
        )


def _decode_record(value: object) -> FinancialEvidenceRepositoryRecord:
    if not isinstance(value, dict) or frozenset(value) != _RECORD_KEYS:
        raise FinancialEvidenceRepositoryIntegrityError("invalid repository record schema")
    if value["schema_version"] != REPOSITORY_SCHEMA_VERSION:
        raise FinancialEvidenceRepositoryIntegrityError("unsupported repository schema_version")
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or frozenset(evidence) != _EVIDENCE_KEYS:
        raise FinancialEvidenceRepositoryIntegrityError("invalid evidence schema")
    span = evidence["span"]
    if not isinstance(span, dict) or frozenset(span) != _SPAN_KEYS:
        raise FinancialEvidenceRepositoryIntegrityError("invalid evidence span schema")
    try:
        record = FinancialEvidenceRecord(
            evidence_id=evidence["evidence_id"],
            document_id=evidence["document_id"],
            chunk_id=evidence["chunk_id"],
            issuer=evidence["issuer"],
            evidence_type=evidence["evidence_type"],
            claim=evidence["claim"],
            span=EvidenceSpan(
                start_char=span["start_char"],
                end_char=span["end_char"],
                exact_text=span["exact_text"],
            ),
            source_url=evidence["source_url"],
            published_on=date.fromisoformat(evidence["published_on"]),
            content_hash=evidence["content_hash"],
            confidence=evidence["confidence"],
            extractor_version=evidence["extractor_version"],
        )
    except (FinancialEvidenceExtractionError, TypeError, ValueError) as exc:
        raise FinancialEvidenceRepositoryIntegrityError(
            f"invalid stored evidence: {exc}"
        ) from exc
    try:
        _validate_record_identity(record)
    except FinancialEvidenceRepositoryIntegrityError:
        raise
    except FinancialEvidenceRepositoryError as exc:
        raise FinancialEvidenceRepositoryIntegrityError(
            f"invalid stored evidence identity: {exc}"
        ) from exc
    expected_hash = _hash_json(evidence)
    if value["record_hash"] != expected_hash:
        raise FinancialEvidenceRepositoryIntegrityError("repository record hash mismatch")
    return FinancialEvidenceRepositoryRecord(record, expected_hash)


class GovernedFinancialEvidenceRepository:
    """Deterministic, local, immutable repository for Step 7 evidence."""

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)):
            raise FinancialEvidenceRepositoryError("repository root must be a path")
        supplied = Path(root)
        if not supplied.is_absolute():
            raise FinancialEvidenceRepositoryError("repository root must be absolute")
        if not supplied.exists() or not supplied.is_dir() or supplied.is_symlink():
            raise FinancialEvidenceRepositoryError(
                "repository root must be an existing non-symlink directory"
            )
        self._root = supplied.resolve(strict=True)
        self._records = self._root / "records"
        self._extractions = self._root / "extractions"
        self._prepare_directory(self._records)
        self._prepare_directory(self._extractions)

    @property
    def root(self) -> Path:
        """Return the explicit resolved repository root."""

        return self._root

    def store(
        self,
        manifest: EvidenceExtractionManifest,
    ) -> FinancialEvidenceWriteResult:
        """Validate and append one complete Step 7 extraction manifest."""

        self._validate_manifest(manifest)
        evidence_ids = [record.evidence_id for record in manifest.evidence_records]
        extraction_payload = {
            "document_id": manifest.document_id,
            "evidence_ids": evidence_ids,
            "extraction_id": manifest.deterministic_hash,
            "extractor_version": manifest.extractor_version,
            "schema_version": REPOSITORY_SCHEMA_VERSION,
        }
        extraction_path = self._extraction_path(manifest.deterministic_hash)
        if extraction_path.exists():
            existing_payload = self._read_json(extraction_path)
            self._validate_stored_extraction_payload(existing_payload)
            if existing_payload != extraction_payload:
                raise FinancialEvidenceConflictError(
                    f"conflicting extraction_id: {manifest.deterministic_hash}"
                )
        for record in manifest.evidence_records:
            destination = self._record_path(record.evidence_id)
            if destination.exists() and self._read_record_path(destination).evidence != record:
                raise FinancialEvidenceConflictError(
                    f"conflicting evidence_id: {record.evidence_id}"
                )
        created = 0
        existing = 0
        for record in manifest.evidence_records:
            payload = _evidence_payload(record)
            stored = {
                "evidence": payload,
                "record_hash": _hash_json(payload),
                "schema_version": REPOSITORY_SCHEMA_VERSION,
            }
            destination = self._record_path(record.evidence_id)
            if self._write_immutable(destination, _canonical_json(stored)):
                created += 1
            else:
                existing_record = self._read_record_path(destination)
                if existing_record.evidence != record:
                    raise FinancialEvidenceConflictError(
                        f"conflicting evidence_id: {record.evidence_id}"
                    )
                existing += 1
        manifest_created = self._write_immutable(
            extraction_path, _canonical_json(extraction_payload)
        )
        if not manifest_created:
            existing_payload = self._read_json(extraction_path)
            if existing_payload != extraction_payload:
                raise FinancialEvidenceConflictError(
                    f"conflicting extraction_id: {manifest.deterministic_hash}"
                )
            self._validate_stored_extraction_payload(existing_payload)
        return FinancialEvidenceWriteResult(
            extraction_id=manifest.deterministic_hash,
            evidence_ids=tuple(evidence_ids),
            records_created=created,
            records_existing=existing,
            manifest_created=manifest_created,
        )

    def exists(self, evidence_id: str) -> bool:
        """Return whether an exact committed evidence ID exists."""

        path = self._record_path(evidence_id)
        if not path.exists():
            return False
        self._read_record_path(path)
        return True

    def get(self, evidence_id: str) -> FinancialEvidenceRepositoryRecord:
        """Retrieve one exact evidence record or raise not found."""

        path = self._record_path(evidence_id)
        if not path.exists():
            raise FinancialEvidenceNotFoundError(f"evidence not found: {evidence_id}")
        return self._read_record_path(path)

    def query(self, query: FinancialEvidenceQuery) -> FinancialEvidenceQueryResult:
        """Apply bounded exact filters with deterministic ordering."""

        if not isinstance(query, FinancialEvidenceQuery):
            raise FinancialEvidenceRepositoryError(
                "query must be a FinancialEvidenceQuery"
            )
        extraction_ids: frozenset[str] | None = None
        if query.extraction_id is not None:
            extraction = self._read_extraction(query.extraction_id)
            extraction_ids = frozenset(extraction["evidence_ids"])

        matches: list[FinancialEvidenceRepositoryRecord] = []
        for record in self.list_records():
            evidence = record.evidence
            if query.evidence_id is not None and evidence.evidence_id != query.evidence_id:
                continue
            if query.document_id is not None and evidence.document_id != query.document_id:
                continue
            if extraction_ids is not None and evidence.evidence_id not in extraction_ids:
                continue
            if query.evidence_type is not None and evidence.evidence_type != query.evidence_type:
                continue
            if (
                query.source_start_char is not None
                and evidence.span.start_char != query.source_start_char
            ):
                continue
            if (
                query.source_end_char is not None
                and evidence.span.end_char != query.source_end_char
            ):
                continue
            matches.append(record)
        returned = tuple(matches[: query.limit])
        return FinancialEvidenceQueryResult(
            records=returned,
            total_matches=len(matches),
            truncated=len(matches) > query.limit,
            limit=query.limit,
        )

    def list_records(self) -> tuple[FinancialEvidenceRepositoryRecord, ...]:
        """List every committed record in deterministic evidence-ID order."""

        return tuple(self._read_record_path(path) for path in self._record_files())

    def repository_manifest(self) -> FinancialEvidenceRepositoryManifest:
        """Derive reproducible integrity metadata from committed data."""

        records = self.list_records()
        extractions = tuple(
            self._read_extraction(path.stem) for path in self._extraction_files()
        )
        evidence_ids = tuple(record.evidence.evidence_id for record in records)
        extraction_ids = tuple(item["extraction_id"] for item in extractions)
        material = {
            "evidence": [
                {
                    "evidence_id": record.evidence.evidence_id,
                    "record_hash": record.record_hash,
                }
                for record in records
            ],
            "extraction_ids": list(extraction_ids),
            "schema_version": REPOSITORY_SCHEMA_VERSION,
        }
        return FinancialEvidenceRepositoryManifest(
            schema_version=REPOSITORY_SCHEMA_VERSION,
            record_count=len(records),
            extraction_count=len(extractions),
            evidence_ids=evidence_ids,
            extraction_ids=extraction_ids,
            deterministic_hash=_hash_json(material),
        )

    def audit(self) -> FinancialEvidenceAuditResult:
        """Validate all committed files without modifying the repository."""

        record_files = self._audit_candidates(self._records)
        extraction_files = self._audit_candidates(self._extractions)
        issues: list[FinancialEvidenceAuditIssue] = []
        valid_records = 0
        valid_extractions = 0
        for path in record_files:
            try:
                if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                    raise FinancialEvidenceRepositoryIntegrityError(
                        f"unsafe committed repository entry: {path.name}"
                    )
                self._read_record_path(path)
                valid_records += 1
            except FinancialEvidenceRepositoryError as exc:
                issues.append(self._issue(path, exc))
        for path in extraction_files:
            try:
                if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                    raise FinancialEvidenceRepositoryIntegrityError(
                        f"unsafe committed repository entry: {path.name}"
                    )
                self._read_extraction_path(path)
                valid_extractions += 1
            except FinancialEvidenceRepositoryError as exc:
                issues.append(self._issue(path, exc))
        issues.sort(key=lambda item: (item.relative_path, item.message))
        manifest = None
        if not issues:
            manifest = self.repository_manifest()
        return FinancialEvidenceAuditResult(
            valid=not issues,
            committed_record_count=len(record_files),
            committed_extraction_count=len(extraction_files),
            valid_record_count=valid_records,
            valid_extraction_count=valid_extractions,
            issues=tuple(issues),
            manifest=manifest,
        )

    def _prepare_directory(self, path: Path) -> None:
        if path.exists():
            if not path.is_dir() or path.is_symlink():
                raise FinancialEvidenceRepositoryError(
                    f"repository layout is unsafe: {path.name}"
                )
        else:
            path.mkdir(mode=0o700)
        self._assert_inside_root(path)

    def _assert_inside_root(self, path: Path) -> None:
        try:
            path.resolve(strict=True).relative_to(self._root)
        except (OSError, ValueError) as exc:
            raise FinancialEvidenceRepositoryError(
                "repository path escapes supplied root"
            ) from exc

    def _record_path(self, evidence_id: str) -> Path:
        _validate_evidence_id(evidence_id)
        return self._records / f"{evidence_id}.json"

    def _extraction_path(self, extraction_id: str) -> Path:
        _validate_digest(extraction_id, "extraction_id")
        return self._extractions / f"{extraction_id}.json"

    def _record_files(self) -> tuple[Path, ...]:
        paths = self._candidate_files(self._records)
        for path in paths:
            _validate_evidence_id(path.stem)
        return paths

    def _extraction_files(self) -> tuple[Path, ...]:
        paths = self._candidate_files(self._extractions)
        for path in paths:
            _validate_digest(path.stem, "extraction_id")
        return paths

    def _candidate_files(self, directory: Path) -> tuple[Path, ...]:
        self._assert_inside_root(directory)
        paths: list[Path] = []
        for path in directory.iterdir():
            if path.name.startswith(".") or path.suffix == ".tmp":
                continue
            if path.is_symlink() or not path.is_file():
                raise FinancialEvidenceRepositoryIntegrityError(
                    f"unsafe committed repository entry: {path.name}"
                )
            if path.suffix != ".json":
                raise FinancialEvidenceRepositoryIntegrityError(
                    f"unexpected committed repository entry: {path.name}"
                )
            self._assert_inside_root(path)
            paths.append(path)
        return tuple(sorted(paths, key=lambda item: item.name))

    def _audit_candidates(self, directory: Path) -> tuple[Path, ...]:
        self._assert_inside_root(directory)
        return tuple(
            sorted(
                (
                    path
                    for path in directory.iterdir()
                    if not path.name.startswith(".") and path.suffix != ".tmp"
                ),
                key=lambda item: item.name,
            )
        )

    def _write_immutable(self, destination: Path, content: bytes) -> bool:
        self._assert_inside_root(destination.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".pending-", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                return False
            self._fsync_directory(destination.parent)
            return True
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_json(self, path: Path) -> object:
        if path.is_symlink() or not path.is_file():
            raise FinancialEvidenceRepositoryIntegrityError(
                f"unsafe repository file: {path.name}"
            )
        self._assert_inside_root(path)
        try:
            return json.loads(path.read_bytes(), object_pairs_hook=self._unique_object)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinancialEvidenceRepositoryIntegrityError(
                f"malformed repository JSON: {path.name}"
            ) from exc

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise FinancialEvidenceRepositoryIntegrityError(
                    f"duplicate JSON object key: {key}"
                )
            value[key] = item
        return value

    def _read_record_path(self, path: Path) -> FinancialEvidenceRepositoryRecord:
        decoded = _decode_record(self._read_json(path))
        if path.stem != decoded.evidence.evidence_id:
            raise FinancialEvidenceRepositoryIntegrityError(
                "evidence filename does not match stored evidence_id"
            )
        return decoded

    def _read_extraction(self, extraction_id: str) -> dict[str, Any]:
        path = self._extraction_path(extraction_id)
        if not path.exists():
            raise FinancialEvidenceNotFoundError(
                f"extraction not found: {extraction_id}"
            )
        return self._read_extraction_path(path)

    def _read_extraction_path(self, path: Path) -> dict[str, Any]:
        value = self._read_json(path)
        self._validate_stored_extraction_payload(value)
        if path.stem != value["extraction_id"]:
            raise FinancialEvidenceRepositoryIntegrityError(
                "extraction filename does not match extraction_id"
            )
        for evidence_id in value["evidence_ids"]:
            record = self.get(evidence_id)
            if record.evidence.document_id != value["document_id"]:
                raise FinancialEvidenceRepositoryIntegrityError(
                    "extraction references evidence from another document"
                )
        records = tuple(self.get(item).evidence for item in value["evidence_ids"])
        if _expected_extraction_id(value["document_id"], records) != value["extraction_id"]:
            raise FinancialEvidenceRepositoryIntegrityError(
                "extraction manifest identity mismatch"
            )
        return value

    @staticmethod
    def _validate_extraction_payload(value: object) -> None:
        if not isinstance(value, dict) or frozenset(value) != _MANIFEST_KEYS:
            raise FinancialEvidenceRepositoryIntegrityError(
                "invalid extraction manifest schema"
            )
        if value["schema_version"] != REPOSITORY_SCHEMA_VERSION:
            raise FinancialEvidenceRepositoryIntegrityError(
                "unsupported extraction schema_version"
            )
        _validate_safe_id(value["document_id"], "document_id")
        _validate_digest(value["extraction_id"], "extraction_id")
        if value["extractor_version"] != SUPPORTED_EXTRACTOR_VERSION:
            raise FinancialEvidenceRepositoryIntegrityError(
                "unsupported extraction extractor_version"
            )
        evidence_ids = value["evidence_ids"]
        if not isinstance(evidence_ids, list):
            raise FinancialEvidenceRepositoryIntegrityError(
                "extraction evidence_ids must be a list"
            )
        for evidence_id in evidence_ids:
            _validate_evidence_id(evidence_id)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise FinancialEvidenceRepositoryIntegrityError(
                "duplicate evidence_id in extraction manifest"
            )

    @classmethod
    def _validate_stored_extraction_payload(cls, value: object) -> None:
        try:
            cls._validate_extraction_payload(value)
        except FinancialEvidenceRepositoryIntegrityError:
            raise
        except FinancialEvidenceRepositoryError as exc:
            raise FinancialEvidenceRepositoryIntegrityError(
                f"invalid stored extraction manifest: {exc}"
            ) from exc

    @staticmethod
    def _validate_manifest(manifest: EvidenceExtractionManifest) -> None:
        if not isinstance(manifest, EvidenceExtractionManifest):
            raise FinancialEvidenceRepositoryError(
                "store requires a Step 7 EvidenceExtractionManifest"
            )
        _validate_safe_id(manifest.document_id, "document_id")
        for record in manifest.evidence_records:
            _validate_record_identity(record)
        expected = _expected_extraction_id(manifest.document_id, manifest.evidence_records)
        if manifest.deterministic_hash != expected:
            raise FinancialEvidenceRepositoryIntegrityError(
                "Step 7 extraction manifest hash mismatch"
            )

    def _issue(
        self,
        path: Path,
        error: FinancialEvidenceRepositoryError,
    ) -> FinancialEvidenceAuditIssue:
        return FinancialEvidenceAuditIssue(
            relative_path=path.relative_to(self._root).as_posix(),
            message=str(error),
        )
