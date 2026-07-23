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

## Step 7: Governed financial-evidence extraction

Sigil can now turn validated Step 6 chunks into bounded, keyword-matched
financial evidence records with exact source spans, stable SHA-256 identities,
deterministic ordering, and a hashed extraction manifest. Extraction remains
offline and side-effect free; Hermes retains orchestration, approvals,
knowledge-graph operations, and durable evidence storage.

## Step 8: Governed financial-evidence repository

Sigil can now persist validated Step 7 extraction manifests in an explicit
caller-supplied local repository using immutable canonical JSON records,
append-safe idempotent writes, SHA-256 integrity checks, bounded exact queries,
and read-only deterministic audits. The repository stores evidence without
interpreting it and does not perform analysis, network access, model inference,
trading, or direct Hermes knowledge-graph mutation.

## Step 9: Governed external financial-data providers

Sigil now has a provider-neutral, immutable acquisition boundary with an explicit
adapter registry, exact HTTPS host and operation allowlists, runtime-only credential
injection, bounded transport/retries/rate limits/cache, normalized JSON envelopes, and
non-secret provenance. The initial SEC EDGAR adapter supports bounded company
submissions and company facts requests by validated CIK and requires an honest
caller-supplied SEC user-agent identity. Step 9 obtains data only; it does not interpret
it, recommend investments, trade, schedule collection, or write into evidence or graph
stores. See
[`GOVERNED_EXTERNAL_FINANCIAL_DATA_PROVIDERS.md`](docs/GOVERNED_EXTERNAL_FINANCIAL_DATA_PROVIDERS.md).

### Step 9A: Governed Alpaca read-only market data

The optional Alpaca adapter adds four bounded, GET-only stock market-data operations
through the same provider-neutral boundary: latest bar, latest quote, latest trade,
and historical bars. It is restricted to `data.alpaca.markets`, requires both
runtime-only Alpaca headers, and cannot access brokerage state or place trades,
including paper trades. See
[`GOVERNED_ALPACA_MARKET_DATA_ADAPTER.md`](docs/GOVERNED_ALPACA_MARKET_DATA_ADAPTER.md).
