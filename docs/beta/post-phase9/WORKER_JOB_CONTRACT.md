# Common Worker / Job Contract

## Stage 2 status

Stage 2 defines a provider-neutral, fail-closed worker/job contract on top of
the Stage 1 governed integration registry.

It does not dispatch jobs, activate integrations, install dependencies, expose
credentials, authorize shell or filesystem access, or grant financial authority.

## Contract flow

Every future worker request follows:

`intent -> immutable job contract -> Hermes admission -> registry validation ->
bounded queue/run lifecycle -> normalized result -> evidence validation ->
audit projection`

## Core guarantees

- Every job has deterministic job, correlation, idempotency, input, and contract
  identities.
- Every job targets exactly one registry integration, capability, machine, and
  profile.
- Admission requires an independently decided Hermes admission decision.
- Unknown, uncertified, quarantined, unsupported, or unapproved targets fail
  closed.
- Cost, runtime, attempts, input size, and output size are bounded.
- Evidence and approval requirements are explicit.
- Results are normalized and tied to the admitted immutable contract.
- Cancellation and completion-unknown are explicit lifecycle states.
- Durable snapshots and lifecycle evidence are integrity checked.
- Corrupt storage fails closed.
- Sensitive values, private host paths, and private endpoints are rejected.
- Broker submission, execution, capital, portfolio, policy, credential,
  activation, and installation authority remain denied.

## Lifecycle

`proposed -> admitted | rejected`

`admitted -> queued | cancellation_requested`

`queued -> running | cancellation_requested | failed`

`running -> succeeded | failed | cancellation_requested | completion_unknown`

`cancellation_requested -> cancelled | completion_unknown`

Terminal states do not transition further.

## Deferred work

Stage 2 does not yet provide:

- transport or dispatch;
- worker registration;
- adapter execution;
- external service activation;
- runtime credential resolution;
- queue scheduling;
- Hermes WebUI integration;
- Paperclip, Buzz, or Buzznode adapters.

Those remain later-stage work.
