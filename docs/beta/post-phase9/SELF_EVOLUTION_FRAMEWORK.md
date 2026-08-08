# Governed Self-Evolution Framework

## Stage 9 status

Stage 9 defines a governed, non-executing framework for proposing, reviewing,
testing, certifying, rejecting, quarantining, and assessing improvements.

The framework does not modify Sigil, Hermes, integrations, models, prompts,
routes, policies, repositories, or deployed systems.

## Purpose

The framework enables Hermes to produce a structured statement such as:

> Here is an evidence-backed improvement opportunity, the proposed change,
> expected benefit, risk assessment, bounded experiment, test requirements,
> budget, rollback plan, independent reviews, experiment result, regression
> evidence, certification state, and promotion readiness.

That statement remains descriptive. Stage 9 cannot apply the proposal.

## Modeled concepts

Stage 9 models immutable records for:

- improvement opportunities;
- observed problems and inefficiencies;
- source evidence and provenance;
- affected components and integrations;
- proposed improvements;
- expected benefits;
- risk levels and mitigations;
- blast radius;
- isolated experiment plans;
- success and guardrail metrics;
- required tests;
- certification requirements;
- exact-decimal cost budgets;
- runtime, attempt, compute, and byte budgets;
- rollback triggers and steps;
- rollback verification;
- independent reviews;
- experiment results;
- regression evidence;
- promotion readiness;
- proposal lifecycle;
- append-only hash-linked lifecycle evidence.

## Opportunity boundary

An improvement opportunity requires:

- immutable opportunity identity;
- category;
- problem statement;
- affected components;
- affected integrations;
- canonical observation time;
- evidence references;
- deterministic digest.

Evidence must include a content digest, provenance, observation time, and a
repository-relative reference.

## Proposal boundary

An improvement proposal requires:

- immutable proposal identity;
- matching opportunity identity and digest;
- expected benefits;
- risk assessment;
- isolated experiment plan;
- bounded budget;
- rollback plan;
- independent-review requirement;
- creator identity;
- lifecycle state;
- deterministic digest.

A proposal cannot apply itself.

## Risk semantics

Risk levels include:

- `low`
- `moderate`
- `high`
- `critical`

High and critical risks require security review.

Critical-risk proposals are blocked from promotion readiness during Stage 9.

## Experiment boundary

Experiment plans are definitions only.

Every Stage 9 experiment must remain:

- isolated;
- paper-only;
- bounded by runtime;
- bounded by attempts;
- bounded by compute;
- bounded by input and output bytes;
- bounded by exact-decimal cost;
- protected by success metrics;
- protected by guardrail metrics;
- covered by required tests;
- covered by certification requirements.

`execution_enabled` must remain false.

## Rollback boundary

Rollback plans define:

- trigger conditions;
- rollback steps;
- verification tests;
- maximum recovery duration.

Stage 9 cannot execute rollback automatically.

## Independent review

Review records require:

- immutable review identity;
- reviewer identity;
- canonical review time;
- decision;
- review scope;
- evidence digest;
- comments reference.

The proposal creator cannot count as an independent approving reviewer.

## Experiment results

Experiment results record:

- normalized outcome;
- metric results;
- passed and failed tests;
- regression evidence;
- runtime;
- attempts;
- compute units;
- input and output bytes;
- exact-decimal cost;
- deterministic digest.

Results are validated against the proposal’s experiment budget.

Stage 9 does not create these results by running experiments.

## Promotion readiness

Promotion readiness includes:

- `not_ready`
- `evidence_incomplete`
- `review_required`
- `experiment_required`
- `certification_required`
- `regression_blocked`
- `risk_blocked`
- `ready`

Readiness requires:

- complete evidence;
- sufficient independent reviews;
- passed experiment;
- all required tests passed;
- all certification requirements satisfied;
- no regression evidence;
- acceptable risk.

`ready` does not authorize promotion.

## Lifecycle

Proposal lifecycle states include:

- `draft`
- `evidence_pending`
- `ready_for_review`
- `under_review`
- `changes_requested`
- `experiment_approved`
- `experiment_rejected`
- `experiment_recorded`
- `certification_pending`
- `promotion_ready`
- `promotion_rejected`
- `quarantined`
- `archived`

Transitions are explicitly allowlisted.

Lifecycle events are append-only and hash-linked.

## Authority boundary

The framework cannot:

- modify source code;
- modify tests;
- modify configuration;
- alter prompts;
- alter policies;
- alter routes;
- alter model selection;
- execute experiments;
- execute rollback;
- run shell commands;
- access arbitrary filesystems;
- install dependencies;
- commit Git changes;
- push Git changes;
- open pull requests;
- merge pull requests;
- approve its own proposal;
- promote a proposal;
- bypass tests;
- access credentials;
- spend money;
- submit broker orders;
- mutate portfolio state;
- bypass Hermes governance.

The inherited authority boundary remains:

- `paper_only = true`
- `broker_submission = false`
- `execution_authorized = false`
- `approval_authority = false`
- `capital_authority = false`
- `portfolio_mutation = false`
- `policy_mutation = false`
- `credential_access = false`
- `arbitrary_shell = false`
- `arbitrary_filesystem = false`
- `governance_bypass = false`
- `activation_authorized = false`
- `installation_authorized = false`

## Deferred work

Stage 9 does not include:

- automated source modification;
- automated experiment execution;
- automated test execution;
- automated rollback;
- Git commits;
- Git pushes;
- pull-request creation;
- dependency installation;
- model replacement;
- prompt mutation;
- routing mutation;
- policy mutation;
- deployment;
- promotion;
- live integration activation.

Execution and routing remain later-stage responsibilities.
