# Mac worker setup and rollback runbook

Mac: browser automation, computer use, heavier local models, coding and
desktop execution — reachable from Titan over the private Tailscale link.
The Mac is explicitly allowed to be offline without breaking Titan; nothing
in this runbook makes the Mac a hard dependency for Titan's own services.

## Prerequisites

- macOS, joined to the private Tailscale network.
- `hermes` CLI installed.
- Optional: [Ollama](https://ollama.com) installed and running, if using
  local heavier models on the Mac.

## 1. Verify Tailscale connectivity first

```bash
tailscale status
# The Mac itself should show as "Self"/online; Titan should show as an
# online peer before you rely on reaching it.
```

## 2. Configure environment

```bash
cd /path/to/hermes-platform   # this checkout, on the Mac
cp deploy/mac/mac.env.example ~/.hermes/.env.mac-additions
chmod 600 ~/.hermes/.env.mac-additions
# Merge the relevant lines into ~/.hermes/.env (or keep as a separate file
# your shell profile sources) — fill in:
#   GITHUB_TOKEN, HERMES_LINK_TOKEN (same value as Titan's), HERMES_LINK_TITAN_URL
```

`HERMES_LINK_TOKEN` must match the value Titan's `hermes-link` service was
configured with — this is a shared secret between coordinator and node, not
a Mac-specific credential.

## 3. Establish the Titan tunnel

Hermes Link on Titan binds to loopback only (by design — see the Titan
runbook). Reach it from the Mac via an SSH tunnel over Tailscale:

```bash
ssh -N -L 19320:127.0.0.1:9320 <titan-tailscale-hostname> &
```

For a persistent tunnel across reboots/sleep, wrap this in `autossh` or a
launchd `LaunchAgent` (`KeepAlive: true`, `RunAtLoad: true`) — not included
here since the exact supervision preference varies; the one-liner above is
the minimum to validate the setup.

## 4. Configure the Hermes Link coordinator

```bash
mkdir -p ~/Library/Application\ Support/Hermes/link
cp deploy/mac/mac-coordinator.json.example ~/.hermes/mac-coordinator.json
# Edit ~/.hermes/mac-coordinator.json:
#   - replace REPLACE_ME_USERNAME in credential_registry_path
#   - replace tailnet_dns_identity with Titan's real MagicDNS name
#   - replace authenticated_identity_ref if using the signed-registry path
#   - set "enabled": true once the above are filled in
```

## 5. Grant computer-use permissions

```bash
hermes computer_use doctor
# Follow its guidance to grant Accessibility + Screen Recording permissions
# to the terminal/app running Hermes (System Settings → Privacy & Security).
hermes computer_use doctor   # re-run to confirm both are now granted
```

## 6. Optional: periodic health-check launchd agent

```bash
mkdir -p ~/Library/Logs/hermes
cp deploy/mac/com.hermes.link-healthcheck.plist.example \
  ~/Library/LaunchAgents/com.hermes.link-healthcheck.plist
# Edit the plist: replace every REPLACE_ME_* placeholder with real
# absolute paths.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.link-healthcheck.plist
```

This is optional and read-only — it never blocks anything else from
working, and the Mac remains fully offline-capable without it.

## 7. Verify

```bash
python3 scripts/fleet_connectivity_check.py --node titan --config ~/.hermes/mac-coordinator.json
hermes link status
hermes computer_use doctor
```

## Rollback

```bash
# Stop the tunnel (kill the ssh process from step 3, or its supervisor).

# Unload the optional health-check agent:
launchctl bootout gui/$(id -u)/com.hermes.link-healthcheck 2>/dev/null || true
rm ~/Library/LaunchAgents/com.hermes.link-healthcheck.plist

# Disable the coordinator without deleting config (safest first step):
#   set "enabled": false in ~/.hermes/mac-coordinator.json

# Full removal:
rm ~/.hermes/mac-coordinator.json
rm ~/.hermes/.env.mac-additions
```

Nothing in this runbook installs a system-level (root) service or opens any
inbound port on the Mac — every piece is user-scoped and additive. Rollback
never affects Titan; Titan's gateway, cron, and Telegram surface all
continue operating exactly as before regardless of Mac state.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `fleet_connectivity_check.py` reports `node_offline` | Titan is asleep/unreachable/tailscaled down on Titan | this is the expected, correctly-fail-closed result — confirms the check is working, not broken |
| `fleet_connectivity_check.py` reports `config_error` "still has a placeholder" | `tailnet_dns_identity` in `mac-coordinator.json` wasn't filled in | replace it with Titan's real MagicDNS name from `tailscale status` |
| `hermes link status` fails despite tunnel running | port mismatch between the SSH tunnel's local port and `base_url` in `mac-coordinator.json` | confirm both use the same port (default `19320`) |
| `hermes computer_use doctor` still reports permissions missing after granting | macOS caches TCC state per-binary; if running from a new terminal app/build, re-grant for that specific binary | check the exact process name `doctor` reports and grant it explicitly in System Settings |
