# BrowserOS on Hostinger

BrowserOS remains an external AGPL-3.0 program. Hermes does not vendor, link,
modify, or redistribute its source; integration is only through the MCP process
boundary. Distribution and network-use implications still require license review.

## Verified upstream boundary

- Regular BrowserOS supports Linux through AppImage and Debian packages;
  BrowserOS neo does not support Linux.
- BrowserOS `148.0.7966.97` uses internal ports 9000 (stable MCP proxy), 9100
  (CDP), and 9200 (sidecar HTTP/MCP backend). Both the proxy and backend bind
  `0.0.0.0` by design; `allow_remote_in_mcp=false` is request policy, not a
  socket-bind control.
- A fresh profile defaults to rejecting the Docker bridge peer. The container
  entrypoint atomically enables only BrowserOS's `allow_remote_in_mcp` request
  preference. This is safe only with the private bridge and loopback-only host
  publication described here.
- The production boundary therefore runs BrowserOS on a private Docker bridge
  and publishes only `127.0.0.1:9239` to container port 9000. CDP and the
  backend are never published. Anyone who can reach MCP controls the signed-in
  browser, so the loopback publish must not be widened.
- Ubuntu's container policy blocks Chromium's unprivileged user-namespace
  sandbox under Docker's default seccomp profile. The service does **not** use
  `--no-sandbox` or `SYS_ADMIN`: it permits the outer user-namespace syscall
  with `seccomp=unconfined`, while running non-root with all capabilities
  dropped, `no-new-privileges`, a read-only root filesystem, bounded tmpfs
  mounts, and memory/CPU limits. Chromium then establishes its own sandbox.
- BrowserOS and its built-in alarm scheduler must remain running. Scheduled
  tasks run in a hidden browser window and have an upstream ten-minute limit.
- BrowserOS requires X11 on this server. Xvfb works without a desktop, Wayland,
  DBus user session, GPU flags, or software-rendering flags. Its backend exposes
  `/system/health`; readiness is ultimately MCP initialization plus `tools/list`.
  The image healthcheck performs both protocol operations over container loopback.

## Prepared topology

Run BrowserOS as the unprivileged `browseros` container user, with mode `0700`
state bind-mounted from `/var/lib/hermes-browseros/home`, an Xvfb display that
disables TCP, and only the MCP proxy published to host loopback. Do not install a
desktop environment or pass invented Chromium profile flags. The normal XDG
directory is the tested persistent profile boundary.

Example preparation (operator-reviewed; not executed by this change):

```sh
sudo install -d -o 987 -g 987 -m 0700 /var/lib/hermes-browseros/home
sudo install -d -o root -g root -m 0755 /etc/hermes
sudo install -o root -g root -m 0600 browseros.env /etc/hermes/browseros.env
curl -fL https://cdn.browseros.com/download/BrowserOS.deb -o BrowserOS.deb
docker build --build-arg BROWSEROS_UID=987 --build-arg BROWSEROS_GID=987 \
  -t hermes-browseros:148.0.7966.97 .
```

The Ubuntu 24.04 image explicitly installs native `libasound2t64` before the
BrowserOS Debian package; otherwise APT can select an OSS compatibility provider
that lacks required ALSA symbols. Sign into each required account individually
from a private, operator-controlled session. Never import a personal Chrome profile.

## Health and canary

Hermes health is MCP initialization plus `tools/list`, bounded by configured
timeouts. Browser status exposes provider, Hostinger location, state, MCP
connection, and active sessions. There is no claim of upstream process health
beyond that protocol check.

After operator enablement, use only a harmless canary: discover tools, navigate
to `https://example.com`, and read the title. Do not submit forms, authenticate,
purchase, message, post, or invoke financial sites during acceptance.

## Rollback

1. Set `HERMES_BROWSER_ENABLED=false` and restart Hermes.
2. Stop and disable `browseros.service`.
3. Confirm nothing listens beyond loopback and Hermes reports `Disabled`.
4. Preserve `/var/lib/hermes-browseros` for forensic/recovery review; do not delete
   it automatically because it contains sensitive authenticated browser state.
