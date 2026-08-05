from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import socket

import pytest

from sigil.integrations.documents import (
    EvidenceExtractionManifest,
    FinancialDocument,
    FinancialDocumentIngestor,
    FinancialEvidenceConflictError,
    FinancialEvidenceNotFoundError,
    FinancialEvidenceQuery,
    FinancialEvidenceRepositoryError,
    FinancialEvidenceRepositoryIntegrityError,
    GovernedFinancialEvidenceExtractor,
    GovernedFinancialEvidenceRepository,
    SourceProvenance,
)


def extraction(
    content: str = "Revenue rose. Debt fell. Liquidity remained strong.",
) -> EvidenceExtractionManifest:
    document = FinancialDocument(
        issuer="FireCat Holdings",
        document_type="10-Q",
        title="Quarterly report",
        published_on=date(2026, 7, 20),
        content=content,
        provenance=SourceProvenance(
            source_url="https://www.sec.gov/Archives/example.txt",
            retrieved_at=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            publisher="U.S. Securities and Exchange Commission",
        ),
    )
    ingested = FinancialDocumentIngestor(chunk_chars=200, overlap_chars=40).ingest(document)
    return GovernedFinancialEvidenceExtractor().extract(ingested)


def repository(tmp_path: Path) -> GovernedFinancialEvidenceRepository:
    root = tmp_path / "repository"
    root.mkdir(parents=True)
    return GovernedFinancialEvidenceRepository(root)


