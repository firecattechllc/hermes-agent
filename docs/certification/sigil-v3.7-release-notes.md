# Sigil 3.7.0 — Governed Fleet Release Notes

Status: **development build, not signed/notarized/tagged**. This document
describes what was implemented and directly verified during the 3.7.0
development cycle. It does not claim public-release readiness.

## Summary

Sigil 3.7.0 connects the Sigil desktop application to the Hermes Fleet
Unification live runtime (Prime/Titan/Mac control plane, merged into `main`
at commit `315262220`) for the first time. Previously, `apps/sigil` had no
code path to `hermes_cli.prime` at all — this release adds the minimum
coherent governed adapter needed on both sides of that boundary, plus real
fleet-visibility UI in Mission Control, without weakening any existing
safety boundary.

## What changed

### Hermes Prime backend (`hermes_cli/prime/`)

- **`POST /v1/sigil/route`** (`sigil_route_server.py`, wired into
  `server.py`): assembles real `PrimeGovernedProviderAdapter`s from live
  registered-node endpoints (`FleetRuntime.registry`) plus an
  operator-configured per-node Ollama model-alias map
  (`HERMES_PRIME_NODE_MODEL_ALIASES`), and dispatches through the same two
  independent admission/health gates every other governed dispatch path
  uses. Fails closed (empty aliases, unregistered nodes, stale health,
  revoked identity) by construction — an alias being configured is never
  treated as authorization by itself.
- **`GET /v1/fleet/certification`**: exposes Prime's live, periodically
  refreshed certification snapshot.
- **Real certification wiring**: `hermes_cli.prime.certification.certify_fleet`
  previously had six of its eleven boolean inputs supplied only by hardcoded
  `True` test fixtures — nothing in the repository actually computed them.
  Added `production_certification_selftests.py` (real, assertion-driven
  selftests for identity-registry conflict detection, event schema
  validity, health protocol compatibility, admission default-deny,
  Sigil-contract restrictions, and remote-maintenance default-deny) and
  `certification_cli.py` (a CLI wrapper — `python -m
  hermes_cli.prime.certification_cli` — that runs all of them, the five
  existing live-runtime selftests, real evidence-chain verification, and
  optionally Stage 1 regression, against a real deployed `state_root`).
  `entrypoints.py`'s Prime service now runs this for real at startup and on
  `HERMES_PRIME_CERTIFICATION_INTERVAL_SECONDS`, off the request-serving
  thread, and never reports CERTIFIED without it.
- **Bug fix**: `FleetRuntime`'s default `MissionControlService` ignored an
  explicitly supplied `state_root` and fell back to `get_hermes_home()`
  (`Path.home()`), which crashes under a locked-down systemd service
  account with no real home directory — found deploying Prime for real.

### Sigil desktop bridge (`apps/sigil/src/sigil/desktop_bridge/`)

- **`prime_fleet.py`** (new): Sigil has no import dependency on `hermes_cli`
  (a separate package, not staged into the packaged Electron build) — this
  module talks to Prime purely over its HTTP contract via stdlib `urllib`,
  using `HERMES_PRIME_BASE_URL` / `HERMES_PRIME_AUTH_TOKEN`. Two functions:
  `prime_fleet_status()` (node inventory + certification) and
  `prime_sigil_route()` (dispatch one advisory request). Both fail closed
  and honest — not-configured and unreachable are distinct, explicit states,
  never a fabricated healthy fleet or successful route.
- Wired into `runner.py`'s allow-listed command set as `prime_fleet_status`
  and `prime_sigil_route`.

### Sigil desktop UI (`apps/sigil-desktop/`)

- New **`PrimeFleetPanel`** component in Mission Control's Overview section:
  real node list (identity, role, connection state, last-seen, model
  inventory), real certification status, and a governed-routing test control
  that sends a harmless probe through `POST /v1/sigil/route` and displays
  Prime's real accepted/rejected result — including the real rejection code,
  never hidden. Explicit loading, not-configured, and unreachable-Prime
  states. Persistent "Paper Only / Broker Submission Disabled / Execution
  Authority Disabled / Prime Governed" badges.
- Electron IPC: `sigil:get-prime-fleet-status`, `sigil:prime-sigil-route`
  added to `main.ts`/`preload.ts`, registered in the release-guardian
  feature-coverage registry (`release-certification/features.json`).

## Known limitation: mac-routed Sigil operations

`hermes_cli.prime.sigil_routing.DEFAULT_OPERATION_ROUTES` routes four of the
five supported advisory operations (`advisory_valuation`,
`advisory_risk_assessment`, `advisory_portfolio_construction`,
`advisory_research_summary`) to the `mac` fleet node — the same node Sigil
itself runs on. `hermes_cli.prime.sigil_contract.SigilContractRequest`
correctly rejects a request whose caller and service identity are the same
(`"a Sigil contract request cannot self-address"`). In a single-Mac-node
topology, this means those four operations cannot currently complete end to
end; only `advisory_financial_sentiment` (routed to Titan, a genuinely
different node) does. This was discovered during live deployment validation
and is reported here rather than worked around by weakening the
self-address safety check. Real resolution requires either a second
higher-capability compute node, or a considered redesign of which identity
represents "Sigil" in the contract — out of scope for 3.7.0.

## Directly verified (live hardware, 2026-08-05)

- Prime deployed and running as a systemd service on `hydra-prime`
  (Raspberry Pi 5), bound only to its Tailscale interface.
- Titan deployed and running as a systemd service on `hydra-titan`
  (Raspberry Pi 5), alongside its pre-existing, undisturbed Nursery
  services.
- Mac worker deployed and running as a launchd agent on this machine.
- All three nodes registered with Prime and heartbeating with real,
  reported Ollama model inventories.
- Real fleet certification reached `CERTIFIED` against live state (Stage 1
  regression included, not skipped).
- A real `advisory_financial_sentiment` request routed through Prime to
  Titan's Ollama (`qwen3:0.6b`) and back, with a real evidence reference.
- Stale/revoked-node fail-closed dispatch behavior verified against the
  live deployment.
- Hydra Live checked via Tailscale and found offline — left disconnected,
  not fabricated as healthy.

## Not yet done for 3.7.0

- Packaging, code signing, notarization (`npm run release:mac`, `npm run
  release:certify`) — not run. No `v3.7.0-release.json` exists; it is
  machine-generated from a real signed build, not hand-authored.
- Full accessibility audit (keyboard-only pass, contrast check) beyond the
  semantic-HTML/ARIA-live conventions already used.
- Playwright E2E (this app's frontend test suite is Vitest; no Playwright
  config exists in `apps/sigil-desktop`).
- Approval-record UX upgrade, desktop-use governance UI, and the remaining
  Mission Control panels (Jobs/Evidence/Blocked-Actions as dedicated
  panels) described in the original 3.7 UI spec — the existing
  `RuntimeVisibilityCard`, `ProposalDetails`, and `IntegrationRegistryPanel`
  already cover audit/evidence/approval-adjacent surfaces; a dedicated
  redesign of those was not attempted in this cycle to keep this release
  coherent and real rather than partially mocked.
