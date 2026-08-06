# Titan Installation Runbook

This runbook installs the governed Titan documentation worker on a Titan
(Raspberry Pi 5-class) node. It does **not** cover installing Hermes itself
or the `hydra-docs` vault — both are prerequisites (see below).

## Prerequisites

- Hermes is already deployed on this Titan node (any location — this
  runbook does not assume a path; you will read the real path out of your
  own deployment in step 3).
- `git` and, for pull-request creation, the GitHub CLI (`gh`) are installed
  and `gh auth login` has been run as the account that should open Titan's
  automation PRs, with access to the private `firecattechllc/hydra-docs`
  repository.
- (Optional, recommended) Ollama is installed with an official Gemma model
  pulled, sized for a Pi 5, e.g.:

  ```
  ollama pull gemma3:4b
  ```

  If Ollama is unavailable or unreachable, the worker automatically falls
  back to a deterministic, prose-free generator — this is not a hard
  requirement.

## 1. Create the dedicated service user

```
sudo useradd --system --home-dir /var/lib/hermes-docs-worker \
  --shell /usr/sbin/nologin hermes-docs
```

No step in this runbook or in the worker itself ever runs as root.

## 2. Clone the documentation vault

```
sudo mkdir -p /opt/hermes-docs
sudo git clone git@github.com:firecattechllc/hydra-docs.git /opt/hermes-docs/hydra-docs
sudo chown -R hermes-docs:hermes-docs /opt/hermes-docs/hydra-docs
```

Verify `origin` points at the real repo and `main` is checked out:

```
sudo -u hermes-docs git -C /opt/hermes-docs/hydra-docs remote -v
sudo -u hermes-docs git -C /opt/hermes-docs/hydra-docs branch --show-current
```

## 3. Create runtime state and configuration directories

```
sudo mkdir -p /var/lib/hermes-docs-worker
sudo chown hermes-docs:hermes-docs /var/lib/hermes-docs-worker
sudo chmod 700 /var/lib/hermes-docs-worker

sudo mkdir -p /etc/hermes
sudo cp deploy/titan/docs-worker.env.example /etc/hermes/docs-worker.env
sudo chown root:hermes-docs /etc/hermes/docs-worker.env
sudo chmod 640 /etc/hermes/docs-worker.env
```

Edit `/etc/hermes/docs-worker.env`:

- Set `HERMES_DOCS_WORKER_HERMES_SOURCE_DIR` to the **actual** path of the
  Hermes checkout running on this node — read it from your real deployment
  (e.g. `WorkingDirectory=` of the Hermes systemd unit), never assumed.
- Confirm `HERMES_DOCS_WORKER_GITHUB_REPO=firecattechllc/hydra-docs`.
- Review `HERMES_DOCS_WORKER_SYSTEMD_ALLOWLIST` against the units actually
  present on this node.
- Leave secrets blank unless you have a specific value to set (there should
  be none required for this worker — it authenticates to GitHub via `gh`'s
  own credential store, not an env var).

See `docs/hermes-docs-worker/configuration-reference.md` for every key.

## 4. Install the Python package

```
cd /opt/hermes/current   # your Hermes install root
sudo -u hermes-docs /opt/hermes/venv/bin/python -m pip install -e .
```

(Adjust to however this Titan node installs the `hermes-agent` package —
`hermes_docs_worker` ships as part of it.)

## 5. Validate configuration before installing any timer

```
sudo -u hermes-docs \
  HERMES_DOCS_WORKER_HERMES_SOURCE_DIR=... \
  /opt/hermes/venv/bin/python -m hermes_docs_worker validate-config
```

Or, once `/etc/hermes/docs-worker.env` is in place:

```
sudo -u hermes-docs bash -c \
  'set -a; source /etc/hermes/docs-worker.env; set +a; \
   /opt/hermes/venv/bin/python -m hermes_docs_worker validate-config'
```

Do not proceed until this prints `configuration OK`.

## 6. Dry run before enabling anything

```
sudo -u hermes-docs bash -c \
  'set -a; source /etc/hermes/docs-worker.env; set +a; \
   /opt/hermes/venv/bin/python -m hermes_docs_worker collect --dry-run'
```

This collects real evidence and generates real Markdown in memory, but
writes nothing to the vault and makes no git or GitHub calls. Review its
output. See `docs/hermes-docs-worker/dry-run-and-manual-run.md` for the full
set of exact commands (all three modes, plus one manual governed run).

## 7. Install the systemd units

```
sudo cp deploy/titan/systemd/hermes-docs-*.service /etc/systemd/system/
sudo cp deploy/titan/systemd/hermes-docs-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

If the daily report time (default `03:00`) needs to differ from the
packaged default, use a drop-in rather than editing the packaged unit:

```
sudo systemctl edit hermes-docs-daily.timer
# [Timer]
# OnCalendar=
# OnCalendar=*-*-* 05:30:00
```

## 8. Enable and start the timers

```
sudo systemctl enable --now hermes-docs-evidence.timer
sudo systemctl enable --now hermes-docs-daily.timer
sudo systemctl enable --now hermes-docs-weekly.timer
```

## 9. Verify

```
sudo systemctl list-timers 'hermes-docs-*'
sudo -u hermes-docs /opt/hermes/venv/bin/python -m hermes_docs_worker status
sudo journalctl -u hermes-docs-evidence.service -n 50
```

Watch for the first automation PR to appear against
`firecattechllc/hydra-docs` after the next scheduled evidence run finds a
meaningful change, and review it like any other PR — this worker never
merges it for you.

## Rollback / disable

See `docs/hermes-docs-worker/rollback-and-disable.md`.
