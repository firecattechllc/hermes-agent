# Governed Research Dossier Engine

## Purpose

Step 14 builds an immutable, evidence-backed research dossier for one exactly
resolved issuer and, when applicable, one equity or ETF. It organizes supplied
verified evidence and deterministic calculations. It does not browse, acquire
data, write to the Hermes knowledge graph, recommend an investment, approve a
trade, execute a trade, or mutate broker, journal, ledger, portfolio, accounting,
or risk state.

A dossier is a reproducible derived report. Source evidence remains authoritative
only for what the source states. A persisted or displayed dossier never replaces
the evidence from which it was built.

## Architecture

`sigil.dossiers` is separated into:

- `models.py`: frozen, slotted identity, evidence, section, conclusion, and
  dossier contracts.
- `policy.py`: bounded versioned construction policy and deterministic policy
  identity.
- `input.py`: provider-neutral normalized input surface.
- `claims.py` and `sections.py`: narrow public contracts for evidence claims and
  structured sections.
- `financials.py`: exact-decimal deterministic derivations.
- `completeness.py`: explicit completeness and high-confidence eligibility.
- `engine.py`: side-effect-free construction and read-only inspection.
- `comparison.py`: deterministic same-entity and same-security comparison.

No module has a transport, credential, filesystem, approval, execution, journal
append, ledger append, risk mutation, or graph mutation dependency.

## Trust boundaries and source-of-truth hierarchy

The hierarchy is:

1. Verified source evidence is authoritative for the bounded fact actually
   supported by its locator.
2. Provider-normalized observations are authoritative only inside their stated
   entity, security, period, currency, unit, completeness, and provenance
   contract.
3. Derived values are calculations whose source observations and formula are
   explicit.
4. Conclusions and summaries are interpretations. They are never evidence.

Unsupported source-fact claims are rejected. Missing facts stay missing,
conflicts remain visible, stale and truncated inputs remain explicit, and a
sentiment result cannot override a financial fact.

## Identity

`ResearchEntityIdentity` binds issuer ID, legal and normalized names, CIK when
supplied, and provider identifiers. `ResearchSecurityIdentity` binds security
ID, issuer ID, symbol, exchange when supplied, instrument, security type,
currency, CIK, and provider identifiers. Only equities and ETFs in supported
currencies are accepted.

Resolution is deterministic. An unresolved issuer, invalid or conflicting CIK,
issuer/security mismatch, cross-issuer evidence, or mismatched security evidence
fails closed. Ticker-only probabilistic matching is not performed.

## Evidence and claims

Each `ResearchEvidenceReference` preserves source and record identity, source and
fact SHA-256 digests, source and acquisition timestamps, exact entity and
optional security binding, document or filing identity, bounded locator,
extraction method, verification, completeness, truncation, and supersession
status. Document evidence requires a locator. Excerpts are optional and bounded
to avoid retaining excessive copyrighted text.

Every source-fact `ResearchEvidenceClaim` has at least one exact evidence
reference. A derived claim has source claim IDs and a deterministic formula.
Claim identities cover the complete normalized contract, so changing evidence,
value, period, formula, freshness, contradiction status, or materiality changes
the SHA-256 identity.

Callers remain responsible for validating that the cited locator actually
supports the associated normalized statement. The engine rejects missing
citations; it does not use semantic guessing to repair a bad citation.

## Conflicts, supersession, and gaps

Conflicts retain competing values and evidence references. An unresolved
conflict cannot select a value. A resolved conflict requires an explicit
selection and reason; policy or operator identity can be recorded. Newest is not
selected automatically.

An amended filing may supersede an original only when its amendment relationship
is supplied and verified. Filing observations retain amendment and acquisition
metadata.

Gaps record their section, reason, required evidence type, related claims,
materiality, staleness, truncation, and resolution. Typical reasons include
missing periods, filings, price, business or governance evidence, portfolio or
risk context, unsupported acquisition capability, and truncated sources.
Unresolved gaps and conflicts deterministically generate research questions;
core question tracking does not require an LLM.

## Financial normalization and formulas

Financial observations use canonical decimal strings and include exact period,
fiscal year and quarter, annual or quarterly kind, instant or duration kind,
currency, unit, filing/evidence provenance, amendment status, completeness, and
confidence. Duplicate facts are rejected. Annual and quarterly facts, different
currencies, different units, and instant and duration observations are never
silently combined.

Implemented formula primitives are:

- Growth: `(current - prior) / prior`.
- CAGR: `(ending / beginning) ^ (1 / elapsed fiscal years) - 1`.
- Margin or conversion ratio: `numerator / denominator`.
- Free cash flow: `operating cash flow - capital expenditures`.
- Net cash: `cash and equivalents - total debt`; a negative result is net debt.

The same ratio contract supports gross, operating, net, and free-cash-flow
margins, debt ratios, yields, and other descriptive metrics when compatible
inputs are supplied. Zero or invalid denominators return structured
unavailability. Missing debt or cash is never treated as zero. Instant facts are
not summed and missing quarters are not inferred.

