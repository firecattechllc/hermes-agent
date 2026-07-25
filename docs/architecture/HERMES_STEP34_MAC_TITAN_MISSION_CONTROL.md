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
Step 32 does not yet provide cancellation, automatic background replay while
the desktop process is stopped, streaming, report history, or attachment
transport; the UI does not simulate those capabilities.

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
