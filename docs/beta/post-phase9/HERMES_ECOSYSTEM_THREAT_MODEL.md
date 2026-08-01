# Hermes Ecosystem Threat Model

## Provisional status and certification risk

Phase 9 implementation is locally validated, but live-node certification remains
unproven in committed evidence. This provisional Stage 0 threat model authorizes
no implementation, installation, or runtime activation. Beginning a post–Phase 9
runtime stage before authenticated connectivity, a real read-only task,
cancellation/reconciliation, bounded failover, and durable evidence round-trip
are recorded is itself a release-blocking threat.

## Assets

Hermes policy and approvals, service and agent identities, credentials, private
repositories and worktrees, budgets, audit/evidence chains, machine placement,
browser sessions, collaboration history, Sigil research and paper state, and
release/rollback artifacts.

## Trust boundaries

- User to Hermes approval boundary.
- Hermes control plane to every external adapter.
- Signed event or transport identity to Hermes execution admission.
- Worker/container/browser boundary to host and other identities.
- External content to prompts and evidence stores.
- Sigil research/paper boundary to financial execution.

## Threats and required controls

| Threat | Control |
|---|---|
| Local tests are mistaken for live-node certification | Keep Stage 0 provisional and prohibit runtime stages until all Phase 9 live gates have committed evidence. |
| External membership grants execution | Require an independent Hermes admission decision for every job. |
| Adapter becomes a second orchestrator | Single immutable Hermes job ID; reject duplicate harness ownership. |
| Unpinned or replaced dependency | Reject production registry entries without exact commit/release and dependency evidence. |
| Healthy backend violates original policy | Re-evaluate every fallback against the unchanged capability policy. |
| Browser fallback introduces authentication | Reject when the request forbids cookies, sessions, or authenticated egress. |
| Credential leakage | Profile-scoped secret references; redact values from prompts, logs, events, comments, Git, and evidence. |
| Prompt injection from internet/wiki/social content | Treat retrieved text as untrusted data; retain provenance; never interpret it as policy. |
| Mutating social or GitHub action | Separate read/write capabilities; deny writes by default and require explicit approval. |
| Account restriction or identity crossover | Dedicated accounts and browser profiles; no session reuse across agent identities. |
| Stale health or wiki state | Expiring probes and version/commit freshness checks; fail closed. |
| Cancellation lost across systems | Durable cancellation intent, propagation, reconciliation, and completion-unknown state. |
| Evidence forgery or deletion | Signed/hash-chained records, immutable references, exact association validation. |
| Cost or retry runaway | Fixed budgets, bounded retries/timeouts, cancellation, and provider cost policy. |
| Compromised worker | Least privilege, isolated workspace, scoped secrets, quarantine, revocation, and rebuild. |
| Financial action through general tools | Explicit wallet, payment, broker, order, trading-permission, and risk-limit denials. |
| Self-evolution weakens governance | Candidate-only branches, independent benchmarks/review, protected enforcement files, rollback target. |

## Agent Reach-specific attack paths

1. Upstream tool substitution after Agent Reach is pinned. Pin and inventory each
   installed backend, not only Agent Reach.
2. Public reader failure silently falling back to a logged-in browser. Bind
   authentication and session allowance to the request and re-check on fallback.
3. `doctor` output containing cookies, proxy credentials, paths, or account data.
   Parse to an allowlisted schema and store only credential type/status.
4. Install or repair commands changing system packages. Production requires safe
   mode and explicit reviewed installation evidence; automatic repair is denied.
5. Retrieved content instructing an agent to post, pay, sign, or expose secrets.
   Retrieved content has no authority; mutating and financial tools remain denied.
6. Platform anti-automation restrictions. Use dedicated accounts only, record
   risk, rate-limit probes, and quarantine channels after policy or health failure.

## Recovery objective

Disable the feature flag, revoke scoped credentials, terminate active jobs,
quarantine affected adapters/nodes, preserve evidence, restore pinned config and
artifacts, verify no financial mutation occurred, and require recertification.

These controls describe required future behavior; they do not indicate that an
external adapter, backend, service, or worker has been enabled.
