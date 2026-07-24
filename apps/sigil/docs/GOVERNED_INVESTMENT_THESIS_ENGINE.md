# Governed Investment Thesis and Counter-Thesis Engine

## Purpose

Step 15 converts one exact Step 14 `ResearchDossier` into an immutable,
evidence-linked investment hypothesis and an independently constructed
counter-thesis. It is a structured interpretation layer for governed review. It
is not evidence, investment advice, a recommendation, a target price, a trade
approval, or an execution capability.

## Architecture and trust boundaries

`sigil.theses.models` defines frozen, slotted contracts; `policy` defines bounded
caller controls; `engine` validates references and constructs packages; `audit`
offers read-only inspection and deterministic regeneration; and `comparison`
reports identity-based changes without labeling them favorable or unfavorable.

The source-of-truth hierarchy is:

1. immutable normalized evidence and its provenance;
2. the exact Step 14 dossier and its claims, conflicts, gaps, conclusions, and
   questions;
3. optional immutable Step 11–13 portfolio and risk context;
4. the Step 15 interpretation.

Step 15 never changes a higher layer. It has no network, broker, journal, ledger,
risk-report, dossier, approval, scheduling, persistence, or knowledge-graph
mutation adapter.

## Identity binding and input contract

`InvestmentThesisInput` binds an exact dossier identity, issuer, security, and
caller-selected claim, conflict, and gap IDs. The engine rejects unresolved or
mismatched entities, unknown references, cross-issuer arguments, unselected
claims, unsupported instruments or currencies, duplicate identities, secret
material, and non-canonical values. A ticker is never sufficient without a
resolved issuer and security.

Optional portfolio and risk contexts are represented only by immutable source
identities and bounded relevance records. Acquisition is not implemented.
Portfolio ownership is optional and cannot alter issuer facts or argument
evidence.

## Arguments, pillars, and counter-thesis

Every factual `ThesisArgument` contains supporting dossier claims. Contradicting
claims remain attached and visible. The causal mechanism, assumptions,
timeframe, materiality, freshness, and completeness are explicit, and the
argument is marked as interpretation.

Thesis pillars group independently identified arguments with their claim links,
assumptions, dependencies, catalysts, risks, invalidation conditions, and
monitoring indicators. Claim quantity alone does not establish completeness.
Pillar classifications remain sensitive to freshness, conflicts, gaps, source
coverage, and unsupported assumptions.

The counter-thesis has separate pillars and separately directed arguments. Each
counter-pillar requires an alternative causal explanation and a failure
mechanism. Reusing thesis arguments or merely negating a thesis proposition is
rejected. Positive thesis evidence is not automatically treated as refuting the
counter-thesis.

## Causal chains, assumptions, and expected developments

Causal chains are explicitly interpretive. They preserve initiating conditions,
intermediate mechanisms, observable and financial effects, supporting and
contradicting arguments, assumptions, dependencies, timeframes, failure points,
and monitoring requirements. Correlation is never promoted to proven causation.

Assumptions are classified, evidence-linked, materiality-labeled, monitorable,
and optionally time-bounded. Unsupported material assumptions block readiness.
Expected operating, financial, competitive, capital-allocation, governance, and
risk developments are uncertain hypotheses with bounded observation windows and
explicit confirmation and contradiction criteria—not forecasts or facts.

## Catalysts, risks, and valuation dependencies

Catalysts use closed event categories, bounded windows, supporting claims,
dependencies, observable effects, uncertainty, and source provenance.
Unscheduled catalysts must be labeled speculative; dates are never fabricated.

Risks use transparent qualitative probability and impact categories, explicit
mechanisms, supporting and contradicting claims, optional Step 13 metric
references, monitoring indicators, and invalidation relationships. Opaque risk
scores are forbidden.

Valuation dependencies may record required metrics, assumptions, sensitivity
direction, and invalidation relationships. Step 15 does not calculate intrinsic
value, fair value, entry price, target price, expected return, or allocation.

