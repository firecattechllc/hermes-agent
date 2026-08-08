# Fleet Unification — Stages 2 through 9

## Status of this document

This is the authoritative architecture record for Fleet Unification Stages
2 through 9, written during the branch `fleet-unification-stages2-9` off the
immutable Stage 1 baseline commit `e717a5187` ("Add fleet safety and
certification integrity gates"). It documents both what was built and — per
the discrepancy-recording obligation below — what pre-existing roadmap
evidence actually exists in this repository.

## 1. Roadmap discrepancy record (read this first)

**No pre-existing document in this repository defines "Fleet Unification
Stages 2 through 9" stage-by-stage.** This was verified by:

- searching the full repository for `fleet unification` (case-insensitive)
  across Markdown, Python, JSON, and YAML — the only two hits are
  `docs/certification/sigil-fleet-failover-certification.md` and
  `docs/certification/sigil-golden-master-v3.5.0-post-gamma.md`, both of
  which describe **Stage 1 only** and explicitly scope Stage 1 as a
  *certification-integrity correction*, not a feature-building stage:
  > "Fleet networking, Mission Control unification, and remote fleet
  > maintenance are explicitly out of scope for Fleet Unification Stage 1."
- searching `docs/roadmap/`, `.plans/`, `AGENTS.md`, and every `docs/`
  subdirectory for a Stage 2-9 definition — none exists.
- inspecting the branch list for a `fleet-unification-stage*` naming
  pattern beyond `fleet-unification-stage1-safety-certification` — none
  exists for stages 2-9.

What **does** exist, and what this Stage 2-9 effort is actually built from:

1. The task specification's own "KNOWN STAGE 2 REQUIREMENTS" section (A
   through H: unified event stream, fleet identity consolidation, Prime
   admission, unified evidence, shared health protocol, Sigil service
   contract, remote maintenance governance, fleet certification). This is
   treated as the authoritative Stage 2 definition, since no repository
   document contradicts or supersedes it.
