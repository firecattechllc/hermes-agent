# Post-Phase 9 Hermes Ecosystem Program

> **STATUS: PHASE 9 LIVE-NODE CERTIFICATION COMPLETE — COMMITTED EVIDENCE ON FILE**
>
> Phase 9 live-node certification is complete and recorded in committed evidence
> (`docs/architecture/hydra-ecosystem/evidence/PHASE9_LIVE_CERTIFICATION.json`,
> verified against `PHASE9_FLEET_EVIDENCE.jsonl`). Sigil remains paper-only and
> broker submission remains disabled; all external systems remain disabled by
> default until their own stage gates pass. These Stage 0 documents are
> architecture planning only; their existence does not authorize implementation,
> installation, configuration, or activation.

Hermes remains the final authority for identity, permissions, budgets, model and
provider routing, tools, placement, admission, evidence, approval, promotion,
rollback, and audit. External systems provide bounded capabilities; they do not
grant execution authority.

Hermes WebUI, Buzz, Buzznode, Paperclip, Agent Reach, Self-Evolution, and all community plugins remain disabled and uninstalled.
Sigil remains paper-only and broker submission remains disabled.

These external systems have no independent execution authority and remain
disabled by default.

## Documents

- [HERMES_ECOSYSTEM_ASSESSMENT.md](HERMES_ECOSYSTEM_ASSESSMENT.md) — repository findings and gap analysis.
- [HERMES_ECOSYSTEM_ROADMAP.md](HERMES_ECOSYSTEM_ROADMAP.md) — staged pull-request program.
- [HERMES_ECOSYSTEM_THREAT_MODEL.md](HERMES_ECOSYSTEM_THREAT_MODEL.md) — cross-system threats and controls.
- [HERMES_ECOSYSTEM_DECISIONS.md](HERMES_ECOSYSTEM_DECISIONS.md) — Stage 0 architecture decisions.
- [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md), and
  [THREAT_MODEL.md](THREAT_MODEL.md) — operator summaries.
- The remaining policy documents define registry, roles, fleet, external
  contracts, Sigil, self-evolution, and recovery boundaries.

## Stage gate

Every integration starts disabled. A catalog listing, network reachability,
channel membership, successful health probe, or external authentication state
does not authorize execution. Each later stage requires its own branch, threat
review, deterministic tests, rollback evidence, and draft pull request.

Before any later stage begins, committed certification evidence must demonstrate
authenticated Titan/Mac/Prime connectivity, one real read-only task, cancellation
and reconciliation, bounded failover, and a durable evidence round-trip.
