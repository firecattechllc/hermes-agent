# Hermes Add-on Prerequisite Program

## Status

**PREREQUISITE AND DISCOVERY ONLY.** Nothing in this document, the accompanying
matrices, or the accompanying safety review installs, activates, deploys, or
connects any add-on. No live Paperclip, Buzz, Supabase, Hydra Live, Alexa+,
Obsidian, or FRED integration exists after this branch merges.

This program builds directly on the already-certified Stage 0-12 governed
ecosystem architecture in [docs/beta/post-phase9/](../beta/post-phase9/README.md)
and [docs/sigil/ECOSYSTEM_STAGE12_CERTIFICATION.md](../sigil/ECOSYSTEM_STAGE12_CERTIFICATION.md)
(Stage 12D decision: `READY`, zero unresolved blockers, `docs/sigil/evidence/ECOSYSTEM_STAGE12D_GOLDEN_MASTER_READINESS.json`).
That program built the governance skeleton — disabled-by-default adapters,
registries, and worker contracts for Paperclip, Buzz Relay, Buzznode, Hermes
WebUI, Hermes Wiki, Agent Reach, Self-Evolution, and fleet routing. It
deliberately left the *real-world upstream identity and operator access* for
each integration unresolved. This program resolves that.

> **Documentation discrepancy found and not silently resolved:** `docs/beta/post-phase9/README.md`
> still carries a "PROVISIONAL — BLOCKED ON PHASE 9 LIVE-NODE CERTIFICATION" banner,
> while every other document in the same directory (`ARCHITECTURE.md`,
> `HERMES_ECOSYSTEM_ROADMAP.md`, `INTEGRATION_REGISTRY.md`, `SIGIL_BOUNDARY.md`,
> `ROLLBACK_AND_RECOVERY.md`, `THREAT_MODEL.md`) states "Phase 9 live-node
> certification is complete and merged." Committed evidence
> (`docs/architecture/hydra-ecosystem/evidence/PHASE9_LIVE_CERTIFICATION.json`,
> `"certified": true`, all gates `"ok": true`) supports the *second* reading —
> the README banner appears stale. This program treats Phase 9 as certified,
> per the committed evidence, but flags the stale README for a follow-up
> documentation-only fix. This is an observation, not a decision made on the
> operator's behalf.

---

## Track 1 — Paperclip

