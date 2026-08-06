# Operator Approval Workflow

The worker never merges what it opens. This is the human side of that
contract.

## What arrives in your review queue

An open PR against `firecattechllc/hydra-docs`, branch
`automation/titan-docs-YYYYMMDD-HHMM`, titled
`Update Titan fleet evidence YYYY-MM-DD`, labeled `automation, titan-docs`.
The PR body includes:

- the run ID and a fact/error/contradiction count,
- the list of files changed,
- a wiki-link validation summary,
- an explicit statement that the worker cannot merge itself.

## What to check before approving

1. **Status vocabulary discipline.** Every claim should be tagged
   Implemented/Configured/Verified/Deployed/Unknown/Degraded/Blocked/Planned.
   If something reads as live (`Deployed`, `Verified`) but the underlying
   evidence in `09-Evidence/FLEET-VERIFICATION-MATRIX.md` or
   `TITAN-DAILY-EVIDENCE.md` doesn't support that, reject and file an issue
   against the worker — that would be a genuine bug, not an expected
   outcome.
2. **No secrets.** Scan the diff for anything that looks like a token,
   password, private key, or IP address that shouldn't be there. This
   should be structurally impossible (see the threat model), but review as
   if it weren't guaranteed.
3. **Contradictions.** If `OPERATIONS-DASHBOARD.md` lists an open
   contradiction or a new incident draft appears under
   `00-Inbox/incidents/`, read it before merging — it exists specifically
   to surface something the evidence disagreed with itself about.
4. **Wiki-links.** If the PR body reports broken links, decide whether the
   target should exist (create it in a follow-up) or the link should be
   removed in review.
5. **Scope.** The PR should only touch the worker's known output paths
   (`01-Dashboards/`, `09-Evidence/`, `01-Daily/`, `SOURCE-PROVENANCE.md`,
   `00-Inbox/incidents/`). Anything else is unexpected and worth pausing on.

## Merging

Merge (or request changes) through GitHub's normal review flow, exactly like
any other PR. There is no special "approve for the worker" step — the
worker has no way to detect or react to the merge; it will simply see the
new content in `main` on its next run and stop proposing it (idempotency).

## If a PR sits open too long

- The worker will **not** open a second PR while one from it is already
  open (`github_pr.find_existing_titan_pr`), so evidence keeps accumulating
  in the worker's local retention store but nothing new lands in your queue
  until this one is resolved.
- Closing the PR without merging is safe — the worker will detect no open
  PR on its next run and propose fresh content again.

## Escalation

An incident draft under `00-Inbox/incidents/` is explicitly marked
`Status: Draft — requires human review` and is never auto-escalated,
auto-paged, or auto-resolved. Treat it like any other new file in the PR:
read it, decide whether it represents a real incident, and act (or don't)
outside this worker entirely.
