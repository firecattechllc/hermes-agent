# Governed Hydra Live repair operator playbook

This capability operates on a configured target and runtime-owned secret reference. It never embeds an address, credential, or private key in a proposal or evidence artifact, and it exposes command identifiers rather than an unrestricted shell.

## Workflow

1. Configure a `RemoteTarget` using either an SSH host alias or a configured endpoint and a `SecretReference` (`env`, `keyring`, `file`, or `runtime`). Keep the referenced value outside Git and prompts.
2. Run the read-only discovery catalogue. Persist only `MaintenanceEvidence`, which redacts credentials, authorization headers, environment secrets, private keys, machine/boot IDs, and configured private addresses.
3. Run `diagnose_hydra_live`. Deterministic checks must substantiate model advice before a proposal exists.
4. Generate one or both playbooks. The heartbeat playbook atomically patches the governed script after snapshotting it. The duplicate-Tailscale playbook defaults to disabling the stale Snap service while preserving APT Tailscale; removal is a separately classified destructive proposal.
5. Review the exact proposal checksum, affected services, downtime, validation commands, rollback command, and approval scopes. Supply approvals bound to that checksum.
6. Execute through `GovernedMaintenanceExecutor`. It creates and validates a rollback manifest before the first mutation, records sanitized evidence, and invokes the single rollback operation on a failed or partial step.
7. Certify from fresh evidence. Success requires Hydra Live active, the expected process owning TCP 3130, three consecutive successful heartbeat timer executions, healthy connected APT Tailscale, stale Snap absent or disabled, preserved SSH, no unexpected failed services, and secret-free evidence.

## Approval policy

Separate approval scopes cover changes under `/opt/hydra-os`, systemd units, sudoers, package disable, package removal, Tailscale restart, SSH restart, firewall mutation, and reboot. An approval is valid only for the exact proposal ID and checksum. Missing or stale approval fails before snapshots or mutation.

For this implementation/certification run, use fakes only. Do not connect to Hydra Live, remove packages, change firewall policy, restart SSH/Tailscale, reboot, or deploy.

## Rollback

Every changed file snapshot records path, opaque snapshot reference, owner, group, mode, checksum, and capture time. The manifest fixes reverse-order rollback command IDs before mutation. A transport provides one governed `rollback(manifest)` operation that restores file and service/package state and returns sanitized evidence.
