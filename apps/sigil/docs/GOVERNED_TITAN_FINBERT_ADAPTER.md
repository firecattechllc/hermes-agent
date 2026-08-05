# Sigil Step 3 — Governed Titan FinBERT Adapter

## Purpose

Connect Sigil's `FinancialSentimentPort` to the locally certified FinBERT model
on Titan without coupling financial domain logic to SSH, HTTP, subprocesses, or
a particular deployment mechanism.

## Architecture

`TitanFinBERTAnalyzer` constructs a versioned, auditable inference envelope and
passes it through `TitanFinBERTTransport`.

A production transport may later use the governed Mac ↔ Titan Hermes link. The
adapter itself grants no shell access and performs no networking.

## Enforced constraints

Every request declares:

- local inference only
- no model download
- no external API
- no trade execution

Responses are rejected when their schema, model identity, score shape, score
range, or confidence is invalid.

## Availability behavior

`GovernedSentimentRouter` prefers Titan FinBERT. A deterministic offline analyzer
may be used only when fallback is explicitly enabled. Strict workflows can
disable fallback and fail closed.

## Out of scope

Step 3 does not:

- expose unrestricted SSH or shell execution
- download or update FinBERT
- start a Titan daemon
- call cloud models or market-data services
- trade, publish, spend, or modify portfolios
