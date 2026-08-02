# Governed AI status and inspection

Sigil exposes a bounded, credential-free inspection surface for its governed AI foundation. It is observational only: AI analysis is advisory, paper-only, and cannot authorize execution, mutate a portfolio, approve proposals, authorize capital, submit broker orders, execute shell commands, or invoke a model.

## Backend commands

The desktop bridge explicitly allowlists `ai_status`, `ai_registry_status`, `ai_evidence_status`, `ai_artifact_status`, `ai_recent_artifacts`, `ai_artifact_get`, and `ai_recent_failures`. Limits are integers from 1 through 50. Artifact lookup accepts only an exact immutable `analysis-artifact-<sha256>` identity. Unknown commands, malformed identities, arbitrary payloads, and oversized limits fail closed.

`ai_status` is a versioned summary of service enablement, registry and model counts, local Gemma configuration health, last success and failure, durable evidence and artifact health, record counts, and recoverable-tail detection. Its authority fields are invariant: `paper_only=true`, while `execution_authorized`, `broker_submission`, `portfolio_mutation`, `approval_authority`, and `secrets_exposed` are false.

Registry inspection returns only provider/model identities, family and version, execution location, declared capabilities, health and enabled states, trust/privacy/cost tiers, and allowed/prohibited responsibilities. It excludes endpoints, credentials, headers, authentication settings, and environment values.

Evidence inspection returns bounded immutable identities, routing correlations, provider/model/capability metadata, success or sanitized failure classification, digests, fallback state, timestamps, and paper-only authority. It never returns prompts or raw outputs. Artifact inspection reads and revalidates Phase 3 artifacts, returning bounded structured analysis fields, evidence identities, limitations, confidence, and freshness. Recent failures use a fixed sanitized message.

## Desktop boundary and presentation

Electron exposes one named `getAIStatus` preload method over a dedicated IPC channel; it does not expose generic backend invocation, prompt input, environment access, provider configuration, or artifact mutation. Existing context isolation and backend authority remain unchanged.

Mission Control adds a restrained, read-only AI Foundation panel. It shows enabled/disabled state, configured local Gemma availability, registry counts, evidence and artifact health, the latest success and failure classification, and explicit paper-only/no-execution-authority language. It has no AI execution controls and remains safe when AI, Ollama, a model, a route, evidence, or artifacts are unavailable or corrupt.

## Security and future providers

Inspection does not perform network health probes or model calls, so `configured_unverified` is intentionally distinct from available. Durable stores are opened only when their data and lock files already exist; truncated or corrupt data is reported without recovery writes. Credentials, raw prompts, raw provider output, secret-bearing URLs, and unrestricted environment information are never serialized.

FinBERT status, EmbeddingGemma retrieval status, Kronos forecast status, and Titan/Mac/Prime fleet status are future read-only extensions. They must retain this explicit allowlist, bounded sanitized contracts, immutable evidence, and advisory-only authority before they can be exposed.
