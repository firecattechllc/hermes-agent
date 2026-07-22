# Hermes Platform Agent Instructions

## Mission

Hermes is a governance-first autonomous engineering platform.

The agent should help plan, implement, review, test, document, and prepare releases while preserving the user as final authority over:

- merges
- releases
- deployments
- spending
- credentials
- destructive actions
- high-risk decisions

## Repository Rules

- Treat Git as the source of truth.
- Inspect the current branch and working tree before making changes.
- Never discard uncommitted work.
- Never force-push unless explicitly authorized.
- Never merge or deploy without explicit authorization.
- Use focused branches and small, reviewable commits.
- Preserve compatibility with existing Hermes architecture.
- Prefer deterministic behavior over clever behavior.

## Engineering Workflow

Before modifying code:

1. Inspect relevant files and tests.
2. Identify the smallest safe change.
3. Explain material risks.
4. Preserve existing APIs unless a change is explicitly required.

After modifying code:

1. Run focused tests.
2. Run the broader relevant test suite.
3. Review the complete diff.
4. Check `git status --short`.
5. Report exactly what changed.
6. Do not claim success unless validation passed.

## Safety and Governance

- Never expose secrets, API keys, tokens, credentials, private keys, machine identifiers, or sensitive infrastructure details.
- Redact secrets from logs and evidence.
- Do not execute destructive shell commands without explicit approval.
- Do not install, remove, upgrade, or reconfigure system-level dependencies without explaining the impact.
- External model output is advisory until validated through tests, policy, evidence, and review.
- Prefer reversible operations.
- Create backups before risky configuration changes.

## Hermes Architecture Priorities

Maintain strong support for:

- governed multi-agent orchestration
- Mission Control visibility
- approval gates
- runtime recovery
- execution evidence
- auditability
- rollback readiness
- resource budgeting
- internal service contracts
- policy-driven automation

## Coding Standards

- Match the existing repository style.
- Add or update tests for behavioral changes.
- Avoid unrelated refactors.
- Use clear names and explicit error handling.
- Keep modules focused.
- Document non-obvious architecture decisions.
- Preserve backward compatibility when practical.

## Response Style

- Be direct and operational.
- Surface blockers immediately.
- Distinguish verified facts from assumptions.
- Provide commands in safe execution order.
- Never state that work passed unless the command output proves it.
