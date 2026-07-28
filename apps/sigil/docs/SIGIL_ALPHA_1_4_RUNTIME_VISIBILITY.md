# Sigil Alpha 1.4 Runtime Visibility

Sigil Alpha 1.4 exposes the governed local paper runtime as a deterministic,
read-only visibility projection. The projection cannot submit an order or grant
execution authority.

## Operational states

- `running`: automation is active and a local paper cycle is scheduled.
- `paused`: automation will not advance until the owner explicitly resumes it.
- `stopped`: automation will not advance until the owner explicitly starts it.
- `blocked`: automation cannot advance because a critical governance condition
  is unresolved.

The desktop also displays the paper-only environment, connection state,
automation mode, completed cycle count, last and next cycle timestamps, record
counts, latest proposal and execution status, and the next governed action.

## Health meanings

- `healthy`: runtime ledger, balances, orders, connection, and reconciliation
  checks are consistent.
- `degraded`: the runtime remains observable, but a connection, service, or
  consistency check requires attention.
- `blocked`: recovery, corruption, or another fail-closed health result prevents
  execution from advancing.

The projection retains the more specific backend health value in `raw_health`.

## Blocking reason codes

| Code | Meaning |
| --- | --- |
| `automation_paused` | The owner manually paused automation. |
| `automation_safety_paused` | Runtime health or authorization triggered a safety pause; manual resume is required after recovery. |
| `automation_stopped` | The owner stopped automation. |
| `authorization_required` | No active paper authorization exists. |
| `authorization_revoked` | Paper authorization was revoked. |
| `authorization_expired` | Paper authorization expired. |
| `runtime_health_degraded` | Runtime health is degraded. |
| `runtime_health_recovery_required` | Reconciliation or recovery is required. |
| `runtime_health_corrupt` | Runtime state failed an integrity or consistency check. |
| `runtime_health_locked` | Runtime state is unavailable due to a lock condition. |
| `services_degraded` | Connection or required services are degraded. |
| `execution_authorization_false` | Real broker execution authorization is false. |
| `broker_submission_unavailable` | Real broker transport is unavailable. This is informational for local paper simulation. |

## Local paper execution and broker submission

`paper_execution_available` describes whether the governed local simulator has
healthy state and active paper authorization. It does not imply access to a
broker. `broker_submission_available` describes the separate real-broker
transport boundary and remains false in Alpha 1.4. The desktop presents broker
unavailability as a separate informational safety fact, not as a local paper
execution failure.

## Safety pause and recovery

When a running runtime becomes unhealthy, Sigil records a `safety_paused` audit
event, clears the next scheduled cycle, and persists `pause_cause: safety` with
the health reason. The desktop labels this as a safety-triggered pause rather
than a manual owner action.

If health later recovers, the health indicator returns to healthy but automation
remains paused. Sigil never silently restarts. The owner must explicitly resume
automation, which clears the prior pause cause and creates normal control audit
evidence.

## Acceptance tests

Backend acceptance covers healthy running, manual pause, stop, inactive
authorization, unhealthy auto-pause, broker/local-paper separation, recovery
without automatic resume, and safe upgrade of persisted Alpha 1.3 schema state.

Desktop acceptance covers runtime status, cycle counts and timestamps, the
paused/stopped no-schedule state, blocking reasons, newest-first audit events,
paper-versus-broker wording, safety-versus-manual pause labeling, and continued
Start/Pause/Resume/Stop control behavior.

Release certification commands:

```text
PYTHONPATH=apps/sigil/src .venv/bin/python -m pytest apps/sigil/tests -q
npm run typecheck --workspace @firecattechnology/sigil-desktop
npm run test --workspace @firecattechnology/sigil-desktop
npm run lint --workspace @firecattechnology/sigil-desktop
npm run build --workspace @firecattechnology/sigil-desktop
```
