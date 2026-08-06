# Exact Commands: Dry Run and One Manual Governed Run

Assumes `/etc/hermes/docs-worker.env` is already in place (see the
installation runbook) and the package is installed in the venv at
`/opt/hermes/venv`. Substitute paths as needed for your environment.

## Dry run (every mode; no git/GitHub side effects)

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker collect --dry-run
'
```

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker daily --dry-run
'
```

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker weekly --dry-run
'
```

Each prints a one-line summary (`run <id> (<mode>): dry-run, N facts, M
file(s) would change`), any collector errors, and any broken wiki-links. No
file in the vault checkout is written, no branch is created, nothing is
pushed, and no PR is opened, regardless of the summary.

## Read-only status / config checks (no evidence collection)

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker validate-config
'
```

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker status
'
```

## One manual governed run (real: may commit, push, and open a PR)

Run this only after a dry run has been reviewed and after confirming `gh
auth status` succeeds as the `hermes-docs` user. This is exactly what
`hermes-docs-evidence.service` runs on its own schedule — running it by hand
just triggers it once, now:

```
sudo -u hermes-docs bash -c '
  set -a; source /etc/hermes/docs-worker.env; set +a
  /opt/hermes/venv/bin/python -m hermes_docs_worker collect
'
```

Or, via systemd (equivalent, but logged to journald under the unit name):

```
sudo systemctl start hermes-docs-evidence.service
sudo journalctl -u hermes-docs-evidence.service -n 50 --no-pager
```

Expected outcomes, all of them legitimate (not errors):

- `no meaningful documentation change` — the vault already reflects current
  evidence; nothing was committed.
- `an automation PR is already open awaiting review: <url>` — resolve that
  PR first (see `docs/hermes-docs-worker/operator-approval-workflow.md`).
- `opened <PR url>` — a new automation PR was created; review it.

## Local development / test dry run (no Titan, no real vault)

For trying the CLI against a disposable, local-only setup (two throwaway
git repos, no GitHub calls) rather than a real Titan deployment:

```
tmp=$(mktemp -d)
mkdir -p "$tmp/hermes-source" "$tmp/docs-repo"
git -C "$tmp/hermes-source" init -q && git -C "$tmp/hermes-source" \
  -c user.email=a@b.c -c user.name=test commit -q --allow-empty -m init
git -C "$tmp/docs-repo" init -q -b main && git -C "$tmp/docs-repo" \
  -c user.email=a@b.c -c user.name=test commit -q --allow-empty -m init
git init -q --bare "$tmp/origin.git"
git -C "$tmp/docs-repo" remote add origin "$tmp/origin.git"
git -C "$tmp/docs-repo" push -q -u origin main

HERMES_DOCS_WORKER_HERMES_SOURCE_DIR="$tmp/hermes-source" \
HERMES_DOCS_WORKER_DOCS_REPO_PATH="$tmp/docs-repo" \
HERMES_DOCS_WORKER_STATE_DIR="$tmp/state" \
HERMES_DOCS_WORKER_GITHUB_REPO="test-org/test-repo" \
python -m hermes_docs_worker collect --dry-run
```

`$tmp` must **not** be under `/Users/...` (the Mac-dependency guard rejects
it) — use a path like `/tmp/...` on macOS, which is what the command above
does implicitly via `mktemp -d`. Never point `HERMES_DOCS_WORKER_GITHUB_REPO`
at a real repository for this kind of local trial unless you intend a real
`gh pr create` call — `--dry-run` prevents that here, but a non-dry-run
invocation with a real repo slug will reach the real GitHub API the moment
`gh` is authenticated on the machine you're running from.
