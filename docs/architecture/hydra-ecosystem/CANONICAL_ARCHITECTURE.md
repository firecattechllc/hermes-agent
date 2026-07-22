# Canonical Hydra Ecosystem Architecture

## Status

- Architecture state: baseline approved for stabilization
- Evidence basis: direct discovery of MacBook, Prime, Titan, and Hydra Live
- Branch: `agent/hydra-ecosystem-architecture-baseline`
- Scope: current four-machine private engineering and application platform

This document defines the authoritative responsibility, trust, communication,
deployment, and recovery model for the Hydra ecosystem.

## Architectural objective

The Hydra ecosystem is a private, governed, four-machine platform designed to:

- keep the user as final authority over merges, releases, deployments, spending,
  credentials, and high-risk actions;
- separate engineering control, identity and routing, execution, and application
  runtime responsibilities;
- let Hermes coordinate work without granting any single worker unrestricted
  authority;
- use GitHub as the governed source of truth for code, evidence, releases, and
  rollback packages;
- support safe recovery when any one machine is offline or compromised;
- provide a path from the current Mac-hosted engineering environment to an
  always-on private engineering company in a box.

## Canonical topology

```text
                              USER
                               |
                   approvals / policy / recovery
                               |
                   +-----------v-----------+
                   |      Mission Control  |
                   | phone / Mac interface |
                   +-----------+-----------+
                               |
                       governed requests
                               |
                 +-------------v--------------+
                 | MacBook Engineering Authority|
                 | Hermes control plane today   |
                 | source checkout and release  |
                 +------+------+----------------+
                        |      |
              signed plan      | governed artifacts
              and policy       | GitHub branches, PRs,
                        |      | releases, evidence
                        |      |
          +-------------v--+   +----------------------+
          | Hydra Prime     |                          |
          | identity,       |                          |
          | membership,     |                          |
          | routing, policy |                          |
          +--------+--------+                          |
                   | trusted overlay                   |
                   | and service identity              |
          +--------v--------+                 +--------v--------+
          | Hydra Titan     |                 | GitHub          |
          | durable worker  |<--------------->| source of truth |
          | build/test/jobs |                 | evidence/release|
          +--------+--------+                 +-----------------+
                   |
             sealed release package
             plus approval evidence
                   |
          +--------v--------+
          | Hydra Live      |
          | isolated app and|
          | local AI runtime|
          +-----------------+
```

## Node responsibilities

### 1. MacBook — engineering authority and current Hermes control plane

The MacBook is the current authoritative engineering workstation.

Canonical responsibilities:

1. Host the authoritative Hermes control plane until Titan is certified for
   durable orchestration.
2. Receive user approvals and translate them into governed work requests.
3. Create architecture decisions, implementation plans, release candidates, and
   rollback plans.
4. Maintain local working copies for interactive engineering and emergency repair.
5. Sign or attest release decisions when signing is introduced.
6. Serve as the break-glass recovery console for Prime, Titan, and Hydra Live.

The MacBook must not remain the permanent single point of operation.

### 2. Hydra Prime — identity, membership, routing, and policy authority

Prime is the ecosystem control gateway, not the general-purpose build worker.

Canonical responsibilities:

1. Maintain device membership and trusted-node identity.
2. Provide or coordinate private overlay routing through Tailscale.
3. Host lightweight policy, enrollment, revocation, health, and fleet metadata.
4. Maintain the canonical device registry.
5. Reject unknown, revoked, stale, or policy-noncompliant nodes.
6. Store encrypted identity backups and small critical control-plane records.
7. Provide fleet-presence and heartbeat information to Mission Control.

Prime must not:

- perform unrestricted autonomous code execution;
- hold every application secret in plaintext;
- replace GitHub as source of truth;
- become the primary heavy build, model, or application runtime.

### 3. Hydra Titan — durable engineering execution plane

Titan is the always-on worker and future permanent Hermes command center.

Canonical responsibilities:

1. Execute governed Hermes jobs after admission by the control plane.
2. Run builds, tests, static analysis, packaging, evidence collection, and
   scheduled maintenance in isolated workspaces.
3. Host long-lived worker agents and queues after certification.
4. Pull approved source only from GitHub or verified artifact storage.
5. Produce immutable job evidence, checksums, logs, tests, and manifests.
6. Build sealed Hydra Live and Sigil deployment packages.
7. Maintain caches and disposable build environments.
8. Support automatic retry and recovery within approved resource limits.

Titan may prepare work, but cannot independently authorize:

- production deployment;
- protected-branch merges;
- release publication;
- spending outside approved budgets;
- credential rotation;
- destructive infrastructure changes.

### 4. Hydra Live — isolated application and local AI runtime

Hydra Live is an ARM64 Ubuntu VMware guest and is an application runtime
boundary, not an engineering authority.

Canonical responsibilities:

1. Run the Hydra Live desktop or application server.
2. Host local inference and user-facing runtime components.
3. Run approved Sigil OS or Sigil application workloads later.
4. Accept only checksum-verified or signed release packages produced by Titan
   and approved by the user.
