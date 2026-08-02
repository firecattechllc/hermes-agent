# Sigil governed AI evidence and local Gemma adapter

## Phase 2 boundary

Phase 2 connects the model-neutral Phase 1 contracts to durable local evidence and supplies one
backend-only Ollama-compatible Gemma adapter. It adds no renderer API, preload capability,
startup dependency, proposal approval, capital authorization, portfolio mutation, or broker
submission path. Model output remains advisory and `paper_only=true` with
`broker_submission=false` throughout the contracts and evidence ledger.

## Evidence lifecycle

`DurableAIEvidenceLedger` stores routing decisions, fallback decisions, invocation attempts,
successful results, failed results, and provider-health rejections. Each JSONL record contains
the evidence and correlation identities, provider/model/version and registry identity,
capability and execution location, timing, routing/fallback state, outcome classification,
input/output digests, sanitized metadata, paper-only flags, a strict sequence, previous-record
hash, and canonical SHA-256 entry hash.

The ledger deliberately does not extend the order-execution journal. That journal has a closed
execution-state machine and mixing advisory model evidence into it would weaken its domain
boundary. The AI ledger instead reuses the same repository durability conventions: an absolute
caller-supplied state root, non-symlink paths, mode-restricted files, advisory locking,
append-and-fsync, directory fsync, canonical JSON, immutable identities, and hash-chain
validation.

Only digests of model inputs and outputs are durable. Full prompts, raw outputs, credentials,
authorization headers, secret-bearing environment values, and credential-bearing metadata are
not written to the ledger.

## Restart and reconciliation

Opening an empty ledger returns no records. Valid history is loaded in deterministic sequence
order and survives process restart. Duplicate evidence identities, unexpected record shapes,
unsupported schema versions, sequence gaps, broken previous hashes, and invalid entry hashes
fail closed. A final unterminated record is treated as a torn append and truncated back to the
last fsynced newline; earlier validated records remain available.

Ledger construction does not audit or invoke a provider during application startup. Corrupt
history prevents new evidence-backed invocation from proceeding but does not grant authority,
alter portfolio state, or prevent the rest of Sigil from starting safely.

## Local Gemma configuration

The adapter reads configuration only through backend environment values:

- `SIGIL_AI_GEMMA_ENABLED`: `1`, `true`, or `yes` enables the adapter.
- `SIGIL_AI_GEMMA_ENDPOINT`: credential-free loopback HTTP endpoint, such as
  `http://127.0.0.1:11434`.
- `SIGIL_AI_GEMMA_MODEL`: configured Ollama model identity; no model size is hard-coded.
- `SIGIL_AI_GEMMA_MODEL_VERSION`: governed version label; defaults to `configured-v1`.
- `SIGIL_AI_GEMMA_TIMEOUT_MS`: bounded request timeout; defaults to 30000 ms.

Configuration is disabled when the enable flag is absent. Enabling without both endpoint and
model fails configuration validation. Only `localhost`, `127.0.0.1`, and `::1` HTTP endpoints
are accepted; user information, passwords, query strings, and fragments are rejected. No
credential is accepted or forwarded by this adapter.

## Health and invocation behavior

The health probe uses the Ollama-compatible `/api/tags` endpoint and requires the configured
model to be present. Invocation uses `/api/chat` with `format=json` and no streaming. Missing
Ollama, offline endpoints, absent models, timeouts, rejected requests, malformed envelopes,
malformed structured JSON, model mismatch, and capability mismatch become sanitized Phase 1
failure classifications. Every attempted invocation is evidence-backed when a ledger is
supplied.

No model is downloaded or installed. Tests use an injected deterministic transport and make no
external network calls.

## Hermes handoff

`GovernedModelWorkRequest` is the narrow future Hermes boundary. It carries task/request
correlations, one requested capability and responsibility, privacy requirements, digest-only
evidence context, and a governed structured-output contract. It converts to the existing Phase 1
routing request and carries no approval, capital, portfolio, shell, or broker authority. Full
Hermes orchestration is deferred.

## Future phases

Phase 3 should bind the ledger and adapter into a read-only backend analysis service, add
operator-visible provider/evidence status through the existing authority boundary, and define
durable output-artifact handling. Later registrations can add FinBERT sentiment,
EmbeddingGemma retrieval, Kronos forecasting, and governed fleet Gemma providers without
changing the ledger format or granting execution authority.
