# Buzznode Adapter

## Stage 6 status

Stage 6 defines a disabled-by-default governed Buzznode worker-host adapter
over the Stage 1 integration registry and Stage 2 worker/job contract.

Buzznode is modeled as a persistent, isolated worker-host capability for agents
that may later require durable workspaces, browser sessions, long-lived state,
or dedicated machine resources.

Hermes remains the central authority.

No Buzznode is provisioned, connected, authenticated, or executed during
Stage 6.

## Modeled concepts

The adapter models immutable references for:

- node and machine identity;
- worker-host role;
- platform and architecture;
- worker profile;
- bounded CPU, memory, storage, runtime, job, and browser-session limits;
- declared capabilities;
- isolated persistent workspace references;
- browser-session references;
- node leases;
- heartbeat evidence;
- worker-contract compatibility;
- job correlation and idempotency;
- node health projection;
- worker lifecycle projection.

## Resource boundary

Resource limits are descriptive policy constraints.

Stage 6 validates:

- CPU bounds;
- memory bounds;
- storage bounds;
- concurrent-job bounds;
- runtime bounds;
- browser-session bounds;
- observed usage against declared limits.

It does not allocate or reserve actual compute.

## Workspace boundary

Workspace values are immutable references only.

Stage 6 permits:

- immutable repository identity;
- immutable commit revision;
- repository-relative references;
- isolated workspace declaration;
- persistent workspace declaration;
- read-only projection.

Stage 6 rejects:

- absolute host paths;
- home-directory paths;
- traversal outside the repository;
- private endpoint material;
- arbitrary filesystem authority;
- workspace creation;
- worktree creation;
- filesystem mounting.

## Browser-session boundary

Browser sessions are references only.

The adapter cannot:

- launch a browser;
- attach to a browser;
- inspect cookies;
- resolve credentials;
- mutate browser state;
- automate a browser;
- persist secrets.

Browser-session counts are evaluated only against governed resource limits.

## Lease semantics

A lease models the intended relationship between a node and a governed job.

Lease states include:

- `unassigned`
- `reserved`
- `active`
- `expiring`
- `expired`
- `released`
- `invalid`

Active lease states require a worker-job identity. Unassigned leases cannot
reference a job.

Lease validation does not reserve or control a real node.

## Heartbeat and health semantics

Heartbeat evidence is injected by a caller.

Health projection supports:

- `disabled`
- `ready`
- `busy`
- `degraded`
- `stale`
- `offline`
- `incompatible`

The adapter fails closed for:

- missing heartbeat evidence;
- stale evidence;
- future evidence;
- mismatched node identity;
- incompatible worker-contract schema;
- expired or invalid lease;
- resource-limit overage.

## Worker lifecycle projection

Stage 2 worker states project into descriptive Buzznode work states:

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

Buzznode must be represented by a Stage 1 registry entry with:

- matching integration identity;
- the `worker` category;
- an eligible lifecycle state;
- fully denied authority.

Rejected, deprecated, and quarantined entries fail closed.

## Authority boundary

The adapter cannot:

- provision a node;
- connect to a node;
- authenticate;
- resolve credentials;
- use SSH;
- open a shell;
- execute commands;
- launch or control browsers;
- create workspaces;
- create worktrees;
- mount filesystems;
- access arbitrary files;
- dispatch jobs;
- approve work;
- install or activate Buzznode;
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

Stage 6 does not include:

- Buzznode installation;
- node provisioning;
- node discovery;
- SSH connectivity;
- authentication;
- credential mounts;
- live heartbeat collection;
- real lease acquisition;
- workspace creation;
- browser launch;
- browser automation;
- job execution;
- remote cancellation;
- Mission Control projection;
- fleet routing.

Those remain later-stage work.
