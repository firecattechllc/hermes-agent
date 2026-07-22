# Hermes v1.0 Rollback Plan

## Objective

Restore Hermes to the last approved stable release while preserving evidence,
governance boundaries, operator authority, and recoverable data.

Rollback is never automatic unless an independently reviewed policy explicitly
authorizes that exact rollback class. Destructive actions require operator
approval.

## Required identities

- Failed release tag:
- Failed release commit:
- Previous stable tag:
- Previous stable commit:
- Rollback operator:
- Incident or mission identifier:

## Rollback triggers

A rollback may be proposed for:

- release artifact corruption
- failed clean installation
- critical regression
- governance bypass
- evidence-chain corruption
- persistence incompatibility
- Mission Control integrity failure
- security vulnerability
- provider execution outside approved boundaries

## Pre-rollback checks

- [ ] Incident evidence captured
- [ ] Current commit and tag recorded
- [ ] Current configuration backed up
- [ ] Current persistence data backed up
- [ ] Previous stable artifact verified
- [ ] Previous stable checksums verified
- [ ] Compatibility impact reviewed
- [ ] Operator explicitly approved rollback
- [ ] Deployment and provider activity paused when required

## Rollback procedure

1. Record the failed release state.
2. Preserve logs, evidence, manifests, and checksums.
3. Verify the approved rollback target.
4. Verify the rollback artifact signature and checksums.
5. Restore code or package to the approved stable version.
6. Restore only compatible configuration and persistence data.
7. Keep providers disabled until separately approved.
8. Run rollback smoke tests.
9. Confirm Mission Control visibility.
10. Record the completed rollback evidence.

## Rollback validation

- [ ] Expected stable version is active
- [ ] Expected stable commit is active
- [ ] Hermes starts successfully
- [ ] Governed workflow smoke test passes
- [ ] Mission Control smoke test passes
- [ ] Persistence integrity passes
- [ ] No credentials were exposed
- [ ] No unapproved provider execution occurred
- [ ] No unapproved spending occurred
- [ ] Working state is documented

## Rollback disposition

- Status: `not_required` / `prepared` / `approved` / `completed` / `failed`
- Operator:
- Timestamp:
- Evidence bundle:
- Notes:
