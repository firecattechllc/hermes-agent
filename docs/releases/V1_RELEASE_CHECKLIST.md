# Hermes v1.0 Release Checklist

This checklist governs the first stable Hermes release.

Completing implementation work does not authorize a release. Every release,
publication, deployment, provider activation, credential use, spending action,
rollback, or destructive operation remains subject to explicit operator
approval.

## 1. Release candidate identity

- [ ] Release branch was created from the current protected `main`
- [ ] Local `main` matched `origin/main` before branching
- [ ] Working tree was clean before release preparation
- [ ] Intended release commit SHA was recorded
- [ ] Intended semantic version was recorded
- [ ] Intended calendar-version tag was recorded
- [ ] Previous stable release tag was recorded
- [ ] Release scope was frozen
- [ ] No unrelated feature work was included

Required evidence:

- Release commit:
- Semantic version:
- Calendar tag:
- Previous tag:
- Release branch:
- Operator:

## 2. Version and metadata update

- [ ] Project version was updated consistently
- [ ] Package metadata reports the intended version
- [ ] Lockfile remains valid
- [ ] Release notes contain the correct repository links
- [ ] Changelog contains only commits in the release range
- [ ] Contributor attribution is accurate
- [ ] Version update is isolated in a reviewable commit
- [ ] No tag or publication occurred during version preparation

## 3. Canonical validation

The following must pass from the exact release candidate commit:

- [ ] Step 29 system-integration suite
- [ ] Step 32 Mac ↔ Titan link focused suite and private-auth boundary review
- [ ] Step 33 whole-system knowledge graph focused suite and discovery safety review
- [ ] Complete agent-role suite
- [ ] Mission Control suite
- [ ] Autonomous backlog suite
- [ ] MCP session-expiration suite
- [ ] Packaging and metadata suite
- [ ] Full CI Python test slices
- [ ] Python end-to-end suite
- [ ] Desktop Playwright end-to-end suite
- [ ] Ruff blocking enforcement
- [ ] Ruff and type-check comparison
- [ ] Windows footgun scan
- [ ] Lockfile validation
- [ ] Python compileall
- [ ] OSV dependency scan
- [ ] Supply-chain risk scan
- [ ] Git whitespace validation
- [ ] Clean working-tree verification

Record exact totals:

- Step 29:
- Step 32: `docs/architecture/HERMES_STEP32_MAC_TITAN_LINK.md`
- Step 33: `docs/releases/STEP33_WHOLE_SYSTEM_KNOWLEDGE_GRAPH.md`
- Agent roles:
- Mission Control:
- Autonomous backlog:
- MCP session suite:
- Packaging and metadata:
- Full CI:
- Other:

## 4. Governance certification

- [ ] Operator approval remains required for release
- [ ] Operator approval remains required for deployment
- [ ] Operator approval remains required for rollback
- [ ] Operator approval remains required for provider activation
- [ ] Operator approval remains required for credential access
- [ ] Operator approval remains required for spending
- [ ] No approval was synthesized by software or an AI agent
- [ ] High-risk operations fail closed
- [ ] Mission Control events remain sanitized
- [ ] Evidence records contain no credentials or secrets
- [ ] Release readiness is tied to the exact candidate SHA
- [ ] Release disposition cannot become ready with blocking findings

## 5. Security and sanitization

- [ ] Repository history was scanned for obvious secrets
- [ ] Release diff was scanned for credentials and private keys
- [ ] Architecture evidence contains no prohibited secrets
- [ ] No personal access tokens are included
- [ ] No API keys are included
- [ ] No authorization headers are included
- [ ] No private Tailscale keys are included
- [ ] No production credentials were accessed during preparation
- [ ] No external systems were modified during preparation
- [ ] Security scanning reports no new blocking findings

## 6. Release artifacts

Prepare, checksum, and inventory every artifact before publication.

