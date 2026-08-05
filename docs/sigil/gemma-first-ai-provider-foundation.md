# Sigil Gemma-first governed AI provider foundation

## Scope

Phase 1 establishes model-neutral contracts and deterministic routing evidence. It does not
download models, call a network provider, expose AI through Electron, or grant execution
authority. Sigil remains safe to start when every AI provider is unavailable.

## Gemma-first, model-agnostic design

Gemma is a preference expressed by `preferred_model_family="gemma"` and an ordered execution
location preference. It is not a runtime dependency. Capability suitability, privacy, trust,
health, cost, enabled state, and responsibility policy are hard eligibility gates and always
override preference. The default representable order is local Gemma, fleet Gemma, a governed
specialist, then an approved external fallback.

## Provider contract

`sigil.ai.provider.ModelProvider` declares provider/model identity, capabilities, versioned
input and output contracts, timeout, execution location, health, and structured results.
`DeterministicProvider` exercises success, unavailable, timeout, malformed-output, and
capability/model-identity mismatch outcomes without model downloads or network calls.

## Registry schema

`GovernedModelRegistry` contains immutable provider and model registrations. Each model records
family/version, versioned capabilities, execution location, context limit, input types,
structured-output support, cost/trust/privacy tiers, health, enabled state, and allowed and
prohibited responsibilities. The registry rejects duplicates, unknown providers, location
contradictions, and incomplete governance prohibitions. Its revision is a canonical SHA-256
identity over sorted registry metadata.

No model may authorize capital, change policy, approve a proposal, submit a broker order,
bypass operator confirmation, or fabricate missing evidence.

## Capability routing

Capabilities are explicit `.v1` identifiers. A routing request supplies required capabilities,
preferred family, privacy and trust requirements, maximum cost, location preference, timeout,
fallback policy, responsibility, and task/evidence correlation identities. Routing evaluates
all candidates in stable provider/model order, records every rejection reason, and ranks eligible
candidates deterministically by location preference, family preference, specialization, provider,
and model identity.

## Evidence lifecycle

Routing decisions and provider invocations create immutable SHA-256 evidence identities.
Invocation evidence records correlations, provider/model and registry identity, capability,
execution location, timestamps, outcome classification, input/output digests, sanitized metadata,
`paper_only=true`, and `broker_submission=false`. Raw prompts, credentials, and secrets are not
stored in evidence.

## Fail-closed behavior

Routing selects nothing when capability, health, privacy, trust, cost, enabled state, or
responsibility constraints cannot be satisfied. A prohibited responsibility fails before
candidate selection. When fallback is disabled, an unavailable preferred route produces a
structured failure instead of silently choosing another model. `route_registry_data` turns
untrusted catalog validation errors into a structured `registry_invalid` decision with no
selected model.

## Future integration points

- Gemma reasoning adapters can implement `ModelProvider` for local or governed fleet execution.
- FinBERT registers `financial_sentiment.v1` and sentiment-only responsibility.
- EmbeddingGemma registers embeddings and retrieval/reranking capabilities.
- Kronos registers time-series forecasting with time-series input contracts.
- Hermes consumes routing and invocation evidence for analysis/orchestration without receiving
  capital or broker authority.
- Titan, Mac, and Prime become governed execution locations/providers whose health and privacy
  metadata participate in the same registry and router.

Phase 2 should add a durable evidence ledger and one governed Gemma adapter behind the existing
backend authority boundary, without moving provider credentials or execution into the renderer.
