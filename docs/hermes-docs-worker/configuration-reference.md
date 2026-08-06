# Configuration Reference

Configuration lives at `/etc/hermes/docs-worker.env` (see
`deploy/titan/docs-worker.env.example` for the canonical, commented
template). Every key is validated once, fail-closed, by
`hermes_docs_worker.config.DocsWorkerConfig.from_env` — an invalid or
Mac-dependent configuration is rejected before any collector, generator, or
git operation runs.

| Key | Required | Default | Notes |
| --- | --- | --- | --- |
| `HERMES_DOCS_WORKER_HERMES_SOURCE_DIR` | yes | — | Absolute path to the Hermes checkout actually running on this Titan node. Discovered from your real deployment, never assumed. |
| `HERMES_DOCS_WORKER_DOCS_REPO_PATH` | no | `/opt/hermes-docs/hydra-docs` | The `hydra-docs` vault checkout. |
| `HERMES_DOCS_WORKER_STATE_DIR` | no | `/var/lib/hermes-docs-worker` | Run lock, evidence retention journal, last-PR-timestamp. |
| `HERMES_DOCS_WORKER_GITHUB_REPO` | yes | — | `owner/repo` slug. Every `gh pr` call targets this repo explicitly, independent of the checkout's local git remotes. |
| `HERMES_DOCS_WORKER_GIT_REMOTE` | no | `origin` | |
| `HERMES_DOCS_WORKER_MAIN_BRANCH` | no | `main` | |
| `HERMES_DOCS_WORKER_GIT_USER_NAME` / `_GIT_USER_EMAIL` | no | `Titan Docs Worker` / `titan-docs-worker@hermes.local` | Commit author identity. |
| `HERMES_DOCS_WORKER_OLLAMA_ENDPOINT` | no | `http://127.0.0.1:11434` | Titan-local only; rejected if it looks like a Mac address/hostname. |
| `HERMES_DOCS_WORKER_OLLAMA_MODEL` | no | `gemma3:4b` | Must be an official Gemma tag actually pulled on this node. |
| `HERMES_DOCS_WORKER_OLLAMA_TIMEOUT_SECONDS` | no | `30` | 1–120. |
| `HERMES_DOCS_WORKER_SYSTEMD_ALLOWLIST` | no | evidence/daily/weekly + `ollama.service` | Comma-separated `*.service` names. The **only** units this worker will ever query. |
| `HERMES_DOCS_WORKER_EXTRA_FILESYSTEM_ALLOWLIST` | no | (empty) | Additional absolute paths a collector/generator may touch, beyond `HERMES_SOURCE_DIR`/`DOCS_REPO_PATH`/`STATE_DIR`. |
| `HERMES_DOCS_WORKER_FLEET_NODE_KEYS` | no | `prime,mac,hydra_live` | Natural keys read from the Prime fleet registry. |
| `HERMES_DOCS_WORKER_MAX_DIFF_BYTES` | no | `200000` | 1024–50,000,000. |
| `HERMES_DOCS_WORKER_MAX_FILES_CHANGED` | no | `25` | 1–500. |
| `HERMES_DOCS_WORKER_MIN_PR_INTERVAL_SECONDS` | no | `21600` (6h) | Minimum time between two PRs this worker opens. |
| `HERMES_DOCS_WORKER_MAX_RUN_SECONDS` | no | `600` | Wall-clock ceiling for one run. |
| `HERMES_DOCS_WORKER_MAX_SUBPROCESS_SECONDS` | no | `30` | Ceiling for any single `git`/`gh`/`systemctl` call; must not exceed `MAX_RUN_SECONDS`. |
| `HERMES_DOCS_WORKER_EVIDENCE_RETENTION_DAYS` | no | `30` | 1–365. |
| `HERMES_DOCS_WORKER_EVIDENCE_MAX_FILES` | no | `500` | 10–100,000. |
| `HERMES_DOCS_WORKER_PR_LABELS` | no | `automation,titan-docs` | Applied via `gh pr create --label`; must already exist on the target repo. |
| `HERMES_DOCS_WORKER_HERMES_TEST_EVIDENCE_PATH` | no | (unset → `Unknown`) | JSON file (`{"passed": bool, "run_at": str}`) a separate test runner writes. Read-only; this worker never executes tests. |
| `HERMES_DOCS_WORKER_FORBIDDEN_MAC_ADDRESSES` | no | (empty) | Extends (never replaces) the built-in Mac-dependency blocklist. |
| `HERMES_DOCS_WORKER_LOG_LEVEL` | no | `INFO` | |

## Non-configurable, by design

- **Branch name format** (`automation/titan-docs-YYYYMMDD-HHMM`) and
  **commit message format** (`Update Titan fleet evidence YYYY-MM-DD`) are
  fixed constants (`config.AUTOMATION_BRANCH_PREFIX`,
  `config.COMMIT_MESSAGE_PREFIX`), not environment-configurable — this is
  part of the governance contract itself, not an operator knob.
- **Daily/weekly schedule times** are systemd timer properties
  (`OnCalendar=`), not read from this env file — systemd timers don't read
  process environment. Use `systemctl edit hermes-docs-daily.timer` /
  `hermes-docs-weekly.timer` to override.

## GitHub authentication

This worker shells out to `gh`, so `gh` itself must already be authenticated
as the `hermes-docs` service user before any real (non-dry-run) run. Two
ways to do that:

- **Preferred**: run `sudo -u hermes-docs gh auth login` once, interactively,
  during install (step 3 of the installation runbook). `gh` stores the
  credential under that user's `$HOME/.config/gh/`, which
  `deploy/titan/systemd/*.service`'s `ReadWritePaths=/var/lib/hermes-docs-worker`
  covers since the service user's home is that directory.
- **Alternative**: set `GH_TOKEN` in `/etc/hermes/docs-worker.env` to a
  fine-grained personal access token scoped to `firecattechllc/hydra-docs`
  pull-request creation only. If you do this, treat the file exactly like
  any other secret: `chmod 640`, `chown root:hermes-docs`, and **never**
  commit a populated copy — the same rule the `.env.example` header states
  for every other governed worker's env file in this repo.

`docs-worker.env.example` ships with no populated credential either way.
