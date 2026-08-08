# Ecosystem Discovery and Catalog

## Stage 8 status

Stage 8 defines a disabled-by-default governed ecosystem discovery catalog over
the Stage 1 integration registry.

The catalog evaluates externally supplied evidence. It does not perform
autonomous discovery.

## Modeled concepts

The catalog models immutable records for:

- discovered integration identity;
- canonical project metadata;
- category classification;
- capability classification;
- repository identity;
- immutable commit or release identity;
- maintainer evidence;
- license evidence;
- maturity evidence;
- activity evidence;
- security evidence;
- freshness;
- machine suitability;
- profile suitability;
- capability compatibility;
- registry compatibility;
- integration overlap;
- conflicts;
- known risks;
- threat-model references;
- recommendation state;
- admission readiness;
- deterministic discovery and assessment digests.

## Discovery boundary

Discovery evidence is injected by a caller and may represent:

- manual review;
- repository snapshot;
- release manifest;
- documentation snapshot;
- security review;
- license review;
- activity review.

Stage 8 does not fetch this evidence itself.

## Evidence requirements

A complete catalog assessment requires:

- repository evidence;
- license evidence;
- activity evidence;
- security evidence.

Evidence must include:

- immutable identity;
- canonical observation time;
- content digest;
- source identity;
- provenance;
- repository-relative reference.

Credential material, private endpoints, private paths, and traversal references
fail closed.

## Compatibility evaluation

The catalog evaluates:

- supported machines against the governed fleet;
- supported profiles against approved worker profiles;
- declared capabilities against approved capabilities;
- discovery identity against the Stage 1 registry;
- repository identity;
- category identity;
- immutable pinned identity.

Compatibility states include:

- `compatible`
- `partial`
- `incompatible`
- `unknown`

## Conflict and overlap evaluation

Conflicts model:

- the conflicting integration;
- overlapping capabilities;
- bounded severity;
- reason;
- evidence reference.

Moderate conflicts require governed review.

High-severity conflicts block admission and produce a quarantine
recommendation.

## Recommendation states

The catalog may recommend:

- `hold`
- `review`
- `reject`
- `sandbox_candidate`
- `pilot_candidate`
- `certification_candidate`
- `quarantine`

Recommendations are descriptive only.

They do not install, activate, admit, promote, or certify an integration.

## Admission readiness

Admission-readiness states include:

- `not_ready`
- `evidence_incomplete`
- `conflicted`
- `risk_blocked`
- `ready_for_review`
- `ready_for_sandbox`

The catalog cannot perform admission.

Hermes and the Stage 1 registry remain authoritative.

## Authority boundary

The catalog cannot:

- browse the web;
- crawl repositories;
- call external APIs;
- authenticate;
- resolve credentials;
- clone repositories;
- read arbitrary filesystems;
- execute shell commands;
- install integrations;
- activate integrations;
- mutate the integration registry;
- admit workers;
- dispatch jobs;
- approve work;
- promote lifecycle state;
- mutate policy;
- submit broker orders;
- mutate portfolio state;
- authorize or spend capital;
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

Stage 8 does not include:

- autonomous web discovery;
- repository crawling;
- package-registry crawling;
- release-feed polling;
- live license detection;
- live activity analysis;
- live security scanning;
- installation;
- activation;
- registry mutation;
- Mission Control projection;
- Agent Reach integration;
- self-evolution;
- fleet routing.

Those remain later-stage work.
