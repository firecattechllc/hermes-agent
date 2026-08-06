"""Governed, evidence-first maintenance for configured remote hosts.

The boundary deliberately accepts command identifiers rather than shell text.
Transports resolve those identifiers from a closed catalogue, which keeps model
diagnosis advisory and prevents this module from becoming a remote shell.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from enum import Enum
from typing import Callable, Iterable, Mapping, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class SecretReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str = Field(..., pattern=r"^(env|keyring|file|runtime)$")
    key: str = Field(..., min_length=1, max_length=256)

    @field_validator("key")
    @classmethod
    def _reference_only(cls, value: str) -> str:
        if "-----BEGIN" in value or re.search(r"(?i)(password|token|secret)=", value):
            raise ValueError("credential values are forbidden; use a secret reference")
        return value.strip()


class RemoteTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str = Field(..., min_length=1, max_length=128)
    host_alias: Optional[str] = Field(default=None, min_length=1, max_length=253)
    endpoint: Optional[str] = Field(default=None, min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(..., min_length=1, max_length=64)
    credential: SecretReference
    private_addresses: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _one_destination(self) -> "RemoteTarget":
        if (self.host_alias is None) == (self.endpoint is None):
            raise ValueError("exactly one host alias or configured endpoint is required")
        return self


class CommandMode(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"
    CONNECTIVITY = "connectivity"


class ApprovalScope(str, Enum):
    MODIFY_HYDRA = "modify_hydra"
    MODIFY_SYSTEMD = "modify_systemd"
    MODIFY_SUDOERS = "modify_sudoers"
    DISABLE_PACKAGE = "disable_package"
    REMOVE_PACKAGE = "remove_package"
    RESTART_TAILSCALE = "restart_tailscale"
    RESTART_SSH = "restart_ssh"
    FIREWALL = "firewall"
    REBOOT = "reboot"
    RESTART_HERMES_HYDRA_LIVE = "restart_hermes_hydra_live"


READ_ONLY_COMMANDS: Mapping[str, Tuple[str, ...]] = {
    "service_state": ("systemctl", "show", "--no-page"),
    "failed_services": ("systemctl", "--failed", "--no-legend"),
    "unit_definition": ("systemctl", "cat"),
    "journal_excerpt": ("journalctl", "--no-pager", "-n", "80", "-u"),
    "process_ownership": ("ps", "-eo", "pid,user,args"),
    "listening_sockets": ("ss", "-ltnp"),
    "apt_tailscale": ("dpkg-query", "-W", "tailscale"),
    "snap_tailscale": ("snap", "list", "tailscale"),
    "firewall_state": ("ufw", "status"),
    "timer_state": ("systemctl", "list-timers", "--all"),
    "file_metadata": ("stat", "--printf=%U:%G:%a:%s:%Y"),
    "file_checksum": ("sha256sum",),
    "ssh_probe": ("true",),
    "tailscale_status": ("tailscale", "status", "--json"),
}


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    def __call__(self, argv: Tuple[str, ...], timeout: int) -> CommandResult: ...


_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_SENSITIVE = (
    re.compile(r"(?im)^(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?im)\b(token|password|secret|api[_-]?key)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"(?im)\b(machine-id|boot-id)\s*[=:]\s*([0-9a-f-]+)"),
    re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE),
)


def redact_evidence(value: str, *, private_addresses: Iterable[str] = ()) -> str:
    """Return bounded evidence with known credentials and host identifiers removed."""
    clean = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    for pattern in _SENSITIVE:
        clean = pattern.sub("[REDACTED]", clean)
    for address in sorted({item for item in private_addresses if item}, key=len, reverse=True):
        clean = clean.replace(address, "[REDACTED_ADDRESS]")
    return clean[:16_384]


class MaintenanceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str = Field(..., min_length=1, max_length=128)
    target_id: str = Field(..., min_length=1, max_length=128)
    command_id: str = Field(..., min_length=1, max_length=128)
    collected_at: int = Field(..., ge=0)
    exit_code: int
    output: str = Field(..., max_length=16_384)
    successful: bool
    checksum: str = Field(..., min_length=64, max_length=64)

    @classmethod
    def build(cls, *, target: RemoteTarget, command_id: str, result: CommandResult,
              collected_at: int) -> "MaintenanceEvidence":
        output = redact_evidence(
            "\n".join(part for part in (result.stdout, result.stderr) if part),
            private_addresses=target.private_addresses,
        )
        payload = {
            "target_id": target.target_id, "command_id": command_id,
            "collected_at": collected_at, "exit_code": result.exit_code,
            "output": output, "successful": result.exit_code == 0 and not result.timed_out,
        }
        return cls(evidence_id=f"remote_evidence_{_digest(payload)[:24]}",
                   checksum=_digest(payload), **payload)


class SSHInspectionAdapter:
    """Read-only SSH discovery using an injected transport runner."""

    def __init__(self, target: RemoteTarget, runner: CommandRunner) -> None:
        self.target = target
        self._runner = runner

    def inspect(self, command_id: str, *, collected_at: int, subject: str = "") -> MaintenanceEvidence:
        if command_id not in READ_ONLY_COMMANDS:
            raise PermissionError("remote inspection command is not allow-listed")
        if subject.startswith("-") or any(ch in subject for ch in ";|&`$\n\r"):
            raise ValueError("unsafe inspection subject")
        destination = self.target.host_alias or self.target.endpoint
        remote = READ_ONLY_COMMANDS[command_id] + ((subject,) if subject else ())
        # Only one "--" is correct: it ends ssh's own option parsing before
        # the destination. Everything after the destination is already
        # unambiguously the remote command; a second "--" there gets sent
        # to the remote shell, which (e.g. bash) tries to parse it as an
        # option to itself and fails closed with a shell usage error. Found
        # via a live smoke test against Hydra Live -- the fake-runner unit
        # tests below only assert argv shape, so they could never catch
        # this; a real remote shell was required.
        argv = ("ssh", "-p", str(self.target.port), "--", f"{self.target.user}@{destination}") + remote
        result = self._runner(argv, 20)
        return MaintenanceEvidence.build(
            target=self.target, command_id=command_id, result=result, collected_at=collected_at
        )


class SSHMaintenanceAdapter:
    """Real SSH transport for the narrow ``MUTATION_COMMANDS`` allow-list only.

    The first concrete (non-simulated) mutating executor in this module.
    Every other mutation command referenced by existing playbooks
    (``atomic_patch_heartbeat_no_sudo``, ``disable_snap_tailscale``, ...)
    remains unimplemented here on purpose -- ``execute`` raises
    ``PermissionError`` for anything not in ``MUTATION_COMMANDS``, mirroring
    ``SSHInspectionAdapter``'s read-only allow-list pattern exactly. A
    ``GovernedMaintenanceExecutor`` still enforces that every step it runs
    carries the matching ``RepairApproval`` scope before this adapter is
    ever reached; this class adds no bypass of that gate.
    """

    def __init__(self, target: RemoteTarget, runner: CommandRunner, inspector: SSHInspectionAdapter) -> None:
        self.target = target
        self._runner = runner
        self._inspector = inspector

    def snapshot(self, path: str, timestamp: int) -> "FileSnapshot":
        """Capture pre-mutation service state as the rollback reference point.

        ``path`` is a systemd unit name here, not a filesystem path -- there
        is no file to snapshot for a service restart. ``snapshot_ref`` holds
        the digest of the ``systemctl show`` output captured immediately
        before mutation, so a reviewer can later diff pre/post state.
        """

        evidence = self._inspector.inspect("service_state", subject=path, collected_at=timestamp)
        return FileSnapshot(
            path=f"systemd-unit:{path}",
            snapshot_ref=f"service-state:{_digest(evidence.output)[:24]}",
            owner="root",
            group="root",
            mode="0644",
            checksum=_digest(evidence.output),
            captured_at=timestamp,
        )

    def execute(self, command_id: str) -> CommandResult:
        if command_id not in MUTATION_COMMANDS:
            raise PermissionError("mutation command is not allow-listed for real execution")
        destination = self.target.host_alias or self.target.endpoint
        remote = MUTATION_COMMANDS[command_id]
        argv = ("ssh", "-p", str(self.target.port), "--", f"{self.target.user}@{destination}") + remote
        return self._runner(argv, 30)

    def rollback(self, manifest: "RollbackManifest") -> Tuple[MaintenanceEvidence, ...]:
        """Re-verify the affected unit is active; re-attempt start if it is not.

        There is no meaningful "undo" for a restart beyond ensuring the
        service ends up running, so rollback here means: check status, and
        if the unit somehow ended up inactive, issue one bounded ``start``
        (never ``restart`` again, to avoid a rollback loop).
        """

        now = int(time.time())
        results = []
        for snapshot in manifest.snapshots:
            unit = snapshot.path.removeprefix("systemd-unit:")
            evidence = self._inspector.inspect("service_state", subject=unit, collected_at=now)
            results.append(evidence)
            if "active (running)" not in evidence.output:
                destination = self.target.host_alias or self.target.endpoint
                argv = (
                    "ssh", "-p", str(self.target.port), "--", f"{self.target.user}@{destination}",
                    "sudo", "systemctl", "start", unit,
                )
                self._runner(argv, 30)
                results.append(self._inspector.inspect("service_state", subject=unit, collected_at=now))
        return tuple(results)


class FindingCode(str, Enum):
    INTERACTIVE_SUDO_SYSTEMD = "interactive_sudo_systemd"
    DUPLICATE_TAILSCALE = "duplicate_tailscale"
    HYDRA_PORT_HEALTHY = "hydra_port_healthy"


class FleetFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: FindingCode
    summary: str
    evidence_refs: Tuple[str, ...]


def diagnose_hydra_live(evidence: Iterable[MaintenanceEvidence]) -> Tuple[FleetFinding, ...]:
    items = tuple(evidence)
    text = "\n".join(item.output.lower() for item in items)
    refs = tuple(sorted(item.evidence_id for item in items))
    findings = []
    if "sudo" in text and ("no tty" in text or "password is required" in text or "a terminal is required" in text):
        findings.append(FleetFinding(code=FindingCode.INTERACTIVE_SUDO_SYSTEMD,
                                     summary="interactive sudo blocks the systemd heartbeat oneshot", evidence_refs=refs))
    if "tailscale" in text and "snap.tailscale.tailscaled" in text and "tailscaled.service" in text:
        findings.append(FleetFinding(code=FindingCode.DUPLICATE_TAILSCALE,
                                     summary="APT and Snap Tailscale installations coexist", evidence_refs=refs))
    if ":3130" in text and "hydra-lived" in text and "hydra-live.service" in text:
        findings.append(FleetFinding(code=FindingCode.HYDRA_PORT_HEALTHY,
                                     summary="TCP 3130 is owned by the expected Hydra Live process", evidence_refs=refs))
    return tuple(findings)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RepairStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_id: str
    command_id: str
    mode: CommandMode
    affected_services: Tuple[str, ...] = ()
    required_approvals: Tuple[ApprovalScope, ...] = ()
    validation_command_ids: Tuple[str, ...] = ()
    rollback_command_id: str
    changed_files: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _mutation_is_approved(self) -> "RepairStep":
        if self.mode != CommandMode.READ_ONLY and not self.required_approvals:
            raise ValueError("mutating repair steps require explicit approval")
        return self


class RepairProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    proposal_id: str
    target_id: str
    risk: RiskLevel
    expected_downtime: str
    finding_refs: Tuple[FindingCode, ...]
    steps: Tuple[RepairStep, ...]
    evidence_refs: Tuple[str, ...]
    checksum: str

    @staticmethod
    def calculate_checksum(values: Mapping[str, object]) -> str:
        canonical = dict(values)
        canonical.pop("proposal_id", None)
        canonical.pop("checksum", None)
        risk = canonical["risk"]
        canonical["risk"] = risk.value if isinstance(risk, RiskLevel) else risk
        canonical["finding_refs"] = [item.value if isinstance(item, FindingCode) else item for item in canonical["finding_refs"]]
        canonical["steps"] = [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in canonical["steps"]]
        return _digest(canonical)

    @classmethod
    def build(cls, **values: object) -> "RepairProposal":
        checksum = cls.calculate_checksum(values)
        return cls(proposal_id=f"repair_proposal_{checksum[:24]}", checksum=checksum, **values)

    @model_validator(mode="after")
    def _integrity(self) -> "RepairProposal":
        checksum = self.calculate_checksum(self.model_dump(exclude={"proposal_id", "checksum"}))
        if self.checksum != checksum or self.proposal_id != f"repair_proposal_{checksum[:24]}":
            raise ValueError("repair proposal integrity mismatch")
        return self


class RepairApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    approval_id: str
    proposal_id: str
    proposal_checksum: str = Field(..., min_length=64, max_length=64)
    scopes: Tuple[ApprovalScope, ...]
    actor_id: str
    approved_at: int = Field(..., ge=0)
    reason: str


class FileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    snapshot_ref: str
    owner: str
    group: str
    mode: str
    checksum: str = Field(..., min_length=64, max_length=64)
    captured_at: int = Field(..., ge=0)


class RollbackManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    manifest_id: str
    proposal_id: str
    snapshots: Tuple[FileSnapshot, ...]
    rollback_command_ids: Tuple[str, ...]
    checksum: str

    @classmethod
    def build(cls, proposal: RepairProposal, snapshots: Iterable[FileSnapshot]) -> "RollbackManifest":
        snapshots = tuple(snapshots)
        commands = tuple(reversed(tuple(step.rollback_command_id for step in proposal.steps)))
        payload = {"proposal_id": proposal.proposal_id,
                   "snapshots": [item.model_dump(mode="json") for item in snapshots],
                   "rollback_command_ids": commands}
        checksum = _digest(payload)
        return cls(manifest_id=f"rollback_{checksum[:24]}", checksum=checksum, **payload)


class MaintenanceAdapter(Protocol):
    def snapshot(self, path: str, timestamp: int) -> FileSnapshot: ...
    def execute(self, command_id: str) -> CommandResult: ...
    def rollback(self, manifest: RollbackManifest) -> Tuple[MaintenanceEvidence, ...]: ...


MUTATION_POLICY: Mapping[str, Tuple[CommandMode, Tuple[ApprovalScope, ...]]] = {
    "atomic_patch_heartbeat_no_sudo": (CommandMode.REVERSIBLE, (ApprovalScope.MODIFY_HYDRA,)),
    "disable_snap_tailscale": (CommandMode.REVERSIBLE, (ApprovalScope.DISABLE_PACKAGE,)),
    "remove_snap_tailscale": (CommandMode.DESTRUCTIVE, (ApprovalScope.REMOVE_PACKAGE,)),
    "atomic_replace_systemd_unit": (CommandMode.REVERSIBLE, (ApprovalScope.MODIFY_SYSTEMD,)),
    "atomic_replace_sudoers": (CommandMode.REVERSIBLE, (ApprovalScope.MODIFY_SUDOERS,)),
    "restart_tailscale": (CommandMode.CONNECTIVITY, (ApprovalScope.RESTART_TAILSCALE,)),
    "restart_ssh": (CommandMode.CONNECTIVITY, (ApprovalScope.RESTART_SSH,)),
    "change_firewall": (CommandMode.CONNECTIVITY, (ApprovalScope.FIREWALL,)),
    "reboot": (CommandMode.CONNECTIVITY, (ApprovalScope.REBOOT,)),
    "restart_hermes_hydra_live": (CommandMode.CONNECTIVITY, (ApprovalScope.RESTART_HERMES_HYDRA_LIVE,)),
}

# Closed catalogue of the *only* mutating commands this codebase can execute
# via SSHMaintenanceAdapter. Deliberately far narrower than MUTATION_POLICY:
# every other mutation command referenced by an existing playbook
# (atomic_patch_heartbeat_no_sudo, disable_snap_tailscale, ...) remains
# simulation-only (no concrete adapter implements them) and this run does
# not add one. Adding a real executor for one command is already a
# meaningful trust boundary; silently making every mutation "real" in the
# same pass would be reckless.
MUTATION_COMMANDS: Mapping[str, Tuple[str, ...]] = {
    "restart_hermes_hydra_live": ("sudo", "systemctl", "restart", "hermes-hydra-live.service"),
}


class RepairExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_id: str
    proposal_id: str
    state: str
    rollback_manifest: RollbackManifest
    executed_steps: Tuple[str, ...]
    evidence: Tuple[MaintenanceEvidence, ...]
    rolled_back: bool


class GovernedMaintenanceExecutor:
    """Consume exact approvals, snapshot first, and roll back on partial failure."""

    def execute(self, *, proposal: RepairProposal, approvals: Iterable[RepairApproval],
                adapter: MaintenanceAdapter, timestamp: int) -> RepairExecution:
        for step in proposal.steps:
            policy = MUTATION_POLICY.get(step.command_id)
            if policy is None:
                raise PermissionError("repair command is not allow-listed")
            required_mode, required_scopes = policy
            if step.mode != required_mode or not set(required_scopes).issubset(step.required_approvals):
                raise PermissionError("repair step does not satisfy command policy")
        approvals = tuple(approvals)
        approved = {scope for item in approvals
                    if item.proposal_id == proposal.proposal_id and item.proposal_checksum == proposal.checksum
                    for scope in item.scopes}
        required = {scope for step in proposal.steps for scope in step.required_approvals}
        missing = sorted(scope.value for scope in required - approved)
        if missing:
            raise PermissionError(f"missing repair approvals: {', '.join(missing)}")
        snapshots = tuple(adapter.snapshot(path, timestamp) for step in proposal.steps for path in step.changed_files)
        manifest = RollbackManifest.build(proposal, snapshots)
        if not manifest.snapshots and any(step.changed_files for step in proposal.steps):
            raise RuntimeError("rollback manifest is incomplete")
        evidence, executed = [], []
        for step in proposal.steps:
            result = adapter.execute(step.command_id)
            item = MaintenanceEvidence.build(
                target=RemoteTarget(target_id=proposal.target_id, host_alias="governed-target",
                                    user="governed", credential=SecretReference(provider="runtime", key="execution-boundary")),
                command_id=step.command_id, result=result, collected_at=timestamp,
            )
            evidence.append(item)
            if not item.successful:
                evidence.extend(adapter.rollback(manifest))
                return RepairExecution(execution_id=f"repair_execution_{proposal.checksum[:24]}",
                    proposal_id=proposal.proposal_id, state="rolled_back", rollback_manifest=manifest,
                    executed_steps=tuple(executed), evidence=tuple(evidence), rolled_back=True)
            executed.append(step.step_id)
        return RepairExecution(execution_id=f"repair_execution_{proposal.checksum[:24]}",
            proposal_id=proposal.proposal_id, state="completed", rollback_manifest=manifest,
            executed_steps=tuple(executed), evidence=tuple(evidence), rolled_back=False)


class CertificationObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    hydra_service_active: bool
    port_3130_owner: str
    heartbeat_timer_results: Tuple[bool, ...]
    apt_tailscale_active: bool
    apt_tailscale_connected: bool
    snap_tailscale_absent_or_disabled: bool
    ssh_connectivity_preserved: bool
    unexpected_failed_services: Tuple[str, ...] = ()
    evidence: Tuple[MaintenanceEvidence, ...] = ()


class RepairCertification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    certified: bool
    checks: Tuple[Tuple[str, bool], ...]
    evidence_refs: Tuple[str, ...]


def certify_hydra_live(observation: CertificationObservation) -> RepairCertification:
    serialized = json.dumps([item.model_dump(mode="json") for item in observation.evidence])
    secret_free = redact_evidence(serialized) == serialized
    checks = (
        ("hydra_live_active", observation.hydra_service_active),
        ("tcp_3130_expected_owner", "hydra-lived" in observation.port_3130_owner and "hydra-live.service" in observation.port_3130_owner),
        ("heartbeat_three_consecutive", len(observation.heartbeat_timer_results) >= 3 and all(observation.heartbeat_timer_results[-3:])),
        ("apt_tailscale_healthy", observation.apt_tailscale_active and observation.apt_tailscale_connected),
        ("stale_snap_absent_or_disabled", observation.snap_tailscale_absent_or_disabled),
        ("ssh_preserved", observation.ssh_connectivity_preserved),
        ("no_unexpected_failed_services", not observation.unexpected_failed_services),
        ("evidence_secret_free", secret_free),
    )
    return RepairCertification(certified=all(result for _, result in checks), checks=checks,
        evidence_refs=tuple(sorted(item.evidence_id for item in observation.evidence)))
