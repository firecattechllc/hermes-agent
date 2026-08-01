# Governed FinBERT financial sentiment

FinBERT is Sigil's first specialist model inside the governed AI registry, capability router, analysis service, immutable evidence ledger, sanitized artifact store, Hermes handoff, and read-only Mission Control inspection. Its sole capability is `financial_sentiment.v1`. Results are advisory evidence and never authorize capital, approve proposals, mutate portfolios, change policy, execute orders, access credentials, or run a shell.

## Registration and configuration

The local provider registers family `finbert`, local execution, text input, structured output, trusted/local-only privacy, free cost, and the advisory responsibilities financial sentiment analysis, news and earnings sentiment, market context, proposal support, and risk analysis. Every governed prohibited responsibility remains explicit.

Backend-only variables are `SIGIL_AI_FINBERT_ENABLED`, `SIGIL_AI_FINBERT_MODEL`, `SIGIL_AI_FINBERT_MODEL_VERSION`, `SIGIL_AI_FINBERT_DEVICE`, `SIGIL_AI_FINBERT_TIMEOUT_MS`, `SIGIL_AI_FINBERT_MAX_INPUT_CHARS`, `SIGIL_AI_FINBERT_MAX_BATCH_SIZE`, and `SIGIL_AI_FINBERT_LOCAL_FILES_ONLY`. Defaults are disabled, CPU, 15 seconds, 20,000 characters, batch size 8, and local-files-only. Local-files-only cannot be disabled through this contract. Configuration values, model filesystem paths, and environment data are not exposed to the renderer.

Torch and Transformers are optional. Imports and model loading are lazy and occur only for an explicitly routed invocation. Model loading uses `local_files_only=true`; no startup or test download is permitted. Missing packages, weights, corrupt files, load failures, and timeouts become sanitized provider failures. They do not block backend, desktop, runtime snapshot, paper ledger, proposal review, confirmation dialogs, audit history, Gemma, or packaged launch.

## Request and output contracts

A governed sentiment request binds request/task identities, `financial_sentiment.v1`, an advisory responsibility, source type and identity, source SHA-256 digest, trusted evidence digests, English language, privacy, timestamp, timeout, paper-only state, and fallback permission. Supported source types are bounded news, earnings-call, SEC filing, analyst note, company announcement, market commentary, and proposal-evidence excerpts. Oversized, non-English, credential-bearing, executable, unknown-source, or digest-mismatched input fails closed. Full source text exists only at the backend invocation boundary and is excluded from durable evidence and artifacts.

The versioned result contains positive, neutral, and negative scores, deterministic label and confidence, model/source identities, source digest, analysis time, and limitations. Scores must be finite values from zero through one, sum to one within tolerance, and agree with the deterministic maximum-score label. Authority fields are invariant: `paper_only=true`; execution authorization, broker submission, portfolio mutation, and approval authority are false.

## Evidence, artifacts, and aggregation

The existing analysis service persists the routing decision, invocation attempt, and success or sanitized failure for every routed request. Only a valid successful result creates a `GovernedSentimentArtifact` in the existing hash-chained analysis artifact store. The artifact preserves source identity/digest, score distribution, confidence, limitations, freshness, routing/invocation evidence identities, and source evidence references. Restart reads revalidate the same artifact schema and hash chain; malformed output creates rejection evidence but no authoritative artifact.

Optional aggregation accepts 1–100 validated artifacts and positive weights. It returns the weighted distribution, transparent label counts, time window, freshness, source identities, limitations, and confidence capped by source confidence. It preserves disagreement and cannot create proposals, orders, or authority.

## Hermes and Mission Control

The Hermes handoff carries correlation, source identity/digest, privacy, evidence references, responsibility, expected sentiment schema, timeout, and fallback policy. Backend authority resolves the bounded source text, validates its digest, and calls the same governed analysis service. Success returns the sentiment artifact and evidence identities; failure returns the existing structured classification, routing summary, and limitations. No autonomous monitoring or scheduling is included.

The existing `ai_status`, registry, recent-artifact, and exact-artifact inspection commands add sanitized FinBERT health, model identity/version, device class, latest label/confidence/source identity, artifact count, freshness, and limitations. Mission Control presents that status inside AI Foundation with the existing advisory-only and no-execution-authority language. It exposes no raw text, tokenizer state, hidden state, raw model output, filesystem path, credential, environment variable, prompt control, or execution control.

EmbeddingGemma retrieval, Kronos forecasting, and Titan/Mac/Prime fleet routing remain future phases and are not enabled by this integration.
