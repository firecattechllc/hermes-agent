# Step 29 system integration certification

Step 29 prepares deterministic, machine-readable evidence that the implemented
Steps 1-28 operate as one governed platform. It inventories the architecture,
checks cross-system identities and authority invariants, records a sanitized
evidence-chain manifest, exercises a local failure matrix, and prepares release
and rollback manifests.

The integration scenario is local and deterministic. It imports every module
named by the Steps 1-28 architecture inventory and exercises the real governed
model-routing and intelligence budget-planning interfaces as a composed data
flow. Each proof has a deterministic result hash. Provider behavior,
fallback, remote maintenance, release preparation, and rollback preparation are
simulated through records and deterministic adapters. Step 29 does not contact a
provider, execute remote maintenance, merge, tag, release, deploy, or roll back.

Certification fails closed. All governance invariants require explicit results;
cross-system identity maps must be complete and exact; and evidence records must
have one project/task association, supported schema versions, contiguous stable
ordering, unique identities, sanitized references, content hashes, and a valid
chain hash. Checksummed append-only JSONL stores reject conflicting replays,
idempotency collisions, corrupt checksums, invalid schemas, malformed records,
and truncated tails. Mission Control registers seven Step 29 event types and
publishes sanitized, deterministic, event-specific records idempotently.

The failure-injection matrix is local and non-executing. It records deterministic
classification, containment, retry and fallback eligibility, recovery advice,
operator escalation, terminal state, and evidence references for invalid input,
missing or expired authority, budget exhaustion, provider faults, bounded-loop
exhaustion, runtime staleness, repeated workflow failure, evidence corruption,
idempotency collision, association mismatch, policy rejection, and cancellation.

Run the certification tests with:

```shell
.venv/bin/python -m pytest -q tests/hermes_cli/test_agent_roles/test_system_integration_certification.py
```

`ready_for_operator_review` means the declared suites and invariants passed but
still requires operator approval. `conditionally_ready` records advisory risks
or skipped checks. `blocked` records a blocking finding, dirty repository, or
failed suite; `failed` records a critical finding. Release preparation only
creates sanitized manifests. Merge, tag, release, deployment, destructive
actions, credential access, spending increases, and rollback remain operator
decisions.
