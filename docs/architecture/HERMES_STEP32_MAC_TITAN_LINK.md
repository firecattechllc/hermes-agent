# Hermes Step 32 — Mac ↔ Titan Communication Link

## Purpose and roles

Step 32 creates the first governed communication boundary between Mac Hermes
(“Big Sister”) and Titan Hermes (“Little Sister”). Big Sister remains the senior
engineering, review, teaching, and lesson-authoring node. Little Sister is the
always-on nursery for bounded practice, operations, durable memory, and local
inference. Titan's local capabilities and queue do not depend on Mac being
online; an unreachable Mac produces durable retry state, never message loss.

This milestone transports typed data and governed artifacts. It does not grant
either node new execution authority.

## Trust boundaries

The link has three independent boundaries:

1. A private transport such as Tailscale supplies reachability and encryption.
   Tailscale identity is not application authentication.
2. The FastAPI adapter requires a pluggable bearer-token verifier. Tokens are
   resolved at runtime from an environment or file reference and are never
   placed in envelopes, journals, telemetry, examples, or error messages.
3. The application service validates the exact sender and recipient, message
   type, payload size, approval state, and channel policy before acceptance.

The default bind host and client URL are loopback. Public binding is rejected by
configuration. A reviewed private-interface deployment can be added later; it
is intentionally not performed by Step 32.

## Envelope and prohibited authority

Every message uses schema version 1 and carries message, correlation and
conversation identifiers; sender and recipient nodes; type and priority;
creation time; JSON payload; confidence; evidence and artifact references;
approval and delivery state; and bounded retry metadata. Initial types are
`chat`, `task_request`, `task_result`, `lesson_package`, `status`, `escalation`,
`acknowledgement`, and `error`.

Unknown fields and schema versions fail closed. Payload validation recursively
denies command, shell, sudo/root, script, deployment, publishing, spending,
external messaging, destructive-operation, production-mutation, credential,
secret, and authentication fields. References must be sanitized URIs. Approval
metadata can only refer to an approval decision; it cannot create one.

## Message lifecycle and persistence

The durable store is a permission-restricted, checksummed, hash-chained JSONL
journal. Its latest record projects each message into one of:

`queued → delivered → acknowledged`

or:

`queued → retryable → dead_lettered`

Messages may also be `failed` or `rejected`. Duplicate message IDs with
identical immutable content return the existing result. Reuse with different
content is an identity collision. Writes loop until complete and fsync before
returning. Replay checks sequence, checksum, association, and hash-chain
continuity. Only an unterminated final fragment is recovered after a torn write;
completed corrupt records fail closed.

Retries are bounded and record attempts, exponential backoff time, the last
safe error code, and terminal dead-letter state. Synchronization never silently
drops records. The milestone-one `sync` command checks reachability and reports
the queue; automatic replay authority is future work.

## Titan API and Mac client

The private Titan adapter exposes authenticated operations:

- `GET /status`
- `GET /queue`
- `POST /chat`
- `POST /task`
- `POST /lesson`
- `GET /reports/latest`

POST routes require the corresponding structured envelope. Requests have a
configured byte limit and produce structured authentication, validation, policy,
and not-found errors. No route executes commands.

The typed Mac client supports all six operations using an injected transport,
so tests need no live network or hardware. Timeouts and unreachable Titan return
a retryable `titan_unreachable` result instead of raising. The design leaves a
transport seam for future streaming but Step 32 is request/response only.

## Presence and observability

Status reports node identity and role, observed presence, service version,
uptime, queue counts, nursery/Ollama/FinBERT/memory-index health, last sync,
pending escalations, degraded components, and an evidence timestamp. Missing
health providers report `unknown`; they are never inferred as healthy.

Mission Control receives only lifecycle metadata: message ID/type, node IDs,
delivery state, correlation, retry attempt, and safe reason code. Message bodies,
credentials, and authentication material are excluded. Accepted and policy-
rejected structured messages remain in the audit journal.

## Configuration example

Configuration is disabled by default. Safe illustrative values are:

```yaml
enabled: false
node_id: mac-hermes
node_role: big_sister
titan_base_url: http://127.0.0.1:9320
bind_host: 127.0.0.1
authentication_provider: bearer_env
authentication_token_reference: env:HERMES_LINK_TOKEN
connect_timeout_seconds: 2
read_timeout_seconds: 10
queue_path: ~/.hermes/link
maximum_retries: 3
maximum_payload_bytes: 65536
```

For the CLI, set `HERMES_LINK_TITAN_URL` to a reviewed private endpoint and
provide `HERMES_LINK_TOKEN` through the existing secret-management process.
Never commit either a real host or token.

## Local development and validation

No Tailscale, Ollama, FinBERT, Titan, Mac hardware, or external API is needed:

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hermes_link -q
.venv/bin/python -m pytest tests/hermes_cli/test_mission_control -q
.venv/bin/ruff check hermes_cli/hermes_link tests/hermes_cli/test_hermes_link
```

Operator commands are `hermes link status`, `hermes link queue`,
`hermes link chat <message>`, and `hermes link sync`; each supports `--json`.

## Safe deployment concept, rollback, and future work

Deployment to Titan is not part of this change. It requires Matthew's approval
for the private bind/interface, Tailscale ACL or service advertisement, runtime
secret provisioning, service supervision, storage path, and health-provider
wiring. Before enabling, verify both node identities, rotate a dedicated
application token, test rejection from an unauthenticated peer, and retain the
local queue during upgrades.

Rollback is reversible: set `enabled: false`, stop the separately approved
Titan service, and leave the append-only journal intact for audit/replay. Do not
delete queue data during rollback. Reverting the CLI/API code does not change
Titan's existing nursery or local inference behavior.

Future milestones may add governed automatic replay, sync lifecycle events,
token-provider plugins, reviewed private-interface binding, streaming, and a Mac
presence UI. None is implied or authorized by Step 32.
