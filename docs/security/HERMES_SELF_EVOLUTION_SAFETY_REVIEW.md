# Hermes Self-Evolution — Pre-Implementation Safety Review

## Purpose and scope

This review evaluates whether Hermes Self-Evolution should be granted any
capability beyond its current state, ahead of the Hermes Add-on Prerequisite
Program. It is a review, not a change. **It enables nothing.** Self-Evolution
remains disabled and non-executing after this document merges.

Evidence basis: `apps/sigil/src/sigil/self_evolution.py` (1,108 lines, the
actual implementation), `docs/beta/post-phase9/SELF_EVOLUTION_FRAMEWORK.md`
(Stage 9 architecture contract), `docs/beta/post-phase9/SELF_EVOLUTION_POLICY.md`
(policy summary), `docs/sigil/evidence/SELF_EVOLUTION_STAGE9_CERTIFICATION.json`
(Stage 9 certification), and `docs/sigil/evidence/ECOSYSTEM_STAGE12D_GOLDEN_MASTER_READINESS.json`
(final ecosystem-wide certification sweep, decision `READY`, zero unresolved
blockers, includes Self-Evolution in its Stage 12A component inventory).

## Current disabled contracts

Every proposal, experiment plan, and lifecycle event modeled by
`self_evolution.py` is a **frozen, immutable dataclass record** — a
description of a hypothetical change, not a change. `ExperimentPlan.execution_enabled`
defaults to `False` and is validated on construction (self_evolution.py:513,
551). The framework holds no reference to a live Git checkout, shell,
package manager, or model registry it could mutate.

## Proposal-only versus execution authority

**Proposal-only, unconditionally, today.** The framework can produce a
structured opportunity → proposal → experiment-plan → review → promotion-readiness
record. It cannot apply that record to anything. This is enforced at the type
level (the objects that would be needed to execute — a shell handle, a file
writer, a Git client, a deployment trigger — do not exist in this module) and
restated explicitly in the authority boundary section of every Stage 9
document.

## Code generation, test generation, skill installation, dependency installation

None of these exist in the current implementation. `ImprovementProposal` and
`ExperimentPlan` can *describe* an expected change and required tests, but
nothing in `self_evolution.py` writes a file, generates code, installs a
skill, or runs a package manager. This matches `SELF_EVOLUTION_FRAMEWORK.md`'s
explicit deferred-work list.

## Shell execution, filesystem modification, Git operations, deployment authority, rollback

All explicitly denied by the authority boundary restated in every relevant
document and structurally absent from the code: no `subprocess`, no file
writes, no Git bindings, no deployment client exist in `self_evolution.py`.
`RollbackPlan` records *trigger conditions and steps as data* — Stage 9
cannot execute rollback; a human or a later, separately authorized stage
would.

## Quarantine and approval requirements

`ProposalState` includes `QUARANTINED` as a terminal-adjacent lifecycle
state. `IndependentReview` requires a reviewer identity distinct from the
proposal's creator — `SELF_EVOLUTION_POLICY.md` states explicitly "the
generator cannot be the sole evaluator." High and critical `RiskLevel`
proposals require security review, and critical-risk proposals are blocked
from ever reaching `promotion_ready` state during Stage 9 — there is no code
path that lets a critical-risk proposal self-certify.

## Budgets and resource limits

`EvolutionBudget` bounds runtime, attempts, compute, input/output bytes, and
cost (`Decimal`-exact, not float) for every experiment plan. These are
descriptive limits an experiment *plan* declares — since Stage 9 cannot run
experiments, the limits currently constrain nothing in practice, but they are
present and validated so that a future execution stage inherits real bounds
rather than inventing them under pressure.

## Evidence

`EvolutionEvidenceRef` requires content digest, provenance, observation time,
and a repository-relative reference for every opportunity and proposal.
`EvolutionLifecycleEvent` records are append-only and hash-linked
(self_evolution.py `create_lifecycle_event`, `transition_proposal`) —
consistent with the append-only evidence pattern used elsewhere in the
certified Stage 1-12 program.

## Adversarial prompt resistance

The framework's own inputs (problem statements, evidence references, review
comments) pass through `_validate_sanitized` before being accepted into any
record (self_evolution.py:200). Because the framework has no execution
surface, a successful prompt injection against it could at most produce a
*misleading proposal record* — it cannot escalate to code execution,
filesystem access, or credential exposure, since those capabilities are
structurally absent, not merely policy-denied. This is the strongest
property of the current design and should be preserved by any future stage:
prompt-controlled content should never gain a more privileged code path than
"data field in an immutable record."

## Supply-chain protections

Not yet applicable in a meaningful sense — the framework proposes no
dependency installation. Once (if) a future stage adds candidate code
generation or dependency changes, the existing Stage 1 governed integration
registry pattern (pinned commit/release identity, rejected unpinned
production entries) is the correct model to reuse rather than inventing a
parallel mechanism.

## Scope containment

`ImprovementProposal.affected_components` and `affected_integrations` are
required, bounded fields — a proposal must declare its blast radius as data.
Nothing currently reads or enforces that declaration against a real
filesystem or module boundary, because nothing currently executes.

## Emergency disable switch

There is currently **nothing running to disable** — the framework has no
active execution loop, service, or scheduled job. The practical "emergency
disable" today is that the Stage 1 integration registry starts empty and
disabled by default, and Self-Evolution is not represented as an enabled
registry entry. This is adequate for the current proposal-only posture but is
**not** a substitute for a real, single, documented kill switch once any
future stage proposes granting execution authority — that stage must define
one explicitly rather than relying on "there was nothing to turn off."

## Recommendation

**Remain proposal-only.** Nothing discovered in this review — including the
completed Stage 9 certification and the Stage 12D Golden Master sweep —
provides evidence that would justify narrowing this recommendation to a
specific safe execution capability. The framework's safety currently comes
from the *absence* of an execution surface, not from policy alone; any future
proposal to add one (code writes, shell, Git, deployment) should be treated
as a new, separately reviewed stage with its own threat model, budgets,
independent review requirement, and an explicit, tested emergency disable
switch — not as an incremental extension of Stage 9.

This recommendation does not change any code or configuration. Self-Evolution
remains exactly as disabled after this document merges as before it.
