"""Governed financial-document ingestion contracts."""

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
    "FinancialDocument",
    "FinancialDocumentIngestor",
    "IngestedDocument",
    "SourceProvenance",
]