5. Report health, deployed version, package identity, and rollback readiness.
6. Preserve application data only in documented persistent volumes.
7. Remain replaceable from a clean VM image plus encrypted backups.

Hydra Live must not:

- build production releases from unreviewed source;
- own ecosystem identity or enrollment;
- merge code or approve releases;
- receive broad repository credentials;
- be the sole location of important user data;
- expose application ports without documented authentication and firewall policy.

## Control-plane and execution split

### Control plane

Initially hosted on the MacBook, later eligible for migration to Titan.

Responsibilities:

- accept user intent;
- plan work;
- apply policy;
- assign scoped roles;
- admit or reject execution;
- enforce budgets and approvals;
- verify evidence;
- prepare merge and release recommendations;
- expose Mission Control status.

### Execution plane

Primarily hosted on Titan.

Responsibilities:

- clone exact refs;
- create isolated workspaces;
- run agents and tools;
- execute tests and scans;
- assemble packages;
- emit evidence;
- clean disposable state.

Hydra Live is a deployment target. Prime is a policy and identity dependency.

## Source of truth

GitHub is authoritative for:

- repositories and protected branches;
- pull requests and review history;
- architecture decisions;
- test and validation evidence;
- release manifests and tags;
- deployment packages or immutable artifact references;
- rollback instructions.

No node-local checkout is authoritative by itself.

## Trust boundaries

### User authority

Only the user may grant final approval for:

- protected-branch merge;
- production release;
- Hydra Live deployment;
- expenditure outside standing budgets;
- device enrollment and revocation;
- high-risk or destructive actions;
- policy changes expanding agent authority.

### Prime identity domain

Prime determines which devices and service identities belong to the fleet.
Network reachability alone does not grant authorization.

### Titan worker domain

Titan may execute model-generated or untrusted code only in isolated,
resource-limited workspaces using scoped, short-lived credentials.

### Hydra Live runtime domain

Hydra Live receives deployable artifacts, not unrestricted engineering access.
Runtime secrets remain inside the runtime boundary.

### External providers

Model providers, brokerage APIs, market-data APIs, and registries are outside
the trusted fleet and require scoped keys, rate limits, cost limits, and policy.

## Canonical communication flows

### Engineering flow

```text
User approval
  -> Hermes control plane
  -> policy and budget admission
  -> Titan isolated execution
  -> tests, scans, evidence, package
  -> GitHub pull request or release candidate
  -> independent verification
  -> user merge or release approval
```

### Hydra Live deployment flow

```text
Approved GitHub release
  -> Titan package builder
  -> checksum, manifest, SBOM, rollback bundle
  -> user deployment approval
  -> Hydra Live deployment agent
  -> health and acceptance checks
  -> deployment evidence
  -> retain previous known-good version
```

### Identity flow

```text
User enrolls device
  -> Prime verifies policy
  -> Tailscale and service identity established
  -> least-privilege role assigned
  -> heartbeat and inventory reported
  -> user may revoke at any time
```

### Recovery flow

```text
Failure detected
  -> stop or isolate workload
  -> preserve evidence
  -> identify last known-good release
  -> approve rollback when required
  -> restore immutable package and backup
  -> run acceptance tests
  -> close incident with audit record
```

## Deployment contract

Every deployable Hydra or Sigil release should contain:

- release identifier and Git commit SHA;
- build timestamp and builder identity;
- target platform and architecture;
- dependency locks;
- checksums;
- software bill of materials;
- test and scan results;
- configuration schema version;
- migration plan when applicable;
- rollback package and instructions;
- required secrets by reference, never value;
- approval record;
- post-deployment acceptance checks.

Hydra Live must reject packages with missing or invalid required evidence.

## Data and secret placement

### MacBook

Interactive developer credentials and emergency recovery material may live here
using the operating-system keychain, never Git.

### Prime

Encrypted fleet identity, policy, revocation, and recovery metadata. Prime must
not become a universal secrets store.

### Titan

Short-lived worker credentials and narrowly scoped package-signing access.
Build logs must redact secrets.

### Hydra Live

Runtime-only application secrets and persistent application data through a
managed configuration path, excluded from images, repositories, and logs.

## Availability and failure behavior

### MacBook unavailable

- existing Prime networking and Hydra Live services may continue;
- new high-risk engineering approvals pause;
- Titan may finish already admitted safe jobs;
- no automatic production release occurs.

### Prime unavailable

- existing overlay sessions may temporarily continue;
- new enrollment and revocation updates pause;
- privileged requests fail closed after identity expiry.

### Titan unavailable

- engineering jobs pause or explicitly fall back to MacBook;
- Hydra Live continues the last known-good release;
- no evidence is fabricated.

### Hydra Live unavailable

- engineering and identity services continue;
- deployment is blocked;
- restore a clean VM from known-good image and backup.

## Current stabilization risks

### Hydra Live

