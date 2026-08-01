# Governed AI Fleet Routing

Phase 9 places already validated Phase 8 specialist steps across authenticated Titan, Mac, and Prime registrations. Phase 8 remains the only planner and retains dependency, retry, fallback, interaction, and terminal-state authority. Fleet routing only selects and invokes a suitable node; nodes cannot expand plans or approve downstream actions.

## Identity, registration, and roles

Every immutable node identity contains a stable node and role identity, platform declaration, trust/privacy tier, fleet execution location, abstract transport identity, authenticated identity reference, registration/heartbeat timestamps, and explicit paper-only flags. A known display name is not authentication. Enabled registrations require an exact authenticated identity reference supplied by the existing fleet or tailnet authority.

- **Titan** is the always-on first preference for orchestration support, retrieval, FinBERT, compatible Kronos-small, and lightweight Gemma work.
- **Mac** is preferred when declared CPU/memory requirements exceed Titan, including stronger reasoning, complex synthesis, heavier compatible forecasts, and future multimodal work.
- **Prime** supplies compatible backup, overflow, and isolated allowlisted worker capacity.

Registrations declare exact provider/model/tokenizer identities, vector dimensions and corpus revisions where relevant, capabilities, task types, memory/CPU/accelerator class, concurrency, duration and payload bounds, verified enforcement, health/load, maintenance, and draining state. Generic shell, arbitrary code/filesystem/network access, credentials, brokers, portfolios, and recursive workers are denied.

## Placement and data locality

`FleetRoutingRequest` carries only immutable orchestration/step/task identities, capability and responsibility, exact compatibility constraints, privacy/trust/cost policy, preferred/excluded nodes, latency/duration/resources, bounded retry/fallback/escalation policy, and digest-only inputs/evidence. Prohibited responsibilities and unbounded requests fail before routing.

The deterministic preference is local backend, Titan, Mac, Prime, then an explicitly approved external fallback. Eligibility overrides preference: authentication, enabled/healthy/current heartbeat, privacy, trust, exact model/tokenizer/vector/corpus identity, resources, verified isolation, maintenance, draining, timeout, and operator exclusions are mandatory. High-resource work may select Mac before Titan. Remaining ties use load and immutable node ID.

Private evidence stays on eligible privacy-compatible nodes. Corpus-bound retrieval requires the exact corpus revision and vector dimension. Kronos requires the exact model/tokenizer pairing. Raw source text, OHLCV, prompts, secrets, environment variables, renderer-defined addresses, and credentials are not fleet-routing inputs.

## Transport and remote tasks

`GovernedFleetTransport` is an injected authenticated adapter boundary aligned with existing tailnet/Hermes transport. It accepts exact node and task identities—not URLs, credentials, commands, shell text, or code. Requests and results are bounded and digest linked. Duplicate task identities are rejected in-process, node identity is re-authenticated, response association and input/output digests are verified, and malformed or spoofed responses fail closed.

Remote tasks use allowlisted worker task types, capability, digests, expected schema, duration/input/output/memory/CPU bounds, privacy/trust policy, and an exact cancellation token. Results contain sanitized sorted payload, node/provider/model identity, resource summary, lifecycle state, limitations, and evidence. They explicitly deny execution and broker authority.

Resource declarations are enforced in placement and transport where supported. A node without verified enforcement is ineligible when the request requires the stronger guarantee. The system does not claim process-level memory, CPU, network, or filesystem isolation that a node has not certified.

## Health, freshness, failover, and ambiguity

Sanitized heartbeats report authenticated identity reference, coordinator observation and node timestamps, state, available capabilities/models, load, active/queued tasks, pressure/thermal classifications, maintenance/draining state, last structured failure, and transport health. Process lists, paths, addresses, logs, secrets, and environment data are excluded.

Heartbeats older than 180 seconds, future-dated observations, or clock skew beyond 120 seconds are ineligible. Maintenance and draining nodes receive no new work. A definitive transient failure may perform one evidenced failover to the next eligible node. Safety failures never retry.

Transport loss after dispatch becomes `completion_unknown`. The coordinator queries the exact task identity when possible and does not automatically duplicate potentially completed work. Lifecycle states are `not_started`, `acknowledged`, `running`, `succeeded`, `failed`, `cancelled`, `cancellation_requested`, and `completion_unknown`.

Cancellation uses an exact task identity, authenticated requester boundary, exact token, and durable request/reconciliation evidence. Evidence is never deleted. A cancellation cannot turn an unverified result into an accepted artifact.

## Persistence, evidence, and recovery

Fleet evidence is separate from orchestration, artifacts, proposals, portfolios, brokers, and execution state under `governed-ai-fleet-v1/fleet-evidence.jsonl`. It uses canonical JSON, restrictive permissions, locking, append plus `fsync`, directory `fsync`, a hash chain, duplicate rejection, schema validation, restart replay, and torn-tail recovery.

Evidence types cover registration/authentication, heartbeat and health rejection, routing, dispatch, acknowledgement/start/result, timeout, retry/failover, cancellation, completion ambiguity, result verification, and acceptance/rejection. Records retain only bounded identities, digests, sanitized classifications, timestamps, and no-authority state.

The fleet specialist adapter implements the existing Phase 8 `SpecialistStepExecutor` seam. It returns a normal sanitized Phase 8 step result and cannot alter the orchestration plan.

## Inspection, Mission Control, and startup independence

Read-only inspection exposes bounded node counts, Titan/Mac/Prime state, capabilities/load, active/queued/unknown tasks, latest route/failover, recent failures, evidence health, and clock warnings. Mission Control adds those fields to the existing AI Foundation panel with paper-only, broker-disabled, and no-execution-authority language. No generic remote controls are present.

Fleet disabled, empty, offline, stale, corrupt, skewed, incompatible, ambiguous, or cancellation-pending states cannot block desktop/backend startup, runtime snapshots, local AI, paper ledgers, proposal review, audit, Mission Control, or packaged launch.

## Certification

Deterministic CI uses mock nodes/transports for authenticated and spoofed identity, duplicate registration, Titan/local-first, Mac escalation, Prime backup, exact compatibility, privacy/trust/resources, maintenance/draining/load, stale/skewed health, transport integrity/replay, failover, ambiguity, cancellation, restart/corruption, Phase 8 integration, inspection, packaging, and safety invariants.

For later operator-run device preflight, run `apps/sigil/scripts/certify-governed-ai-fleet.py` with explicit node, role, authenticated identity reference, transport identity, and timestamps. Supply provider/model together when a model is verified; omit both for an authenticated worker-only node rather than fabricating inventory. It performs no network access or configuration changes and emits only identity-reference digests and a sanitized report. Actual connectivity, read-only task, cancellation, failover, and evidence round-trip certification remains blocked until operators supply authenticated live Titan/Mac/Prime access; it must never be inferred from local mocks.
