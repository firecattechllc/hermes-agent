# Hermes Ecosystem Assessment

## Provisional status and Phase 9 prerequisite

Phase 9 implementation is locally validated, but Phase 9 live-node certification
remains unproven in committed evidence. This assessment is provisional Stage 0
architecture planning only. It does not authorize implementation, installation,
configuration, or activation. No post–Phase 9 runtime integration may begin until
authenticated Titan/Mac/Prime connectivity, one real read-only task, cancellation
and reconciliation, bounded failover, and a durable evidence round-trip are
completed and recorded.

## Scope and evidence basis

This provisional Stage 0 assessment maps the current checkout at commit
`45ad3c127` and defines possible integration seams without changing runtime
behavior. The commit contains locally validated Phase 9 implementation, not
committed proof of the required live-node gates. External projects are candidates
only until Phase 9 is certified and their repository, license, release,
dependencies, and behavior are pinned and reviewed in the governed integration
registry.

All proposed integrations remain disabled by default and have no independent
execution authority. Sigil remains paper-only with broker submission disabled.

## Existing functionality

| Concern | Existing source of truth | Reuse direction |
|---|---|---|
| Roles and assignments | `hermes_cli/agent_roles/models.py` | Extend role policy rather than duplicate identities. |
| Launch admission | `launch.py`, `launch_validation.py`, `workflow_dispatch.py` | Require a valid Hermes contract before any adapter call. |
| Execution lifecycle | `runtime_execution.py` and workflow stores | Correlate external activity to immutable Hermes job IDs. |
| Model routing | `model_routing.py`; `apps/sigil/src/sigil/ai/routing.py` | Keep capability, cost, privacy, trust, and health policy centralized. |
| Fleet and placement | `fleet_inventory.py`; Sigil `ai/fleet.py` | Reuse authenticated registration, health, placement, cancellation, and quarantine concepts. |
| Private transport | `hermes_cli/hermes_link/` | Use signed, replay-resistant transport; do not expose arbitrary shell or URLs. |
| Evidence and audit | Mission Control journals, system certification, Sigil AI ledgers | Store sanitized immutable references and fail closed on corruption or missing evidence. |
| Mission Control | `hermes_cli/mission_control/`; Sigil desktop bridge/UI | Project external status without importing external authority. |
| Provider and tools config | `providers.py`, `provider_catalog.py`, `tools_config.py` | Add disabled, schema-validated integration configuration. |
| Financial boundary | Sigil order, risk, autonomous-paper, and broker-submission modules | Preserve paper-only defaults and deny financial execution. |

## Missing functionality

- A provider-neutral governed integration registry with pinned versions and
  lifecycle state.
- A single worker contract spanning local profiles, hosted agents, Buzznode,
  Paperclip employees, and coding runtimes.
- Explicit adapters and correlation contracts for WebUI, Paperclip, Buzz, wiki,
  ecosystem discovery, Agent Reach, and self-evolution.
- Cross-adapter cancellation propagation, duplicate-harness rejection, common
  budget enforcement, and consistent quarantine/rollback records.
- Version-aware knowledge provenance and stale-source rejection.

## Proposed future integration candidates

- Hermes WebUI: private operator cockpit and health/deep-link target.
- Paperclip and Hermes Paperclip Adapter: organizational and assignment layer.
- Buzz/Buzz relay: signed collaboration events and searchable history.
- Buzznode: isolated persistent workstation execution class.
- Agent Reach: optional governed internet capability selection layer.
- Hermes Agent Self-Evolution: candidate generation, initially skills only.

## Reference-only systems

- Hermes-Wiki is evidence-bearing reference material, never runtime authority.
- Awesome Hermes Agent is discovery input, never an install allowlist.
- GitHub remains the source of truth for reviewed code, evidence, releases, and
  promotion history.

## Deferred components

- Authenticated Agent Reach social channels and every mutating channel operation.
- Tool implementation, prompt, governance, credential, deployment, financial,
  or continuous self-evolution.
- Public control surfaces, autonomous publishing, payments, wallets, live
  trading, and broker submission.

## Integration points

1. Registry records bind an external component to a pinned source identity,
   approved profiles/machines, required credentials, and rollback procedure.
2. The common worker contract binds each external execution to immutable Hermes
   job, identity, role, workspace, tool, network, secret, budget, approval, and
   evidence policy.
3. Adapters translate external state and events; Hermes admission remains the
   only path to execution.
4. Mission Control displays sanitized projections and immutable evidence links.
5. Release Guardian validates registry, policy, tests, evidence, branch state,
   and rollback readiness without gaining merge authority.

## Agent Reach assessment

Upstream describes Agent Reach as a selector, installer, health checker, and
router; actual operations are performed by tools such as Jina Reader, `yt-dlp`,
`gh`, feedparser, Exa through MCP, OpenCLI, and channel-specific CLIs. Therefore
Hermes must govern both the requested capability and the selected backend. An
Agent Reach health result alone is not authorization or evidence of policy
compliance.

Upstream supports safe and dry-run installation, `doctor`, channel-specific
configuration, and uninstall dry-run. It also supports cookie/browser-session
channels that carry account restriction and credential exposure risks. Stage 8A
must pin Agent Reach and every installed upstream dependency, prohibit automatic
system-package installation in production, and reject authenticated or mutating
fallbacks during the initial public-read pilot.

Initial pilot capabilities are limited to public webpage reads, YouTube
transcripts, RSS/Atom, public GitHub reads, and semantic web search. No component
is installed in Stage 0.

## Conflicts and overlaps

- Hermes already has provider, browser, web-search, tools, fleet, and evidence
  concepts. Agent Reach must not create a second permission or routing plane.
- Buzz and Paperclip both model identities, projects, assignments, comments, and
  events. They must share Hermes correlation IDs rather than launch independent
  harnesses for the same job.
- WebUI and Sigil Mission Control overlap as operator surfaces; WebUI cannot gain
  Sigil financial authorization.
- Paperclip cost reporting is observational; Hermes budgets remain admission
  controls.
- External wiki and catalog freshness may conflict with installed code; installed
  source and observed tests win.

## Stage 0 conclusion

The repository has strong reusable governance primitives. The safe program is
adapter-first and registry-first: establish identity, policy, evidence, and
rollback contracts before installing external services. No broad refactor or
runtime behavior change is justified or authorized in Stage 0. This conclusion
is provisional and cannot open Stage 1 or any runtime stage until the missing
Phase 9 live-node certification is committed and independently reviewable.
