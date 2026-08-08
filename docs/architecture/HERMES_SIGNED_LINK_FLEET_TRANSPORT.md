# Signed Hermes Link Fleet Transport

## Scope and threat model

This milestone upgrades the private Hermes Link application boundary from a
static-bearer-only option to versioned signed requests. Tailscale supplies
encrypted reachability and authenticated device identity; Hermes Link supplies
separate application authentication and binds every request to the exact Mac
coordinator, target node, method, path, payload digest, time, nonce, credential,
algorithm, and request identity.

The boundary assumes a peer or intermediary may capture, replay, reorder, alter,
or redirect requests. It fails closed on an invalid signature, mismatched node,
payload, path, or method, unknown/revoked/expired credential, reused nonce,
duplicate request identity, corrupt replay state, or a timestamp outside the
configured skew window. It does not grant shell, filesystem, credential,
network, broker, portfolio, approval, spending, deployment, publication, or
autonomous trading authority.

## Signed request version 1

`hmac-sha256` signs canonical sorted compact JSON containing:

- `request_id`
- `coordinator_node_id`
- `target_node_id`
- uppercase HTTP method
- canonical path without a query string
- SHA-256 of canonical compact JSON payload, or the empty body
- Unix timestamp
- random nonce
- credential identity
- algorithm identity
- schema version

These fields are carried in `X-Hermes-Link-*` headers. The signature and secret
are never written to replay evidence. JSON is canonicalized before hashing, so
ambiguous or malformed payloads fail before task admission.

The default accepted clock skew is 120 seconds. Nonces and request identities
remain unique for a bounded 600-second retention window. Accepted identities
are synchronously appended to a mode-0600 restart-safe journal before the
application handler runs. A replay after process restart is rejected.

## Credentials, rotation, and revocation

Credential registries contain metadata and only `env:` or absolute `file:`
references. Secret files must be regular, non-symlink files with mode 0600 or
stricter. A credential binds one coordinator identity to one target identity and
has activation, expiry, and `active`, `retiring`, or `revoked` state.

One active credential is allowed per node pair. Rotation creates a new active
credential and moves the previous credential to a bounded retiring overlap.
Both verify during that overlap. Revoked, expired, or not-yet-active credentials
fail closed. Operator reports include credential identities and lifecycle state,
never secret values.

Initialize, verify, rotate, and revoke with:

```bash
.venv/bin/python scripts/hermes-link-credentials.py initialize \
  --root /absolute/private/root \
  --credential-id credential-titan-001 \
  --coordinator-node-id node-mac \
  --target-node-id node-titan

.venv/bin/python scripts/hermes-link-credentials.py verify \
  --root /absolute/private/root

.venv/bin/python scripts/hermes-link-credentials.py enroll \
  --root /absolute/private/root \
  --credential-id credential-prime-001 \
  --coordinator-node-id node-mac \
  --target-node-id node-prime

.venv/bin/python scripts/hermes-link-credentials.py rotate \
  --root /absolute/private/root \
  --credential-id credential-titan-002 \
  --coordinator-node-id node-mac \
  --target-node-id node-titan \
  --overlap-seconds 3600

.venv/bin/python scripts/hermes-link-credentials.py revoke \
  --root /absolute/private/root \
  --credential-id credential-titan-001
```

Secrets are generated internally and never accepted as command arguments or
printed. Copying or enrolling the external credential files is an operator
deployment action, not an application-startup action.

## Mac coordinator

The backend client selects signed mode when these backend-only references exist:

- `HERMES_LINK_CREDENTIAL_REGISTRY`
- `HERMES_LINK_COORDINATOR_NODE_ID`
- `HERMES_LINK_TARGET_NODE_ID`
- the reviewed private `HERMES_LINK_TITAN_URL`

The renderer and preload do not set or receive these values. A missing registry,
node identity, secret reference, or remote node leaves the optional Link
unavailable and does not prevent Sigil startup. The legacy bearer client remains
for compatibility but is not used by the signed deployment templates.

## Titan and Prime deployment

`deploy/hermes-link/hermes-link.service` runs under the dedicated `hermes`
identity with no new privileges, an empty capability set, strict system and home
protection, private devices and temporary storage, write access only to the
fleet state directory, and a restrictive umask. The service binds to loopback.
Operators expose it only through an authenticated tailnet mechanism or an
authenticated bounded tunnel; it never binds a public interface.

Titan and Prime examples declare exact identities and resource bounds. Prime
starts with only `research_preparation`, implemented as a deterministic
digest-only handler. Raw prompt content is not available to that handler.
Network, shell, filesystem, credentials, broker, portfolio, and recursive
worker spawning are all false and validated at startup.

The deployment tool requires the current tailnet node identity, DNS identity,
hostname, governed node identity, external registry, and service configuration.
It uses authenticated Tailscale SSH by default; conventional SSH is an explicit
operator-selected fallback and still must target the verified tailnet DNS name.
Without `--install` it performs a dry run. Installation transmits credentials
through process stdin, not arguments, stages an immutable content-addressed
Hermes/Sigil source snapshot under `/opt/hermes-link/releases`, and never
modifies `/opt/hermes/current`. Titan reuses its compatible existing virtual
environment through a dedicated symlink. A node without that runtime receives
an isolated `/opt/hermes-link/venv` containing only the pinned service
dependencies during the explicit operator installation; application startup
never installs or downloads anything. The tool installs only the dedicated
source, unit, config, credential, environment, and state paths and fails closed
if the resulting imports or service health check fails.

## Task lifecycle and ambiguity

The signed fleet adapter transports the existing `GovernedRemoteTask` and
`GovernedRemoteResult` contracts. It does not add a planner or generic task
language. The remote service admits only explicitly registered task types,
exact node identities, bounded timeouts, digest-only inputs, safe schemas, and
bounded structured results.

Accepted tasks persist as acknowledged before execution. Success or failure is
terminal and immutable. Cancellation requires the exact task and cancellation
token identities. A lost result response is surfaced to the Phase 9 coordinator
as transport ambiguity; the coordinator records `completion_unknown` and uses
the signed exact-task query. It never automatically duplicates execution.
Result identity and input/output digests are revalidated by the existing fleet
transport before evidence acceptance.

## Evidence and privacy

The signed replay journal contains request identity, a one-way nonce digest,
credential identity, acceptance time, and event classification. Phase 9 fleet
evidence separately records dispatch, acknowledgement, result, cancellation,
failure, and ambiguity reconciliation with hash-chain integrity. Neither store
contains secret material, reusable signatures, raw prompts, raw model output,
environment values, transport addresses, broker data, or portfolio data.

## Validation and live certification

Before installation, validate the service config with:

```bash
/opt/hermes/current/venv/bin/python -m hermes_cli.hermes_link.runtime \
  --config /etc/hermes-link/service.json --validate
```

For each node, run the deployment tool in dry-run mode first, verify the
tailnet identity independently, then repeat with `--install`. Certify signed
status, bounded dispatch, replay rejection, cancellation, lost-result query,
restart recovery, and sanitized evidence. Never use SSH reachability alone as
proof of application authentication.

Rollback stops and disables only `hermes-link.service` and restores the previous
operator-retained service/config bundle. Preserve `/var/lib/hermes-link` for
audit and replay safety. Revoking the affected credential prevents subsequent
use; do not delete evidence to simulate revocation.
