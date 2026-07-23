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
