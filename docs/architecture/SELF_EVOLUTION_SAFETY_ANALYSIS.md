# Hermes Self-Evolution — Safety Analysis

**Status: engineer-authored analysis, not an independent review.** Same
caveat as `PAPERCLIP_SECURITY_ANALYSIS.md`: written by the agent
implementing the one small addition this document authorizes, not a
substitute for the "dedicated safety review" operator decision 6 requires
before any real execution/apply/rollback machinery is built.

## What already exists (unchanged this run)

`apps/sigil/src/sigil/self_evolution.py`'s `EvolutionFrameworkConfig` and
`ImprovementProposal` hardcode every dangerous capability `False` at
construction time: `can_modify_source`, `can_execute_experiment`,
`can_commit`, `can_push`, `can_open_pull_request`, `can_promote`,
`can_self_approve`. `IndependentReview` requires a reviewer identity
distinct from the proposal's creator (a proposal cannot self-review).
`RollbackPlan` raises if `automatic_rollback_enabled=True`. These
guarantees are unchanged and unweakened by this run.

## Threat model for the one addition made this run (diff production)

**Asset**: repository source integrity, evidence trail honesty.
**Threat actors considered**: a proposal-generation process that is
buggy, adversarially prompted, or compromised.

- **T1 — fabricated diff claims to have been applied.** Mitigated: the new
  `produce_evidence_diff()` function returns a plain string; nothing in
  this codebase treats its return value as "applied." No file write, git
  operation, or subprocess call exists anywhere in the new code.
- **T2 — diff generation used to exfiltrate file contents outside the
  repo.** Mitigated: the function takes `old_content`/`new_content` as
  plain string arguments already in the caller's possession; it performs
  no file I/O of its own (no `open()`, no path argument), so it cannot
  read anything the caller didn't already have.
- **T3 — diff output used as a code-execution vector (e.g., a caller
  blindly applies the returned patch text via `subprocess`).** Not
  mitigated by this module -- that is a caller-side risk. Documented
  explicitly in the function's docstring: the returned string is
  evidence/display text only, and this module makes no claim about what
  a caller does with it. Any future "apply this diff" capability remains
  exactly as gated (`can_modify_source=False`, hardcoded) as before.
- **T4 — resource exhaustion via pathological diff input.** Mitigated:
  both inputs are length-capped (`_MAX_CONTENT_BYTES`); oversized input
  raises rather than hangs on a huge `difflib` computation.

## Scope decision

Given the threat model above, this run implements **only**
`produce_evidence_diff()`: a pure, stdlib-`difflib`-based function that
turns two strings into a unified diff string, with no filesystem access,
no subprocess, no network, no state. It does not implement "sandboxed
testing" (an `ExperimentPlan`'s `execution_enabled` remains hardcoded
`False` and no sandbox runtime is built) -- constructing a real execution
sandbox correctly is a substantially larger, higher-risk task that
deserves its own dedicated design and review, not a rushed addition
alongside eight other work items in one session.

## Recommendation

Commission the real independent safety review (operator decision 6) before
building anything beyond diff production -- specifically before any
sandboxed execution capability, which is where the actual risk in this
subsystem lives.
