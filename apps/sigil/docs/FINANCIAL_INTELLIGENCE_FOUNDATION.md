# Sigil Step 2 — Financial Intelligence Foundation

## Purpose

Establish normalized, evidence-backed financial domain primitives and the first
governed sentiment-analysis workflow.

## Included

- Instruments and filing metadata
- Financial document provenance with SHA-256 integrity
- Explicit confidence scores and rationales
- Normalized sentiment results
- A model-neutral financial sentiment port
- A deterministic offline reference analyzer
- Hermes evidence recording for every completed analysis

## FinBERT boundary

FinBERT is intentionally implemented behind `FinancialSentimentPort`. The Step 2
foundation does not download models, call external APIs, or depend on Titan being
online. A future Titan adapter can load the already-certified local FinBERT model
and return the same `SentimentResult` contract.

## Governance

The workflow performs analysis only. It cannot trade, place orders, spend money,
publish research, modify portfolios, or bypass Hermes approval controls.
