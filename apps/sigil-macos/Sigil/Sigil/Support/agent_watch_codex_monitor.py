#!/usr/bin/env python3
"""Monitor a documented Codex app-server and emit content-free lifecycle state."""

from __future__ import annotations

import datetime
import http.server
import json
import os
import pathlib
import pwd
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

children: list[subprocess.Popen] = []
latest_event: dict | None = None
event_lock = threading.Lock()


def local_user_home() -> pathlib.Path:
    return pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)


def codex_binary() -> pathlib.Path | None:
    candidates = [
        local_user_home() / ".local/bin/codex",
        pathlib.Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        pathlib.Path("/opt/homebrew/bin/codex"),
        pathlib.Path("/usr/local/bin/codex"),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def write_event(directory: pathlib.Path, pid: int, event: str) -> None:
    global latest_event
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {
        "agent": "codex",
        "processID": pid,
        "event": event,
        "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with event_lock:
        latest_event = record
    if "--serve" in sys.argv:
        return
    target = directory / f"codex-{pid}.json"
    fd, temporary = tempfile.mkstemp(prefix=".codex-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EvidenceHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/v1/evidence":
            self.send_error(404)
            return
        with event_lock:
            record = latest_event
        if record is None:
            self.send_response(204)
            self.end_headers()
            return
        body = json.dumps(record, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        return


def start_evidence_server() -> http.server.ThreadingHTTPServer | None:
    if "--serve" not in sys.argv:
        return None
    index = sys.argv.index("--serve")
    port = int(sys.argv[index + 1]) if index + 1 < len(sys.argv) else 47841
    source = http.server.ThreadingHTTPServer(("127.0.0.1", port), EvidenceHandler)
    threading.Thread(target=source.serve_forever, daemon=True).start()
    return source


def stop(_signum=None, _frame=None) -> None:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    raise SystemExit(0)


def request(proxy: subprocess.Popen, request_id: int | None, method: str, params=None) -> None:
    body = {"method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    proxy.stdin.write(json.dumps(body, separators=(",", ":")) + "\n")
    proxy.stdin.flush()


def event_from_statuses(statuses: list[dict], previously_active: bool) -> tuple[str | None, bool]:
    types = [status.get("type") for status in statuses]
    flags = {flag for status in statuses for flag in status.get("activeFlags", [])}
    if "waitingOnApproval" in flags:
        return "approvalRequired", True
    if "waitingOnUserInput" in flags:
        return "waitingForInput", True
    if "systemError" in types:
        return "failed", False
    if "active" in types:
        return "beganWork", True
    if previously_active and statuses and all(value in {"idle", "notLoaded"} for value in types):
        return "completed", False
    return None, False


def main() -> int:
    binary = codex_binary()
    if binary is None:
        return 0
    event_dir = local_user_home() / "Library/Application Support/SigilDev/AgentWatch/Events"
    evidence_server = start_evidence_server()
    server = subprocess.Popen([str(binary), "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    children.append(server)
    request(server, 1, "initialize", {"clientInfo": {"name": "sigil_agent_watch", "title": "Sigil Agent Watch", "version": "1"}, "capabilities": {"experimentalApi": True, "optOutNotificationMethods": ["item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta", "item/commandExecution/outputDelta", "turn/diff/updated", "thread/tokenUsage/updated"]}})
    request(server, None, "initialized")
    request_id = 10
    last_poll = 0.0
    previously_active = False
    live_mode = next((mode for mode in ("--live-test", "--live-approval", "--live-input") if mode in sys.argv), None)
    live_test = live_mode is not None
    live_thread_request_id = 3 if live_test else None
    live_thread_id = None
    if live_test:
        request(server, live_thread_request_id, "thread/start", {"cwd": "/tmp", "ephemeral": True})
    while server.poll() is None:
        now = time.monotonic()
        if now - last_poll >= 2:
            request(server, request_id, "thread/list", {"limit": 100, "sortKey": "updated_at", "useStateDbOnly": True})
            request_id += 1
            last_poll = now
        readable, _, _ = select.select([server.stdout], [], [], 0.5)
        if not readable:
            continue
        line = server.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        params = message.get("params", {})
        if method == "turn/started":
            write_event(event_dir, server.pid, "beganWork")
        elif method == "turn/completed":
            status = params.get("turn", {}).get("status")
            write_event(event_dir, server.pid, "failed" if status == "failed" else "completed")
            if live_test:
                return 0
        elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"}:
            write_event(event_dir, server.pid, "approvalRequired")
        elif method == "item/tool/requestUserInput" and params.get("autoResolutionMs") is None:
            write_event(event_dir, server.pid, "waitingForInput")
        if live_test and message.get("id") == live_thread_request_id:
            live_thread_id = message.get("result", {}).get("thread", {}).get("id")
            if live_thread_id:
                prompts = {
                    "--live-test": "Reply with exactly: Agent Watch lifecycle verified.",
                    "--live-approval": "Run the shell command: echo agent-watch-approval > /tmp/agent-watch-approval.txt",
                    "--live-input": "Before answering, use the request_user_input tool to ask which lifecycle state should be verified.",
                }
                turn = {"threadId": live_thread_id, "input": [{"type": "text", "text": prompts[live_mode]}]}
                if live_mode == "--live-approval":
                    turn["approvalPolicy"] = "untrusted"
                    turn["sandboxPolicy"] = {"type": "readOnly", "networkAccess": False}
                request(server, 4, "turn/start", turn)
        data = message.get("result", {}).get("data")
        if not isinstance(data, list):
            continue
        statuses = [item.get("status", {}) for item in data if isinstance(item, dict) and isinstance(item.get("status"), dict)]
        event, previously_active = event_from_statuses(statuses, previously_active)
        if event:
            write_event(event_dir, server.pid, event)
    if evidence_server is not None:
        evidence_server.shutdown()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        raise SystemExit(main())
    finally:
        stop()
