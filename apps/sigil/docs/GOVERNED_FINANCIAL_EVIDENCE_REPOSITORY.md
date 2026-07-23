# Sigil Step 8 — Governed Financial Evidence Repository

## Purpose and trust boundary

Step 8 provides deterministic, local persistence for validated
`EvidenceExtractionManifest` values produced by Step 7. It stores and retrieves
evidence; it does not interpret what that evidence means.

The caller must supply an explicit, existing repository root. Step 8 creates its
layout only inside that root. It does not write to a home directory, global
Hermes state, or the Hermes knowledge graph. Inputs cross a fail-closed trust
boundary: Step 7 record identities, extraction-manifest hashes, supported
versions, and model validation are checked again before storage. Every stored
value is validated again when read.

## Storage layout

The standard-library-only repository uses canonical JSON:

```text
<caller-supplied-root>/
  records/
    sigil-evidence-<sha256>.json
  extractions/
    <step-7-manifest-sha256>.json
```

Each evidence file contains one immutable Step 7 record, the repository schema
version, and a SHA-256 hash of the canonical evidence payload. Each extraction
file preserves the Step 7 document ID, extractor version, ordered evidence IDs,
and deterministic extraction ID. Indexes and repository metadata are derived
from these committed files rather than maintained as mutable state.

Hidden pending files and files ending in `.tmp` are never committed records.
Unexpected visible entries, symlinks, malformed JSON, schema mismatches,
filename/identity mismatches, missing associations, and hash failures fail
closed.

## Immutable and idempotent writes

Writes use a temporary file in the destination directory, flush and `fsync` its
contents, create the final filename with create-only hard-link semantics, and
`fsync` the directory. A final record is therefore never exposed with partial
content.

The first write creates the immutable record. Repeating the exact write is
idempotent and reports the records that already existed. Different content
under an existing evidence ID or extraction ID raises
`FinancialEvidenceConflictError`; existing content is never replaced, deleted,
or repaired. Existing bundle records are preflighted before new records are
appended.

## Canonicalization and integrity

Canonical JSON is UTF-8 with sorted keys, compact separators, Unicode preserved,
non-finite numbers rejected, and duplicate stored keys rejected. SHA-256 is used
for evidence payload, extraction, and repository hashes. Timestamps are not
added by Step 8 and cannot influence any identity.

Step 8 preserves the evidence IDs and extraction hash produced by Step 7. It
recomputes the Step 7 identity material and refuses forged or internally
inconsistent values. `repository_manifest()` deterministically sorts committed
identifiers and hashes their content hashes and extraction associations, so the
same records yield the same repository manifest across runs and platforms.

## Bounded exact retrieval

`FinancialEvidenceQuery` supports exact filters for evidence ID, document ID,
extraction ID, evidence type, and exact source-span start/end offsets. Results
are ordered by evidence ID and returned as immutable tuples. Limits must be
between 1 and 1,000. `total_matches` and `truncated` make every limited result
explicit; retrieval never silently truncates.

`get()` provides exact evidence-ID lookup with explicit
`FinancialEvidenceNotFoundError`. Extraction lookup also reports missing
manifests explicitly. There is no fuzzy, semantic, probabilistic, or model-based
search.

## Read-only audit

`audit()` enumerates committed record and extraction entries, validates every
file and association, reports deterministic structured issues and valid/total
counts, and recomputes the repository manifest only when the repository is
fully valid. Audit never writes, deletes, or repairs data.

## Hermes handoff and future integration

Step 8 is the durable boundary after governed ingestion and extraction:

```text
document
→ Step 6 governed ingestion
→ Step 7 deterministic evidence extraction
→ Step 8 immutable governed evidence repository
→ future Hermes knowledge-graph registration and governed financial analysis
```

Future Hermes integrations may authorize repository creation, register validated
identities in the global knowledge graph, attach approval evidence, or expose
governed service ports. Those integrations must retain Step 8 provenance,
identity, bounds, and fail-closed behavior rather than bypassing them.

## Explicit non-capabilities

Step 8 does not recommend or score securities, infer unsupported facts, manage
portfolios, trade, call external APIs, access the network, invoke an LLM, perform
semantic search, schedule autonomous research, approve actions, mutate evidence,
repair corruption, or write directly to the Hermes knowledge graph.