2. A large, already-implemented set of governed subsystems on this branch
   under `hermes_cli/agent_roles/`, `hermes_cli/knowledge/`,
   `hermes_cli/hermes_link/`, and `apps/sigil/src/sigil/`, corresponding
   almost exactly to the capability list named in the task specification
   ("governed Hydra Live repair; fleet inventory; model routing; model
   execution; intelligence and efficiency; Titan local learning; Big
   Sister/Little Sister learning; Mac/Titan communication; whole-system
   knowledge graph; Mission Control"). These map to the git history of the
   (separate, unmerged) `step24`-`step34` branches by name and by content,
   but **that step-branch numbering is explicitly not the same as Fleet
   Unification stage numbering** (the task specification says so directly),
   and those capabilities were already merged onto this branch's ancestry
   independently of any "Fleet Unification" label.
3. `docs/architecture/hydra-ecosystem/CANONICAL_ARCHITECTURE.md`, which
   already names and scopes **"Prime"** as a *planned, unimplemented*
   ecosystem identity/membership/policy/routing authority — "the ecosystem
   control gateway" — with responsibilities that match the task
   specification's Stage 2C ("Prime admission service") almost verbatim,
   including "provide fleet-presence and heartbeat information to Mission
   Control" and "reject unknown/revoked/stale/noncompliant nodes." No code
   implementing Prime existed anywhere in the repository before this work.

**Resolution applied** (per the instruction to record the discrepancy and
implement the safest coherent interpretation): Stage 2 is implemented
exactly as specified in the task's own A-H list, under a new `hermes_cli/prime/`
package — the pre-named, pre-scoped, but previously empty "Prime" slot.
Stages 3 through 9 are defined here, for the first time, as the
**consolidation of the seven already-implemented fragmented capability
areas under the Stage 2 Prime control plane**, in the order those
capabilities were originally built (matching the step24-34 branch
ordering, since no other ordering evidence exists):

| Stage | Name | Consolidates |
|---|---|---|
| 3 | Fleet inventory unification | `hermes_cli.agent_roles.fleet_inventory` |
| 4 | Hydra Live / remote maintenance governance | `hermes_cli.agent_roles.remote_maintenance`, `hydra_live_playbooks` |
| 5 | Model routing & execution admission-awareness | `hermes_cli.agent_roles.model_routing`, `model_execution` |
| 6 | Intelligence & efficiency evidence linkage | `hermes_cli.agent_roles.intelligence_engine` and siblings |
| 7 | Learning hierarchy & Titan/Sigil fleet identity | `hermes_cli.agent_roles.learning_hierarchy` (Big/Little Sister), `sigil.ai.fleet`, `sigil.integrations.sentiment.*` (Titan) |
| 8 | Mac/Titan/Prime communication & knowledge graph | `hermes_cli.hermes_link`, `hermes_cli.knowledge` |
| 9 | Mission Control unification & fleet certification | cross-cutting: producer identity, whole-system certification |

This mapping is **this document's own construction**, not a rediscovered
prior roadmap — stated explicitly so a future reader does not mistake it
for pre-existing authority it does not have.

## 2. What "consolidation" means here, precisely

The seven capability areas above are each large, mature, independently
governed subsystems (fleet_inventory.py is 494 lines, remote_maintenance.py
413 lines, model_execution.py 544 lines, intelligence_engine.py plus five
sibling files, learning_hierarchy.py 593 lines, hermes_link 13 files,
knowledge/ 6 files backed by SQLite). Each already has its own tests,
its own append-only evidence store, and — for six of the seven — its own
`*_visibility.py` adapter already publishing into the *existing* Mission
Control `TelemetryEvent` stream (see `hermes_cli/mission_control/models.py`
and `hermes_cli/agent_roles/model_routing_visibility.py` for the canonical
example of that existing pattern).

Given that, **Fleet Unification Stages 2-9 do not re-implement any of these
seven subsystems.** Re-implementing already-certified, already-tested
governed logic inside a financial trading platform's fleet layer would be
reckless, would violate "avoid parallel identity, health, event, evidence,
policy, admission, certification ... systems," and was explicitly out of
scope for what a single session can safely and honestly complete.

Instead, Stage 2-9 consolidation means:

1. **Stage 2 builds the missing control-plane primitives** — canonical
   identity, a shared health protocol, a unified evidence layer, a
   deterministic admission service, a governed Sigil contract, extended
   remote-maintenance governance (windows/expiration/revocation), and fleet
   certification — none of which existed anywhere in the repository before
   this work (confirmed by exhaustive search; see Section 5.4).
2. **Stages 3-9 connect to Stage 2 via adapters**, not modification. Every
   adapter in `hermes_cli/prime/identity.py` and
   `hermes_cli/knowledge/mission_control_bridge.py` accepts the
   **pre-existing** object shapes from the seven subsystems and produces a
   canonical `FleetIdentity` / Mission Control event — it never edits the
   producing subsystem's own files. `hermes_cli/knowledge/` was the one
   subsystem with **zero** Mission Control integration before this work
   (confirmed by grep — zero `mission_control` references anywhere in that
   package); it now publishes via the event types (`knowledge_snapshot_recorded`,
   `knowledge_drift_recorded`) that were already reserved in the closed
   event-type set but never wired up.
3. **Genuinely new, deeper runtime integration** of Prime admission/health
   gating *inside* `model_execution.py._admit`, `remote_maintenance.py`'s
   executor, etc. is identified as real, concrete, honestly-reported
   remaining work in Section 8 — it was not attempted in this session
   because it requires modifying seven independently-certified subsystems'
   call signatures, which risks the "preserve backward compatibility"
   requirement and cannot be done safely without dedicated per-subsystem
   review beyond what a single session can respect.

## 3. Immutable Stage 1 baseline — what was preserved and how

Stage 1 (commit `e717a5187` and its ancestors `5bb9afc33`, `48c08880b`,
`c9d00eb3a`, `470cbf47a`) added two fail-closed CI gate scripts:

- `apps/sigil/scripts/verify_certification_evidence.py`
- `apps/sigil/scripts/verify_public_execution_isolation.py`

Neither script was modified, weakened, or bypassed by this work. Both are
invoked, unmodified, by `hermes_cli.prime.certification.run_stage1_regression`
(a subprocess call against the real files on disk — never mocked) and by
`tests/hermes_cli/test_prime/test_stage1_regression.py`, which fails the
whole test if either script's exit code is non-zero. `certify_fleet(...)`
refuses to ever produce a `CERTIFIED` status unless `stage1_regression_passed`
is positively `True` — `None` (not run) is treated identically to a failure.

The paper-only / broker-isolation boundary itself
(`apps/sigil/src/sigil/desktop_bridge/paper_execution.py`'s hard rejection
of any non-`"paper"` environment or `broker_submission=True` request, and
`verify_public_execution_isolation.py`'s AST-level ban on importing
`PublicEquityExecutionProvider` from production code) was not touched.
`hermes_cli/prime/sigil_contract.py` never imports from
`sigil.desktop_bridge` or `sigil.integrations.providers` at all — it only
defines the governed request/response envelope that would sit in front of
an eventual Sigil call, and locks `advisory`, `paper_only`,
`broker_submission_denied`, `execution_authority_denied`, and
`production_mutation_denied` to their only safe values via pydantic
validators that reject construction outright if any is set otherwise.

## 4. Architecture overview

```
                         ┌─────────────────────────────┐
                         │      hermes_cli.prime         │  Stage 2 control plane
                         │  identity / health / evidence │  (new this session)
                         │  admission / sigil_contract /  │
                         │  remote_maintenance_governance/│
                         │  certification / visibility    │
                         └───────────┬─────────────────┘
                                     │ adapters (read pre-existing shapes,
                                     │ never modify the producing module)
        ┌────────────────────────────┼────────────────────────────────────┐
        │                            │                                     │
┌───────▼────────┐   ┌───────────────▼──────────┐   ┌───────────────────▼──────┐
│ hermes_cli.     │   │ hermes_cli.agent_roles     │   │ hermes_cli.hermes_link /  │
│ knowledge       │   │ fleet_inventory,           │   │ apps/sigil (sigil.ai.fleet,│
│ (Stage 8/9,     │   │ remote_maintenance,        │   │ sigil.worker_contract,     │
│ now wired to    │   │ model_routing/execution,   │   │ sigil.certification —      │
│ mission_control)│   │ intelligence_engine,       │   │ Stage 1 baseline, untouched)│
│                 │   │ learning_hierarchy         │   │                             │
└─────────────────┘   └────────────────────────────┘   └─────────────────────────────┘
                                     │
                         ┌───────────▼─────────────┐
                         │ hermes_cli.mission_control │  pre-existing, reused as-is;
                         │ TelemetryEvent journal      │  6 new event types added to
                         │ (append-only, schema-versioned)│ its closed set
                         └──────────────────────────┘
```

## 5. Stage 2 control plane — component detail

### 5.1 Unified Mission Control event stream (A)

`hermes_cli/mission_control/models.py`'s `TelemetryEvent` already satisfied
nearly every literal requirement in the task specification before this
session — event ID, closed-set event type, schema version, timestamp,
correlation ID, causation ID, deterministic serialization (pydantic +
canonical JSON), strict validation (`extra` fields rejected only where the
subsystem models use `ConfigDict(extra="forbid")`; `TelemetryEvent` itself
already rejects unknown `event_type`/`severity`/`schema_version` via
`field_validator`s), and append-only semantics (enforced at the
`MissionControlStore` layer via `fcntl`-locked, `"a"`-mode-only file
writes with no update/delete API).

What this session added: 6 new event types
(`prime_identity_registered`, `prime_health_reported`,
`prime_admission_decided`, `prime_sigil_contract_invoked`,
`prime_remote_maintenance_decided`, `prime_fleet_certified`) to the closed
`_TELEMETRY_EVENT_TYPES` set, and wired `hermes_cli/knowledge/` — the one
subsystem that had never published anything — to the two event types
already reserved for it (`knowledge_snapshot_recorded`,
`knowledge_drift_recorded`) via `hermes_cli/knowledge/mission_control_bridge.py`.

No new event envelope type was created; `hermes_cli.prime.visibility` and
`hermes_cli.knowledge.mission_control_bridge` both build ordinary
`TelemetryEvent` instances and publish via the existing
`MissionControlService.append_event_once`, following the exact
`*VisibilityAdapter`/`*VisibilityService` convention already used by every
other governed subsystem.

**Note on "producer identity":** `TelemetryEvent` has no dedicated
`producer_identity_id` field; provenance is carried via `project_id` /
`agent_id` plus a `payload["source"]` string convention used by every
existing producer. Rather than changing `TelemetryEvent`'s schema (a
breaking change to every existing event in every existing journal), Prime's
events carry the producer's canonical `FleetIdentity` reference inside
`payload["source"] = "prime"` plus a fully-serialized identity/decision
object in the payload — consistent with the existing convention rather than
a parallel one.

