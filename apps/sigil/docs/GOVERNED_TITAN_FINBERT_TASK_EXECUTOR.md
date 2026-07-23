# Sigil Step 5 — Governed Titan FinBERT Task Executor

## Purpose

Provide the server-side execution boundary for the Step 4 Hermes-link transport.

The executor accepts only the versioned `sigil.finbert.inference` task envelope,
validates its identity, constraints, capabilities, document limits, and model
selection, then delegates only the document text to an injected local FinBERT
runtime.

## Security boundary

The executor fails closed unless:

- the Hermes task and FinBERT request schemas are supported;
- the task type is exactly `sigil.finbert.inference`;
- shell, sudo, external network, trading, spending, and publishing are denied;
- inference is local-only;
- model downloads and external APIs are disabled;
- the requested model is the certified FinBERT model;
- document metadata and text are present and bounded;
- local inference returns non-negative probability mass.

## Runtime separation

This step deliberately does not import Transformers, download a model, expose a
network service, store secrets, or manage systemd. A Titan deployment adapter
must inject an already certified local FinBERT inference implementation.

The returned task result preserves the original task ID and matches the response
shape consumed by Sigil Steps 3 and 4.
