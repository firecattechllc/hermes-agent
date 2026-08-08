# Governed EmbeddingGemma retrieval

EmbeddingGemma is Sigil's local embedding and semantic-retrieval specialist inside the existing governed registry, capability router, analysis service, immutable AI evidence ledger, sanitized analysis artifact store, Hermes handoff, and read-only Mission Control inspection. Retrieval is advisory context only and cannot authorize capital, approve proposals, mutate portfolios, change policy, execute or submit orders, access credentials, delete sources, or run a shell.

## Provider and configuration

The provider registers family `embeddinggemma` with `embeddings.v1` and `semantic_retrieval.v1`, local execution, bounded text inputs, normalized structured vectors, trusted/local-only privacy, free cost, and read-only research, evidence, proposal, audit, market-context, and orchestration-support responsibilities. Every existing prohibited responsibility plus ungoverned source deletion is explicit.

Backend-only variables are `SIGIL_AI_EMBEDDING_GEMMA_ENABLED`, `SIGIL_AI_EMBEDDING_GEMMA_MODEL`, `SIGIL_AI_EMBEDDING_GEMMA_MODEL_VERSION`, `SIGIL_AI_EMBEDDING_GEMMA_DEVICE`, `SIGIL_AI_EMBEDDING_GEMMA_TIMEOUT_MS`, `SIGIL_AI_EMBEDDING_GEMMA_MAX_INPUT_CHARS`, `SIGIL_AI_EMBEDDING_GEMMA_MAX_BATCH_SIZE`, `SIGIL_AI_EMBEDDING_GEMMA_VECTOR_DIMENSION`, `SIGIL_AI_EMBEDDING_GEMMA_LOCAL_FILES_ONLY`, `SIGIL_AI_RETRIEVAL_MAX_RESULTS`, and `SIGIL_AI_RETRIEVAL_MIN_SCORE`. Defaults are disabled, CPU, local-files-only, bounded input/batch/results, and no download. Model sources and filesystem paths are converted into path-free registry identities before inspection.

Sentence Transformers, Torch, Transformers, native libraries, and model weights are optional. Model import and construction are lazy and only occur for an explicitly routed backend invocation. `local_files_only=true` is mandatory. Missing dependencies, weights, corrupt files, load failures, malformed dimensions, and timeouts return sanitized failures without affecting startup, runtime snapshot, paper state, proposals, confirmations, audit history, Gemma, FinBERT, or packaged launch.

## Governed sources and deterministic chunks

Only backend-domain registration of enumerated news evidence, SEC and earnings excerpts, company announcements, analyst notes, proposal evidence, research artifacts, audit evidence, operator-approved notes, and sanitized AI artifacts is accepted. Each immutable source binds type, external identity, version, digest, corpus, timestamps/freshness, privacy, trust, language, length, chunk count, and optional superseded source identity. New versions create new identities; history is never overwritten.

Source text is bounded, English-only, digest-verified, and rejected if empty, oversized, credential-bearing, executable, or unsupported. There is no filesystem crawl, URL ingestion, scraping, mailbox import, or renderer indexing API. Deterministic whitespace-aware chunking produces immutable chunk/source/corpus identities, content digests, character counts, freshness, privacy, and trust. Bounded sanitized chunk text is stored locally because semantic retrieval needs an excerpt; raw source documents are not stored in source metadata.

## Embeddings and vector persistence

Every vector must contain the configured exact dimension, finite numeric values, positive magnitude, and unit normalization. An immutable embedding record binds provider/model/version, source/chunk identities and digests, vector dimension and digest, registry revision, invocation evidence, timestamp, and no-authority fields. Raw vectors are stored only inside the backend retrieval store and never appear in general artifacts, inspection, preload, or Mission Control.

`DurableRetrievalStore` resides under `governed-ai-retrieval-v1`, logically separate from portfolio, paper ledger, broker, proposal, and execution-audit state. It requires an absolute non-symlink root, mode-0700 directory, mode-0600 files, a file lock, canonical hash-chained JSONL records, append/fsync and directory fsync, immutable duplicate rejection, model/version/dimension compatibility, restart validation, and bounded truncated-tail recovery. Corruption disables retrieval safely and does not grant authority or block Sigil startup.

## Retrieval, ranking, privacy, and freshness

A retrieval request binds request/task identities, query digest and transient backend query, corpus and source-type filters, privacy, minimum trust, freshness, result limit, minimum score, fallback policy, evidence digests, and paper-only authority. Queries reject credentials, executable content, URLs, filesystem references, unsupported responsibilities, excessive limits, and digest mismatch. Query text and vectors are excluded from evidence and durable retrieval artifacts.

Cosine similarity uses normalized vectors with exact dimension matching. Privacy, trust, corpus, source type, freshness, and minimum-score filters apply before result emission. Scores are clamped to `[0,1]`; sorting is descending score with immutable source/chunk tie-breaks. Source diversification places one result per source before repeated chunks. Results remain bounded and explicitly annotate stale/current state, source/version traceability, digests, bounded excerpts, and evidence references. Empty matches are a successful advisory result, not invented evidence.

## Indexing, evidence, artifacts, and Hermes

The backend indexing method registers one governed source, deterministically chunks it, routes `embeddings.v1`, embeds bounded batches, validates vectors, records routing/attempt/result evidence, and atomically appends the source/chunks/embeddings. Failures create durable sanitized evidence but no authoritative index bundle.

Retrieval routes `semantic_retrieval.v1`, embeds and validates the digest-bound query, searches the governed store, and persists a `GovernedRetrievalArtifact` through the existing analysis artifact store. The artifact contains only query digest, corpus identities, ranked sanitized references, scores, freshness, limitations, and routing/invocation evidence. It carries `paper_only=true` and false execution, broker, portfolio, and approval authority.

Hermes supplies correlation and query digests, corpus/source filters, privacy/trust/freshness requirements, result limit, minimum score, and evidence digests. Backend authority resolves and verifies bounded query text before using the same service. It returns the artifact/evidence identities, ranked references, limitations, freshness, routing summary, or structured failure. No autonomous loops, continuous indexing, scheduling, or agent delegation are added.

## Inspection and future phases

Existing read-only AI status and artifact inspection report sanitized EmbeddingGemma enablement, path-free model identity/version, conservative availability, vector dimension, corpus/source/chunk/embedding counts, vector-store health, recoverable tail state, last indexing/retrieval time, latest result count/freshness, limitations, and last failure. Mission Control adds these values to the existing AI Foundation panel while retaining advisory-only and no-execution-authority language. No raw query, raw vector, unrestricted content, model path, credential, environment value, or execution control is exposed.

Kronos forecasting, autonomous orchestration, and Titan/Mac/Prime fleet routing remain future phases and are not enabled here.
