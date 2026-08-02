# Governed Hermes Orchestration

Sigil's Hermes orchestration layer coordinates existing governed AI capabilities for one bounded advisory research workflow. It does not create a second model router or analysis runtime: specialist execution remains behind the existing capability-router and analysis-service boundary, and orchestration persists only identities, validated structured results, and evidence.

## Workflow and contracts

The versioned request accepts an immutable orchestration and task identity, an advisory objective, digest-only evidence context, allowlisted capabilities and responsibilities, privacy/trust/cost requirements, and fixed step, parallelism, timeout, fallback, and human-interaction bounds. It always carries `paper_only=true` and `broker_submission=false`. Capital authorization, approvals, policy or portfolio mutation, orders, credentials, generic shell/network/filesystem access, autonomous trading, self-modifying governance, and confirmation bypass are rejected before planning.

The deterministic plan supports, when requested:

1. EmbeddingGemma governed retrieval.
2. FinBERT financial sentiment.
3. Kronos market forecasting when valid governed series evidence exists.
4. Gemma synthesis after its specialist dependencies.

Every step has an immutable identity, input digests, capability and responsibility, expected schema, preferred model family, timeout, bounded retry policy, fallback policy, and optional interaction or worker point. Dependency-safe execution batches never exceed the validated maximum parallelism; final step and artifact ordering remains deterministic. Plans are proposals for computation, not authority.

The specialist executor is a narrow backend adapter point. Production callers resolve governed inputs and invoke the existing `GovernedAnalysisService`, which already owns capability routing, provider invocation, structured-output validation, and routing/invocation evidence. Orchestration never stores unrestricted prompts or raw provider output.

## Retry, fallback, and failures

At most one retry is allowed for classified transient failures such as provider timeout, temporary provider unavailability, worker unavailability, or recoverable communication failure. Authority violations, policy mismatch, malformed evidence, invalid schema, unsafe or credential-bearing output, and broker/execution directives are never retried.

When a capability is unavailable, an explicitly validated fallback may omit the step and produce a partial advisory artifact with the missing capability and limitation intact. With fallback disabled, the orchestration fails closed. Results are never fabricated. Failed dependencies are skipped, and conflicting specialist findings remain recorded as disagreements rather than being collapsed into a false consensus.

## Durable state and evidence

Orchestration state is stored separately under `governed-ai-orchestration-v1/orchestrations.jsonl`. The append-only, checksummed hash chain uses restricted permissions, same-directory temporary writes, `fsync`, and atomic replacement. It persists request and plan identities, revisions, step results and evidence identities, retries/fallbacks, interactions, worker result identities, final artifact identity, structured failures, and timestamps.

Readers recover a valid prefix after a truncated final write and reject corruption or unsupported schemas without granting authority. Duplicate orchestration identities and changes to terminal records fail closed. Paused interactions survive restart; cancellation changes only orchestration state and cannot mutate proposals, portfolios, brokers, the paper ledger, or execution state.

The final immutable orchestration artifact is stored in the existing analysis artifact store. It includes completed, failed, and skipped steps; specialist artifact and evidence identities; sanitized findings, risks, disagreements, missing evidence, limitations, confidence, and freshness. It explicitly denies execution, broker submission, portfolio mutation, and approval authority.

## Human interaction and optional surfaces

Human interactions have exact immutable identities, bounded choices, expiry, and durable response state. Missing, duplicate, invalid, or expired responses fail closed. A response only resumes an advisory workflow; existing Sigil confirmations and proposal approvals remain the sole downstream authority.

- **Buzz** is an optional sanitized communication gateway for status, exact human-input requests, and bounded result summaries. It cannot invoke models, mutate plans, approve proposals, authorize capital, access credentials, run commands, or submit orders.
- **Atlas** is an optional read-only projection of bounded orchestration, plan, step, capability, evidence, artifact, failure, retry, limitation, freshness, and no-authority state. It exposes no write methods, prompts, raw model output, secrets, environment variables, or filesystem paths.
- **OpenWorker** is an optional in-process boundary for explicitly registered deterministic task types. Registrations declare identity, location, privacy/trust, timeout, output limits, availability, and permissions. Network, arbitrary filesystem, shell, credentials, broker, portfolio, recursive delegation, and generic code execution are denied. Output and elapsed time are bounded; unsafe, oversized, or credential-bearing output fails closed. Where process-level memory isolation cannot be guaranteed, no untrusted worker is admitted.

All three surfaces are startup-independent. Absence or unavailability cannot block the desktop, backend, runtime snapshot, paper ledger, proposal review, audit timeline, or existing AI inspection.

## Inspection and Mission Control

Read-only AI inspection reports enabled and store health, bounded state counts, pending interactions, latest sanitized orchestration summary, optional-surface availability, artifact/evidence health, and explicit paper-only/no-authority fields. Mission Control adds restrained orchestration and coordination status to the existing AI Foundation panel. It adds no execution control and continues to display advisory-only, paper-only, and no-execution-authority language.

Failure classifications are structured and sanitized, including service disabled, provider or worker unavailable, timeout, policy mismatch, unsafe output, resource limit, dependency failure, operator cancellation, and store corruption. Credentials, raw prompts, raw specialist output, environment values, and local paths are excluded.

## Future fleet routing

Titan, Mac, and Prime fleet routing is intentionally deferred. A later phase may add governed remote placement only by preserving the same immutable contracts, digest-only handoff, explicit trust/privacy policy, bounded resources, evidence lifecycle, optional startup behavior, and zero trading or approval authority.