def test_store_and_exact_retrieval(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()

    result = repo.store(manifest)
    record = manifest.evidence_records[0]

    assert result.extraction_id == manifest.deterministic_hash
    assert result.records_created == manifest.evidence_count
    assert result.records_existing == 0
    assert result.manifest_created is True
    assert repo.exists(record.evidence_id) is True
    assert repo.get(record.evidence_id).evidence == record


def test_exact_document_extraction_type_and_span_queries(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)
    first = manifest.evidence_records[0]

    by_document = repo.query(FinancialEvidenceQuery(document_id=manifest.document_id))
    by_extraction = repo.query(
        FinancialEvidenceQuery(extraction_id=manifest.deterministic_hash)
    )
    by_type = repo.query(FinancialEvidenceQuery(evidence_type=first.evidence_type))
    by_span = repo.query(
        FinancialEvidenceQuery(
            source_start_char=first.span.start_char,
            source_end_char=first.span.end_char,
        )
    )

    expected = tuple(sorted(manifest.evidence_records, key=lambda item: item.evidence_id))
    assert tuple(item.evidence for item in by_document.records) == expected
    assert by_extraction.records == by_document.records
    assert tuple(item.evidence for item in by_type.records) == (first,)
    assert tuple(item.evidence for item in by_span.records) == (first,)


def test_listing_and_repository_manifest_are_deterministic(tmp_path: Path) -> None:
    first_repo = repository(tmp_path / "first")
    second_repo = repository(tmp_path / "second")
    manifest = extraction()
    first_repo.store(manifest)
    second_repo.store(manifest)

    first_ids = tuple(item.evidence.evidence_id for item in first_repo.list_records())

    assert first_ids == tuple(sorted(first_ids))
    assert first_repo.repository_manifest() == second_repo.repository_manifest()
    assert first_repo.repository_manifest().record_count == manifest.evidence_count


def test_duplicate_write_is_idempotent(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)

    result = repo.store(manifest)

    assert result.records_created == 0
    assert result.records_existing == manifest.evidence_count
    assert result.manifest_created is False


def test_conflicting_duplicate_write_fails(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)
    changed = replace(manifest.evidence_records[0], issuer="Different Issuer")
    conflicting = replace(
        manifest,
        evidence_records=(changed, *manifest.evidence_records[1:]),
    )

    with pytest.raises(FinancialEvidenceConflictError):
        repo.store(conflicting)


def test_malformed_and_forged_stored_records_fail_closed(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)
    record = manifest.evidence_records[0]
    path = repo.root / "records" / f"{record.evidence_id}.json"
    path.write_text("{not-json")

    with pytest.raises(FinancialEvidenceRepositoryIntegrityError, match="malformed"):
        repo.get(record.evidence_id)

    repo = repository(tmp_path / "forged")
    repo.store(manifest)
    path = repo.root / "records" / f"{record.evidence_id}.json"
    stored = json.loads(path.read_text())
    stored["evidence"]["span"]["exact_text"] = "Revenue fell."
    path.write_text(json.dumps(stored))
    with pytest.raises(FinancialEvidenceRepositoryIntegrityError):
        repo.get(record.evidence_id)


def test_forged_step7_evidence_and_manifest_are_rejected(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    forged_record = replace(manifest.evidence_records[0], claim="Unsupported claim.")
    forged_manifest = replace(
        manifest,
        evidence_records=(forged_record, *manifest.evidence_records[1:]),
    )
    with pytest.raises(FinancialEvidenceRepositoryIntegrityError, match="manifest hash"):
        repo.store(forged_manifest)

    forged_identity = replace(
        manifest.evidence_records[0],
        evidence_id="sigil-evidence-" + "0" * 64,
    )
    forged_manifest = replace(
        manifest,
        evidence_records=(forged_identity, *manifest.evidence_records[1:]),
    )
    with pytest.raises(FinancialEvidenceRepositoryIntegrityError, match="identity"):
        repo.store(forged_manifest)


def test_missing_evidence_and_extraction_are_explicit(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    missing_id = "sigil-evidence-" + "0" * 64

    assert repo.exists(missing_id) is False
    with pytest.raises(FinancialEvidenceNotFoundError):
        repo.get(missing_id)
    with pytest.raises(FinancialEvidenceNotFoundError):
        repo.query(FinancialEvidenceQuery(extraction_id="0" * 64))


def test_empty_repository_has_stable_manifest_and_audit(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    result = repo.query(FinancialEvidenceQuery())
    audit = repo.audit()

    assert result.records == ()
    assert result.total_matches == 0
    assert result.truncated is False
    assert audit.valid is True
    assert audit.manifest == repo.repository_manifest()
    assert audit.manifest.record_count == 0


@pytest.mark.parametrize("root_kind", ["relative", "missing", "file", "symlink"])
def test_invalid_repository_roots(
    tmp_path: Path, root_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if root_kind == "relative":
        monkeypatch.chdir(tmp_path)
        root = Path("relative")
        root.mkdir()
    elif root_kind == "missing":
        root = tmp_path / "missing"
    elif root_kind == "file":
        root = tmp_path / "file"
        root.write_text("not a directory")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root = tmp_path / "link"
        try:
            root.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are unavailable")

    with pytest.raises(FinancialEvidenceRepositoryError):
        GovernedFinancialEvidenceRepository(root)


@pytest.mark.parametrize(
    "query",
    [
        FinancialEvidenceQuery,
    ],
)
def test_query_requires_query_model(
    tmp_path: Path, query: object
) -> None:
    with pytest.raises(FinancialEvidenceRepositoryError):
        repository(tmp_path).query(query)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"evidence_id": "../escape"},
        {"document_id": "../escape"},
        {"extraction_id": "../escape"},
        {"evidence_type": "valuation"},
        {"source_start_char": -1},
        {"source_end_char": True},
        {"source_start_char": 2, "source_end_char": 1},
        {"limit": 0},
        {"limit": 1_001},
        {"limit": True},
    ],
)
def test_invalid_query_filters_and_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(FinancialEvidenceRepositoryError):
        FinancialEvidenceQuery(**kwargs)


def test_strict_limit_reports_truncation_and_results_are_immutable(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.store(extraction())

    result = repo.query(FinancialEvidenceQuery(limit=1))

    assert len(result.records) == 1
    assert result.total_matches == 3
    assert result.truncated is True
    with pytest.raises(FrozenInstanceError):
        result.truncated = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.records[0] = result.records[0]  # type: ignore[index]


def test_temporary_files_are_ignored_but_symlink_escape_fails_closed(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)
    (repo.root / "records" / ".pending-interrupted.tmp").write_text("partial")

    assert len(repo.list_records()) == manifest.evidence_count

    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    link = repo.root / "records" / "escape.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    with pytest.raises(FinancialEvidenceRepositoryIntegrityError, match="unsafe"):
        repo.list_records()


def test_audit_success_and_corruption_detection(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manifest = extraction()
    repo.store(manifest)

    success = repo.audit()

    assert success.valid is True
    assert success.valid_record_count == manifest.evidence_count
    assert success.valid_extraction_count == 1
    target = repo.root / "records" / f"{manifest.evidence_records[0].evidence_id}.json"
    target.write_text("{}")
    failure = repo.audit()
    assert failure.valid is False
    assert failure.manifest is None
    assert failure.valid_record_count == manifest.evidence_count - 1
    assert failure.issues


def test_audit_reports_unsafe_committed_entry(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    unsafe = repo.root / "records" / "unexpected"
    unsafe.mkdir()

    result = repo.audit()

    assert result.valid is False
    assert result.committed_record_count == 1
    assert result.issues[0].relative_path == "records/unexpected"


def test_store_does_not_use_network_or_write_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    sentinel = tmp_path / "outside.txt"
    sentinel.write_text("unchanged")

    def deny_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", deny_network)
    repo = GovernedFinancialEvidenceRepository(root)
    repo.store(extraction())

    assert sentinel.read_text() == "unchanged"
    assert {path.name for path in tmp_path.iterdir()} == {"outside.txt", "repository"}
    assert all(path.is_relative_to(root) for path in root.rglob("*"))
