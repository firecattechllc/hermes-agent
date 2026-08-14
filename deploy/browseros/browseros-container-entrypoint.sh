#!/bin/sh
set -eu

mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" /tmp/.X11-unix
chmod 700 "$XDG_CONFIG_HOME"
chmod 1777 /tmp/.X11-unix

# Docker bridge peers are non-loopback inside the container. Permit them at the
# BrowserOS request-policy layer; the host publishes the proxy on loopback only.
local_state="$XDG_CONFIG_HOME/browser-os/Local State"
mkdir -p "$(dirname "$local_state")"
if [ -f "$local_state" ]; then
    jq 'setpath(["browseros", "server", "allow_remote_in_mcp"]; true)' \
        "$local_state" >"$local_state.tmp"
else
    jq -n 'setpath(["browseros", "server", "allow_remote_in_mcp"]; true)' \
        >"$local_state.tmp"
fi
chmod 600 "$local_state.tmp"
mv "$local_state.tmp" "$local_state"

browser_pid=""

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
xvfb_pid=$!

cleanup() {
    if [ -n "$browser_pid" ]; then
        kill -TERM "$browser_pid" 2>/dev/null || true
    fi
    kill -TERM "$xvfb_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

/usr/bin/browseros "$@" &
browser_pid=$!
wait "$browser_pid"
