# Integration Registry Contract

## Provisional status

Phase 9 is locally validated but lacks committed live-node certification. This
Stage 0 registry contract is provisional planning only; no entry exists at
runtime, and no listing authorizes installation or activation.

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
