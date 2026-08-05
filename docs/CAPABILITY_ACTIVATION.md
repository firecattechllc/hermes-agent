# Hermes capability activation — FireCat deployment

This document explains how to read and act on
[`docs/capability-manifest.yaml`](capability-manifest.yaml), and summarizes
what was activated, what remains credential-gated, and what was
deliberately excluded for this Titan + Mac deployment.

## Reading the manifest

Load it with the schema-validated loader rather than reading the YAML by
hand for anything programmatic:

```python
from hermes_cli.capability_manifest import load_capability_manifest
manifest = load_capability_manifest()
print(manifest.counts_by_state())
```

Every entry records `state`, `reason`, and a `validation_command` you can
re-run to confirm the classification still holds. States:

| State | Meaning |
|---|---|
| `active` | Implemented, configured, and reachable today. |
| `available` | Implemented and safe, not yet wired/configured for this deployment. |
| `blocked_credentials` | Implemented, needs a secret not present in this environment. |
| `blocked_runtime` | Implemented, needs infrastructure not yet running (e.g. a provisioned Titan/Mac). |
| `not_selected` | Deliberately excluded — irrelevant, or unsafe without a dedicated approval policy this deployment doesn't yet define. |
| `failed_validation` | Was expected to work; the validation command did not pass when last checked. |

## What's active today (no further action needed)

Core tools: persistent memory, session search, skills (+ skill authoring),
subagent delegation (+ parallel batch mode), cron scheduler, local browser
automation, vision/image paste, local terminal backend, MCP client
(curated catalog + security blocklist), live model routing, `hermes
doctor`.

Governance: Mission Control telemetry, dangerous-command approval gates
(verified: cron and interactive work share the identical gate; cron
defaults to fail-closed), autonomous backlog state tracking, knowledge
graph.

Communication: local/free TTS (Edge TTS, NeuTTS, KittenTTS, Piper — no
credentials needed).

Fleet: `scripts/fleet_connectivity_check.py` (new — see below).

## What's credential-gated (needs a secret, then works with zero code changes)

See `deploy/secrets.env.example` for the full placeholder list. Highest
priority for this deployment:

1. **`TELEGRAM_BOT_TOKEN`** — unlocks the Telegram gateway, phone approval
   prompts, and job notifications in one step (all three are implemented
   already; only the credential is missing).
2. **`HERMES_LINK_TOKEN`** — unlocks `hermes link {status,queue,sync,chat}`
   end to end. Confirmed by directly running `hermes link status` in this
   environment: it fails with `HERMES_LINK_TOKEN is not configured`, not a
   missing-feature error.
3. **`GITHUB_TOKEN`** — required for GitHub to actually function as source
   of truth for skill publishing and any git-backed workflow.

Discord, Slack, WhatsApp, Signal, Email, calendar, Spotify, image
generation, and paid TTS providers are all fully implemented and
intentionally left disabled pending their own credentials — see
`deploy/secrets.env.example` for the exact variable names per platform.

## What's blocked on runtime infrastructure (not a code or credential gap)

- `hermes_link_titan_node` / `hermes_link_mac_node` — the protocol and
  config shape exist (`deploy/hermes-link/*.example`, `deploy/titan/`,
  `deploy/mac/`); what's missing is an actual provisioned, Tailscale-joined
  Titan Pi and Mac to point them at. See the two runbooks.

## What's implemented but not wired into any live path (`available`)

`hermes_cli/agent_roles/` (78 files — workflow orchestration, dispatch
admission, runtime supervision/recovery, fleet inventory, specialized agent
roles) and `hermes_cli/prime/` (identity/admission/evidence/certification)
are both fully built and unit-tested but have no CLI subcommand or runtime
import anywhere in this branch. Wiring either into a live path is a
substantial separate engineering initiative — not a configuration change —
and is out of scope for this activation pass. Note: a separate branch,
`fleet-unification-live-runtime` (already pushed, not merged), implements
exactly this kind of live wiring for `hermes_cli/prime/` specifically
(durable node registry, heartbeat, governed dispatch, HTTP control plane);
review/merge that work rather than re-deriving it here.

## What's deliberately excluded (`not_selected`)

- **`deliverable_mode`** — does not exist as a distinct capability anywhere
  in the codebase; not fabricating one.
- **`computer_use_mac_routing`** — automatic Titan-to-Mac dispatch for
  computer-use tasks would require new bridging logic between
  `tools/computer_use/` and `hermes_link`, carrying real desktop-mutation-
  across-a-network-boundary risk that needs its own dedicated approval
  policy. Flagged as a concrete follow-up, not silently dropped.

## Governance for scheduled/autonomous work (Phase 5)

Verified directly (see commit history for the full trace): cron-triggered
tool calls pass through the exact same `tools/approval.py` guard as
interactive calls (`tools/terminal_tool.py`'s `_check_all_guards` call site
has no cron-specific bypass branch). Cron sessions default to **fail-closed**
(`approvals.cron_mode: deny`) for anything flagged dangerous, since no human
is present to approve — an operator must explicitly opt into
`approvals.cron_mode: approve` per cron profile to change that.
`HERMES_YOLO_MODE` is frozen once at process import specifically so a
mid-process job or skill cannot escalate it. Mission Control telemetry
covers cron turns transparently via the same `AIAgent.run_conversation`
path interactive turns use — no separate wiring needed.

`hermes_cli/autonomous_backlog/` is pure state tracking (candidate →
triaged → approved → scheduled → claimed → planning → executing →
completed); it does not itself execute anything, so there is no execution
path to audit yet. The intended executor
(`hermes_cli/agent_roles/dispatcher.py`) is part of the orphaned
`agent_roles` package described above.

## Next steps

1. Fill in `TELEGRAM_BOT_TOKEN`, `HERMES_LINK_TOKEN`, `GITHUB_TOKEN` in your
   real (never-committed) environment file.
2. Follow [`docs/runbooks/TITAN_DEPLOYMENT_RUNBOOK.md`](runbooks/TITAN_DEPLOYMENT_RUNBOOK.md)
   to provision Titan.
3. Follow [`docs/runbooks/MAC_WORKER_RUNBOOK.md`](runbooks/MAC_WORKER_RUNBOOK.md)
   to configure the Mac.
4. Run `scripts/smoke_test_fleet.py` (non-destructive) to confirm the whole
   picture end to end.
