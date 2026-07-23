from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256

import pytest

from sigil.integrations.documents import (
    SUPPORTED_EXTRACTOR_VERSION,
    DocumentChunk,
    EvidenceExtractionManifest,
    EvidenceSpan,
    FinancialDocument,
    FinancialDocumentIngestor,
    FinancialEvidenceExtractionError,
    FinancialEvidenceRecord,
    GovernedFinancialEvidenceExtractor,
    SourceProvenance,
)


def ingest(content: str) -> object:
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
    return FinancialDocumentIngestor(chunk_chars=200, overlap_chars=40).ingest(document)


def make_record(**changes: object) -> FinancialEvidenceRecord:
    values = {
        "evidence_id": "sigil-evidence-example",
        "document_id": "sigil-doc-example",
        "chunk_id": "sigil-doc-example-chunk-0000-example",
        "issuer": "FireCat Holdings",
        "evidence_type": "revenue",
        "claim": "Revenue increased.",
        "span": EvidenceSpan(0, 18, "Revenue increased."),
        "source_url": "https://example.com/report",
        "published_on": date(2026, 7, 20),
        "content_hash": sha256(b"content").hexdigest(),
        "confidence": 1.0,
        "extractor_version": SUPPORTED_EXTRACTOR_VERSION,
    }
    values.update(changes)
    return FinancialEvidenceRecord(**values)


def test_extracts_multiple_types_with_exact_document_spans() -> None:
    document = ingest(
        "Revenue increased 12%. Debt declined while liquidity remained strong. "
        "The board declared a dividend."
    )

    manifest = GovernedFinancialEvidenceExtractor().extract(document)

    assert {record.evidence_type for record in manifest.evidence_records} == {
        "revenue",
        "debt",
        "liquidity",
        "dividend",
    }
    assert manifest.evidence_count == 4
    for record in manifest.evidence_records:
        assert (
            document.normalized_content[record.span.start_char : record.span.end_char]
            == record.span.exact_text
        )
        assert record.claim == record.span.exact_text
        assert record.document_id == document.document_id
        assert record.source_url == document.provenance.source_url


def test_ids_hash_and_ordering_are_stable_across_runs() -> None:
    document = ingest("Debt fell. Revenue rose. Cash flow improved and risk declined.")
    extractor = GovernedFinancialEvidenceExtractor()

    first = extractor.extract(document)
    second = extractor.extract(document)

    assert first == second
    assert [record.evidence_id for record in first.evidence_records] == [
        record.evidence_id for record in second.evidence_records
    ]
    assert first.deterministic_hash == second.deterministic_hash
    assert [(record.span.start_char, record.evidence_type) for record in first.evidence_records] == [
        (0, "debt"),
        (11, "revenue"),
        (25, "cash_flow"),
        (25, "risk"),
    ]


def test_no_matches_produces_empty_valid_manifest() -> None:
    document = ingest("The meeting began at noon. The chair called the meeting to order.")

    manifest = GovernedFinancialEvidenceExtractor().extract(document)

    assert manifest.evidence_records == ()
    assert manifest.evidence_count == 0
    assert len(manifest.deterministic_hash) == 64


def test_overlap_derived_duplicate_is_suppressed() -> None:
    sentence = "Revenue increased significantly during the reported quarter."
    document = ingest(f"{'Background text. ' * 10}{sentence} {sentence}")

    manifest = GovernedFinancialEvidenceExtractor().extract(document)
    identities = [
        (record.evidence_type, record.span.start_char, record.span.end_char)
        for record in manifest.evidence_records
    ]

    assert len(identities) == len(set(identities))


def test_rejects_non_step6_input_and_malformed_chunk_relationships() -> None:
    extractor = GovernedFinancialEvidenceExtractor()
    with pytest.raises(FinancialEvidenceExtractionError, match="IngestedDocument"):
        extractor.extract(object())

    document = ingest("Revenue increased.")
    mismatched = replace(
        document,
        chunks=(replace(document.chunks[0], document_id="sigil-doc-wrong"),),
    )
    with pytest.raises(FinancialEvidenceExtractionError, match="document_id"):
        extractor.extract(mismatched)

    bad_hash = replace(
        document,
        chunks=(replace(document.chunks[0], sha256="0" * 64),),
    )
    with pytest.raises(FinancialEvidenceExtractionError, match="sha256 does not match"):
        extractor.extract(bad_hash)

    bad_span = replace(
        document,
        chunks=(replace(document.chunks[0], end_char=len(document.normalized_content) + 1),),
    )
    with pytest.raises(FinancialEvidenceExtractionError, match="invalid source span"):
        extractor.extract(bad_span)

    bad_id = replace(
        document,
        chunks=(replace(document.chunks[0], chunk_id=f"{document.document_id}-chunk-0000-bad"),),
    )
    with pytest.raises(FinancialEvidenceExtractionError, match="chunk_id"):
        extractor.extract(bad_id)

    unsupported_schema = replace(document, schema_version=2)
    with pytest.raises(FinancialEvidenceExtractionError, match="schema_version"):
        extractor.extract(unsupported_schema)


