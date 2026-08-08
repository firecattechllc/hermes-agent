# Rollback and Recovery

## Stage 1 status

Phase 9 live-node certification is complete and merged. Stage 1 is authorized
only for atomic registry replacement, fail-closed loading, hash-linked lifecycle
evidence, and read-only inspection. Registry rollback restores a prior reviewed
registry revision; it does not authorize installation, removal, credential
action, service operation, or activation. Later stages remain unimplemented and
disabled.

Every stage records its parent commit, pinned external versions, configuration
before/after digest, installed dependencies, data migrations, evidence locations,
disable switch, credential revocation steps, and restore verification.

Recovery order:

1. Stop admission and propagate cancellation.
2. Disable and quarantine the integration/backend/node.
3. Revoke scoped credentials without logging values.
4. Preserve audit and completion-unknown evidence.
5. Restore the prior pinned configuration/artifact.
6. Verify health, repository state, budgets, and Sigil financial denials.
7. Require independent review and recertification before re-enable.

Agent Reach rollback additionally uses uninstall dry-run before any removal,
records affected upstream dependencies, avoids deleting shared tools without
explicit approval, and verifies that browser sessions and unrelated profiles were
not modified.
