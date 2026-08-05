# Sigil Step 7 — Governed Financial Evidence Extraction

## Purpose and boundary

Step 7 converts validated Step 6 financial-document chunks into bounded,
traceable financial evidence records. Sigil owns the domain-specific matching
and validation. Hermes remains the source of truth for orchestration, approvals,
memory, scheduling, knowledge-graph operations, and durable evidence storage.

The extractor is deterministic, offline, side-effect free, standard-library
only, and fully driven by injected `IngestedDocument` values.

## Data flow

1. A governed upstream collector supplies text and provenance to Step 6.
2. Step 6 validates, normalizes, hashes, and chunks the document.
3. Step 7 accepts only that validated `IngestedDocument` contract.
4. Step 7 revalidates document/chunk identity, ordering, hashes, and exact source
   relationships before matching.
5. Transparent keyword rules select sentences and retain their exact,
   document-relative source spans.
6. The resulting `EvidenceExtractionManifest` can be handed to a governed Hermes
   integration for authorization and durable evidence persistence.

Step 7 itself neither writes evidence nor invokes Hermes.

## Deterministic identity and hashing

An evidence ID is SHA-256 over canonical JSON containing the document ID,
evidence type, exact document-relative span, exact-text hash, and supported
extractor version. Records are sorted by source position, evidence type, and ID.
Overlap-derived records with the same type and exact document span are
suppressed; conflicting duplicates fail closed.

The manifest hash is SHA-256 over canonical JSON containing its document ID,
extractor version, and ordered evidence identity and source material. Identical
validated input and configuration therefore produce identical records, IDs,
ordering, and manifest hashes.

## Supported evidence types

- `revenue`
- `profit`
- `loss`
- `guidance`
- `risk`
- `liquidity`
- `debt`
- `cash_flow`
- `dividend`
- `capital_expenditure`

## Limits and limitations

Configuration is bounded for chunks per document, evidence records per chunk,
and normalized claim length. Claims only normalize whitespace; `exact_text`
always preserves the precise source substring.

Keyword matching identifies candidate statements, not verified economic truth.
It does not understand negation, tables, accounting context, materiality, issuer
comparisons, or whether a statement is predictive. A keyword match must not be
treated as an investment conclusion. Downstream workflows must retain source
traceability and apply their own governed validation.

## Explicit non-capabilities

Step 7 cannot browse, download, call an external model, trade, publish, spend,
schedule work, approve actions, make autonomous investment decisions, modify a
knowledge graph, or create a competing evidence store. Hermes performs any
authorized evidence-storage handoff through its existing governed interfaces.
