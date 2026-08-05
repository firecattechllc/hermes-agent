# Titan node deployment files

Titan is the always-on Raspberry Pi 5 node: messaging gateway, scheduler and
automations, lightweight orchestration, persistent memory/knowledge
services, notifications, fleet coordination and health checks.

Full step-by-step deployment and rollback procedure:
[`docs/runbooks/TITAN_DEPLOYMENT_RUNBOOK.md`](../../docs/runbooks/TITAN_DEPLOYMENT_RUNBOOK.md).

## Files in this directory

- **`titan.env.example`** — Titan-specific environment variables (GitHub
  token, Telegram bot token, Hermes Link token, terminal backend). Copy to
  `/etc/hermes/titan.env`, fill in real values, `chmod 600`.

## Files reused from `deploy/hermes-link/` (not duplicated here)

- **`../hermes-link/titan.service.json.example`** — the Hermes Link node
  service configuration (safe defaults: `network_allowed`, `shell_allowed`,
  `filesystem_allowed`, `credentials_available_to_tasks` all `false`).
  Copy to `/etc/hermes-link/service.json` on Titan.
- **`../hermes-link/hermes-link.service`** — the real, hardened systemd unit
  that runs `python -m hermes_cli.hermes_link.runtime --config
  /etc/hermes-link/service.json`. Install as-is.

These are kept in `deploy/hermes-link/` rather than copied here to avoid two
divergent copies of the same file — Titan and any future Linux fleet node
share the identical link-protocol config shape.

## Titan's other two services

- **Gateway** (Telegram, cron dispatch, notifications): installed via the
  existing `hermes gateway install --system` command, which generates and
  manages its own systemd unit — no hand-written unit file needed for this
  piece. See the runbook for exact flags.
- **Cron scheduler**: runs embedded inside the gateway process
  (`kanban.dispatch_in_gateway`, default `true`) — no separate service.

## Security defaults preserved

- `titan.service.json.example` binds to `127.0.0.1` only; the Hermes Link
  service is never exposed on Titan's Tailscale interface directly. Reach it
  through an SSH tunnel or `tailscale serve` mapping to that loopback port —
  see the runbook.
- `network_allowed`, `shell_allowed`, `filesystem_allowed`,
  `credentials_available_to_tasks`, `recursive_workers_allowed` all remain
  `false`. Do not flip these without a dedicated review — they exist
  specifically so Titan can accept governed research-preparation tasks
  without gaining shell/filesystem/credential/recursive-dispatch authority.
