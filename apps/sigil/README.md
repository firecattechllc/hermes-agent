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

### Step 9B: Governed Public equity trading execution

The optional Public adapter adds real-money-capable, policy-gated equity/ETF execution
without turning provider acquisition into a mutation API. Every order requires a
canonical immutable proposal, fresh portfolio safety checks, exact Public preflight, a
separate exact single-use human approval, and a submission intent recorded before the
outbound request. UUID order IDs are reused after ambiguous transport outcomes, status
reconciliation is explicit, and cancellation has its own approval. Execution is disabled
without an explicit finite policy. Margin, shorts, options, crypto, bonds, replacement,
extended hours, autonomous scheduling, hosted MCP use, and arbitrary broker calls are
forbidden. See
[`GOVERNED_PUBLIC_EQUITY_TRADING_EXECUTION.md`](docs/GOVERNED_PUBLIC_EQUITY_TRADING_EXECUTION.md).

### Step 10: Governed durable execution journal and reconciliation

Sigil can now inject a caller-supplied local execution journal into Step 9B. The
journal persists immutable canonical JSON lifecycle events in a SHA-256 hash chain,
uses locked atomic append and file/directory durability barriers, rejects corruption
and conflicts, and provides read-only recovery classifications. Submission and
cancellation intent are durable before broker mutation; ambiguous outcomes require
exact reconciliation with the original UUID and never trigger automatic broker
mutation. Human approvals and all Step 9B trading restrictions remain unchanged. See
[`GOVERNED_DURABLE_EXECUTION_JOURNAL.md`](docs/GOVERNED_DURABLE_EXECUTION_JOURNAL.md).

### Step 11: Governed brokerage account and portfolio state

Sigil can now acquire bounded, read-only Public account, position, order, and execution state
as immutable exact-decimal snapshots bound to one protected account. Canonical SHA-256
identities cover normalized state, timestamps, provenance, completeness, and truncation.
Caller-supplied freshness policy produces an explicit fail-closed pre-trade eligibility result.
A separate read-only reconciliation service compares broker truth with Step 10 intent and
history without mutating either system or treating absence as retry authority. See
[`GOVERNED_BROKERAGE_PORTFOLIO_STATE.md`](docs/GOVERNED_BROKERAGE_PORTFOLIO_STATE.md).

### Step 12: Governed portfolio ledger and performance accounting

Sigil now derives an immutable, exact-decimal portfolio accounting view from
provenance-backed broker activity and Step 11 observations. A caller-supplied
local repository persists closed normalized event types as canonical JSON in a
strict SHA-256 hash chain with atomic locked append, bounded storage, exact
idempotency, and fail-closed corruption detection. Deterministic replay produces
cash, FIFO lots, basis, realized and unrealized gain or loss, valuation,
time-weighted and bounded money-weighted returns, benchmark comparison, and
governed period-close artifacts. Reconciliation remains read-only, incomplete
history is never called lifetime performance, and no accounting result can
authorize a trade. See
[`GOVERNED_PORTFOLIO_LEDGER_ACCOUNTING.md`](docs/GOVERNED_PORTFOLIO_LEDGER_ACCOUNTING.md).

### Step 13: Governed portfolio risk engine

Sigil now derives immutable, exact-decimal portfolio exposure, concentration,
liquidity, historical volatility/drawdown, correlation/covariance, benchmark
beta and tracking error, historical VaR and expected shortfall, caller-defined
stress results, and policy-limit violations from exact Step 11/12 and
caller-supplied normalized inputs. Read-only proposed-trade simulation fails
closed on stale, partial, unavailable, margin/short, or blocking-limit
conditions; eligibility is advisory and can never approve or execute a trade.
See
[`GOVERNED_PORTFOLIO_RISK_ENGINE.md`](docs/GOVERNED_PORTFOLIO_RISK_ENGINE.md).

### Step 14: Governed research dossier engine

Sigil can now organize exact issuer/security identity, verified evidence claims,
normalized financial history, deterministic derived analyses, business and
governance context, filings, risks, sentiment, valuation, and optional portfolio
and risk relevance into an immutable SHA-256-identified dossier. Conflicts,
gaps, staleness, truncation, questions, completeness, and conclusion provenance
remain explicit. Construction and same-security comparison are offline and
side-effect free; a dossier cannot browse, recommend, approve, trade, mutate
Steps 9B–13, or write to the Hermes knowledge graph. See
[`GOVERNED_RESEARCH_DOSSIER_ENGINE.md`](docs/GOVERNED_RESEARCH_DOSSIER_ENGINE.md).

### Step 15: Governed investment thesis and counter-thesis engine

Sigil can now transform one exact Step 14 dossier into immutable,
SHA-256-identified thesis and independently evidenced counter-thesis arguments.
The engine preserves claim provenance, contradictions, gaps, staleness,
assumptions, causal interpretations, catalysts, risks, testable invalidation
conditions, falsification tests, monitoring contracts, valuation dependencies,
and optional portfolio/risk relevance. Transparent confidence, completeness,
and review-readiness classifications fail closed; readiness is never a trade
approval. Construction, audit, regeneration, and comparison are offline,
deterministic, non-advisory, and side-effect free. See
[`GOVERNED_INVESTMENT_THESIS_ENGINE.md`](docs/GOVERNED_INVESTMENT_THESIS_ENGINE.md).
