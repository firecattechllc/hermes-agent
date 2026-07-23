# Sigil

Sigil is FireCat's Hermes-native financial intelligence application.

## Boundary

Sigil owns domain behavior: company research, market intelligence, portfolio analysis,
strategy, risk, watchlists, and financial reports.

Sigil does **not** reimplement Hermes orchestration, scheduling, memory, model routing,
knowledge graph, discovery, governance, approvals, or evidence storage. Those capabilities
are consumed through explicit ports in `sigil.integrations.hermes`.

## First vertical slice

`ResearchCompanyWorkflow` validates a company-research request, retrieves Hermes graph and
memory context, requests governed analysis, records evidence, and returns a structured report.
The default test adapter is deterministic and offline; production Hermes transport is deliberately
left behind the same interface.

## Local verification

```bash
cd apps/sigil
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
```

## Step 2: Financial intelligence

Sigil now includes normalized instruments, filing metadata, source provenance,
confidence scoring, sentiment contracts, and a governed offline financial-text
analysis workflow. FinBERT implementations plug in through
`FinancialSentimentPort`.

## Step 3: Governed Titan FinBERT adapter

Sigil can now route financial sentiment analysis through a versioned,
transport-neutral Titan FinBERT adapter. Requests require local-only inference,
forbid downloads and external APIs, validate model identity and response shape,
and may use an explicitly governed deterministic fallback.

## Step 4: Hermes-link FinBERT transport

Sigil can now submit FinBERT inference through the governed Hermes `POST /task`
interface. The transport requires authenticated HTTPS (or loopback HTTP for
tests), enforces time and response-size limits, denies dangerous capabilities,
and validates task correlation and completion before accepting a result.

## Step 5: Governed Titan FinBERT task executor

Sigil now includes the server-side execution boundary for Hermes-link FinBERT
tasks. It accepts only the versioned financial-sentiment task contract, denies
dangerous capabilities, enforces local-only inference constraints and document
limits, invokes an injected certified local runtime, normalizes probabilities,
and returns a strictly correlated completed-task result.

## Step 6: Governed financial-document ingestion

Sigil can now validate and normalize bounded financial documents, preserve source
provenance, compute deterministic SHA-256 identities, reject duplicate content,
produce stable overlapping chunks, and emit evidence-ready manifests for Hermes
storage. This boundary remains offline and does not browse, download, summarize,
trade, or create a competing evidence database.
