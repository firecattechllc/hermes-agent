# Titan Documentation Worker — Architecture

## Purpose

The Titan documentation worker (`hermes_docs_worker`) is the always-on evidence
and documentation node for the Hermes fleet. Running on Titan (a Raspberry Pi
5-class node), it periodically:

1. collects read-only evidence about Titan's own health, the Hermes runtime,
   Ollama, and the rest of the fleet (via the existing governed fleet
   registry);
2. generates conservative, status-tagged Markdown updates for the private
   `firecattechllc/hydra-docs` Obsidian vault;
3. opens a GitHub pull request when a run produces a meaningful documentation
   change.

GitHub remains canonical. The worker **can** collect, draft, commit, push an
automation branch, and open a PR. It **cannot** merge a PR, push to `main`,
delete a remote branch, or tag a release — not as a policy switch, but
because no code path in the package implements any of those actions (see
[Structural safety boundaries](#structural-safety-boundaries)).

## Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | Fail-closed configuration (`DocsWorkerConfig.from_env`); every field validated once at construction, including a Mac-dependency scan reused from `hermes_cli.prime.omniroute_config`. |
| `proc.py` | The single choke point for every subprocess call (`git`, `gh`, `systemctl`). Enforces argument-separated argv, a closed executable allowlist, and a bounded timeout. |
| `locking.py` | Non-blocking single-run lock (`flock`) so overlapping systemd timers skip cleanly instead of piling up. |
| `budgets.py` | Diff-size, files-changed, PR-frequency, and wall-clock-deadline enforcement. |
| `redaction.py` | Secret pattern matching/masking, applied before persistence and before any model prompt. |
| `evidence.py` | `EvidenceFact`/`EvidenceSnapshot` (pydantic, frozen); `make_fact()` is the collector-facing constructor that redacts first and never raises; `EvidenceRetentionStore` is a flock-protected, age/count-pruned JSONL journal. |
| `provenance.py` | Per-document provenance footers and the auto-managed block inside `SOURCE-PROVENANCE.md`. |
| `significance.py` | Evidence-level change scoring (`ChangeSignificance`) and the byte-for-byte no-change (idempotency) gate against the on-disk vault. |
| `contradiction.py` | Cross-fact contradiction detection (same subject, conflicting statuses in one run) plus the shared `VaultContradiction` record shape. |
| `wikilinks.py` | `[[WikiLink]]` extraction and validation against a vault index (including files this same run is about to create). |
| `repo_sync.py` | The documentation repo guard: clean/on-main/fetch/fast-forward-only, fails closed on dirty, diverged, or unreachable. |
| `git_ops.py` | Branch/commit/push for the automation branch only — no merge, delete, or tag function exists in the module. |
| `github_pr.py` | `GitHubClient` protocol + `GhCliClient` (shells to `gh`); only `find_open_pr`/`create_pr` exist. |
| `ollama_client.py` | `OllamaProseClient` (local Gemma via Ollama, redacts prompt and output) and `DeterministicFallbackClient` (always skips prose). |
| `markdown_gen.py` | Deterministic, template-free Markdown generation for every output document; a final `redact_text()` pass on every rendered document. |
| `collectors/` | One read-only collector per evidence source (see below). |
| `orchestrator.py` | Wires collection → normalization → contradiction detection → generation → wikilink validation → (if warranted) commit/push/PR, gated by lock, repo guard, dedupe, budgets, and no-change detection. |
| `cli.py`, `__main__.py` | `python -m hermes_docs_worker {collect,daily,weekly,status,validate-config}`. |

## Evidence collectors

All under `hermes_docs_worker/collectors/`, all read-only:

- `system_health.py` — disk, memory, temperature, uptime (`/proc`, `shutil.disk_usage`).
- `systemd_state.py` — `systemctl show` for an **explicit allowlist** of units only; no discovery/list-units call exists.
- `ollama_state.py` — Ollama version/reachability/installed models via a bounded HTTP GET to the Titan-local endpoint.
- `hermes_runtime.py` — Hermes source-checkout presence (static) plus Hermes-related systemd units (live), delegated to `systemd_state`.
- `worker_config_evidence.py` — this worker's own configuration validity, plus optional local test evidence read from a JSON file the worker never writes or executes.
- `git_state.py` — read-only `git` state of the Hermes source checkout on Titan.
- `fleet_status.py` — Prime/Mac/Hydra Live status read **only** from `hermes_cli.prime.fleet_registry.FleetRegistryStore`, the existing governed fleet registry. No new network probe to the Mac is ever made.
- `vault_contradictions.py` — unresolved contradiction notes already recorded in the vault (`00-Inbox/incidents/*.md` frontmatter), parsed as data, never as instructions.

## Status vocabulary

Every generated statement is tagged with one of `Implemented`, `Configured`,
`Verified`, `Deployed`, `Unknown`, `Degraded`, `Blocked`, `Planned`
(`status.py`). `Verified`/`Deployed` are reserved for a live, currently
observed signal (a systemd unit actually running, a reachable Ollama
endpoint, a fleet-registry connection state) — never for something inferred
from source code, a template, or a test file. Absence of evidence collapses
to `Unknown`, never to an optimistic default.

## Pipeline (`orchestrator.run_worker`)

```
acquire run lock (non-blocking)
  → repo_sync.ensure_repo_synced (dirty/diverged/unreachable → fail closed)
  → github_pr.find_existing_titan_pr (open PR already awaiting review → skip)
  → collect_evidence (7 collectors, each failure isolated to a collector_error)
  → contradiction.detect_status_contradictions
  → markdown_gen.render_* (+ prose from ollama_client, if reachable)
  → wikilinks.validate_generated_content
  → EvidenceRetentionStore.append (age/count pruned)
  → significance.any_content_changed vs. the on-disk vault → skip if unchanged
  [dry_run stops here]
  → budgets.check_diff_budget
  → write files (filesystem-allowlist-checked)
  → git_ops.create_automation_branch / stage_and_commit / push_automation_branch
  → budgets.check_pr_frequency
  → github_pr.create_pr
  → git_ops.return_to_main (always, success or failure)
```

`dry_run=True` (the `--dry-run` CLI flag) stops immediately after the
no-change check — no branch, commit, push, or PR, regardless of what the
evidence says.

## Structural safety boundaries

These are not runtime checks layered on top of a general-purpose git/GitHub
client — the capability is simply absent from the module surface:

- `git_ops.py` has no function that runs `git merge` against anything but a
  local fast-forward in `repo_sync.py` (never on the automation branch, never
  pushed), no `--force`/`--delete` push, and no `git tag` call.
- `github_pr.py`'s `GitHubClient` protocol and `GhCliClient` implementation
  define exactly two operations: `find_open_pr` and `create_pr`. There is no
  merge, close, or approve method anywhere in the module.
- `proc.py` is the only place any subprocess is invoked from, and it
  hard-codes the executable allowlist (`git`, `gh`, `systemctl`) and refuses
  a single shell string as argv.

See `docs/hermes-docs-worker/threat-model.md` for the full analysis.

## Redaction and provenance

`redaction.py` is applied at every boundary that matters: `evidence.make_fact`
redacts before an `EvidenceFact` is even constructed (falling back to
`Unknown` rather than raising if a value still looks secret after
redaction); `ollama_client.py` redacts both the outbound prompt and the
inbound model response; `markdown_gen.py` applies a final `redact_text()`
pass on every rendered document as a defensive backstop.

Every generated document ends with a provenance footer
(`provenance.render_provenance_footer`) naming the collectors that
contributed to it and when. `SOURCE-PROVENANCE.md` is updated in place via an
auto-managed HTML-comment-delimited block (`provenance.render_source_provenance_update`)
that never touches human-authored content outside that block.
