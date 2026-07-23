"""Governed financial-document ingestion and evidence-extraction contracts."""

from .evidence import (
    SUPPORTED_EVIDENCE_TYPES,
    SUPPORTED_EXTRACTOR_VERSION,
    EvidenceExtractionManifest,
    EvidenceSpan,
    FinancialEvidenceExtractionError,
    FinancialEvidenceRecord,
    GovernedFinancialEvidenceExtractor,
)
from .ingestion import (
    DocumentChunk,
    DocumentIngestionError,
    FinancialDocument,
    FinancialDocumentIngestor,
    IngestedDocument,
    SourceProvenance,
)

__all__ = [
    "DocumentChunk",
    "DocumentIngestionError",
    "EvidenceExtractionManifest",
    "EvidenceSpan",
    "FinancialDocument",
    "FinancialDocumentIngestor",
    "FinancialEvidenceExtractionError",
    "FinancialEvidenceRecord",
    "GovernedFinancialEvidenceExtractor",
    "IngestedDocument",
    "SUPPORTED_EVIDENCE_TYPES",
    "SUPPORTED_EXTRACTOR_VERSION",
    "SourceProvenance",
]
