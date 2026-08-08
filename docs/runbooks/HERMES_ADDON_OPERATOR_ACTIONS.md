# Hermes Add-on Operator Actions

Exact, minimal steps for the operator (not an agent) to unblock each track in
the Hermes Add-on Prerequisite Program. None of these steps are performed by
this task. Nothing here installs, activates, or connects an add-on by itself
— each step only removes one blocking prerequisite.

## Paperclip

1. Review `docs/roadmap/HERMES_ADDON_UPSTREAM_REGISTRY.json` entry `paperclip`
   and confirm `github.com/paperclipai/paperclip` is the intended project.
2. Decide which fleet machine hosts it (Titan is the current recommendation).
3. Do not `git clone` or install it until `hermes-paperclip-integration`
   exists and Stage 2 worker-contract wiring is ready to receive it.

## Buzz

1. Review `docs/roadmap/HERMES_ADDON_UPSTREAM_REGISTRY.json` entries
   `buzz-relay-and-web` and `buzznode` and confirm both readings (single
   upstream for Web+Relay; "Buzznode" as an internal classification, not a
   separate project).
2. Decide self-hosted (Docker Compose on Titan/Prime) vs. Block-hosted relay
   at `buzz.xyz`.
3. Do not install until `hermes-buzz-integration` exists.

## Supabase

The account and one project already exist (org `firecattechllc`, project ref
`qsfyoikpqxtutrcdbkhm`, region `us-east-1`, currently paused/`INACTIVE`).

1. Install the Supabase CLI:
   ```
   brew install supabase/tap/supabase
   ```
2. Authenticate:
   ```
   supabase login
   ```
   This opens a browser-based device-authorization flow. No token is ever
   typed into or echoed by this repository.
3. Link the existing project (this does not change any data or resume it by
   itself; confirm you want it resumed before doing so, since the project is
   currently paused):
   ```
   supabase link --project-ref qsfyoikpqxtutrcdbkhm
   ```
4. Decide on a dev/prod topology (a second project, or Supabase branching
   within this one) before writing the first migration.
5. Only after linking, review tables/migrations/RLS/advisors — this was
   intentionally not done during this discovery pass to avoid triggering a
   resource-state change on a paused project.

## Hydra Live

1. Power on the Hydra Live VMware guest (physical/console action on the host
   running the VM — not remotely reachable while offline).
2. Confirm it reappears in `tailscale status` as the `hydra-live` node.
3. Only then run the existing governed read-only discovery tooling described
   in `docs/operations/hydra-live/OPERATOR_PLAYBOOK.md` — do not run the
   repair playbooks (heartbeat patch, duplicate-Tailscale disable) without a
   fresh discovery pass and explicit approval scoped to the resulting
   proposal checksum.

## Alexa+

1. Decide whether exposing a public HTTPS endpoint (required by both
   supported Alexa integration routes) is acceptable at all for this
   deployment. If not, stop here and mark Alexa+ deferred.
2. If proceeding with the classic route: create a free Amazon developer
   account at `developer.amazon.com` and follow the Smart Home Skills API
   onboarding (Lambda or HTTPS endpoint, OAuth2 account linking,
   certification before publication).
3. If proceeding with the MCP route: apply for Alexa+ for Builders access
   through Amazon's partner channel (`developer.amazon.com/alexaplus/`) —
   this is not currently self-serve, so expect a lead time for Amazon to
   respond.
4. Either way, plan a mandatory phone/Mission Control approval gate for any
   sensitive action a voice command could trigger, before any skill is
   published.

## Obsidian

1. Decide which vault (if any) Hermes should be allowed to read.
2. Set it explicitly:
   ```
   echo 'OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault' >> ~/.hermes/.env
   ```
3. Optionally install Obsidian.app itself (`https://obsidian.md`) if you want
   to edit the vault directly — Hermes's read-only integration does not
   require the app to be installed, only the vault's files to be on disk.
4. Do not rely on the skill's fallback path (`~/Documents/Obsidian Vault`) —
   set the path explicitly so access is a deliberate choice, not an accident
   of directory existence.

## FRED

1. Create a free account at `https://fredaccount.stlouisfed.org`.
2. Generate an API key from the account dashboard (keys cannot be viewed
   without logging in).
3. Set it in the environment Sigil's backend uses, never in this repository:
   ```
   export FRED_API_KEY=...        # or add to the backend's own .env, not committed
   ```
4. Do not approve any live call until `sigil-v4.1-strategy-engine` exists and
   a fake-backed test suite is in place to validate the client first.

## Self-Evolution

1. Read `docs/security/HERMES_SELF_EVOLUTION_SAFETY_REVIEW.md`.
2. No action is required to keep the current proposal-only posture — that is
   the default and the recommendation. Only act if you intend to *propose*
   narrowing it, in which case treat that as a new, separately reviewed
   stage per the review's recommendation.
