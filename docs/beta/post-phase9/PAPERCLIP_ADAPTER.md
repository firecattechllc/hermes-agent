# Paperclip Adapter

## Stage 4 status

Stage 4 defines a disabled-by-default governed Paperclip adapter over the
Stage 1 integration registry and Stage 2 common worker/job contract.

Paperclip is modeled as an organizational and project-management projection
for Hermes. Hermes remains the central control plane and the only authority
for admission, routing, budgets, evidence, approvals, execution controls,
certification, rollback, and promotion.

No live Paperclip service is connected during Stage 4.

## Modeled concepts

The adapter models immutable references for:

- organization and project identity;
- agent and employee identity;
- issue and task assignment;
- agent heartbeat evidence;
- comments and transcript references;
- repository-relative workspace and worktree references;
- exact-decimal cost and governed budget accounting;
- worker job correlation and idempotency;
- evidence references and content digests;
- deterministic worker-to-Paperclip lifecycle projection.

## Worker lifecycle projection

The Stage 2 worker lifecycle projects into Paperclip as follows:

- `proposed` → `backlog`
- `admitted` → `assigned`
- `rejected` → `failed`
- `queued` → `queued`
- `running` → `in_progress`
- `cancellation_requested` → `cancellation_requested`
- `cancelled` → `cancelled`
- `succeeded` → `completed`
- `failed` → `failed`
- `completion_unknown` → `completion_unknown`

This projection is descriptive. It does not mutate a Paperclip service.

## Registry relationship

The adapter validates that Paperclip is represented by a Stage 1 registry
entry with:

- matching immutable integration identity;
- the `organization` category;
- a lifecycle state eligible for local evaluation;
- the standard fully denied authority boundary.

Rejected, deprecated, and quarantined entries fail closed.

## Workspace boundary

Workspace and worktree values are references only.

Stage 4 permits:

- immutable repository identities;
- immutable commit revisions;
- repository-relative workspace references;
- repository-relative worktree references;
- read-only projection.

Stage 4 rejects:

- absolute host paths;
- home-directory paths;
- parent-directory traversal;
- private network endpoints;
- workspace creation;
- arbitrary filesystem access;
- shell execution.

## Heartbeat semantics

Heartbeat evidence is injected by a caller and is never fetched by the adapter.

Status projection supports:

- `disabled`: adapter disabled by policy;
- `ready`: projection is valid and required heartbeat evidence is current;
- `stale`: assigned work lacks current heartbeat evidence;
- `incompatible`: worker-contract schema mismatch;
- `invalid`: reserved for a later projection boundary.

Future, mismatched-agent, and mismatched-issue heartbeat evidence fails closed.

## Cost and budget semantics

Cost data is descriptive exact-decimal accounting.

The adapter:

- preserves the Stage 2 job budget;
- rejects negative cost;
- rejects non-finite cost;
- rejects recorded cost above the governed job budget;
- applies bounded runtime and attempt accounting.

It cannot authorize spending, move capital, or change a budget.

## Authority boundary

Paperclip receives no independent authority.

The adapter cannot:

- connect to a live Paperclip service;
- authenticate;
- access credentials;
- create or modify remote organizations;
- create or modify remote projects;
- assign or reassign remote work;
- dispatch a worker job;
- admit a worker job;
- approve work;
- start an agent;
- create a workspace or worktree;
- execute shell commands;
- access arbitrary filesystems;
- mutate policy;
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

Stage 4 does not include:

- Paperclip installation;
- Paperclip service discovery;
- API client code;
- authentication or token exchange;
- live organization synchronization;
- live issue synchronization;
- live agent heartbeats;
- remote comments;
- remote task mutation;
- worktree creation;
- workspace execution;
- Mission Control projection;
- Buzz relay integration;
- Buzznode integration;
- fleet routing.

Those remain later-stage work.
