# Governed Kronos forecasting

Kronos is Sigil's local financial time-series forecasting specialist. It uses the existing governed model registry, capability router, analysis service, immutable evidence ledger, hash-chained artifact store, reference-only Hermes handoff, read-only inspection commands, and Mission Control AI Foundation panel. Forecasts and evaluations are advisory evidence only: they cannot authorize capital, approve proposals, mutate portfolios, change policy, promote strategies or models, execute orders, or submit broker instructions.

## Registration and configuration

The provider registers as `local-kronos`, family `kronos`, with `time_series_forecasting.v1`, local execution, structured time-series input, free cost class, trusted trust tier, and local-only privacy. Allowed responsibilities are forecasting, market forecasting/context, proposal support, risk/scenario/research analysis, and orchestration support. All platform prohibitions remain explicit, including automatic strategy promotion and automatic forecast-driven trading.

Backend configuration uses `SIGIL_AI_KRONOS_ENABLED`, `SIGIL_AI_KRONOS_MODEL`, `SIGIL_AI_KRONOS_MODEL_VERSION`, `SIGIL_AI_KRONOS_TOKENIZER`, `SIGIL_AI_KRONOS_TOKENIZER_VERSION`, `SIGIL_AI_KRONOS_DEVICE`, `SIGIL_AI_KRONOS_TIMEOUT_MS`, `SIGIL_AI_KRONOS_MAX_SEQUENCE_LENGTH`, `SIGIL_AI_KRONOS_MIN_SEQUENCE_LENGTH`, `SIGIL_AI_KRONOS_MAX_HORIZON`, `SIGIL_AI_KRONOS_LOCAL_FILES_ONLY`, `SIGIL_AI_KRONOS_ALLOWED_INTERVALS`, and `SIGIL_AI_KRONOS_MAX_BATCH_SIZE`. Defaults are disabled, CPU, local-files-only, one-item batches, 32–512 observations, a 64-point maximum horizon, and hourly/daily intervals. Model and tokenizer paths are converted to path-free hashes before registry or renderer exposure.

Torch, Kronos packages, tokenizer code, and weights remain optional. Imports and `from_pretrained` calls happen only during an enabled invocation and always pass `local_files_only=True`. No startup or test download is permitted. Missing dependencies, weights, tokenizer files, corrupt files, mismatched identities, and inference errors produce sanitized unavailable/failure results without affecting Sigil startup.

## Governed market series and forecast request

The immutable v1 market-series contract binds a stable series/source identity, source digest, symbol, asset class, optional venue, interval, timezone, time range, observation/freshness timestamps, exact OHLCV fields, trust/privacy, adjustment state, and paper-only authority. Bars require timezone-aware strictly increasing unique timestamps, finite positive OHLC, valid high/low relationships, and finite non-negative volume. The digest is recomputed over canonical bars. Sequences are bounded and are supplied by existing backend-domain sources; Kronos adds no fetching, URL ingestion, renderer upload, or filesystem crawl.

The immutable request binds request/task identities, `time_series_forecasting.v1`, an advisory responsibility, series identity/digest, symbol, interval, bounded horizon, real uncertainty mode, privacy/trust requirements, fallback policy, timeout, timestamp, and evidence digests. It rejects stale required inputs, mismatched identities, unsupported intervals/horizons, insufficient or oversized sequences, arbitrary prompts, paths, URLs, credentials, and prohibited responsibilities. Generic Gemma cannot impersonate Kronos unless separately registered with the forecasting capability and explicitly allowed by routing fallback.

## Forecast schema and uncertainty

Validated output binds provider/model/tokenizer identities and versions, source identity/digest, symbol, interval, horizon, generation time, freshness, calibration, limitations, and exact ordered forecast points. Each point contains horizon index, continuous timestamp, predicted OHLC, optional volume, and optional close bands. Values must be finite; OHLC and band relationships must hold; identities and exact horizon must match; payloads cannot contain directives, credentials, or execution authority.

The adapter supports deterministic point forecasts and the explicitly requested 0.1/0.5/0.9 quantile mode only when the runtime emits real bands. Point forecasts declare calibration and uncertainty unavailable. Sigil never fabricates confidence intervals.

## Evidence, artifacts, and evaluation

The service records the existing routing decision, invocation attempt, provider result, and output rejection evidence. Evidence persists series and forecast digests rather than raw OHLCV, tensors, hidden states, tokenizer internals, or provider output. Only validated sanitized forecasts enter the existing hash-chained analysis artifact store. Forecast artifacts retain model/tokenizer identities, bounded points, freshness/trust, limitations, and routing/invocation evidence identities with all authority fields false.

Deterministic evaluation compares a governed forecast with later governed observations and records MAE, RMSE, denominator-safe MAPE, directional accuracy, interval coverage when bands exist, per-horizon metrics, sample count, window, identities, and limitations in the same durable artifact store. Missing observations and identity mismatches fail closed. Evaluation is observational: poor performance remains visible and cannot promote a model, alter routing, modify strategy, or imply trading profitability. Multiple evaluation artifacts can be compared externally through their bounded identities and metrics; no automatic winner receives authority.

## Hermes, inspection, and startup independence

Hermes passes only task/series/digest identities, symbol, interval, horizon, uncertainty request, privacy/trust requirements, evidence digests, and the governed output schema. The backend resolves the actual series and returns a sanitized forecast artifact, evidence identities, routing summary, freshness, limitations, or failure classification. Phase 7 adds no scheduling, continuous forecasts, strategy loops, agent delegation, Buzz, Atlas, OpenWorker, fleet routing, or full Hermes orchestration.

Read-only inspection reports enabled/health state, path-free model and tokenizer identities, device class, supported intervals, sequence/horizon bounds, forecast/evaluation counts, latest forecast symbol/interval/horizon/time/uncertainty/freshness/limitations, latest metrics, and sanitized failures. Mission Control displays the same summary in the existing AI Foundation panel without controls or redesign. Raw series, tensors, model/tokenizer paths, environment variables, credentials, and provider output remain backend-only.

Kronos is never required for backend or desktop startup, runtime snapshots, paper ledgers, proposal review, confirmation dialogs, audit evidence, Gemma, FinBERT, EmbeddingGemma, or packaged launch. Disabled, missing, empty, stale, short, corrupt, mismatched, and timed-out states all remain bounded and fail closed. Future orchestration and Titan/Mac/Prime deployment require separate governed phases and authority review.
