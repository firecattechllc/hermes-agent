# Routing and Fleet Convergence

## Stage 10 status

Stage 10 defines a governed, deterministic, non-dispatching fleet routing
layer over the Stage 2 common worker/job contract.

It converges injected evidence for:

- Hermes Titan;
- Hermes Mac;
- future Buzznodes;
- specialized workers;
- standby nodes.

No live routing or execution occurs during Stage 10.

## Purpose

The Stage 10 router can answer:

> Given this governed worker job and current injected fleet evidence, Hermes
> Titan is primary, Hermes Mac is fallback, and Buzznode 001 is excluded
> because its capability declaration does not satisfy the request.

The router cannot send the job anywhere.

## Modeled concepts

Stage 10 models immutable records for:

- fleet node identity;
- machine identity;
- node role;
- node priority;
- trust tier;
- worker-contract schema;
- capabilities;
- supported machines;
- supported profiles;
- hourly cost;
- node enablement;
- health;
- lease state;
- job capacity;
- memory capacity;
- compute capacity;
- running work;
- recent failures;
- latency;
- evidence freshness;
- route eligibility;
- route scoring;
- primary selection;
- ordered fallback selection;
- exclusion reasons;
- deterministic routing decisions.

## Fleet roles

Fleet roles include:

- `primary`
- `senior`
- `persistent_worker`
- `specialized_worker`
- `standby`

Expected initial topology:

- Hermes Titan: primary, always-on node;
- Hermes Mac: senior fallback and development node;
- Buzznodes: future isolated persistent workers;
- additional specialized or standby nodes later.

## Eligibility gates

A node is eligible only when all required gates pass:

- routing layer enabled;
- node enabled;
- compatible worker-contract schema;
- sufficient trust tier;
- required capability available;
- target machine supported;
- target profile supported;
- health evidence current;
- health state eligible;
- lease eligible;
- job slot available;
- required memory available;
- required compute available;
- hourly cost within budget.

Failure at any gate produces a specific exclusion state and reason.

## Health semantics

Eligible health states are:

- `healthy`
- `busy`
- `degraded`

Blocking health states are:

- `stale`
- `offline`
- `incompatible`
- `quarantined`

A busy or degraded node may remain eligible but receives a lower score.

## Lease semantics

Eligible lease states are:

- `available`
- `reserved`
- `active`
- `expiring`

Blocking lease states are:

- `expired`
- `released`
- `invalid`

Stage 10 does not acquire, renew, release, or mutate leases.

## Deterministic scoring

Eligible candidates receive a deterministic score derived from:

- node role;
- health state;
- trust tier;
- configured priority;
- available job capacity;
- running-job penalty;
- recent-failure penalty;
- latency penalty.

Candidates are sorted by:

1. descending score;
2. ascending node identity as a deterministic tie-breaker.

The highest eligible candidate becomes primary.

The next bounded set becomes ordered fallbacks.

## Exclusion states

Route eligibility includes:

- `eligible`
- `disabled`
- `capability_mismatch`
- `machine_mismatch`
- `profile_mismatch`
- `health_blocked`
- `stale_evidence`
- `lease_blocked`
- `capacity_blocked`
- `budget_blocked`
- `trust_blocked`
- `schema_incompatible`

Every excluded node retains explicit reasons.

## Evidence boundary

Fleet evidence is injected by a caller and includes:

- canonical observation time;
- health state;
- lease state;
- bounded resource capacity;
- running-job count;
- recent-failure count;
- latency;
- evidence digest;
- sanitized summary.

Stage 10 does not collect health evidence from live machines.

## Decision boundary

A routing decision includes:

- worker-job identity;
- worker-contract digest;
- primary node;
- ordered fallbacks;
- every evaluated candidate;
- every exclusion reason;
- deterministic decision digest.

A decision is a projection only.

It cannot dispatch or fail over.

## Authority boundary

The routing layer cannot:

- send a worker job;
- dispatch execution;
- start a process;
- connect to a node;
- provision a node;
- use SSH;
- execute shell commands;
- access arbitrary filesystems;
- resolve credentials;
- authenticate;
- start browsers;
- acquire leases;
- mutate capacity;
- install integrations;
- activate integrations;
- mutate policies;
- approve work;
- authorize capital;
- submit broker orders;
- mutate portfolio state;
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

Stage 10 does not include:

- live node discovery;
- live health collection;
- live lease management;
- node provisioning;
- SSH;
- authentication;
- credential resolution;
- worker dispatch;
- remote execution;
- remote cancellation;
- automatic failover;
- integration installation;
- integration activation;
- Mission Control execution controls;
- live Sigil bridge wiring.

Those begin in the Stage 11 integration and bridge phase.
