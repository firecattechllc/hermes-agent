# Ecosystem Services — Missing-Source Report

Produced during the `ecosystem-services-installation` branch's discovery
pass. This report exists because the task's own discovery requirements say:
*"Do not guess repository URLs, package names, APIs, protocols, or service
identities"* and, for anything that can't be verified, *"create a precise
missing-source report, and clearly identify the exact repository URL or
package identity needed."* This is that report.

## Method

1. Repository-wide `grep -ri` for every target service name across all
   tracked files (docs, code, config, tests, `.gitmodules`, dependency
   manifests, Docker/compose files, systemd/launchd units).
2. `git remote -v`, `git branch -a`, `git tag` inspection.
3. `find . -name ".git" -not -path "./.git"` — confirms no vendored/cloned
   external repositories exist anywhere in the tree.
4. `gh repo list firecattechllc --limit 200` — every repository in the
   GitHub org that owns this repo, checked by name.

## Services with a verified local implementation (not covered by this report)

Paperclip, Buzz Relay, Buzznode, the Hermes WebUI operator-dashboard
adapter, Hermes Wiki, Agent Reach, Self-Evolution, and the Ecosystem
Discovery Catalog all have complete, tested, disabled-by-default local data
models under `apps/sigil/src/sigil/*.py` — see
`hermes_cli/prime/service_registry.py`'s `KNOWN_ECOSYSTEM_SERVICES` catalog
for the exact module path of each. These are registered, real, and visible
in Mission Control (`hermes mission-control services <project>`) as
`present_disabled`. They are **not** part of this missing-source report.

## Services with no local implementation and no verified repository

### Buzz Web (as a component distinct from Buzz Relay / Buzznode)

- **Status:** referenced only implicitly. The task's own service list
  separates "Buzz Web," "Buzz Relay," "Centralized Buzz Agents," and
  "Buzznode" into four items; this repository's actual implementation only
  distinguishes two (`buzz_relay_adapter.py` — messaging/event relay,
  `buzznode_adapter.py` — worker-host). No file, doc, or identifier
  anywhere in this repo refers to a "Buzz Web" frontend, dashboard, or
  distinct web service.
- **What's needed to proceed:** the exact repository URL (or package name)
  for a "Buzz Web" component distinct from the relay, plus a specification
  of what it does that Buzz Relay doesn't (a UI? A separate API surface?).
  Without that, registering a `service_key="buzz_web"` entry would be
  fabricating an identity this repository has no evidence for — refused
  per the task's own instruction not to guess service identities.

### Centralized Buzz Agents

- **Status:** no reference anywhere in the repository. `buzz_relay_adapter.py`
  models `BuzzActorRef`/`BuzzActorKind` (individual actors participating in
  relay events) but nothing modeling a "centralized" agent-hosting service
  distinct from Buzznode (the worker-host adapter) or the relay itself.
- **What's needed to proceed:** a repository URL, package name, or even a
  one-paragraph specification distinguishing this from Buzznode/Buzz Relay.

### Awesome Hermes Agent (as a literal external catalog/list)

- **Status:** exactly one sentence anywhere in the repository —
  `docs/beta/post-phase9/HERMES_ECOSYSTEM_ASSESSMENT.md`: *"Awesome Hermes
  Agent is discovery input, never an install allowlist."* No URL, no further
  detail, and nothing in the GitHub org matches this name. The closest local
  analog is `sigil.ecosystem_catalog` (`service_key="ecosystem_catalog"` in
  the registry), which models exactly the *shape* of externally-supplied
  discovery evidence an "awesome-list"-style catalog would produce, but
  contains zero actual entries — it is a schema/validator, not a populated
  catalog.
- **What's needed to proceed:** the actual GitHub URL for the "Awesome
  Hermes Agent" list/repository (in the style of other "awesome-X" curated
  lists), or confirmation that `sigil.ecosystem_catalog` alone satisfies
  this requirement (i.e., there never was meant to be a separate external
  list, only this local schema for *whatever* external catalog might exist).

### Hermes Node (as a project/repository distinct from the physical fleet)

- **Status:** every "Hermes Node" hit in the repository resolves to one of
  two unrelated things: (a) the vendored Node.js runtime under
  `~/.hermes/node/` (`HERMES_NODE` env var, `hermes_cli/gateway.py`,
  `hermes_cli/uninstall.py`, etc.) — completely unrelated to a worker
  runtime; or (b) generic "node = machine in the fleet" terminology used
  loosely across `docs/architecture/hydra-ecosystem/*` for the physical
  Titan/Prime/Hydra Live/MacBook fleet. No distinct "Hermes Node" software
  product or repository exists.
- **Resolution (per the task's own instruction H):** *"If Buzznode is the
  intended worker runtime, document that explicitly."* Buzznode
  (`sigil.buzznode_adapter`, `service_key="buzznode"`) is the closest and
  almost certainly intended local model for a generic worker-node runtime:
  it already models identity, resource limits, capability sets, leases,
  heartbeats, and workspace/browser-session references for "a persistent
  isolated worker host." It is registered under that name in
  `KNOWN_ECOSYSTEM_SERVICES` and is present-disabled like every other
  service in this report's companion catalog. **No separate "Hermes Node"
  project is fabricated or assumed.**

## What was checked and found not to apply

- `firecattechllc` GitHub org (11 repositories total: `hermes-agent`,
  `sigil`, `sigil-ai-studio`, `hermes-foreman`, `foreman-fixture`,
  `hydra-os`, `hydra-alana`, `hydra-infrastructure`, `hydra-scripts`,
  `hydra-docs`, `Alana.exe`) — none matches Buzz Web, Centralized Buzz
  Agents, Awesome Hermes Agent, or a distinct Hermes Node.
- `.gitmodules` — does not exist at the repo root; no submodules are used
  anywhere in this repository.
- Nested `.git` directories anywhere in the tree — none found; nothing has
  ever been vendored/cloned in.
- `git remote -v` — only `origin` (`firecattechllc/hermes-agent`) and
  `upstream` (`NousResearch/hermes-agent`); neither is one of these
  services.
- Root and `apps/*` dependency manifests (`pyproject.toml`, `package.json`,
  `requirements*.txt`) — zero matches for any of these four items as a
  declared dependency.

## Recommendation

Do not fabricate `service_key` entries for Buzz Web, Centralized Buzz
Agents, or a distinct Hermes Node in `hermes_cli/prime/service_registry.py`
— there is nothing local to register and no verified external source to
point at. If a real repository or package identity for any of these
becomes available, add it to `KNOWN_ECOSYSTEM_SERVICES` with a real
`module_path` (if vendored/added as a dependency) or register it through
`EcosystemServiceRegistry.register_external_service()` with a
`VerifiedExternalSource` (pinned commit, checked license, integrity hash) —
both mechanisms already exist and are tested; they were simply never
invoked for these four items because there was nothing verified to give
them.