def test_limits_are_enforced() -> None:
    document = ingest("Revenue and debt and risk were discussed.")

    with pytest.raises(FinancialEvidenceExtractionError, match="max_chunks"):
        GovernedFinancialEvidenceExtractor(max_chunks_per_document=0)
    with pytest.raises(FinancialEvidenceExtractionError, match="max_chunks_per_document"):
        GovernedFinancialEvidenceExtractor(max_chunks_per_document=1).extract(
            ingest(" ".join(f"Revenue item {index}." for index in range(100)))
        )
    with pytest.raises(FinancialEvidenceExtractionError, match="max_evidence_records"):
        GovernedFinancialEvidenceExtractor(max_evidence_records_per_chunk=2).extract(document)
    with pytest.raises(FinancialEvidenceExtractionError, match="max_claim_length"):
        GovernedFinancialEvidenceExtractor(max_claim_length=10).extract(document)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_url": "http://example.com/report"}, "HTTPS"),
        ({"content_hash": "ABC"}, "SHA-256"),
        ({"confidence": float("nan")}, "between 0 and 1"),
        ({"confidence": 1.1}, "between 0 and 1"),
        ({"extractor_version": "future-version"}, "extractor_version"),
        ({"evidence_type": "valuation"}, "unsupported evidence_type"),
    ],
)
def test_record_validation_fails_closed(changes: dict[str, object], message: str) -> None:
    with pytest.raises(FinancialEvidenceExtractionError, match=message):
        make_record(**changes)


@pytest.mark.parametrize(
    "span",
    [
        (-1, 1, "a"),
        (1, 1, "a"),
        (0, 1, ""),
        (0, 2, "a"),
    ],
)
def test_span_validation_fails_closed(span: tuple[int, int, str]) -> None:
    with pytest.raises(FinancialEvidenceExtractionError):
        EvidenceSpan(*span)


def test_manifest_rejects_count_document_duplicate_and_digest_errors() -> None:
    record = make_record()
    base = {
        "document_id": record.document_id,
        "extractor_version": SUPPORTED_EXTRACTOR_VERSION,
        "evidence_records": (record,),
        "evidence_count": 1,
        "deterministic_hash": "0" * 64,
    }

    with pytest.raises(FinancialEvidenceExtractionError, match="evidence_count"):
        EvidenceExtractionManifest(**(base | {"evidence_count": 0}))
    with pytest.raises(FinancialEvidenceExtractionError, match="manifest document_id"):
        EvidenceExtractionManifest(**(base | {"document_id": "sigil-doc-wrong"}))
    with pytest.raises(FinancialEvidenceExtractionError, match="duplicate evidence_id"):
        EvidenceExtractionManifest(
            **(base | {"evidence_records": (record, record), "evidence_count": 2})
        )
    with pytest.raises(FinancialEvidenceExtractionError, match="SHA-256"):
        EvidenceExtractionManifest(**(base | {"deterministic_hash": "invalid"}))


def test_only_standard_library_rule_matching_is_used() -> None:
    document = ingest("Capital expenditures rose. The outlook includes liquidity risk.")

    records = GovernedFinancialEvidenceExtractor().extract(document).evidence_records

    assert {record.evidence_type for record in records} == {
        "capital_expenditure",
        "guidance",
        "liquidity",
        "risk",
    }
    assert all(record.confidence == 1.0 for record in records)


def test_forged_chunk_type_is_rejected() -> None:
    document = ingest("Revenue increased.")
    forged = replace(
        document,
        chunks=(
            DocumentChunk(
                chunk_id=document.chunks[0].chunk_id,
                document_id=document.document_id,
                index=0,
                text="Revenue declined.",
                start_char=0,
                end_char=len(document.normalized_content),
                sha256=sha256(b"Revenue declined.").hexdigest(),
            ),
        ),
    )

    with pytest.raises(FinancialEvidenceExtractionError, match="source span"):
        GovernedFinancialEvidenceExtractor().extract(forged)
