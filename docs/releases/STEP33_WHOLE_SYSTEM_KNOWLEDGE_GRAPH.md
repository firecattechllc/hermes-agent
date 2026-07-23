# Hermes Step 33 — Whole-System Knowledge Graph and Continuous Discovery

## Architecture

Step 33 extends Hermes with an evidence-backed local graph. It does not create
execution authority. Titan (“Little Sister”) can collect and reconcile its own
facts while Mac is offline. Mac (“Big Sister”) remains the senior global
reconciliation and impact-analysis node.

The implementation is split into strict models, conservative configuration,
governed collectors, a transactional SQLite repository, a service layer, and
the `hermes knowledge` operator surface. SQLite uses WAL, bounded busy waits,
idempotent schema creation, and immediate write transactions. No external graph
database or new runtime dependency is required.

## Entity and relationship taxonomy

Entities describe hosts, filesystems, networks, services, containers, models,
repositories, schedules, Python environments, Hermes runtimes, backups, and
configured registries. Every entity has deterministic identity, node
provenance, collector provenance, evidence references, confidence, trust,
first/last observation times, and lifecycle state.

Relationships are closed to:

`HOSTS`, `RUNS`, `DEPLOYED_ON`, `DEPENDS_ON`, `USES`, `CONNECTED_TO`, `STORES`,
`EXPOSES`, `SCHEDULES`, `PRODUCES`, `CONSUMES`, `BACKED_UP_TO`, `MANAGED_BY`,
`PART_OF`, and `COMMUNICATES_WITH`.

## Collector safety model

Discovery is read-only. Collectors are selected from an explicit registry and
commands from an executable allowlist. Commands use argument vectors,
`shell=False`, a sanitized environment, per-command timeouts, and bounded
stdout/stderr. Unavailable tools produce partial snapshots rather than failing
the graph. There is no sudo, privilege escalation, port scanning, package
installation, network probing, deployment, or mutation.

Repository, Python-environment, and backup discovery only use explicitly
approved roots. Defaults contain no filesystem or home-directory scan roots.
Database/API discovery is limited to future configured registries and never
probes arbitrary addresses.

Raw evidence is bounded and recursively redacts keys that indicate credentials,
tokens, secrets, private keys, or authorization data. Git remote credentials
are stripped before persistence.

## Storage, reconciliation, and drift

A node snapshot is applied in one transaction. Upserts retain `first_seen_at`
and update `last_seen_at`. Added, changed, restored, and stale facts produce
immutable change records. Missing facts are not stale until a configurable
number of successful collector snapshots omit them. A failed collector cannot
tombstone prior observations. Historical evidence, snapshots, and change
records are retained.

Canonical JSON uses sorted keys and compact separators; stable IDs and hashes
use SHA-256. Traversal is depth-bounded, cycle-safe, and returns its evidence
references. Impact reports do not claim unaffected capabilities without
adequate coverage.

Trust levels are `unverified`, `observed`, `corroborated`, and `verified`.
Confidence is a numeric estimate from 0 through 1. An observation never becomes
verified merely because it was persisted.

## Mac ↔ Titan federation

Federation payloads extend Step 32’s structured-message approach and support:

- `discovery_snapshot_summary`
- `discovery_change_batch`
- `graph_sync_request` and `graph_sync_response`
- `evidence_request` and `evidence_response`

Envelopes carry schema version, sender/recipient nodes, message and correlation
IDs, UTC creation time, and a canonical content hash. Batches are bounded to
500 records and recursively sanitized. Message IDs are idempotent; reuse with a
different content hash fails closed. Step 32’s checksummed local queue remains
the offline transport and no remote shell/execution field is introduced.

## Operator commands

```text
hermes knowledge discover [--collector NAME] [--node NODE] [--json]
hermes knowledge status [--json]
hermes knowledge entities [--type TYPE] [--name NAME] [--label LABEL] [--node NODE] [--status STATUS] [--json]
hermes knowledge show ENTITY_ID [--json]
hermes knowledge neighbors ENTITY_ID [--direction upstream|downstream|both] [--depth N] [--json]
hermes knowledge changes [--since TIMESTAMP] [--json]
hermes knowledge impact ENTITY_ID [--scenario outage|remove|upgrade] [--json]
hermes knowledge export --redacted --output PATH
hermes knowledge collectors [--json]
```

For fixture isolation set `HERMES_KNOWLEDGE_DB` to a temporary path. Production
deployment, Titan service changes, and scheduling continuous collection remain
separate approval-gated operator actions.

## Sample redacted report

```json
{
  "node_id": "titan-hermes",
  "node_role": "little_sister",
  "entity_count": 12,
  "coverage_by_type": {"host": 1, "repository": 3, "service": 8},
  "stale_count": 0,
  "low_confidence_count": 0,
  "unresolved_conflicts": 0
}
```

## Limitations

Initial command-backed service, Docker, Ollama, schedule, and network collectors
store deliberately bounded observations and degrade when their local command is
absent. They do not infer deployment relationships that evidence does not
prove. Database/API and backup collectors are inert until explicit approved
registries/roots are supplied. Continuous scheduling and transport delivery are
not enabled automatically.

## Rollback

Stop any separately configured discovery schedule, remove Step 33 code and CLI
registration, and retain or archive the SQLite file for audit. Deleting the
database destroys historical evidence and requires explicit operator approval.
Step 32 queues and earlier Hermes runtime behavior are otherwise independent.

## Acceptance checklist

- [x] Strict serializable graph, evidence, drift, impact, and federation models
- [x] Idempotent transactional SQLite repository
- [x] First-seen preservation and missed-snapshot stale protection
- [x] Collector and command allowlists with bounded output
- [x] Conservative approved-root defaults
- [x] Cycle-safe traversal and evidence-backed impact
- [x] Structured, hashed, idempotent federation payloads
- [x] Service API and `hermes knowledge` CLI
- [x] Redacted JSON export
- [x] Deterministic fixture-only focused tests
- [ ] Continuous collection schedule configured by an operator
- [ ] Titan deployment approved and performed by Matthew