### 5.2 Fleet identity consolidation (B) — `hermes_cli/prime/identity.py`

Canonical `FleetIdentity` (kind, natural_key, source, source_reference,
content-addressed `identity_id`, revocation lifecycle). Adapters exist for
every pre-existing identity shape discovered in this repository:
`sigil.ai.fleet.FleetNodeIdentity`, `RemoteTarget`/`InventoryTarget` (two
near-duplicate shapes already in `agent_roles`), Hermes Link node/role
pairs, and Big Sister/Little Sister learning-hierarchy nodes. `IdentityRegistry`
is conflict-resistant: registering the same canonical identity from two
different legacy sources without `allow_supersede=True` raises
`IdentityConflictError` rather than silently overwriting.

Identity grants no authority — enforced structurally by having no
authority-shaped field anywhere on `FleetIdentity`, and documented via a
`grants_no_authority()` no-op method that exists purely so a grep for its
name surfaces every call site that deliberately did not treat identity as
authorization.

### 5.3 Prime admission service (C) — `hermes_cli/prime/admission.py`

`PrimeAdmissionService.evaluate()` is a pure, deterministic function:
identical `AdmissionRequest` + `now` always produces the identical
`AdmissionDecision` (content-addressed `decision_id`). Default is denied:
every one of identity-unknown, identity-revoked, quarantined,
unsupported-policy-version, missing-health, stale/unusable-health,
non-`CERTIFIED` certification status, missing certification evidence
reference, and active restrictions independently produces a reason code and
a non-`ADMITTED` outcome. Quarantine produces a distinct `QUARANTINED`
outcome rather than being folded into `DENIED`. Admission decisions expire
(`revalidate_after`) and `is_current(now)` must be re-checked by every
downstream consumer (`sigil_contract.py` and
`remote_maintenance_governance.py` both do this).

