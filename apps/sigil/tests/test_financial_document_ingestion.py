from datetime import date, datetime, timedelta, timezone

import pytest

from sigil.integrations.documents import (
    DocumentIngestionError,
    FinancialDocument,
    FinancialDocumentIngestor,
    SourceProvenance,
)


def make_document(content: str = "Revenue increased 12%. Operating margin expanded.") -> FinancialDocument:
    return FinancialDocument(
        issuer="FireCat Holdings",
        document_type="10-Q",
        title="Quarterly report",
        published_on=date(2026, 7, 20),
        content=content,
        provenance=SourceProvenance(
            source_url="https://www.sec.gov/Archives/example.txt",
            retrieved_at=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            publisher="U.S. Securities and Exchange Commission",
            accession_number="0000000000-26-000001",
        ),
        metadata=(("ticker", "FIRE"), ("fiscal_period", "Q2")),
    )


def test_ingestion_is_deterministic() -> None:
    first = FinancialDocumentIngestor(chunk_chars=200, overlap_chars=20).ingest(make_document())
    second = FinancialDocumentIngestor(chunk_chars=200, overlap_chars=20).ingest(make_document())

    assert first.document_id == second.document_id
    assert first.content_sha256 == second.content_sha256
    assert first.chunks == second.chunks


def test_normalizes_whitespace_without_rewriting_text() -> None:
    result = FinancialDocumentIngestor().ingest(
        make_document("Revenue   increased.\r\n\r\n\r\nMargin\timproved.")
    )

    assert result.normalized_content == "Revenue increased.\n\nMargin improved."


def test_rejects_duplicate_content_within_ingestor() -> None:
    ingestor = FinancialDocumentIngestor()
    ingestor.ingest(make_document())

    with pytest.raises(DocumentIngestionError, match="duplicate document content"):
        ingestor.ingest(make_document())


def test_rejects_non_https_source() -> None:
    document = make_document()
    unsafe = FinancialDocument(
        issuer=document.issuer,
        document_type=document.document_type,
        title=document.title,
        published_on=document.published_on,
        content=document.content,
        provenance=SourceProvenance(
            source_url="http://example.com/report",
            retrieved_at=document.provenance.retrieved_at,
            publisher="Example",
        ),
    )

    with pytest.raises(DocumentIngestionError, match="absolute HTTPS"):
        FinancialDocumentIngestor().ingest(unsafe)


def test_rejects_future_retrieval_timestamp() -> None:
    document = make_document()
    future = FinancialDocument(
        issuer=document.issuer,
        document_type=document.document_type,
        title=document.title,
        published_on=document.published_on,
        content=document.content,
        provenance=SourceProvenance(
            source_url=document.provenance.source_url,
            retrieved_at=datetime.now(timezone.utc) + timedelta(days=1),
            publisher=document.provenance.publisher,
        ),
    )

    with pytest.raises(DocumentIngestionError, match="future"):
        FinancialDocumentIngestor().ingest(future)


def test_rejects_unsupported_document_type() -> None:
    document = make_document()
    unsupported = FinancialDocument(
        issuer=document.issuer,
        document_type="tweet",
        title=document.title,
        published_on=document.published_on,
        content=document.content,
        provenance=document.provenance,
    )

    with pytest.raises(DocumentIngestionError, match="unsupported document_type"):
        FinancialDocumentIngestor().ingest(unsupported)


def test_chunk_ids_and_hashes_are_stable_and_correlated() -> None:
    content = " ".join(f"Sentence {index} reports financial performance." for index in range(80))
    result = FinancialDocumentIngestor(chunk_chars=300, overlap_chars=40).ingest(
        make_document(content)
    )

    assert len(result.chunks) > 1
    assert [chunk.index for chunk in result.chunks] == list(range(len(result.chunks)))
    assert all(chunk.document_id == result.document_id for chunk in result.chunks)
    assert all(chunk.chunk_id.startswith(result.document_id) for chunk in result.chunks)
    assert all(len(chunk.sha256) == 64 for chunk in result.chunks)


def test_evidence_manifest_omits_raw_text_but_preserves_traceability() -> None:
    result = FinancialDocumentIngestor().ingest(make_document())
    manifest = result.evidence_manifest()

    assert manifest["document_id"] == result.document_id
    assert manifest["source_url"] == result.provenance.source_url
    assert manifest["accession_number"] == "0000000000-26-000001"
    assert manifest["metadata"] == {"ticker": "FIRE", "fiscal_period": "Q2"}
    assert "normalized_content" not in manifest
    assert "text" not in manifest["chunks"][0]


def test_metadata_rejects_duplicate_keys() -> None:
    document = make_document()
    invalid = FinancialDocument(
        issuer=document.issuer,
        document_type=document.document_type,
        title=document.title,
        published_on=document.published_on,
        content=document.content,
        provenance=document.provenance,
        metadata=(("ticker", "FIRE"), ("ticker", "FIRE2")),
    )

    with pytest.raises(DocumentIngestionError, match="duplicate metadata key"):
        FinancialDocumentIngestor().ingest(invalid)


def test_governed_chunk_configuration_is_enforced() -> None:
    with pytest.raises(DocumentIngestionError, match="chunk_chars"):
        FinancialDocumentIngestor(chunk_chars=100)

    with pytest.raises(DocumentIngestionError, match="smaller"):
        FinancialDocumentIngestor(chunk_chars=200, overlap_chars=200)
