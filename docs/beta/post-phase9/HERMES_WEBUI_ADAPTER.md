# Hermes WebUI Adapter

## Stage 3 status

Stage 3 defines a disabled-by-default, read-only Hermes WebUI discovery,
health, and deep-link adapter for the current Hermes fleet:

- Hermes Titan — primary always-on Hermes node.
- Hermes Mac — senior local and development Hermes node.

Buzz remains the planned shared human-agent collaboration workspace. The
Hermes WebUI adapter does not replace Buzz and does not act as an execution
or orchestration authority.

## Scope

The adapter provides only:

- immutable Hermes WebUI target definitions;
- private and tailnet origin validation;
- approved route allowlisting;
- approved query-key allowlisting;
- injected health evidence evaluation;
- stale, unavailable, degraded, incompatible, healthy, and disabled states;
- worker-contract schema compatibility reporting;
- sanitized private deep-link construction.

## Authority boundary

The adapter cannot:

- authenticate;
- resolve or expose credentials;
- probe a network endpoint itself;
- start or stop Hermes;
- dispatch a worker job;
- approve work;
- install or activate an integration;
- execute shell commands;
- access arbitrary filesystems;
- mutate policy;
- submit broker orders;
- mutate financial state.

Health and deep-link availability are informational only and never authorize
execution.

## Default topology

The default disabled target set contains:

- `hermes-titan`
  - role: `primary`
  - private tailnet address
- `hermes-mac`
  - role: `senior`
  - private tailnet address

The targets remain disabled until a later reviewed configuration and
environment certification explicitly enable them.

## Health semantics

- `disabled`: target is disabled by policy.
- `unavailable`: no current evidence or the endpoint did not respond.
- `stale`: evidence exceeded the configured freshness window.
- `incompatible`: the reported worker-contract schema is unsupported.
- `degraded`: current compatible evidence reports degraded health.
- `healthy`: current compatible evidence reports healthy status.

Only healthy or degraded current evidence may expose an approved deep link.
Neither state grants authentication or execution authority.

## Deferred work

Stage 3 does not include:

- real HTTP probing;
- dashboard login;
- dashboard token exchange;
- WebUI session control;
- job execution;
- Mission Control projection;
- Buzz relay;
- Paperclip;
- Buzznode;
- routing between Titan and Mac.

Those remain later-stage work.