### 5.4 Unified evidence layer (D) — `hermes_cli/prime/evidence.py`

`EvidenceRecord` (content-addressed `evidence_id`, schema version,
producer/subject identity, provenance, correlation/causation, sensitivity
tier, redacted summary) plus `PrimeEvidenceStore`, an append-only,
`fcntl`-locked, hash-chained JSONL journal. The storage pattern
(`sequence` / `previous_record_hash` / `entry_hash`, atomic writes,
symlink guards, full-chain re-verification on every read) is a direct copy
of the pattern already used by `sigil.worker_contract.DurableWorkerContractStore`
— deliberately, so this is the *same* evidence-storage convention applied
to new content rather than a sixth incompatible one. Before this session,
the repository already had at least four independent evidence-storage
implementations sharing this hash-chain shape
(`DurableWorkerContractStore`, `sigil.ai.fleet.DurableFleetStore`,
`hermes_link.security.CredentialEvidenceStore`, and the per-subsystem
`*_store.py` files in `agent_roles`); none of those were replaced.
`ExternalEvidenceLink` lets a Prime evidence record point at any of those
pre-existing stores (or at Sigil's markdown-based Stage 1 certification
evidence) by reference and content hash, without re-storing their content.

### 5.5 Shared health protocol (E) — `hermes_cli/prime/health.py`