`RevenueAnalysis`, `MarginAnalysis`, `CashFlowAnalysis`,
`BalanceSheetAnalysis`, `ShareCountAnalysis`, and
`CapitalAllocationAnalysis` hold structured derived results. Trend
classification is deterministic: the latest two comparable derived values yield
improving, deteriorating, stable, or insufficient evidence. It is not a
recommendation.

## Structured research sections

The contracts cover business description, products and services, revenue model,
segments, geographies, customers, suppliers, distribution, regulation, assets,
and intellectual property. Each company-specific business profile requires
evidence claims; generic industry filler is not accepted as a verified fact.

Management and governance contracts support executives, roles, tenure, board
structure, independence, auditors, and governed observations. “Management
quality” cannot be a source fact; an assessment must be a supported derived
conclusion.

Filing history retains filing type, date, reporting period, digest, locator,
acquisition, completeness, extraction, and amendment relationship. The policy
supports 10-K, 10-Q, 8-K, and their amendments; the dossier engine does not add
new SEC endpoints.

Risk-factor observations bind category, title, normalized summary, evidence,
materiality, freshness, recurrence, and added/removed/changed/unchanged state.
Text length and sentiment do not determine severity. Litigation and regulatory
disclosures, competition, and industry observations are separate evidence-backed
contracts.

## Sentiment

Sentiment inputs retain model identity and version, exact input digest,
timestamp, label, exact confidence output, and evidence reference. Model output
is an observation, not a source fact or trading signal. Missing, stale, or
unsupported sentiment remains explicit.

## Valuation

Valuation context accepts caller-supplied normalized descriptive observations,
including market capitalization, enterprise value, price multiples, enterprise
multiples, yields, and supplied historical comparisons. Values bind exact
timestamps, evidence, denominators, and currency.

Stale prices cannot be represented as current. Negative or zero denominators
that make a multiple meaningless return unavailability. Missing cash, debt, or
shares is not silently replaced with zero. Step 14 creates no intrinsic value,
price target, or buy/sell/hold recommendation.

## Portfolio and risk relevance

Optional `PortfolioRelevance` and `RiskRelevance` are read-only snapshots of
caller-supplied Step 11–13 context. They may describe holding state, weight,
basis, realized or unrealized result, concentration, liquidity, limits, and
stress relevance. A dossier remains constructible when the security is not
owned. These sections do not alter company facts and cannot authorize a
proposed trade.

## Conclusions and prohibited language

`ResearchConclusion` supports observed strength or weakness, improving,
deteriorating or stable trends, unresolved conditions, contradictions, and
insufficient evidence. Each conclusion has supporting and contradicting claim
IDs, gaps, materiality, confidence classification, optional rule identity,
timestamp, and deterministic digest.

Conclusions reject buy, sell, hold, target-price, guaranteed-outcome, trade, and
allocation instructions. Statements are bounded and evidence-linked.

## Completeness and high-confidence eligibility

Completeness is one of `complete`, `substantially_complete`, `partial`,
`materially_incomplete`, or `unavailable`. The calculation exposes required
section coverage, minimum evidence, financial period count, filing count,
material gaps, material conflicts, stale evidence, and truncation.

High-confidence use fails closed for unresolved identity, a missing required
section, insufficient financial history or filing coverage, conflicts or gaps
beyond policy limits, and stale or truncated material evidence. This is
evidence-coverage eligibility, not model confidence. Complete does not mean an
investment recommendation.

## Identities, provenance, and comparison

Canonical, sorted serialization and SHA-256 cover policy, entity, security,
evidence, claims, conflicts, gaps, questions, conclusions, completeness,
timestamps, and provenance. A material input or derived-result change changes
the dossier identity.

Read-only inspection verifies identity, lists populated sections, summarizes
coverage, resolves claim-to-evidence and evidence-to-claim links, lists
conflicts, gaps, stale evidence, and questions, and exposes conclusion
provenance.

Comparison accepts only dossiers for the same exact entity and security. It
reports added, removed, and changed claims; conflicts and resolutions; gaps and
closures; changed financial, risk, and conclusion observations; and
completeness improvement, deterioration, or no change. A change is not called
good or bad without an explicit deterministic rule.

## Operator responsibilities and limitations

Operators must:

- supply immutable, verified, correctly bound evidence and an injected clock;
- validate source rights, citation support, amendment relationships, units,
  periods, currencies, and materiality;
- preserve source evidence separately from the derived dossier;
- review conflicts, gaps, conclusions, and completeness before relying on the
  report;
- keep human authority over investment, approval, execution, deployment, and
  release decisions.

Live acquisition adapters, semantic citation-entailment verification, production
issuer master data, broader currencies and instruments, accounting-standard
mapping, persistence, and model-assisted prose are not implemented here.
Persistence, if added later, must be caller-directed immutable canonical JSON
with atomic no-overwrite writes, restrictive permissions, symlink and traversal
rejection, and identity verification. Generated narratives must remain bounded,
traceable interpretations and must never fabricate facts.