- [ ] Source archive prepared
- [ ] Python package artifacts prepared
- [ ] Artifact filenames include the intended version
- [ ] SHA-256 checksum file prepared
- [ ] Artifact inventory prepared
- [ ] Build environment recorded
- [ ] Build command recorded
- [ ] Artifact creation is reproducible
- [ ] Artifacts were tested from a clean environment
- [ ] No secrets or local configuration are embedded
- [ ] Artifacts have not been published before operator approval

Artifact inventory:

| Artifact | SHA-256 | Size | Validation |
|---|---|---:|---|
| | | | |

## 7. Rollback readiness

- [ ] Previous stable commit is recorded
- [ ] Previous stable tag is recorded
- [ ] Candidate commit is recorded
- [ ] Rollback target is immutable
- [ ] Rollback procedure was reviewed
- [ ] Rollback verification commands were tested safely
- [ ] Data/schema compatibility was reviewed
- [ ] Persistence compatibility was reviewed
- [ ] Mission Control rollback visibility was reviewed
- [ ] Provider activation can remain disabled during rollback
- [ ] Rollback requires explicit operator approval
- [ ] No destructive rollback action has been performed

## 8. Release evidence bundle

The release evidence bundle must include:

- [ ] Release manifest
- [ ] Exact commit and tree SHA
- [ ] Version metadata
- [ ] Release notes
- [ ] Test summaries
- [ ] CI links or run identifiers
- [ ] Lint results
- [ ] Security scan results
- [ ] Artifact inventory
- [ ] Artifact checksums
- [ ] Rollback manifest
- [ ] Governance certification
- [ ] Sanitization certification
- [ ] Operator approval record
- [ ] Post-release verification plan

Evidence bundle location:

- Path:
- Bundle checksum:

## 9. Signed tag authorization

Before creating the tag:

- [ ] All required checks are complete
- [ ] Release candidate commit is unchanged
- [ ] Evidence bundle is complete
- [ ] Rollback package is complete
- [ ] Operator reviewed the final release notes
- [ ] Operator reviewed the final artifact inventory
- [ ] Operator explicitly approved tag creation
- [ ] Signing identity was verified
- [ ] Tag name does not already exist

Planned tag:

- Semantic version:
- Calendar tag:
- Commit:
- Signing identity:

## 10. GitHub Release authorization

Before publishing:

- [ ] Signed tag exists at the approved commit
- [ ] Signed tag verification succeeds
- [ ] GitHub Release notes match the reviewed notes
- [ ] Artifact checksums match the evidence bundle
- [ ] Correct artifacts are attached
- [ ] Release is initially created as a draft
- [ ] Draft release was reviewed
- [ ] Operator explicitly approved publication
- [ ] Publication does not trigger an unapproved deployment

## 11. Post-release verification

- [ ] Published tag resolves to the approved commit
- [ ] GitHub Release is visible
- [ ] Attached artifacts download successfully
- [ ] Downloaded artifact checksums match
- [ ] Clean installation succeeds
- [ ] Package reports the intended version
- [ ] Hermes starts successfully
- [ ] Core governed workflow smoke test passes
- [ ] Mission Control smoke test passes
- [ ] Provider execution remains disabled unless separately approved
- [ ] No unexpected deployment occurred
- [ ] Repository remains clean and synchronized
- [ ] Release evidence bundle was finalized

## 12. Operator sign-off

### Release preparation

- Operator:
- Date:
- Candidate commit:
- Decision: `approved` / `rejected`
- Notes:

### Signed tag creation

- Operator:
- Date:
- Tag:
- Decision: `approved` / `rejected`
- Notes:

### GitHub Release publication

- Operator:
- Date:
- Release:
- Decision: `approved` / `rejected`
- Notes:

### Deployment or provider activation

This is a separate approval and is not granted by release publication.

- Operator:
- Date:
- Environment:
- Decision: `approved` / `rejected` / `not requested`
- Notes:
