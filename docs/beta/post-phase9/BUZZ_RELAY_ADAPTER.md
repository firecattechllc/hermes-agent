# Buzz Relay Adapter

## Stage 5 status

Stage 5 defines a disabled-by-default governed Buzz Relay adapter over the
Stage 1 integration registry and Stage 2 common worker/job contract.

Buzz is the planned shared collaboration workspace for humans and agents.
Hermes remains the central authority behind Buzz.

No live Buzz relay is connected during Stage 5.

## Modeled concepts

The adapter models immutable references for:

- workspaces, projects, and channels;
- human, agent, and service identities;
- messages and thread relationships;
- signed relay events;
- approval references;
- Git and workflow events;
- worker-job correlation and idempotency;
- evidence references and content digests;
- hash-linked event ordering;
- replay protection;
- deterministic worker lifecycle projection.

## Signed event boundary

Each relay event includes:

- immutable event identity;
- strictly increasing sequence;
- canonical UTC timestamp;
- actor and collaboration-space identity;
- message/thread identity;
- correlation identity;
- idempotency key;
- payload digest;
- previous-event digest;
- signature envelope;
- optional approval, Git, workflow, and evidence references.

The Stage 5 adapter validates event structure but does not perform external
signature verification or key lookup. Cryptographic verification remains a
later live-integration concern.

## Replay protection

The local replay window tracks:

- highest accepted sequence;
- accepted event identities;
- accepted idempotency keys;
- last accepted event digest.

The adapter rejects:

- duplicate event identities;
- duplicate idempotency keys;
- non-increasing sequences;
- broken hash chains;
- stale events;
- future-dated events.

Replay evaluation is descriptive and local. It does not acknowledge or mutate
a live relay.

## Approval boundary

Approval references are evidence only.

A Buzz approval reference cannot:

- approve a worker job;
- admit execution;
- authorize trading;
- authorize capital;
- mutate policy;
- bypass Hermes governance.

Hermes remains the approval authority.

## Git and workflow boundary

Git and workflow events require:

- repository identity;
- immutable commit revision;
- event classification;
- optional repository-relative workflow reference.

Stage 5 does not clone repositories, execute workflows, change branches,
write files, or run shell commands.

## Worker lifecycle projection

Stage 2 worker states project directly into descriptive Buzz work states:

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

This projection never dispatches or controls the worker.

## Registry relationship

Buzz must be represented by a Stage 1 registry entry with:

- matching integration identity;
- the `collaboration` category;
- an eligible lifecycle state;
- fully denied authority.

Rejected, deprecated, and quarantined registry entries fail closed.

## Authority boundary

The adapter cannot:

- connect to a live Buzz relay;
- authenticate;
- resolve credentials;
- subscribe to channels;
- send messages;
- create threads;
- acknowledge events remotely;
- mutate channels or projects;
- approve work;
- dispatch jobs;
- execute workflows;
- access arbitrary filesystems;
- execute shell commands;
- install or activate Buzz;
- mutate policy;
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

Stage 5 does not include:

- Buzz installation;
- live relay discovery;
- authentication;
- relay subscription;
- message delivery;
- thread mutation;
- channel creation;
- project mutation;
- external signature verification;
- signing-key discovery;
- remote replay acknowledgements;
- live Git events;
- workflow execution;
- Mission Control projection;
- Buzznode integration;
- fleet routing.

Those remain later-stage work.