1. `hydra-fleet-heartbeat.service` is failed.
2. Duplicate Snap Tailscale is enabled and failed while native Tailscale runs.
3. Ports 3000, 3099, and 3130 require documented ownership and exposure intent.
4. The process owning port 3130 is not yet identified.
5. The installer ISO remains attached.
6. Docker persistence, restart policy, and backup paths are undocumented.
7. `/home/hydra/hydra-live` needs application-level inspection.

### Titan

1. Hermes was not observed on the active path.
2. Existing listeners need ownership and exposure mapping.
3. NVMe and vault usage need a defined storage layout.
4. Worker isolation, resource limits, and recovery are not certified.

### Prime

1. Existing containers and services must be classified.
2. Vault backup, restore, encryption, and integrity need acceptance tests.
3. Exit-node behavior must be governed.
4. Prime should be reduced to a minimal high-trust service set.

### MacBook

1. The MacBook remains the current Hermes dependency.
2. Local backup and credential recovery need documentation.
3. Free disk space needs monitoring during builds and artifact retention.

## Stabilization sequence

### Phase 1 — freeze and inventory

- record service units, process ownership, containers, volumes, firewall rules,
  and configuration paths;
- classify every service as canonical, temporary, legacy, or unknown;
- identify all persistent data and backup requirements.

Exit criteria:

- every listener has an owner and purpose;
- every persistent volume has a backup decision;
- every enabled custom service has an owner and recovery procedure.

### Phase 2 — repair Hydra Live health

- inspect and repair or intentionally disable the fleet heartbeat;
- remove duplicate Tailscale after confirming native service operation;
- identify port 3130;
- verify UFW policy;
- document Open WebUI, Hydra Cleaner, Ollama, and `hydra-live.service`;
- detach installer ISO;
- create a VMware snapshot;
- prove reboot recovery and service health.

Exit criteria:

- system state is healthy;
- only one Tailscale service is enabled;
- exposure is intentional;
- reboot and rollback tests pass.

### Phase 3 — narrow Prime

- classify and reduce Prime services;
- formalize device registry and revocation;
- define encrypted vault backup and restore;
- document exit-node policy;
- expose a minimal authenticated health and identity interface.

Exit criteria:

- Prime has a documented minimal service set;
- enrollment and revocation tests pass;
- vault restore is proven.

### Phase 4 — certify Titan as worker

- install Hermes from a pinned ref or release;
- create a dedicated Hermes service account;
- define workspace, cache, evidence, artifact, and vault directories;
- enforce CPU, memory, disk, time, network, and spending budgets;
- add systemd supervision and health reporting;
- run a non-production build/test/package workflow;
- test failed-job cleanup and reboot recovery.

Exit criteria:

- Titan executes an admitted job reproducibly;
- evidence matches the source SHA;
- worker failure cannot authorize or deploy a release;
- recovery acceptance tests pass.

### Phase 5 — release pipeline to Hydra Live

- define signed package and manifest formats;
- create a least-privilege deployment agent;
- retain the previous known-good package;
- implement preflight, deployment, health, acceptance, and rollback;
- require user approval for production deployment.

Exit criteria:

- a test package deploys and rolls back without manual file surgery;
- Hydra Live reports exact deployed identity;
- failed acceptance stops promotion and offers rollback.

### Phase 6 — migrate durable Hermes control to Titan

- keep MacBook as recovery authority;
- move scheduling, queues, and worker coordination to Titan;
- retain final user approvals outside autonomous workers;
- prove operation through MacBook disconnect and Titan restart;
- document break-glass return to Mac-hosted control.

Exit criteria:

- Titan operates Hermes continuously;
- MacBook loss does not corrupt active jobs;
- user retains final authority;
- recovery to MacBook is tested.

## Architecture invariants

1. GitHub is source of truth; machines are execution and recovery nodes.
2. User approval is mandatory for merges, releases, deployments, spending, and
   high-risk actions.
3. Prime owns identity and policy, not heavy execution.
4. Titan owns durable engineering execution, not final authorization.
5. Hydra Live owns application runtime, not engineering control.
6. Every deployment maps to an exact source revision and evidence set.
7. Every production deployment has a known rollback path.
8. Secrets never enter repositories, prompts, evidence, or logs in plaintext.
9. Network reachability does not equal authorization.
10. Agent output remains advisory until validated by policy, tests, evidence,
    and approvals.
11. Unknown services cannot silently become canonical.
12. Recovery procedures must be tested.

## Immediate next actions

1. Collect targeted Hydra Live evidence for heartbeat, duplicate Tailscale, port
   3130, UFW, custom services, container persistence, and reboot behavior.
2. Map Titan listener ownership, storage layout, and Hermes readiness.
3. Minimize Prime services and prove device registry, exit-node, and vault recovery.
4. Document MacBook backup, credential recovery, and break-glass control.
5. Certify Titan worker execution and define the Hydra Live deployment contract.

No autonomous production deployment is permitted before these acceptance
conditions are met.
