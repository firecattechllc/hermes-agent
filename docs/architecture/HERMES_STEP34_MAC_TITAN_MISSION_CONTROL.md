# Hermes Step 34 — Mac Titan Mission Control

## Purpose

Hermes Desktop is the primary governed interaction surface for Titan Hermes
(“Little Sister”). The existing Mac Hermes session remains the central
workspace. Titan appears as a persistent, collapsible pane on the right and
uses the Step 32 Mac ↔ Titan communication contract without adding remote-shell
or terminal authority.

## Configuration

Titan connectivity is disabled until both values are supplied to the desktop
process:

```bash
export HERMES_LINK_TITAN_URL="https://titan.<tailnet>.ts.net"
export HERMES_LINK_TOKEN="<resolved by the operator's secret manager>"
```

Do not place either a real private hostname or token in source control,
screenshots, issue reports, or logs. The Electron boundary accepts loopback,
private IPv4/Tailscale ranges, or Tailscale DNS (`*.ts.net`) only. Non-loopback
connections require HTTPS. Public endpoints fail closed before a request is
made.

The bearer token remains in Electron. The renderer receives a narrow typed IPC
capability for the six Step 32 operations and never receives the token or a
generic authenticated fetch function.

## Launch and connectivity check

From the repository root:

```bash
npm --workspace apps/desktop run dev
```

Open the Titan icon in the upper-right toolbar. The header reports connecting,
online, offline, degraded, or unauthorized. “Ask status” calls Step 32
`GET /status`; “Show queue” calls `GET /queue`; and “Show latest report” calls
`GET /reports/latest`. Missing fields display as unavailable and are not
inferred.

For an independent contract check:

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hermes_link -q
```

## Chat, tasks, and lessons

Chat, Task, and Lesson modes build schema-version-1 Step 32 envelopes with
stable message, correlation, and conversation identifiers. Requests use only
the allowlisted `/chat`, `/task`, and `/lesson` routes. The drawer does not
expose SSH, a terminal, arbitrary HTTP paths, filesystem access, or command
execution.

The renderer rejects requests that explicitly ask for sudo/root access,
production deployment, external publishing, spending, secret access,
unrestricted shell access, destructive operations, financial execution, or
external communications. Titan remains responsible for authoritative Step 32
schema and policy validation.

## Offline queue and retry

Messages are written to the renderer's versioned, Titan-specific persisted
conversation store before delivery. Retryable timeout or connectivity failures
remain visibly queued with their original message and correlation identifiers.
While the drawer is mounted, status and queue polling retries queued messages
after reconnection. Step 32's immutable message ID provides duplicate
suppression on Titan.

Titan remains independently operational when the Mac app is closed. The
desktop queue is an outbound convenience cache, not Titan's source of truth.
Titan's Step 34 consumer atomically claims eligible Mac-originated delivered
envelopes in the Step 32 journal. Chat uses a loopback-only Ollama adapter;
tasks and lessons return governed receipts without starting execution. Replies
use a deterministic ID derived from the inbound message, preserve correlation
and conversation IDs, and are fsynced before the inbound message is
acknowledged. An expired claim can therefore be recovered after restart without
creating a second reply. The desktop polls `/queue`; no streaming contract was
added.

## Titan runtime configuration

The supported Titan entry point is:

```bash
/opt/hermes/current/venv/bin/python -m hermes_cli.hermes_link.runtime
```

It binds only to `127.0.0.1`. Configuration is environment-only:

| Variable | Safe default | Purpose |
| --- | --- | --- |
| `HERMES_LINK_TOKEN` | none, required | Existing bearer secret |
| `HERMES_LINK_QUEUE_PATH` | `~/.hermes/link-service/queue` | Existing Step 32 journal directory |
| `HERMES_LINK_PORT` | `9320` | Loopback API port |
| `HERMES_LINK_OLLAMA_URL` | `http://127.0.0.1:11434` | Loopback-only Ollama endpoint |
| `HERMES_LINK_OLLAMA_MODEL` | `qwen3:8b` | Approved local model |
| `HERMES_LINK_MODEL_TIMEOUT_SECONDS` | `60` | Explicit inference deadline |
| `HERMES_LINK_MAXIMUM_INPUT_CHARS` | `16000` | Chat input bound |
| `HERMES_LINK_MAXIMUM_OUTPUT_CHARS` | `16000` | Persisted reply bound |
| `HERMES_LINK_MAXIMUM_OUTPUT_TOKENS` | `2048` | Ollama generation bound |
| `HERMES_LINK_MAXIMUM_RETRIES` | `3` | Retry/dead-letter bound |
| `HERMES_LINK_CLAIM_LEASE_SECONDS` | `120` | Restart recovery lease |
| `HERMES_LINK_POLL_INTERVAL_SECONDS` | `1` | Idle queue polling interval |

The Ollama URL rejects non-loopback hosts and authentication material. Requests
disable thinking and expose no tools. Hidden reasoning is neither requested nor
persisted.

For the current Titan layout, deploy the reviewed checkout without changing the
token, queue directory, Tailscale Serve, DNS, HTTPS, or authentication:

```bash
sudo -u hydra /opt/hermes/current/venv/bin/python -m compileall -q \
  /opt/hermes/current/hermes_cli/hermes_link
sudo systemctl restart hermes-link.service
sudo systemctl status --no-pager hermes-link.service
```

If the existing service unit still invokes
`~/.hermes/link-service/runtime/run_titan_link.py`, replace only that script's
application construction with `hermes_cli.hermes_link.runtime.build_app()` or
update `ExecStart` to the module command above. Preserve its existing
environment file and loopback/Serve configuration. Back up the unit or runtime
script before changing it.

## Live reply-cycle certification

Resolve the bearer token through the existing secret manager without printing
it, then run from Mac:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${HERMES_LINK_TOKEN}" \
  "${HERMES_LINK_TITAN_URL}/status" | jq '{node_id, presence, queue_counts}'
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${HERMES_LINK_TOKEN}" \
  "${HERMES_LINK_TITAN_URL}/queue" |
  jq '[.messages[] | select(
    .sender_node == "titan-hermes" and
    .recipient_node == "mac-hermes"
  ) | {message_id, correlation_id, conversation_id, message_type, delivery_state}]'
```

Send one new uniquely identified chat through the desktop drawer. Certify that
exactly one Titan reply has the same correlation and conversation IDs and that
the original is `acknowledged`. Restart the service once and repeat the queue
query; the reply count and reply message ID must remain unchanged. Do not expose
the token or message bodies in captured evidence.

## Troubleshooting

- **Offline:** confirm Tailscale reachability and that the private Titan service
  is running. The app does not fall back to a public endpoint.
- **Unauthorized:** rotate/provision the dedicated Step 32 application token
  and restart the desktop process so its environment is refreshed.
- **Degraded:** inspect the structured `degraded_components` status value and
  the latest governed report.
- **Queued messages do not deliver:** verify `/status` succeeds, then use “Show
  queue.” Stable message IDs are preserved across retries.
- **Malformed response:** the client marks the connection degraded and does not
  invent missing values. Check the Titan service version and Step 32 schema.
- **Claimed message after a crash:** wait for the configured claim lease. The
  restarted worker reconciles a durable deterministic reply or safely retries.

Current limitations remain cancellation, streaming, report history, attachment
transport, and automatic privileged task/lesson execution. The UI does not
simulate those capabilities.
