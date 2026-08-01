# Hermes Ecosystem Roadmap

## Stage 1 authorization gate

Phase 9 live-node certification is complete and merged. Stage 1 is authorized
only for the pinned, disabled-by-default governed integration registry. Stage 1
does not install or activate an integration. Later stages remain unimplemented,
disabled, and unauthorized until separately approved.

Each stage uses a separate branch and draft pull request. Integrations remain
disabled until their own certification passes.

| Stage | Suggested branch | Smallest complete outcome |
|---:|---|---|
| 0 | `beta-post9-00-assessment` | Provisional architecture, decisions, threat model, and roadmap; no runtime change or activation. |
| 1 | `beta-post9-01-integration-registry` | Pinned registry schema, lifecycle, validation, read-only inspection. |
| 2 | `beta-post9-02-worker-contract` | Fail-closed provider-neutral worker/job contract. |
| 3 | `beta-post9-03-hermes-webui` | Disabled private discovery/health/deep-link adapter. |
| 4 | `beta-post9-04-paperclip-adapter` | Disabled assignment, heartbeat, transcript, worktree, and cost adapter. |
| 5 | `beta-post9-05-buzz-relay` | Signed identity/event mapping, correlation, cancellation, deduplication. |
| 6 | `beta-post9-06-buzznode` | Dedicated-worker registration and per-node certification/quarantine. |
| 7 | `beta-post9-07-hermes-wiki` | Version-aware cited knowledge ingestion with stale detection. |
| 8 | `beta-post9-08-ecosystem-discovery` | Catalog ingestion and sandbox evaluation proposals; no auto-install. |
| 8A | `beta-post9-08a-agent-reach` | Disabled Agent Reach registry/adapter and public-read pilot. |
| 9 | `beta-post9-09-self-evolution` | Isolated low-risk skill candidates with baseline and rollback gates. |
| 10 | `beta-post9-10-routing-and-fleet` | Capability, privacy, cost, health, certification, and placement convergence. |
| 11 | `beta-post9-11-sigil-bridge` | Read-only/paper-only Sigil projections and financial denial certification. |

## Per-stage contract

Every pull request contains explicit scope, architecture contract, threat model,
configuration schema, deterministic tests, rollback strategy, operational
documentation, and evidence artifacts. It performs no unrelated refactor.

## Stage 8A vertical slices

1. Registry-only evaluation: pin Agent Reach and transitive upstream tools;
   record licenses, install behavior, destinations, egress, and rollback.
2. Governed command wrapper: dry-run, safe install, doctor, configuration status,
   per-channel probe, uninstall dry-run, disable, and quarantine. No installation
   occurs merely because commands exist.
3. Capability policy: select an approved healthy backend while preserving the
   original read/write, authentication, browser-session, destination, role,
   machine, budget, and evidence constraints.
4. Public-read pilot: webpage, YouTube transcript, RSS, public GitHub, and
   semantic search only.
5. Certification: disabled default, fallback, authenticated rejection, secret
   redaction, health/staleness, quarantine, rollback, and financial denial tests.

Authenticated social channels require later channel-specific approval and are
not part of the initial pilot.

Stage 2 through Stage 11, including Stage 8A, remain proposed sequence only.
Their implementation, installation, and activation remain disabled and require
separate authorization. Stage 1 grants no installation or activation authority.