`HealthReport` separates liveness, readiness, per-dependency health,
degradation, quarantine, and admission/certification-validity echoes as
independent fields — none implies another. `evaluate_health()` returns
every applicable finding (stale, expired, clock-skew, unsupported-version,
quarantined, not-alive, not-ready, check-failed) rather than collapsing to
a boolean, and `is_usable_for_admission()` is the single fail-closed gate
every other Stage 2 module calls. Adapters exist for the two pre-existing
health-adjacent precedents: `hermes_link.models.HermesLinkStatus`
(component health / presence) and `sigil.ai.fleet.FleetNodeHealth`
(heartbeat freshness, whose own `freshness()` classification is reused
rather than re-derived, so the two systems cannot disagree about what
"stale" means for the same observation).

### 5.6 Sigil service contract (F) — `hermes_cli/prime/sigil_contract.py`

`SigilContractRequest`/`SigilContractResponse` — a typed, versioned,
governed envelope. `advisory`, `paper_only`, `broker_submission_denied`,
`execution_authority_denied`, and `production_mutation_denied` are locked
to their only safe values by pydantic `model_validator`s that reject
construction otherwise (not defaults — a caller cannot opt out).
`SigilContractResponse` likewise can never set
`execution_authority_granted`/`broker_submission_granted` to `True`.
Operations are a closed allow-list (`SUPPORTED_SIGIL_OPERATIONS`), all
advisory-only. `evaluate_sigil_contract_request()` gates on both parties'
current admission and usable health before returning `admitted=True`. This
module composes `sigil.worker_contract`'s existing provider-neutral job
contract and `sigil.ai.fleet`'s existing `_no_authority()` invariant rather
than re-implementing either.

### 5.7 Remote maintenance governance (G) — `hermes_cli/prime/remote_maintenance_governance.py`

Adds what `hermes_cli.agent_roles.remote_maintenance` did not have:
maintenance windows, approval expiration (age-bounded,
`DEFAULT_MAX_APPROVAL_AGE_SECONDS = 3600`), and out-of-band revocation
(`ApprovalRevocation`, since `RepairApproval` itself has no `revoked`
field and is immutable by design). `evaluate_maintenance_request()` is a
pre-flight gate: an `ADMITTED` `MaintenanceDecision` only means a caller
*may proceed* to the pre-existing, unmodified
`GovernedMaintenanceExecutor.execute(...)` — this module never calls that
executor and never touches a `MaintenanceAdapter` transport, so it cannot
become a second, competing execution path. `missing_approval_scopes()`
deliberately mirrors (rather than imports, since it is private) the exact
approval-matching semantics already inside
`GovernedMaintenanceExecutor.execute`.

### 5.8 Fleet certification (H) — `hermes_cli/prime/certification.py`

`certify_fleet()` is a pure function deriving `FleetCertificationStatus`
(`CERTIFIED`/`BLOCKED`/`FAILED`) deterministically from a fixed set of
named checks, mirroring the status-derivation pattern already established
by `hermes_cli.agent_roles.system_integration_certification.SystemIntegrationCertification`
(a `model_validator` rejects any `FleetCertification` whose `status` field
doesn't match what its `checks` imply — status can never be set
independently of the evidence). Critically, `stage1_regression_passed`
being `None` (never actually checked) produces `BLOCKED`, identically to an
actual failure — certification can never claim `CERTIFIED` without a
positive, separately-run confirmation that the immutable Stage 1 scripts
still pass (`run_stage1_regression()`, Section 3).

## 6. Trust and authority boundaries

Per the task specification's explicit list, none of the following imply
any of the others anywhere in this implementation, and this is enforced
structurally (no shared boolean, no inheritance shortcut) rather than by
convention alone:

- **Identity** (`FleetIdentity`) — no authority field exists on the type.
- **Liveness / Readiness / Health** (`HealthReport`) — no authority field;
  `evaluate_health()` never returns anything but findings.
- **Admission** (`AdmissionDecision`) — `grants_no_execution_authority()`
  no-op; no execution/mutation/broker field on the type.
