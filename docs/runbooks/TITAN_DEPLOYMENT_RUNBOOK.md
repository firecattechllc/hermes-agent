# Titan deployment and rollback runbook

Titan: Raspberry Pi 5, always on. Runs the messaging gateway, scheduler,
lightweight orchestration, knowledge/memory services, notifications, and
fleet health checks.

Every command below is real and either was run directly in this repository
during development, or is documented, existing Hermes CLI behavior (`hermes
gateway install`, `hermes cron`, etc.) — none of this is speculative.

## Prerequisites

- Raspberry Pi 5, Raspberry Pi OS (64-bit) or another supported Linux
  distribution, joined to the private Tailscale network.
- `hermes` CLI installed (`Install method: git` or the standard installer —
  see the repository root README for install instructions).
- Python 3.11+ available (matches the repo's supported range).
- A GitHub fine-grained personal access token (source of truth).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).

## 1. Verify Tailscale connectivity first

Before touching any Hermes config, confirm Titan is actually reachable on
the tailnet from wherever you'll manage it:

```bash
tailscale status
# Titan should show up as an "Online" peer with a real MagicDNS name,
# e.g. hydra-titan.<your-tailnet>.ts.net
```

## 2. Install and configure Hermes

```bash
cd /opt/hermes-platform   # or wherever this checkout lives on Titan
sudo mkdir -p /etc/hermes /etc/hermes-link /var/lib/hermes-link
sudo cp deploy/titan/titan.env.example /etc/hermes/titan.env
sudo chmod 600 /etc/hermes/titan.env
# Edit /etc/hermes/titan.env and fill in:
#   GITHUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS,
#   HERMES_LINK_TOKEN (generate with:
#     python -c "import secrets; print(secrets.token_urlsafe(32))")
```

## 3. Configure Hermes Link (Titan's little_sister service)

```bash
sudo cp deploy/hermes-link/titan.service.json.example /etc/hermes-link/service.json
# The defaults are already safe (network_allowed/shell_allowed/
# filesystem_allowed/credentials_available_to_tasks all false) — do not
# flip these without a dedicated review.
sudo cp deploy/hermes-link/hermes-link.service /etc/systemd/system/hermes-link.service
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-link
sudo systemctl status hermes-link
```

The service binds to `127.0.0.1:9320` only (enforced by
`SignedServiceConfig.loopback_only`). To make it reachable from the Mac
over Tailscale without widening that bind, forward a local port on the Mac
through an SSH tunnel over the tailnet:

```bash
# Run from the Mac, or as a persistent tunnel via autossh/launchd:
ssh -N -L 19320:127.0.0.1:9320 titan-tailscale-hostname
```

(`19320` matches the `base_url` already used in
`deploy/hermes-link/mac-coordinator.json.example` / `deploy/mac/mac-coordinator.json.example`.)

## 4. Install the gateway (Telegram, cron dispatch, notifications)

```bash
# Load /etc/hermes/titan.env into the environment first (systemd's
# EnvironmentFile= does this automatically if you install as --system):
sudo hermes gateway install --system --start-now --start-on-login
hermes gateway status
```

This generates and manages its own systemd unit — there is no hand-written
gateway unit file in this repository to maintain separately. Cron runs
embedded inside this same gateway process (`kanban.dispatch_in_gateway`,
default `true`) — no separate cron service is needed.

## 5. Verify

```bash
hermes doctor
hermes cron status              # should show "Gateway is running" once installed
hermes mission-control status
hermes link status              # should succeed once HERMES_LINK_TOKEN is set
python3 scripts/fleet_connectivity_check.py --dns-identity <titan-tailscale-hostname>
```

Send `/start` to your Telegram bot from an allowed user ID and confirm
Titan responds — this is the end-to-end proof the gateway, approval
prompting, and notification paths are all live.

## Rollback

Every step above is additive and independently reversible:

```bash
# Stop and remove the gateway service:
hermes gateway uninstall --system

# Stop and remove the Hermes Link service:
sudo systemctl disable --now hermes-link
sudo rm /etc/systemd/system/hermes-link.service
sudo systemctl daemon-reload

# Remove config (keep a backup if you want to re-enable later):
sudo mv /etc/hermes-link/service.json /etc/hermes-link/service.json.bak
sudo mv /etc/hermes/titan.env /etc/hermes/titan.env.bak
```

No step here modifies GitHub state, deletes data, or touches any other
node — rollback is entirely local to Titan and does not require
coordinating with the Mac or any other fleet member.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `hermes link status` → `HERMES_LINK_TOKEN is not configured` | env file not loaded, or token blank | confirm `/etc/hermes/titan.env` is sourced by the service's `EnvironmentFile=` and the token is set |
| `hermes-link.service` fails to start | `state_root`/`credential_registry_path` directories don't exist or wrong permissions | `sudo mkdir -p /var/lib/hermes-link /etc/hermes-link` first |
| Telegram bot doesn't respond | `TELEGRAM_BOT_TOKEN` unset, or your user ID not in `TELEGRAM_ALLOWED_USERS` | check `hermes gateway status` logs; verify your numeric Telegram user ID |
| Cron jobs never fire | gateway not installed/running | `hermes cron status` explicitly reports "Gateway is not running" when this is the cause |
| Dangerous cron command silently blocked | intentional fail-closed default | this is expected — see `approvals.cron_mode` in `config.yaml` if you deliberately want a specific cron profile to auto-approve |