**Verified candidate:** [`github.com/paperclipai/paperclip`](https://github.com/paperclipai/paperclip)
— MIT license, Paperclip Labs, Inc. Latest release `v2026.722.0` (2026-07-22).
Node.js + React + PostgreSQL, self-hostable with no external account required
(trusted local-loopback mode is the default). HTTP/REST API on port 3100, an
MCP Tool Gateway, and webhook-driven "routines." Agents have roles, reporting
lines, permissions, budgets, and heartbeat-based execution — an almost exact
semantic match for the org/employee/project/issue/heartbeat/worktree/cost
model already described in `docs/beta/post-phase9/PAPERCLIP_ADAPTER.md` and
`PAPERCLIP_CONTRACT.md`.

Three unrelated same-named projects were found and ruled out (a deprecated
Rails file-upload gem, a personal clipboard utility, a small local privacy
companion) — see `HERMES_ADDON_UPSTREAM_REGISTRY.json` for each with its
evidence source. No other plausible candidate exists.

**Hermes governance overlap:** Direct. Paperclip's own scheduling ("routines"),
budgets, approvals, and heartbeat worker model overlap Hermes's existing
scheduling, budget, and admission systems. This is precisely the overlap the
already-built `PAPERCLIP_ADAPTER.md` boundary constrains: Paperclip may
project organizational/assignment state; Hermes remains sole admission,
budget, and approval authority.

**Can run fully privately:** Yes — local-loopback mode requires no external
account, no outbound network dependency, and no cloud service.

**Security/supply-chain:** Checksum-verified installer, per-agent secret
scoping, audit logging, opt-in (disableable) telemetry. No independent
third-party security audit was found during this discovery pass.

**Blocking prerequisite:** Operator confirmation that `paperclipai/paperclip`
is the intended project (high confidence, not yet operator-confirmed), plus a
pinned Stage 1 registry entry and Stage 2 live worker-contract wiring — both
currently absent. The Stage 4 adapter code exists but is disabled and
unconnected.

**Next operator action:** Confirm the candidate; decide self-host placement
(Titan is the natural fit per `FLEET_PLACEMENT.md`); do not install until
Stage 2 is live.

---

## Track 2 — Buzz ecosystem

**Verified candidate for "Buzz Web" and "Buzz Relay":**
[`github.com/block/buzz`](https://github.com/block/buzz) — Apache-2.0, Block,
Inc. Launched 2026-07-21. A single Rust/Axum relay binary serves the
WebSocket relay, REST API, *and* the bundled web UI from one process — so
"Buzz Web" and "Buzz Relay" are the same upstream, not two. Built on the
Nostr protocol (NIP-01/42 signed events, NIP-34 git events, Blossom media).
Backing services: PostgreSQL, Redis, S3/MinIO. Self-hostable via Docker
Compose or Railway, or usable via Block's hosted relay at `buzz.xyz`.

**"Buzznode" — unresolved.** No distinct upstream project by this name was
found. The closest concept inside the verified Buzz repository is `buzz-acp`,
an agent-connection bridge component, not a standalone worker-host product.
Recommendation: treat "Buzznode" as Hermes's *own* worker-host classification
(already implemented as a disabled adapter in
`apps/sigil/src/sigil/buzznode_adapter.py`) that would consume the Buzz
upstream via `buzz-acp`, not as a second external project to source.
Operator should confirm this reading.

**"Centralized Buzz agents/workers"** likewise maps to Hermes's own worker/job
contract (`WORKER_JOB_CONTRACT.md`), not a separate upstream.

**Hermes governance overlap:** High, and the sharpest risk in this whole
program. `buzz-cli` gives agents direct shell-execution and workflow-run
capability. `docs/beta/post-phase9/BUZZ_RELAY_ADAPTER.md` and
`BUZZ_SECURITY.md` already constrain Buzz to a signed-event transport with
zero independent execution authority — that boundary must hold when this is
ever wired live.

**Can run fully privately:** Yes, self-hosted, with the caveat that any
device or agent that needs to reach the relay from outside the local network
needs its own connectivity plan (Tailscale is a natural fit given the
existing Hydra fleet).

**Blocking prerequisite:** Operator decision — self-host vs. Block-hosted
relay; resolution of the "Buzznode" naming question; Stage 1 registry entry;
Stage 5/6 live wiring (adapters exist, disabled, unconnected).

**Next operator action:** Decide hosting model; confirm the Buzznode reading
above.

---

## Track 3 — Supabase

A Supabase account already exists under org **firecattechllc**, containing
**one project**: `firecattechllc's Project` (ref `qsfyoikpqxtutrcdbkhm`,
region `us-east-1`, Postgres `17.6.1`), created 2026-07-10, currently
**status `INACTIVE`** (paused). This was confirmed through the connected
Supabase MCP integration's metadata-only calls (`list_projects`,
`get_project`, `list_organizations`) — no service-role key, access token, or
database password was read or displayed.

**Local Supabase CLI is not installed**, and no `SUPABASE_*` environment
variables are configured in `~/.hermes/.env`. Because the project is paused,
this pass deliberately avoided data-plane calls (`list_tables`,
`list_migrations`, `get_advisors`) that could implicitly trigger a restore —
that would be a resource change, which this task forbids. Migrations, RLS
coverage, and Auth configuration are therefore **unknown and unverified**,
not "empty" — they must be checked after the operator explicitly restores the
project (or after confirming a restore-on-read is acceptable).

**Dev/prod split:** Does not exist yet — only one project. A decision is
needed on whether `sigil-v3.8-supabase-platform` should introduce a second
project for dev/prod separation or use branching within the single project.

**Desktop-safe public configuration model:** Not yet defined. Any client
build must use the publishable/anon key only, never the service-role key;
`get_publishable_keys` / `get_project_url` are the correct tools to source
that configuration when the time comes.

**Account deletion and recovery:** Not yet documented for this project;
should be captured before any production data is written.

**Blocking prerequisite:** CLI install + `supabase login` + `supabase link`;
a decision on restoring the paused project; a dev/prod topology decision;
migrations; RLS policy; Auth configuration; backup/monitoring plan.

**Next operator action:** See `docs/runbooks/HERMES_ADDON_OPERATOR_ACTIONS.md`
for the exact CLI login/link sequence.

---

## Track 4 — Hydra Live

Read-only inspection only; nothing was started, stopped, installed, updated,
or modified on Hydra Live during this task.

**Current Tailscale status (checked 2026-08-05):** `hydra-live` is
**offline**, last seen approximately 52 minutes before this check. By
contrast, `hydra-prime` and `hydra-titan` are both active and directly
connected. `matthews-macbook-air` (this machine) is active.

**Expected hostname/role:** OS hostname `hydra-VMware20-1`, Tailscale node
name `hydra-live`, role: isolated application/local-AI runtime boundary (not
an engineering authority) — per `docs/architecture/hydra-ecosystem/CANONICAL_ARCHITECTURE.md`.

**Existing deployment artifacts / prior discovery:** Extensive — see
`docs/architecture/hydra-ecosystem/evidence/HYDRA_LIVE_DISCOVERY.md`. At last
discovery: Ubuntu 26.04 LTS ARM64 VMware guest, 2 vCPU, 7.7 GiB RAM, ~72 GB
LUKS-encrypted disk. System state was **degraded**: `hydra-fleet-heartbeat.service`
failed, and a duplicate Snap-managed Tailscale service was enabled and
failed alongside the working native `tailscaled`. Ports `3000` (Open WebUI),
`3099` (Hydra Cleaner), and `3130` (unidentified Python service) were
listening on all interfaces with unreviewed exposure intent. Hermes itself
was **not** installed on Hydra Live's PATH.

**Suitability for governed maintenance:** Not currently suitable — the node
is offline, and even when online, `docs/operations/hydra-live/OPERATOR_PLAYBOOK.md`
explicitly restricts the current implementation to fakes only ("Do not
connect to Hydra Live, remove packages, change firewall policy, restart
SSH/Tailscale, reboot, or deploy").

**Blocking prerequisite:** The VM must be brought back online before any
further evidence collection is possible.

**Next operator action:** Power on the Hydra Live VMware guest so Tailscale
reconnects. Do not run any mutating command against it — only the existing
governed read-only discovery tooling — until it is back online and the
operator has reviewed current health.

---

## Track 5 — Alexa+

Two officially supported routes were identified; neither was activated.

1. **Classic Smart Home Skills API / Custom Skills Kit** — fully self-serve,
   free Amazon developer account, publicly documented, requires an AWS
   Lambda or HTTPS endpoint, OAuth2 account linking, and Amazon certification
   before publication. Scope is limited to device-style capabilities (on/off,
   set temperature, lock/unlock) — not general task delegation.

2. **Alexa+ for Builders (MCP add-on)**, announced July 2026 — a much better
   conceptual fit, since it lets a brand "bring their own MCP server" and
   Alexa+ generates a simulator-ready integration package. Account linking
   requires **OAuth 2.1 with mandatory PKCE S256** (implicit grant is not
   supported). As of retrieval, Amazon states this program is **"available to
   select partners working directly with Amazon's team"** — it is not a
   general self-serve program yet.

**Common limitation for both routes:** Amazon requires a **publicly reachable
HTTPS endpoint**. Neither route supports a purely private/local integration.
This directly conflicts with a "Hermes can integrate without exposing
internal services publicly" goal — some public-facing proxy or managed
endpoint would be required regardless of which route is chosen.

**Privacy/voice-data implications:** Both routes send voice-derived intent
data through Amazon's cloud before it reaches any Hermes-controlled endpoint;
this is inherent to any Alexa integration, not specific to either route.

**Phone approval requirement:** Independent of Alexa capability, Hermes's own
governance (`AGENT_ROLES.md`, existing approval-authority model) should still
require a phone/Mission Control approval step for any sensitive action an
Alexa-originated voice command might trigger — voice authentication is not
equivalent to Hermes's existing approval bar.

**Blocking prerequisite:** No Amazon developer account or partner
relationship currently exists for this program; no public endpoint is
provisioned.

**Next operator action:** Decide whether the public-endpoint requirement is
acceptable at all before pursuing either route; if not, mark Alexa+ deferred
indefinitely without blocking any other track.

---

## Track 6 — Obsidian

**Obsidian.app is not installed** on this machine (checked via `/Applications`
and Spotlight metadata search — no scan of any vault contents was performed).
**No vault path is configured specifically for Hermes**: `OBSIDIAN_VAULT_PATH`
is unset in the current shell and absent from `~/.hermes/.env`; the
documented fallback path `~/Documents/Obsidian Vault` does not exist on this
machine either.

Per the task's explicit boundary, **no vault was searched, scanned, opened,
or ingested**, because none has been explicitly configured for Hermes.

The existing generic Hermes *product* already ships a bundled, filesystem-first
Obsidian skill (`~/.hermes/skills/note-taking/obsidian/SKILL.md`) that reads
notes via `read_file`/`search_files`, resolves the vault path once (never
passing `$OBSIDIAN_VAULT_PATH` unexpanded to a tool), and understands
wikilinks (`[[Note Name]]`). That skill is generic to any Hermes user, not
specific to this personal deployment, and is a reasonable foundation for the
read-only integration this track calls for — but it does not itself define
symlink-containment, incremental-sync/hashing, exclusion patterns, or Mission
Control telemetry requirements, which remain to be designed in
`feature/obsidian-knowledge-integration`.

**Blocking prerequisite:** An explicit `OBSIDIAN_VAULT_PATH` decision by the
operator. Nothing else can proceed (by design) until that path is set.

**Next operator action:** Decide which vault (if any) Hermes should read, and
set `OBSIDIAN_VAULT_PATH` explicitly — do not rely on the generic fallback
path, since that grants access by directory-existence rather than deliberate
choice.

---

## Track 7 — FRED

**Endpoint:** `https://fred.stlouisfed.org/api` (documented at
`fred.stlouisfed.org/docs/api/fred/`). **Authentication:** a free
32-character `api_key`, obtained after creating an account at
`fredaccount.stlouisfed.org` — keys cannot be viewed without logging in.
**Rate limit:** 120 requests/minute per key (the single published limit; the
Bank reserves the right to adjust it). **Terms of use:** attribution
required; the data may not be resold as a competing raw-data feed.

**`FRED_API_KEY` is not currently set** in this environment. No live call was
made, consistent with the task's instruction to call the API only with an
explicit, approved key.

**Integration boundary with Sigil 4.1:** No `sigil-v4.1-strategy-engine` code
exists yet in this repository. FRED should plug into Sigil's existing
governed-market-data pattern (see `docs/sigil/governed-kronos-forecasting.md`
for the shape of an existing, similar governed external-data boundary:
immutable series identity, source digest, freshness timestamps, and no
independent capital/trading authority) rather than introducing a parallel
data-fetching mechanism.

**Testing:** A fake/mock FRED client (fixed series/observations, no network)
should back all tests; live calls should be reserved for a separately
approved integration-test path.

**Blocking prerequisite:** A FRED account and API key (operator action, no
code blocker); a Sigil 4.1 module to receive the data (does not exist yet).

**Next operator action:** Create the FRED account, generate a key, and set
`FRED_API_KEY` in the Sigil backend's environment when ready — never commit
it.

---

## Track 8 — Self-Evolution safety review

See [`docs/security/HERMES_SELF_EVOLUTION_SAFETY_REVIEW.md`](../security/HERMES_SELF_EVOLUTION_SAFETY_REVIEW.md)
for the full assessment. Summary: Self-Evolution is already implemented as a
non-executing, proposal-only framework (`apps/sigil/src/sigil/self_evolution.py`),
certified through Stage 9 and swept into the Stage 12D Golden Master
readiness decision (`READY`, zero unresolved blockers). It has no code
modification, test modification, dependency installation, shell execution,
filesystem mutation, Git, deployment, or credential-access authority today.
**Recommendation: remain proposal-only.** No change to that posture is
justified by this discovery pass.

---

## Track 9 — Full access and credential matrix

See [`HERMES_ADDON_ACCESS_MATRIX.json`](HERMES_ADDON_ACCESS_MATRIX.json).

---

## Track 10 — Release architecture

The existing `docs/beta/post-phase9/HERMES_ECOSYSTEM_ROADMAP.md` already
defines and has *completed* Stages 0-12D (`beta-post9-00` through the Stage
12 certification branches) — that program built the disabled governance
skeleton this program now targets for real-world wiring. The branches this
task lists are the **next generation**, each scoped to resolve one upstream
and wire it behind the existing adapter, or to build a track that Stage 0-12
never covered (Supabase, Alexa, Obsidian, FRED, Self-Evolution execution
authority).

| Branch | Builds on | Smallest complete outcome | Prerequisite |
|---|---|---|---|
| `hermes-addon-foundation` | Stage 1/2 registry + worker contract (already merged) | Shared correlation-ID/registry conventions for every add-on branch below; this branch itself (docs only) | This PR |
| `hermes-mission-control-visibility` | `hermes-addon-foundation` | Read-only Mission Control projection of add-on registry/health state | `hermes-addon-foundation` |
| `hermes-continuous-discovery` | Stage 8 ecosystem discovery catalog (already merged) | Wire real (operator-supplied) evidence into the existing discovery catalog for Paperclip/Buzz | `hermes-addon-foundation` |
| `hermes-paperclip-integration` | Stage 4 Paperclip adapter (already merged, disabled) | Pinned registry entry + live Stage 2 worker-contract wiring for `paperclipai/paperclip` | `hermes-addon-foundation`, operator confirmation of upstream identity |
| `hermes-buzz-integration` | Stage 5/6 Buzz Relay + Buzznode adapters (already merged, disabled) | Pinned registry entry + live wiring for `block/buzz`; resolves the "Buzznode" naming question in code | `hermes-addon-foundation`, `hermes-paperclip-integration` (shared correlation-ID patterns) |
| `hermes-agent-reach` | Stage 8A Agent Reach adapter (already merged, disabled) | Public-read pilot activation (webpage/YouTube/RSS/public GitHub/semantic search only), per existing `AGENT_REACH_ADAPTER.md` boundary | `hermes-addon-foundation` |
| `hermes-wiki-catalog` | Stage 7 Hermes Wiki adapter (already merged, disabled) | Real citation-backed knowledge ingestion wiring | `hermes-addon-foundation` |
| `hermes-agent-composition` | Stage 3/10 WebUI + routing (already merged, disabled) | Compose the WebUI, routing, and worker adapters into one operable (still admission-gated) worker path | `hermes-paperclip-integration`, `hermes-buzz-integration` |
| `feature/obsidian-knowledge-integration` | New track — no Stage 0-12 equivalent | Read-only vault ingestion once `OBSIDIAN_VAULT_PATH` is operator-configured; symlink containment; incremental hashing; exclusions | `hermes-addon-foundation`; operator sets `OBSIDIAN_VAULT_PATH` |
| `feature/alexa-integration` | New track — no Stage 0-12 equivalent | Whichever route the operator selects (Smart Home Skills or Alexa+ for Builders), with a mandatory phone-approval gate for sensitive actions | Operator decision on public-endpoint tradeoff; Amazon account/partner access |
| `sigil-v3.8-supabase-platform` | Independent of Stage 0-12 | Supabase CLI link, dev/prod topology, initial migrations, RLS coverage | Operator CLI login/link; dev/prod decision |
| `sigil-v4.1-strategy-engine` | Independent of Stage 0-12; consumes `governed-kronos-forecasting.md` pattern | FRED-backed governed market-context module, advisory only (no capital authority) | `FRED_API_KEY` provisioned; `sigil-v3.8-supabase-platform` if strategy state is persisted there |
| `hermes-self-evolution-safety` | Stage 9 Self-Evolution framework (already merged) | This safety review merged as documentation; no code change | This PR |

**Merge order:** `hermes-addon-foundation` and `hermes-self-evolution-safety`
first (both documentation-only, no cross-dependency). Then
`hermes-paperclip-integration` before `hermes-buzz-integration` (Buzz's
correlation-ID and worker-lifecycle projection patterns should mirror
Paperclip's once one is live). `hermes-mission-control-visibility` and
`hermes-continuous-discovery` can proceed in parallel with either. `sigil-v3.8-supabase-platform`,
`sigil-v4.1-strategy-engine`, `feature/obsidian-knowledge-integration`, and
`feature/alexa-integration` are independent of the Paperclip/Buzz track and
of each other, and of `hermes-agent-composition`, which should be last since
it composes the others.

No branch beyond this one is created by this task — this is documentation
only, per the task's explicit instruction.

---

## Cross-track summary — what blocks what

| Add-on | Exact blocking prerequisite | Next operator action |
|---|---|---|
| Paperclip | Operator confirmation of upstream identity; Stage 2 live wiring | Confirm `paperclipai/paperclip`; decide host machine |
| Buzz | Hosting decision; "Buzznode" naming resolution; Stage 5/6 live wiring | Decide self-host vs. `buzz.xyz`; confirm Buzznode reading |
| Supabase | CLI not installed/linked; paused project; no dev/prod split | Run CLI login/link sequence (see operator runbook) |
| Hydra Live | Node is offline | Power on the Hydra Live VM |
| Alexa+ | No Amazon account/partner access; public-endpoint requirement unresolved | Decide if public-endpoint tradeoff is acceptable |
| Obsidian | No vault path configured | Set `OBSIDIAN_VAULT_PATH` explicitly |
| FRED | No API key provisioned; no Sigil 4.1 module | Create FRED account, generate key, hold for approved use |
| Self-Evolution | N/A — review complete, no execution authority proposed | Read and confirm the safety review's proposal-only recommendation |
