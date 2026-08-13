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
