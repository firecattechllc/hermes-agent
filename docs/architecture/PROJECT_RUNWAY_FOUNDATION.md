# Project Runway — Safe Foundation

Project Runway is Hermes's AI compute procurement, qualification,
routing-intelligence, cost-control, and provider-governance layer. This
document describes the **foundation build**: a fully isolated, disabled-by-
default `runway/` package. It does not execute a single real model call and
does not talk to a real provider — see [What it is not](#what-it-is-not).

## What it is

Runway is a *decide* layer. Given a task (`RouteRequest`: task type, data
sensitivity, required capabilities, expected token usage) and a registry of
qualified providers/models, it picks the route that minimizes
**expected cost per successful task** — not the cheapest route, the cheapest
route *that is likely to actually work* (`runway/scoring.py`). Every decision
is deterministic, explainable (each rejected candidate carries its rejection
reasons; the selected candidate carries its full factor breakdown), and
governed by centralized, configurable weights — no scattered magic numbers.

## What it is not

- **Not connected to anything live.** Zero imports from `hermes_cli.prime`
  (Prime/Titan/OmniRoute) and zero imports from `providers/` /
  `plugins/model-providers/` (Hermes's real execution substrate). See
  [Why no OmniRoute import](#why-no-omniroute-import).
- **Not enabled.** `runway_enabled`, `discovery_enabled`, and
  `synthetic_qualification_enabled` all default to `False`
  (`runway/flags.py`). `external_execution_enabled` cannot be set to `True`
  at all in this build — the validator raises if you try.
- **Not holding secrets.** `ProviderRecord.credential_ref` is an opaque
  pointer string, validated to reject anything shaped like a key/token/
  password. No provider adapter in this phase ever needs a real credential,
  because no provider adapter in this phase ever makes a real request.
- **Not wired into any request path.** Nothing outside `runway/` and
  `tests/runway/` imports this package. Adopting it into the live agent path
  is future, separately-reviewed work.

## Why no OmniRoute import

The spec that produced this build assumed a stable, separate "OmniRoute"
execution substrate to sit above. Repository inspection found two different
things with plausible claim to that name:

1. `hermes_cli/prime/omniroute_*.py` — a Titan-specific local relay. It's
   nested inside Prime (certified/protected) and, as of this build, only
   exists on an **unmerged branch** (`feat/titan-omniroute-freellmapi`), not
   in production.
2. `providers/` + `plugins/model-providers/<name>/` — the real, production,
   merged execution substrate every live Hermes model call actually goes
   through today (wired into `hermes_cli/auth.py`, `agent/transports/chat_completions.py`,
   `run_agent.py`, etc).

Rather than bind to either — one is unmerged and Titan-specific, the other
is live production infrastructure this phase has no mandate to touch —
Runway defines its own narrow contract, `runway/omniroute_port.py`
(`ExecutionPort` protocol), and the only implementation in this phase is
`FakeExecutionAdapter`. A future, separately-certified phase can implement
that protocol against whichever real substrate makes sense at the time,
without any change to `runway/scoring.py` or `runway/router.py`.

## Module map

| Module | Responsibility |
|---|---|
| `flags.py` | `RunwayFeatureFlags` — everything off by default; `external_execution_enabled` hard-locked |
| `classification.py` | `DataClassification` (public…restricted) and the free-form task-type convention |
| `trust.py` | `TrustTier` (A–D) and the trust→data-classification eligibility ceiling |
| `lifecycle.py` | Provider lifecycle state machine + explicit legal-transition graph |
| `capabilities.py` | Normalized capability tags, context/output limits, protocol support |
| `pricing.py` | Integer-micros `CostModel`, deterministic cost + cache-savings math |
| `registry.py` | `ProviderRecord` / `ModelRecord` (model×provider pair) / `Registry` / endpoints |
| `health.py` | Separate provider/model/endpoint health, fake probe only |
| `canary.py` | Model-identity/behavior canary suite + `DeterministicFakeProvider` |
| `qualification.py` | Canary→lifecycle orchestration; `authorize()` is the only path to `APPROVED` |
| `discovery.py` | Fixture-based discovery adapters; always normalizes to `discovered` + Tier D |
| `budget.py` | `BudgetPolicy` + SQLite `BudgetLedger`; rejected spend never mutates the ledger |
| `outcomes.py` | Per task-type×model-key historical outcomes; Bayesian-shrunk success probability |
| `scoring.py` | `RouteScorer` — eligibility filtering + `expected_cost_to_success` ranking |
| `router.py` | `RunwayRouter` — composes the above behind `runway_enabled`; emits telemetry |
| `telemetry.py` | Self-contained structured event log (see below) |
| `omniroute_port.py` | Narrow execution contract + `FakeExecutionAdapter` |
| `storage.py` | Shared SQLite bootstrap (`schema_version` table, idempotent `CREATE TABLE IF NOT EXISTS`) |
| `benchmark.py` | Synthetic all-premium vs. Runway-optimized cost/success comparison |

## Provider lifecycle

```
discovered → pending_qualification → synthetic_testing → qualified → approved
                                            ↑                  ↓         ↓
                                       (retry loop)        (revoke)  degraded/quarantined
```

Every edge is enumerated in `lifecycle.ALLOWED_TRANSITIONS`;
`lifecycle.transition()` raises `InvalidLifecycleTransition` on anything not
in that table — there's no way to jump `discovered → approved`.
`qualification.QualificationService.run_qualification()` walks a provider
through the testing states based on canary results, but only
`QualificationService.authorize()` — a distinct, explicit call requiring a
non-empty `authorized_by` identity — can reach `approved`. Passing a canary
suite never authorizes anything by itself. A canary regression on an already-
`approved`/`degraded` provider quarantines it immediately
(`QuarantineReason.CANARY_REGRESSION` or `IDENTITY_MISMATCH`).

## Trust model

Trust tier (`trust.TrustTier`, Tier A `DIRECT_OFFICIAL` down to Tier D
`EXPERIMENTAL_UNKNOWN`) is a field on `ProviderRecord`, assigned by whoever
constructs the registry — nothing in Runway hardcodes a real provider name
into policy. `trust.TIER_DATA_CLASSIFICATION_CEILING` is the one place that
maps tier → maximum permitted `DataClassification`; `RouteScorer` enforces it
on every candidate regardless of lifecycle state. `discovery.candidate_to_provider()`
always assigns Tier D and `discovered`, no matter what a feed claims about
the provider.

## Routing score

`RouteScorer.route()`:

1. Filters out candidates failing any hard gate (not approved, wrong data
   classification for trust tier, below `minimum_trust_tier`, unqualified
   canary, missing capability, insufficient context, over budget, unhealthy)
   — each rejection carries its specific reason(s).
2. For survivors, computes `estimated_cost_micros` (`pricing.estimate_cost_micros`,
   integer-exact, cache-aware) and a Bayesian-shrunk `predicted_success_probability`
   (`outcomes.HistoricalPerformanceStore.success_probability` — starts at the
   model's quality-score prior, converges to observed history as attempts
   accumulate).
3. Ranks by `expected_cost_to_success_micros = cost / probability` ascending
   — this is the literal optimization target (Principle C), not one input
   among many. A centralized, configurable `RoutingWeights` score is computed
   alongside as a secondary tiebreak and for human-readable explanation.

This is also how "hard tasks skip cheap models" happens without a hardcoded
cheap-first ladder: a cheap model with a poor observed track record on a
task type gets a low probability, which drives its expected-cost-to-success
up, which pushes it below more capable alternatives — purely from evidence.

## Budget

`budget.BudgetLedger` is a local SQLite ledger (`schema_version`-gated, same
idiom as `hermes_state.py`). `spend()` validates every applicable cap
(per-task, hourly, daily, monthly, per-provider, per-model, emergency
reserve vs. premium-escalation ceiling) and writes exactly one row **only on
success** — a rejected spend attempt never touches the table. Every cap
defaults to `0` (no spend permitted) until explicitly configured.

## Telemetry

`telemetry.RunwayTelemetryLog` is a local, append-only JSONL journal shaped
like `hermes_cli.mission_control`'s `TelemetryEvent` (same philosophy:
immutable, typed `event_type`, `correlation_id`), but it does not write to
Mission Control — that store's `event_type` vocabulary is a closed enum in a
shared file (`hermes_cli/mission_control/models.py`) this phase deliberately
leaves untouched. Every event is validated to reject payload keys like
`prompt`, `messages`, `api_key`, `credential`, and any value containing a
secret-shaped substring, before it's ever written.

## Adding a provider candidate (conceptually — no real provider in this phase)

1. A `DiscoveryAdapter` (fixture-based in this phase) produces a
   `DiscoveryCandidate`.
2. `discovery.candidate_to_provider()` normalizes it to `discovered` / Tier D.
   **This step never makes a provider routable.**
3. `QualificationService.run_qualification()` runs the canary suite against a
   `ModelClient` (only `DeterministicFakeProvider` exists in this phase) and
   advances lifecycle state based on the result, up to `qualified` at most.
4. A separate, explicit `QualificationService.authorize()` call — with a
   named `authorized_by` — is required to reach `approved`. Only `approved`
   (or `degraded`) providers are ever considered by `RouteScorer`.
5. Even once `approved`, a provider's trust tier still caps which
   `DataClassification` it may ever receive.

## Feature flags

| Flag | Default | Meaning |
|---|---|---|
| `runway_enabled` | `False` | `RunwayRouter.route()` raises `RunwayDisabled` otherwise |
| `discovery_enabled` | `False` | `discovery.run_discovery()` raises otherwise |
| `synthetic_qualification_enabled` | `False` | `QualificationService.run_qualification()` raises otherwise |
| `external_execution_enabled` | `False`, **cannot be set `True`** | No real adapter exists to enable; flipping this is a future certification step |

`RunwayFeatureFlags.from_env()` reads the first three from
`RUNWAY_ENABLED` / `RUNWAY_DISCOVERY_ENABLED` / `RUNWAY_SYNTHETIC_QUALIFICATION_ENABLED`
and **always** forces `external_execution_enabled=False`, ignoring the
environment entirely.

## Testing

`tests/runway/` (50 tests, `pytest tests/runway -q`) covers:

- **Routing**: cheapest-not-always-selected, failure rate raising effective
  cost, trust-tier-gated selection, hard-task cheap-model skip, context/
  capability/health/budget rejection, cache-preferred routing, same-model-
  two-providers independence.
- **Trust**: discovered-not-routable, Tier D data-classification ceiling,
  quarantine blocking, `authorize()` transition validation, fail-closed
  discovery defaults.
- **Cost**: exact integer token math, cache math, fixed fee, free quota,
  platform fee, currency field integrity, zero float drift across 10k reps.
- **Qualification**: fake pass/fail, canary-regression quarantine, identity-
  mismatch recording, qualification≠authorization, flag gating.
- **Budget**: task/daily/monthly/provider caps, emergency-reserve vs.
  premium-escalation ceiling, untouched ledger on rejection.
- **Telemetry**: route explanation events, sensitive-payload rejection,
  aggregate success/cost metrics, escalation recording.
- **Flags / benchmark**: disabled-by-default, hard-locked execution flag,
  deterministic synthetic benchmark showing Runway-vs-all-premium savings.

Run: `python -m pytest tests/runway -q` (or via the repo's standard test
runner). `python -m runway.benchmark` prints a synthetic cost comparison.

## Multi-gateway exchange layer (`runway/providers/gateway/`)

Generalizes the single-provider commissioning work (formerly a bespoke
`runway/providers/laozhang.py` adapter, now retired) into configuration-driven
infrastructure. Identity chain: **provider** (`GatewayConfig` — business
entity, trust tier, provenance) → **channel/rail** (`ChannelConfig` — a
concrete protocol-bound HTTP surface; a provider may expose several,
scored/gated independently, never collapsed into one trust score) →
**model** (`ExecutionRequest.model_key`, unchanged) → **protocol**
(`GatewayProtocol`) → **provenance** (`RailProvenance`) → **restriction**
(`RailRestriction` + `allowed_client_classes`).

- **Protocol adapters** (`protocols/`): OpenAI Chat Completions, OpenAI
  Responses, Anthropic Messages — one handler per wire format, not per
  company. All three verified against real docs (LaoZhang/OpenAI-compatible
  fetched from docs.laozhang.ai; Anthropic Messages from the bundled
  claude-api skill reference), not guessed.
- **`GatewayExecutionAdapter`** (`execution.py`): the single, protocol-generic
  `ExecutionPort` implementation. Same safety properties as the retired
  bespoke adapter — one HTTP call per `execute()`, no retries, refuses to run
  unless `external_execution_enabled` (still hard-locked False).
- **Eligibility before scoring** (`eligibility.py`, `routing.py`):
  `GatewayRouter` filters candidates by channel eligibility (provider/channel
  enabled, explicitly authorized, global gate, client-class restriction,
  capability-vs-restriction, trust-vs-data-classification, health, balance,
  budget) *before* handing survivors to the existing `RouteScorer`, which is
  reused completely unmodified — a restricted or unauthorized rail can never
  economically outrank an eligible one, because it never reaches scoring.
- **Runway-wide budget vs. upstream balance**: `runway/budget.py` gained an
  optional per-channel daily cap (new `channel_budget_spend` table, additive
  only). `BalanceObservation` (`models.py`) is upstream credit/quota
  *evidence* — read-only, `KNOWN`/`UNKNOWN`/`UNAVAILABLE`/`STALE` — and is
  never fed into `BudgetLedger.spend()`. Provider credit is never Runway-owned
  cash.
- **Pricing**: `PriceObservation` (source, timestamp, confidence,
  `promotional` flag) converts explicitly to `CostModel` via `to_cost_model()`
  — never automatically, so a promotional rate can't silently become the
  learned baseline. `CostModel` itself gained a `promotional: bool` field.
- **Model identity**: `evaluate_identity()` compares requested/advertised/
  reported model and returns `UNVERIFIED`/`CONSISTENT`/`MISMATCH` — never
  inferring authenticity from `reported_model` alone.
- **Config**: strict YAML loader (`config_loader.py`) — "strict" comes free
  from every model already using `extra="forbid"`. No credentials accepted in
  YAML (`credential_ref` is validated the same as everywhere else).

### What was deliberately excluded, and why

Two full sections of the commissioning request were **not implemented**:

1. **A "Sub2API" execution backend.** Sub2API's own public description is a
   subscription-to-API relay that pools personal AI subscriptions (ChatGPT
   Plus, Claude Pro/Claude Code, Gemini Advanced) across multiple users and
   redistributes access as API keys. That is subscription/account-sharing
   arbitrage, not licensed API resale, and it very likely breaches the
   consumer terms of every vendor involved — Anthropic has already blocked
   third-party harnesses from using Claude Max subscription limits over
   exactly this pattern. Nothing in this codebase names or wires up Sub2API.
2. **A named catalog of "candidate providers"** (OpenModel, CCTK, Bluesminds,
   APIKEY.FUN, AIGoCode, Pateway, PPToken, Sui-Xiang, FastAIToken, Aimzoon,
   Hao.ai, Fenno, Lanox, Nagora, ETok). Spot-checking several (APIKEY.FUN,
   PPToken, Pateway) turned up the same subscription-relay pattern
   ("Claude Code", "Codex", pooled-account access). None are represented
   anywhere in this codebase, not even as `incomplete=True` discovery-only
   records — populating the registry with them, even inertly, would still be
   building the readiness scaffold for wiring one in later.

`RailRestriction` deliberately does not include vocabulary shaped around that
ecosystem (`subscription_derived`, `reverse_engineered`, `codex_only`,
`claude_code_only`) — only general-purpose restriction tags that apply to any
legitimate gateway. LaoZhang (`known_channels.py`) remains the one example
channel, already vetted at Tier C (`VETTED_AGGREGATOR`) in the prior
commissioning phase.

If a future session is asked to wire in Sub2API or any of the above names,
treat this section as still in force — the underlying facts haven't changed
just because a request re-describes them differently.

## Local provider credential store

`runway/providers/credentials.py` gained a second `CredentialResolver`
alongside the existing `EnvCredentialResolver`: `FileCredentialResolver`,
resolving `credential_ref` values of the form `"file:VAR_NAME"` against a
local, permission-locked `KEY=value` file at
`~/.config/hermes/runway/providers.env` (never committed — see `.gitignore`;
the real file lives entirely outside the repo).

- `ensure_providers_env()` creates the directory (`0700`) and file (`0600`)
  if missing, and re-asserts those permissions either way — the umask can
  otherwise leave a freshly-created path more permissive than intended.
- `parse_providers_env()` is a strict, non-executing `KEY=value` parser
  (comments, blank lines; rejects malformed entries and duplicate keys) —
  the file is never sourced or evaluated as shell code.
- `FileCredentialResolver.resolve()` fails closed on unsafe permissions or a
  missing variable; `.status()` reports `CONFIGURED`/`MISSING` per name
  without ever exposing the value, and treats a *missing file* as a normal,
  all-`MISSING` state (distinct from unsafe permissions on an *existing*
  file, which still fails closed).
- `runway/cli.py` (`python -m runway.cli credentials edit|status`) drives
  this — `edit` creates/opens the file in `$EDITOR`/`$VISUAL`/`nano`/`vi`
  without ever printing its contents; `status` takes explicit `--var`
  names or `--config <gateway.yaml>` (sourcing `credential_ref` names from
  a real channel registry) — there is no default/hardcoded list of
  provider names anywhere in this mechanism, deliberately: see
  [What was deliberately excluded](#what-was-deliberately-excluded-and-why)
  above. No console-script entry point was registered in `pyproject.toml`
  for this change; invoke via `python -m runway.cli`.

### Desktop entry sheet (manual-editing convenience only)

`credentials desktop-template` writes a staging copy to
`~/Desktop/Runway-Provider-Keys.env` (`write_desktop_template()` /
`render_desktop_template()`) — **Runway never reads from this path
directly**; it exists only so a human has an easy place to paste keys.
Refreshing it never clobbers a value already typed in, and only the *file*
is chmod'd `0600` — the command never touches the Desktop directory's own
permissions, since that folder is the user's general-purpose space, not one
Runway owns. Like `status`, it takes names via `--var`/`--config` only —
still no hardcoded provider list.

`credentials import <path>` (`import_desktop_template()`) parses that file
as data (reusing `parse_providers_env()` verbatim — never sourced/executed)
and upserts its **non-empty** entries into the authoritative
`providers.env`, re-locking `0700`/`0600` afterward. An unfilled blank
placeholder in the source is skipped rather than blanking out an
already-configured credential; a key already in the authoritative store but
absent from the import source is left untouched (merge, never wipe). Output
is names only (`added`/`updated`/`skipped (blank in source)`) — never
values, in output or in any raised exception.

## Production activation requirements (future, separate work)

This build certifies the **foundation only**. Before any live provider
traffic:

1. Select one candidate provider; review its commercial/privacy/provenance
   terms outside of Runway.
2. Create isolated credentials in a secret store; reference them via
   `ProviderRecord.credential_ref` — never inline.
3. Implement one real `ModelClient` (canary) and one real `ExecutionPort`
   adapter against a chosen execution substrate — likely `providers/` +
   `plugins/model-providers/`, not the unmerged Prime/Titan OmniRoute relay,
   per the analysis above — as its own reviewed change.
4. Execute the synthetic canary suite against it; verify billing, model
   identity, tool support, and health/failover behavior.
5. Assign a trust tier deliberately (never inherited from discovery).
6. Explicitly `authorize()` — one provider at a time.
7. Only then consider `external_execution_enabled`, as a separate,
   explicitly-reviewed flag flip — not a side effect of this build.
