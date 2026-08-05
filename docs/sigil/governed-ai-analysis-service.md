# Sigil governed AI analysis service

## Service lifecycle

The Phase 3 service is a Python/backend-domain coordinator over the existing model registry,
capability router, provider protocol, local Gemma adapter, AI evidence ledger, and Hermes handoff.
It is disabled unless explicitly constructed as enabled. It has no renderer, preload, startup,
shell, portfolio, approval, policy, or broker surface.

The request lifecycle is validation, governed routing, durable routing evidence, durable provider
attempt evidence, provider invocation, durable result evidence, structured-output validation,
sanitization, immutable artifact construction, and durable artifact commit. Any rejected stage
returns a structured fail-closed response with no authoritative artifact.

## Request contract and routing

`GovernedAnalysisRequest` is immutable and schema-versioned. It accepts correlation identities,
one declared capability, an advisory responsibility, privacy/cost/trust constraints, ordered
execution locations, explicit fallback permission, a bounded timeout, exact SHA-256 input and
evidence-context identities, the expected output schema, and a timestamp.

Only research analysis, proposal support, evidence summarization, risk analysis, market context,
and orchestration support are accepted. Capital authorization, proposal approval, policy change,
broker submission, order execution, portfolio mutation, credential access, and unrestricted shell
execution are explicitly prohibited. Gemma is only a family preference; registry eligibility and
all governance constraints remain authoritative.

## Provider invocation and output validation

Providers receive a bounded structured object containing only the input digest, evidence-context
digests, expected schema identity, and advisory responsibility. They do not receive renderer
configuration, environment dumps, arbitrary shell instructions, or credentials.

Phase 3 supports `sigil.ai.output.generic-analysis.v1`, containing summary, findings, risks,
trusted evidence references, limitations, and optional confidence. Validation rejects missing or
additional fields, malformed types, oversized output, execution or order instructions,
credential material, and evidence references outside the request context. Raw provider text is
never the authoritative artifact.

## Durable artifact persistence

Sanitized `GovernedAnalysisArtifact` records bind request/task, provider/model/version,
capability/responsibility, routing and invocation evidence, input/output digests, structured
payload, citations, confidence, limitations, and freshness metadata. They permanently assert:

- `paper_only=true`
- `execution_authorized=false`
- `broker_submission=false`
- `portfolio_mutation=false`
- `approval_authority=false`

`DurableAnalysisArtifactStore` is distinct from execution state and AI attempt evidence. It uses
an absolute non-symlink root, restrictive files, advisory locking, canonical JSON, strict
sequence, previous-record hashes, immutable artifact identities, append-and-fsync, directory
fsync, duplicate rejection, and torn-tail recovery. Loaded artifacts are revalidated against the
same output safety schema. Corrupt stores appear as unavailable through service status and cannot
grant authority or prevent the rest of Sigil from starting.

## Evidence and failure behavior

The existing AI evidence ledger remains the source for routing, fallback, invocation attempt,
provider result, provider-health, and output-rejection evidence. Evidence persistence failure
blocks analysis before an artifact can be created. Provider unavailable, timeout, identity or
capability mismatch, privacy/trust rejection, disabled fallback, no candidate, unsafe output, and
artifact corruption all return structured classifications. The service status reports enabled
state, provider health, registry revision, last artifact, last failure, evidence/artifact health,
configured models, paper-only state, and disabled broker submission.

## Hermes handoff

`analyze_hermes` consumes the existing digest-only `GovernedModelWorkRequest`, derives a bounded
analysis input identity, and returns the same artifact/evidence identities, sanitized payload,
limitations, routing summary, or failure classification. It adds no scheduling, autonomous loop,
delegation, approval, or execution behavior.

## Configuration and startup independence

Provider endpoint/model configuration remains backend-only and the local Gemma endpoint remains
credential-free loopback HTTP. Phase 2 environment variables continue to govern that adapter.
The service does not participate in desktop/backend startup unless a backend owner explicitly
constructs it. Missing Ollama, missing models, disabled service, empty stores, recovered torn
tails, provider timeouts, and no eligible candidate do not affect runtime snapshots, paper state,
proposal review, the audit timeline, or packaged launch.

## Future integration

Phase 4 should add read-only backend status/query commands and operator-visible evidence/artifact
inspection without exposing provider configuration. Later providers can register FinBERT for
sentiment, EmbeddingGemma for retrieval, Kronos for forecasting, and Titan/Mac/Prime as governed
fleet locations through the existing registry and provider protocol. Those integrations must not
change the artifact safety schema or execution-authority boundary.