## Invalidation, falsification, and monitoring

Every material pillar must identify a testable invalidation condition.
Conditions specify an observable fact, evidence type, optional exact-decimal
operator and threshold, time window, required source, status, and evaluation
identity. Only caller-supplied immutable observations may be evaluated. Missing
observations produce `unavailable`, never a passing result.

Falsification tests state the hypothesis, required observations, comparison
rule, expected and falsifying results, evaluation window, and availability.
Monitoring indicators define observation contracts, review-frequency
descriptions, stale thresholds, materiality, and relationships. They do not
schedule jobs or create automations.

## Conflicts, gaps, freshness, and readiness

Step 14 conflicts and gaps map to explicit thesis effects:
`blocks_argument`, `weakens_argument`, `blocks_pillar`, `weakens_pillar`,
`blocks_readiness`, `monitoring_required`, or `informational`. Step 15 never
resolves them. A governed resolution requires a new dossier identity followed by
thesis regeneration. Missing, stale, truncated, partial, and unresolved states
remain visible.

Confidence is a transparent classification (`high`, `moderate`, `low`, or
`unavailable`) derived from evidence coverage, contradictions, source diversity,
freshness, conflicts, gaps, assumptions, falsifiability, and counter-thesis
strength. It is never model confidence alone.

Completeness is `complete`, `substantially_complete`, `partial`,
`materially_incomplete`, or `unavailable`. Readiness is `ready_for_review`,
`requires_research`, `blocked`, or `unavailable`. Invalid identity or
provenance, an incomplete dossier, blocking conflicts or gaps, stale or
truncated required evidence, an insufficient counter-thesis, unsupported
material assumptions, or absent invalidation/falsification blocks readiness.
`ready_for_review` explicitly does not mean approved or authorized for trading.

## Deterministic identities, provenance, audit, and comparison

All material contracts use canonical JSON and SHA-256. Package identity covers
the policy, dossier, timestamp, arguments, both sets of pillars, assumptions,
dependencies, causal chains, catalysts, risks, invalidation conditions,
falsification tests, monitoring indicators, expected developments, valuation
dependencies, portfolio/risk context, conflicts, gaps, classifications,
blockers, and provenance. Any material change therefore changes identity.

Provenance records the exact dossier, policy, used Step 14 claim identities,
optional immutable context identities, construction time, and engine version.
Read-only audit functions verify identity, traverse argument/claim links, list
governance components and blockers, summarize confidence components, inspect
conclusion provenance, and deterministically regenerate a package.

Comparison requires the same resolved issuer and security. It detects added,
removed, or changed arguments and pillars; assumptions, catalysts, risks,
invalidation and falsification changes; monitoring, expected-development, and
valuation changes; portfolio/risk relevance changes; freshness and dossier
changes; and confidence, completeness, and readiness transitions.

## Prohibited language and bounded prose

Generated statements reject actionable recommendation terms including buy,
sell, hold, overweight, underweight, accumulate, allocation, position sizing,
trade entry or exit, target/fair price, guaranteed return, and risk-free claims.
Matching uses term boundaries to avoid rejecting unrelated words. Statements
are bounded and traceable to structured arguments and exact dossier claims.

## Operator responsibilities and limitations

Operators must validate the Step 14 dossier, select exact claims, independently
construct both hypotheses, supply immutable observations, review contradictions
and missing evidence, and treat readiness only as permission for human research
review. They remain responsible for legal, regulatory, suitability, approval,
execution, and portfolio decisions outside this system.

Step 15 does not browse, call models, acquire filings or market data, persist
packages, schedule monitoring, produce advice or forecasts, calculate intrinsic
value, support derivatives/margin/shorts/bonds/crypto, approve trades, or mutate
any external state. Production use still requires an approved caller workflow,
independent policy review, durable caller-managed artifact handling if desired,
access controls, operational monitoring, and jurisdiction-specific compliance
review.
