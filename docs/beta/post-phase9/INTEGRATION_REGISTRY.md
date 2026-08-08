# Integration Registry Contract

## Stage 1 status

Phase 9 live-node certification is complete and merged. Stage 1 is authorized
for the deterministic governed registry schema, lifecycle validation, durable
revision identity, append-only lifecycle evidence, and read-only inspection.
The runtime registry starts empty, remains disabled by default, and no listing or
lifecycle state authorizes installation or activation. Stage 2 and all later
stages remain unimplemented and disabled.

Each future entry records project name, repository URL, pinned commit/release,
category, maturity, license, maintainer/activity evidence, credentials, network
and filesystem access, tool permissions, execution model, external transmission,
install mechanism, risks, overlap, evaluation evidence, approved machines and
profiles, rollback instructions, and lifecycle state.

Lifecycle: `discovered`, `under_review`, `rejected`, `sandbox_approved`, `pilot`,
`certified`, `deprecated`, `quarantined`.

Unknown integrations and unpinned production integrations are rejected. Discovery
never activates an entry. Agent Reach additionally inventories each selected
upstream backend and records channel operations, health/probe time, auth/session
requirements, destinations, egress, account risk, roles, machines, evidence, and
disable/rollback instructions.