- **Quarantine** (`QuarantineState`) — a health/admission *input*, never an
  authority grant.
- **Certification** (`FleetCertification`) — `grants_no_operational_authority()`
  no-op; a `CERTIFIED` result is a statement about governance-invariant
  compliance during evaluation, nothing else.
- **Events / evidence** (`TelemetryEvent`, `EvidenceRecord`) — describe
  what occurred; carry no authority semantics.
- **Sigil advisory output** (`SigilContractResponse.advisory_output`) —
  cannot become execution or broker authority; construction-time-enforced.
- **Fleet membership / routing success** — not modeled by this session at
  all (pre-existing `fleet_routing.py` already hardcodes `can_dispatch`
  etc. to `False`; unchanged here).

## 7. Prohibited behaviors — explicit confirmation

Stages 2 through 9, as implemented in this session, do **not** grant, and
contain no code path toward: live broker execution, unrestricted
production mutation, or unrestricted remote command/shell execution. No
new code in `hermes_cli/prime/` calls `subprocess`, `os.system`, `ssh`, or
any process-spawning primitive except `hermes_cli.prime.certification.run_stage1_regression`,
which only ever invokes the two fixed, pre-existing, path-hardcoded Stage 1
verification scripts — never arbitrary or caller-supplied commands.

## 8. Known limitations and honestly-remaining work

1. **No live runtime gating was added inside the seven pre-existing
   subsystems.** `model_execution.py._admit`, `remote_maintenance.py`'s
   executor, `intelligence_engine.py`, and `learning_hierarchy.py` do not
   yet call into `hermes_cli.prime.admission`/`health` at their own
   decision points — Stage 2 provides the primitives and proves (via the
   acceptance suite) that they compose correctly with real objects from
   those subsystems, but wiring live call sites inside seven independently
   certified modules was judged too risky to attempt without dedicated,
   focused review of each subsystem's own test suite and call graph. This
   is the single largest piece of remaining work for a genuinely "unified"
   fleet, and should be done one subsystem at a time, each as its own
   reviewed change.
2. **No live `MaintenanceAdapter` or `FleetTransportAdapter` implementation
   exists anywhere in the repository** (confirmed by the pre-implementation
   research in this session) — both `remote_maintenance.py` and
   `sigil.ai.fleet` are policy/decision-only with no wired transport. This
   predates this session and was not changed; Stage 2's remote-maintenance
   governance layer is therefore also necessarily decision-only today.
3. **`IdentityRegistry` and `PrimeEvidenceStore` are provided as
   composable primitives, not wired into a single running daemon or
   service process.** No long-running "Prime service" was started; this
   session delivers the governed decision logic and storage, not an
   operational deployment. `deploy/hermes-link/prime.service.json.example`
   already exists as a deployment shape for a future Prime process to fill.
4. **`hermes_cli/prime/certification.py`'s self-test booleans
   (`admission_default_deny_selftest_passed`, etc.) are caller-supplied,
   not internally re-derived from a fixed adversarial battery inside
   `certify_fleet()` itself.** The acceptance and unit test suites in
   `tests/hermes_cli/test_prime/` are the actual self-tests; a future
   improvement would be a dedicated `run_admission_selftest()` /
   `run_sigil_contract_selftest()` function that `certify_fleet()` calls
   directly rather than trusting the caller's boolean, closing a
   theoretical gap where a caller could pass `True` without having run
   anything.

## 9. Future extension points

- Per-subsystem live gating (Section 8.1), one subsystem at a time.
- A real `MaintenanceAdapter`/`FleetTransportAdapter` implementation, gated
  behind its own dedicated certification stage — explicitly out of scope
  here per the "no unrestricted remote command execution" requirement.
- Internally re-derived certification self-tests (Section 8.4).
- A `producer_identity_id` field on `TelemetryEvent` itself, as a
  coordinated schema migration (`SUPPORTED_SCHEMA_VERSIONS` would need to
  grow to include both old and new shapes) — deferred rather than
  attempted as a breaking change to the existing journal format.
