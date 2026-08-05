# Agent Reach Adapter

## Stage 8A status

Stage 8A defines a disabled-by-default governed Agent Reach adapter over the
Stage 1 integration registry and Stage 2 worker/job contract.

Agent Reach is modeled as a descriptive capability boundary for evaluating
whether an external agent could satisfy an allowlisted request.

No external agent is contacted during Stage 8A.

## Modeled concepts

The adapter models immutable references for:

- external agent identity;
- organization identity;
- trust tier;
- worker-contract schema;
- declared capabilities;
- supported machines;
- supported worker profiles;
- communication-route references;
- request envelopes;
- response envelopes;
- correlation identities;
- idempotency keys;
- evidence requirements;
- evidence references;
- rate limits;
- in-flight limits;
- request and response byte limits;
- runtime limits;
- exact-decimal cost limits;
- heartbeat evidence;
- freshness;
- compatibility;
- worker lifecycle projection.

## Trust tiers

Trust tiers include:

- `untrusted`
- `observed`
- `reviewed`
- `sandboxed`
- `certified`

The adapter applies a configured minimum trust tier.

Trust classification does not grant execution, messaging, approval, credential,
or financial authority.

## Route boundary

Communication routes are descriptive references only.

Stage 8A allows:

- route identity;
- transport classification;
- repository-relative route reference;
- one-way classification.

Stage 8A rejects:

- live authentication;
- credential exchange;
- private endpoints;
- private host paths;
- arbitrary network destinations;
- direct connection behavior.

## Request envelope

A request envelope includes:

- request identity;
- correlation identity;
- idempotency key;
- requesting actor identity;
- target agent identity;
- allowlisted capability;
- target machine;
- target profile;
- canonical timestamps;
- immutable payload digest;
- rate, byte, runtime, and cost limits;
- evidence requirements;
- deterministic request digest.

The request cannot send itself or dispatch work.

## Response envelope

A response envelope includes:

- response identity;
- matching request identity;
- matching correlation identity;
- responding agent identity;
- normalized response state;
- canonical completion time;
- immutable output digest;
- evidence references;
- runtime accounting;
- response-byte accounting;
- exact-decimal cost accounting;
- deterministic response digest.

The adapter validates responses against the originating request and agent
identity.

It rejects:

- mismatched request identity;
- mismatched correlation identity;
- mismatched agent identity;
- insufficient evidence;
- missing evidence kinds;
- runtime overage;
- response-size overage;
- budget overage.

## Reachability evaluation

Reachability states include:

- `disabled`
- `available`
- `stale`
- `offline`
- `incompatible`
- `rate_blocked`
- `budget_blocked`
- `capability_blocked`
- `trust_blocked`

Availability requires:

- adapter enabled;
- sufficient trust tier;
- compatible worker-contract schema;
- allowlisted capability;
- supported target machine;
- supported target profile;
- available rate capacity;
- available cost budget;
- current heartbeat evidence;
- online agent state.

Availability remains descriptive and does not authorize outreach.

## Worker lifecycle projection

Stage 2 worker states project into descriptive Agent Reach work states:

- `proposed`
- `admitted`
- `rejected`
- `queued`
- `running`
- `cancellation_requested`
- `cancelled`
- `succeeded`
- `failed`
- `completion_unknown`

This projection does not dispatch or execute the worker.

## Registry relationship

Agent Reach must be represented by a Stage 1 registry entry with:

- matching integration identity;
- the `internet_capability` category;
- an eligible lifecycle state;
- fully denied authority.

Rejected, deprecated, and quarantined entries fail closed.

## Authority boundary

The adapter cannot:

- contact an external agent;
- connect to a communication route;
- authenticate;
- exchange credentials;
- send arbitrary messages;
- subscribe to communications;
- dispatch jobs;
- execute work;
- approve actions;
- admit execution;
- mutate Hermes state;
- mutate registry state;
- mutate policy;
- execute shell commands;
- access arbitrary filesystems;
- install or activate an integration;
- submit broker orders;
- mutate portfolio state;
- authorize or spend capital;
- bypass Hermes governance.

The inherited authority boundary remains:

- `paper_only = true`
- `broker_submission = false`
- `execution_authorized = false`
- `approval_authority = false`
- `capital_authority = false`
- `portfolio_mutation = false`
- `policy_mutation = false`
- `credential_access = false`
- `arbitrary_shell = false`
- `arbitrary_filesystem = false`
- `governance_bypass = false`
- `activation_authorized = false`
- `installation_authorized = false`

## Deferred work

Stage 8A does not include:

- Agent Reach installation;
- live route discovery;
- live agent discovery;
- authentication;
- credential exchange;
- message delivery;
- external request submission;
- external response collection;
- signature verification;
- remote job execution;
- remote cancellation;
- Mission Control projection;
- self-evolution;
- fleet routing.

Those remain later-stage work.
