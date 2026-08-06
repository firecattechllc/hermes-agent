# Threat Model

## Assets

- The `firecattechllc/hydra-docs` private repository's `main` branch.
- Titan's own systemd service state and filesystem.
- The Hermes source checkout on Titan (read, never written, by this worker).
- Fleet secrets: tokens, passwords, private keys, `.env` contents, Git
  credentials, and full private IP inventories — none of which may ever
  appear in generated documentation or a model prompt.

## Actors

- **The worker itself**, running unprivileged as `hermes-docs`, on a
  schedule, with no interactive operator present.
- **A local model** (Ollama/Gemma), treated as untrusted output: its text is
  only ever inserted as an inert prose paragraph, never as Markdown
  structure, and is redacted both going in and coming out.
- **Vault content**, treated as untrusted data: read for wiki-link
  validation and contradiction scanning, never parsed as instructions or
  executed.
- **A human reviewer**, who is the only actor that can merge a PR this
  worker opens.

## What the worker is authorized to do

Collect → draft → commit → push an automation branch → open one PR. Nothing
else. See `docs/hermes-docs-worker/architecture.md#structural-safety-boundaries`.

## What the worker must never do (and why each is structurally, not just
## procedurally, prevented)

| Risk | Prevention |
| --- | --- |
| Push to `main` | `git_ops.push_automation_branch` raises if the target branch equals `main`/`master`, and independently refuses any branch not prefixed `automation/titan-docs-`. The only `git merge` call anywhere in the package (`repo_sync.ensure_repo_synced`) is a local `--ff-only` merge of `origin/main` *into* local `main`, never the reverse, and is never pushed. |
| Merge its own PR | `github_pr.GitHubClient`/`GhCliClient` implement exactly `find_open_pr` and `create_pr`. No merge/approve/close method exists in the module — `tests/hermes_docs_worker/test_github_pr.py::test_gh_cli_client_has_no_merge_close_or_delete_capability` asserts this structurally, by introspecting the class, not by testing a specific call site. |
| Delete a remote branch / tag a release | No function in `git_ops.py` calls `git push --delete` or `git tag`; `tests/hermes_docs_worker/test_git_ops.py::test_git_ops_module_has_no_merge_delete_or_tag_capability` asserts this by introspecting the module. |
| Run with a dirty or diverged vault checkout | `repo_sync.ensure_repo_synced` fails closed on `git status --porcelain` output, on not being on `main`, and on any commit reachable from local `main` but not `origin/main` (true divergence vs. simply being behind, which fast-forwards). |
| Depend on the Mac being online | `config.py` reuses `hermes_cli.prime.omniroute_config.validate_no_mac_dependency` to reject any configured path/hostname/address that looks like a Mac (Tailscale address, `.local` hostname, `/Users/...` path, `host.docker.internal`). Fleet status is read from the local, already-maintained fleet registry file, not a live probe of the Mac. |
| Execute a command from vault content or model output | Every subprocess call is hard-coded, argument-separated argv through `proc.run_argv` (`ALLOWED_EXECUTABLES = {git, gh, systemctl}`); no code path interpolates vault or model text into an argv element. `vault_contradictions.py` only ever parses YAML-style `key: value` frontmatter lines as data. |
| Leak a secret into the vault or into a model prompt | Redaction happens at three independent points: `evidence.make_fact` (before an `EvidenceFact` is even constructed — falls back to `Unknown` rather than raise), `ollama_client` (prompt out, response in), and `markdown_gen._finalize` (final pass on every rendered document, defense-in-depth even if the first two somehow miss something). |
| Runaway resource use on a Pi 5 | `budgets.RunDeadline` bounds total wall-clock time; every subprocess/HTTP call takes an explicit timeout; `budgets.check_diff_budget` bounds files-changed and bytes-changed before any commit; systemd units set `MemoryMax=512M`, `CPUQuota=50%`, `TasksMax=32`. |
| PR spam | `budgets.check_pr_frequency` enforces a minimum interval between PRs this worker opens; `github_pr.find_existing_titan_pr` refuses to even start collection if a Titan automation PR is already open. |
| Concurrent runs corrupting state | `locking.run_lock` is a non-blocking `flock`; a second run exits immediately with `AlreadyRunningError` rather than queuing or racing. |
| Unbounded evidence retention filling the SD card | `EvidenceRetentionStore.append` prunes by both age (`evidence_retention_days`) and count (`evidence_max_files`) on every write. |

## Residual risks (accepted, not eliminated)

- **`gh` itself is trusted.** If the `hermes-docs` user's `gh` credential is
  compromised, an attacker with local code execution as that user could run
  `gh` commands this worker doesn't (e.g. `gh pr merge`) directly, bypassing
  this package entirely. Mitigation: `hermes-docs` is unprivileged, the
  systemd units are hardened (`ProtectSystem=strict`, no new privileges, no
  capabilities), and the credential should be scoped to the minimum `gh`
  needs (PR creation) if a fine-grained PAT is used instead of the default
  keyring flow.
- **A compromised Ollama model could still degrade prose quality** (e.g.
  produce misleading but not-secret-shaped text) without tripping redaction.
  Mitigation: model output is confined to a single narrative paragraph,
  never structural content, and every claim in the structured tables comes
  from collector evidence, not the model.
- **The Mac-dependency scan is pattern-based**, not a network-level
  guarantee. A sufficiently obfuscated Mac address (not matching the known
  Tailscale address, hostname markers, or `/Users/` path pattern) could
  theoretically slip through configuration. Mitigation: the worker's actual
  runtime dependencies (`git`, `gh`, local Ollama, `systemctl`, the local
  fleet registry file) are all Titan-local by construction, independent of
  what's in the config.
