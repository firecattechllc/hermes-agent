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

## Repair Strategy Escalation Doctrine

During coding, patching, debugging, and repository maintenance, stop using the
current repair strategy when any of the following occurs:

- The same failure occurs three times.
- The same or functionally identical diff is produced twice.
- A patch reports `old_string and new_string are identical`.
- A patch cannot find its intended target twice.
- The same focused test fails three times for the same apparent root cause.

When triggered:

1. Stop the current patching method immediately.
2. Record the repeated failure and attempted approaches.
3. Switch to a materially different repair strategy.
4. Rewrite only the smallest complete unit required, such as one function,
   class, configuration block, regex tuple, or isolated file section.
5. Do not rewrite an entire file unless the smaller unit cannot be safely isolated.
6. Verify that the file actually changed using a diff, checksum, repr output,
   raw-byte comparison, or another deterministic method.
7. Run the narrowest relevant focused test.
8. Continue only after the edit and test result are verified.
9. Reassess the root cause before attempting another edit if the test still fails.
10. Never repeat the same unsuccessful repair strategy after escalation.

Additional safeguards:

- Use no more than three attempts with one repair strategy.
- Use no more than two attempts that produce an identical diff.
- Do not install packages or alter the virtual environment to bypass a repair failure.
- Do not weaken or rewrite a valid test solely to make it pass.
- Do not claim success unless the mutation and relevant test are verified.
- If no safe alternative remains, stop and report the exact blocker, affected
  files, repository state, and last test output.

## Mission Mutation Boundary

Before every file mutation, verify that the target file is within the active
mission's permitted scope.

If the file is outside that scope:

1. Refuse the mutation.
2. Record the attempted scope violation.
3. Stop the current tool sequence.
4. Request explicit authorization before continuing.

Installing or modifying project instructions is a separate mission from feature
implementation. Do not resume unrelated implementation work during an
instruction-maintenance mission.
