# Governed Hydra Live Stabilization and Repair Workflow

## Objective

Teach Hermes to inspect, propose, approve, execute, roll back, and certify repairs on Hydra Live without uncontrolled shell access or silent destructive changes.

## Verified incident context

Hydra Live inventory captured on 2026-07-21 showed:

- `hydra-live.service` is enabled and healthy, running `/usr/bin/python3 /opt/hydra-os/live/server/hydra-lived` on TCP port `3130`.
- `hydra-fleet-heartbeat.service` fails every minute because `/opt/hydra-os/scripts/hydra-fleet-heartbeat-daemon` invokes interactive `sudo` while running as the non-interactive systemd user `hydra`.
- Two Tailscale installations exist:
  - APT `tailscaled.service` version `1.98.8`, active and connected.
  - Snap `snap.tailscale.tailscaled.service` version `1.88.4`, failed and redundant.
- UFW is inactive.
- System state is `degraded` because the heartbeat service and stale Snap Tailscale service are failed.

## Required Hermes capability

### 1. Discovery

- Connect using a host alias or configured endpoint; no hard-coded secrets.
- Collect service state, unit definitions, journal excerpts, process ownership, listening sockets, package sources, firewall state, timers, and rollback-relevant files.
- Redact credentials, tokens, private-key material, environment values, machine IDs, boot IDs, and sensitive addresses from persisted evidence.

### 2. Diagnosis

- Classify findings and produce evidence-linked root causes.
- For this incident, detect:
  - interactive `sudo` inside a systemd oneshot workflow;
  - duplicate APT/Snap Tailscale installations;
  - healthy ownership of TCP 3130 by `hydra-live.service`.

### 3. Plan

Generate an explicit repair plan, risk rating, affected services, expected downtime, validation commands, and rollback steps.

Default proposed remediation:

- Inspect and patch the heartbeat daemon to remove unnecessary `sudo`, or move narrowly required privilege into a root-owned helper/systemd unit rather than broad passwordless sudo.
- Preserve the working APT Tailscale state.
- Disable and remove the redundant Snap Tailscale package only after approval.
- Do not alter firewall policy in the same change unless separately approved.

### 4. Approval gates

Require explicit user approval before:

- modifying `/opt/hydra-os` or `/etc/systemd/system`;
- changing sudoers;
- disabling or removing packages;
- restarting Tailscale or SSH;
- changing firewall rules;
- rebooting.

### 5. Execution

- Snapshot or copy every changed file with ownership, mode, checksum, and timestamp.
- Use bounded allow-listed commands.
- Apply atomic file replacement where possible.
- Run `systemctl daemon-reload` only when unit files change.
- Avoid restarting healthy Tailscale and SSH unless required.

### 6. Rollback

- Generate and validate a rollback manifest before mutation.
- Restore files, package/service state, ownership, and permissions.
- Provide a single governed rollback operation.

### 7. Certification

Capture before/after evidence and require:

- `hydra-live.service` active;
- TCP 3130 owned by the expected Hydra Live process;
- heartbeat service successful for at least three timer invocations;
- working APT `tailscaled.service` active and connected;
- stale Snap service absent or disabled without failed state;
- `systemctl --failed` empty or only explicitly accepted unrelated failures;
- SSH connectivity preserved;
- no secret values persisted in evidence.

## Architecture constraints

- GitHub remains source of truth for repair definitions, policy, tests, and evidence schemas.
- Runtime credentials remain on the runtime boundary and are referenced, never copied into GitHub or prompts.
- Hermes must distinguish read-only inspection, reversible mutation, destructive mutation, and high-risk connectivity changes.
- Model diagnosis is advisory until validated by deterministic checks and approval policy.
- The user remains final authority for package removal, firewall changes, connectivity changes, deployment, and reboot.

## Suggested implementation slices

1. Remote host target/config model and secret references.
2. Read-only SSH inspection adapter with command allow-list.
3. Fleet finding and evidence models.
4. Repair proposal, risk classification, and approval records.
5. Snapshot and rollback manifest support.
6. Governed execution engine.
7. Hydra Live heartbeat repair playbook.
8. Duplicate Tailscale cleanup playbook.
9. Certification suite and Mission Control visibility.
10. Tests for denial, timeout, partial execution, rollback, redaction, and connectivity preservation.

## Acceptance tests

- Unit tests prove read-only commands cannot mutate host state.
- Unapproved repair execution is rejected.
- Package removal, sudoers changes, firewall changes, Tailscale/SSH restarts, and reboot each require dedicated approval.
- Evidence redaction tests cover tokens, authorization headers, environment values, private keys, machine/boot identifiers, and configured private addresses.
- A simulated heartbeat incident produces the expected diagnosis and plan.
- A simulated failed step triggers deterministic rollback.
- Certification refuses success when heartbeat has not completed three consecutive timer runs.
- Certification refuses success if TCP 3130 changes ownership unexpectedly.
- Certification refuses success if SSH or active APT Tailscale connectivity is lost.

## Deliverables

- Implementation and tests in a dedicated branch.
- Operator documentation and Hydra Live playbook.
- Example sanitized before/after certification evidence.
- Draft PR; no merge or production deployment without user approval.
