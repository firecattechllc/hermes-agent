# Hermes Ecosystem Decisions

## Provisional status

Phase 9 implementation is locally validated; Phase 9 live-node certification
remains unproven in committed evidence. These records are provisional Stage 0
planning decisions. They authorize no implementation, installation, or runtime
activation. No post–Phase 9 runtime integration may begin until the live-node
gates are completed and recorded.

## D-001 — Hermes is the sole execution authority

External identity, channel membership, assignment, health, or authentication is
input to policy; none grants execution. Status: provisionally accepted for
planning.

## D-002 — Registry and worker contracts precede adapters

All integrations depend on a pinned lifecycle registry and a fail-closed common
job contract. Status: provisionally accepted for planning.

## D-003 — Integrations are disabled by default

No service starts, installs dependencies, probes authenticated channels, or
handles jobs before feature enablement and environment certification. Status:
provisionally accepted for planning.

## D-004 — Adapters translate; they do not duplicate orchestration

Hermes job IDs, policies, cancellation, results, and evidence associations are
canonical. Status: provisionally accepted for planning.

## D-005 — Agent Reach is a capability selector, not an authority

Hermes requests a capability. The Agent Reach adapter may select only a pinned,
healthy backend that satisfies the unchanged Hermes policy. Direct upstream tool
invocation must pass the same admission and evidence boundary. Status:
provisionally accepted for planning.

## D-006 — Agent Reach begins with public reads only

The Stage 8A pilot is limited to public webpages, YouTube transcripts, RSS/Atom,
public GitHub reads, and semantic search. Authenticated social access and all
mutations are deferred. Status: provisionally accepted for planning.

## D-007 — No automatic production dependency installation

Stage 8A supports dry-run and safe-mode plans. Production system-package changes
require a separately reviewed, pinned installation action. Status: provisionally
accepted for planning.

## D-008 — Source and observed behavior outrank references

Installed source, tests, and runtime evidence outrank matching official docs,
Hermes-Wiki, catalogs, and external claims. Status: provisionally accepted for
planning.

## D-009 — Sigil remains paper-only for this program

Wallets, payments, signing, broker submission, live orders, risk-limit changes,
and trading-permission changes are denied. Status: provisionally accepted for
planning.

## D-010 — Stage 0 has no runtime behavior

This branch creates provisional documentation only. Its existence cannot be used
as evidence that Phase 9 is certified or as authority to open a runtime stage.
Status: provisionally accepted for planning.

## D-011 — Phase 9 live-node evidence gates every runtime stage

Local validation of the Phase 9 implementation is necessary but insufficient.
Authenticated Titan/Mac/Prime connectivity, one real read-only task, cancellation
and reconciliation, bounded failover, and a durable evidence round-trip must be
completed and committed before any post–Phase 9 runtime work begins. Status:
provisionally accepted for planning.
