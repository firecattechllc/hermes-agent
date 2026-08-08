"""Dev-only, loopback HTTP wrapper around the desktop bridge.

This module exists solely so the native Sigil 4.0 macOS app (a separate,
sandboxed Swift client that cannot spawn subprocesses the way the Electron
main process does) can read governed backend status over plain HTTP instead
of over the stdin/stdout subprocess protocol the Electron app uses.

It duplicates zero backend logic: every route below is a thin pass-through to
:func:`sigil.desktop_bridge.runner.handle_request`, the exact same dispatcher
the Electron main process calls for every dashboard refresh
(``apps/sigil-desktop/electron/main.ts``).

Two explicit, separate allow-lists, GET vs POST:

- ``READ_ROUTE_TO_COMMAND`` (GET): status/inspection commands only. No
  command reachable here ever mutates paper-runtime state.
- ``WRITE_ROUTE_TO_COMMAND`` (POST): exactly the four governed, already-
  audited, paper-only automation lifecycle commands the "Launch" screen
  needs (activate/deactivate/pause/resume) -- nothing else. Every one of
  these already existed in ``handle_request``'s own allow-list and already
  writes its own audit trail entry; this shim adds no new capability to the
  backend, it only makes four already-governed actions reachable over HTTP
  for this one local client. No generic command passthrough exists anywhere
  in this file -- every reachable command is named explicitly, one at a
  time, in one of the two dicts above.

Isolation from the certified Sigil 3.7 app: this shim requires
``SIGIL_DESKTOP_STATE_DIR`` to point at a paper-runtime state directory that
is NOT the one Sigil 3.7 uses (Electron sets that to
``~/Library/Application Support/Sigil/paper-runtime``). The Sigil 4.0 dev
launch script points this shim at a separate ``SigilDev`` state directory, so
nothing this shim does -- reads OR the four governed writes -- can ever
advance, mutate, or otherwise affect Sigil 3.7's live paper-runtime state.
Run this only for local development; it is never started automatically by
Sigil 3.7 or by the packaged Sigil 4.0 app.

Binds to 127.0.0.1 only -- never 0.0.0.0 -- so it is not reachable from
outside this Mac.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sigil.desktop_bridge.runner import handle_request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8799

# Read-only status/inspection commands. None of these ever mutate
# paper-runtime state (aside from runtime_snapshot's own pre-existing,
# already-accepted self-healing writes, identical to what Electron does on
# every dashboard refresh).
READ_ROUTE_TO_COMMAND: dict[str, str] = {
    "/health": "health",
    "/runtime_snapshot": "runtime_snapshot",
    "/ai_status": "ai_status",
    "/prime_fleet_status": "prime_fleet_status",
    "/paper_execution_status": "paper_execution_status",
    "/paper_positions": "paper_positions",
    "/paper_orders": "paper_orders",
    "/paper_fills": "paper_fills",
    "/recent_proposals": "recent_proposals",
    "/recent_candidates": "recent_candidates",
    "/recent_rejections": "recent_rejections",
    "/recent_audit": "recent_audit",
    "/governed_news_status": "governed_news_status",
}

# Exactly the four governed paper-automation lifecycle commands, and nothing
# else. Each already exists in handle_request's own allow-list, is
# paper-only (broker_submission never becomes real/live), and already writes
# its own audit trail entry independent of this shim.
WRITE_ROUTE_TO_COMMAND: dict[str, str] = {
    "/paper_execution_activate": "paper_execution_activate",
    "/paper_execution_deactivate": "paper_execution_deactivate",
    "/paper_execution_pause": "paper_execution_pause",
    "/paper_execution_resume": "paper_execution_resume",
}


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "SigilDevBridgeShim/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(f"[sigil-dev-bridge-shim] {self.address_string()} {format % args}\n")

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, command: str) -> None:
        try:
            result = handle_request({"command": command})
        except Exception as error:  # noqa: BLE001 - always fail closed with JSON
            self._write_json(
                500,
                {"ok": False, "error": "internal_error", "message": str(error)},
            )
            return
        self._write_json(200, result)

    def do_GET(self) -> None:  # noqa: N802
        command = READ_ROUTE_TO_COMMAND.get(self.path)
        if command is None:
            self._write_json(
                404,
                {"ok": False, "error": "not_found", "message": f"No read route for {self.path}"},
            )
            return
        self._dispatch(command)

    def do_POST(self) -> None:  # noqa: N802
        # Drain and discard any request body -- these four commands take no
        # payload, and nothing here ever forwards client-supplied data into
        # handle_request. The command name itself is the only input, and it
        # must be an exact, pre-registered match.
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        if content_length:
            self.rfile.read(content_length)

        command = WRITE_ROUTE_TO_COMMAND.get(self.path)
        if command is None:
            self._write_json(
                404,
                {"ok": False, "error": "not_found", "message": f"No write route for {self.path}"},
            )
            return
        self._dispatch(command)


def main() -> None:
    if not os.environ.get("SIGIL_DESKTOP_STATE_DIR"):
        raise SystemExit(
            "SIGIL_DESKTOP_STATE_DIR is required and must point at an isolated "
            "dev state directory, never Sigil 3.7's paper-runtime directory."
        )

    host = os.environ.get("SIGIL_DEV_BRIDGE_HOST", DEFAULT_HOST)
    if host != "127.0.0.1":
        raise SystemExit("SIGIL_DEV_BRIDGE_HOST must be 127.0.0.1 (loopback only).")
    port = int(os.environ.get("SIGIL_DEV_BRIDGE_PORT", DEFAULT_PORT))

    server = ThreadingHTTPServer((host, port), BridgeHandler)
    print(f"[sigil-dev-bridge-shim] listening on http://{host}:{port} (loopback-only)")
    print(f"[sigil-dev-bridge-shim] read routes:  {', '.join(sorted(READ_ROUTE_TO_COMMAND))}")
    print(f"[sigil-dev-bridge-shim] write routes: {', '.join(sorted(WRITE_ROUTE_TO_COMMAND))}")
    print(f"[sigil-dev-bridge-shim] state dir: {os.environ['SIGIL_DESKTOP_STATE_DIR']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
