"""Governed, evidence-first inventory for configured fleet hosts.

The boundary accepts inventory command identifiers rather than arbitrary shell
text. Commands resolve from a closed, read-only catalogue and execute only
against configured targets with exact proposal-bound approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Iterable, Mapping, Optional, Protocol, Tuple

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
        stripped = value.strip()
        if (
            "-----BEGIN" in stripped
            or re.search(r"(?i)(password|token|secret|api[_-]?key)\s*=", stripped)
        ):
            raise ValueError("credential values are forbidden; use a secret reference")
        return stripped


class InventoryTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str = Field(..., min_length=1, max_length=128)
    host_alias: Optional[str] = Field(default=None, min_length=1, max_length=253)
    endpoint: Optional[str] = Field(default=None, min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(..., min_length=1, max_length=64)
    credential: SecretReference
    private_addresses: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _configured_destination(self) -> "InventoryTarget":
        if (self.host_alias is None) == (self.endpoint is None):
            raise ValueError("exactly one configured host alias or endpoint is required")
        return self


class InventoryMode(str, Enum):
    READ_ONLY = "read_only"
    PRIVILEGED_READ_ONLY = "privileged_read_only"


INVENTORY_COMMANDS: Mapping[str, Tuple[InventoryMode, str, Tuple[str, ...]]] = {
    "system_info": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("uname", "-a"),
    ),
    "os_release": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("cat", "/etc/os-release"),
    ),
    "disk_usage": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("df", "-P"),
    ),
    "memory_usage": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("free", "-m"),
    ),
    "service_inventory": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"),
    ),
    "failed_services": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("systemctl", "--failed", "--no-legend", "--no-pager"),
    ),
    "listening_sockets": (
        InventoryMode.PRIVILEGED_READ_ONLY,
        "inventory:privileged_read",
        ("ss", "-ltnp"),
    ),
    "container_list": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("docker", "ps", "--no-trunc", "--format", "{{json .}}"),
    ),
    "tailscale_status": (
        InventoryMode.READ_ONLY,
        "inventory:read",
        ("tailscale", "status", "--json"),
    ),
}


class InventoryStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(..., min_length=1, max_length=128)
    command_id: str = Field(..., min_length=1, max_length=128)
    mode: InventoryMode
    required_scope: str = Field(..., pattern=r"^inventory:(read|privileged_read)$")
    subject: str = Field(default="", max_length=253)
    timeout: int = Field(default=20, ge=1, le=120)

    @model_validator(mode="after")
    def _closed_policy(self) -> "InventoryStep":
        policy = INVENTORY_COMMANDS.get(self.command_id)
        if policy is None:
            raise ValueError("inventory command is not allow-listed")
        expected_mode, expected_scope, _ = policy
        if self.mode != expected_mode or self.required_scope != expected_scope:
            raise ValueError("inventory step does not satisfy command policy")
        if self.subject.startswith("-") or any(ch in self.subject for ch in ";|&`$\n\r"):
            raise ValueError("unsafe inventory subject")
        return self


class InventoryProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    target_id: str = Field(..., min_length=1, max_length=128)
    steps: Tuple[InventoryStep, ...]
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = ()
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(values: Mapping[str, object]) -> str:
        canonical = dict(values)
        canonical.pop("proposal_id", None)
        canonical.pop("checksum", None)
        canonical["steps"] = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in canonical["steps"]
        ]
        return _digest(canonical)

    @classmethod
    def build(cls, **values: object) -> "InventoryProposal":
        checksum = cls.calculate_checksum(values)
        return cls(
            proposal_id=f"inventory_proposal_{checksum[:24]}",
            checksum=checksum,
            **values,
        )

    @model_validator(mode="after")
    def _integrity(self) -> "InventoryProposal":
        checksum = self.calculate_checksum(
            self.model_dump(exclude={"proposal_id", "checksum"})
        )
        if (
            self.checksum != checksum
            or self.proposal_id != f"inventory_proposal_{checksum[:24]}"
        ):
            raise ValueError("inventory proposal integrity mismatch")
        if not self.steps:
            raise ValueError("inventory proposal requires at least one step")
        return self


class InventoryApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(..., min_length=1, max_length=128)
    proposal_id: str = Field(..., min_length=1, max_length=128)
    proposal_checksum: str = Field(..., min_length=64, max_length=64)
    scopes: Tuple[str, ...]
    actor_id: str = Field(..., min_length=1, max_length=256)
    approved_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)

    @field_validator("scopes")
    @classmethod
    def _known_scopes(cls, scopes: Tuple[str, ...]) -> Tuple[str, ...]:
        allowed = {"inventory:read", "inventory:privileged_read"}
        unknown = sorted(set(scopes) - allowed)
        if unknown:
            raise ValueError(f"unknown inventory approval scopes: {', '.join(unknown)}")
        return scopes


class InventoryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class InventoryRunner(Protocol):
    def __call__(self, argv: Tuple[str, ...], timeout: int) -> InventoryResult: ...


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


def redact_inventory_evidence(
    value: str, *, private_addresses: Iterable[str] = ()
) -> str:
    clean = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
    for pattern in _SENSITIVE:
        clean = pattern.sub("[REDACTED]", clean)
    for address in sorted(
        {item for item in private_addresses if item},
        key=len,
        reverse=True,
    ):
        clean = clean.replace(address, "[REDACTED_ADDRESS]")
    return clean[:16_384]


class InventoryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(..., min_length=1, max_length=128)
    target_id: str = Field(..., min_length=1, max_length=128)
    command_id: str = Field(..., min_length=1, max_length=128)
    collected_at: int = Field(..., ge=0)
    exit_code: int
    output: str = Field(..., max_length=16_384)
    successful: bool
    timed_out: bool
    checksum: str = Field(..., min_length=64, max_length=64)

    @classmethod
    def build(
        cls,
        *,
        target: InventoryTarget,
        command_id: str,
        result: InventoryResult,
        collected_at: int,
    ) -> "InventoryEvidence":
        output = redact_inventory_evidence(
            "\n".join(part for part in (result.stdout, result.stderr) if part),
            private_addresses=target.private_addresses,
        )
        payload = {
            "target_id": target.target_id,
            "command_id": command_id,
            "collected_at": collected_at,
            "exit_code": result.exit_code,
            "output": output,
            "successful": result.exit_code == 0 and not result.timed_out,
            "timed_out": result.timed_out,
        }
        return cls(
            evidence_id=f"inventory_evidence_{_digest(payload)[:24]}",
            checksum=_digest(payload),
            **payload,
        )


class InventoryFindingCode(str, Enum):
    CONTAINER_RUNNING_AS_ROOT = "container_running_as_root"
    FAILED_SERVICES_PRESENT = "failed_services_present"
    DISK_PRESSURE = "disk_pressure"
    INVENTORY_COMMAND_FAILED = "inventory_command_failed"


class InventoryFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: InventoryFindingCode
    summary: str
    evidence_refs: Tuple[str, ...]


def diagnose_fleet_inventory(
    evidence: Iterable[InventoryEvidence],
) -> Tuple[InventoryFinding, ...]:
    items = tuple(evidence)
    findings = []

    for item in items:
        text = item.output.lower()
        refs = (item.evidence_id,)

        if not item.successful:
            findings.append(
                InventoryFinding(
                    code=InventoryFindingCode.INVENTORY_COMMAND_FAILED,
                    summary=f"inventory command {item.command_id} failed or timed out",
                    evidence_refs=refs,
                )
            )
        if item.command_id == "container_list" and (
            "uid=0" in text
            or '"user":"root"' in text
            or " user=root" in text
            or text.startswith("root ")
        ):
            findings.append(
                InventoryFinding(
                    code=InventoryFindingCode.CONTAINER_RUNNING_AS_ROOT,
                    summary="one or more containers appear to run as root",
                    evidence_refs=refs,
                )
            )
        if item.command_id == "failed_services" and text.strip():
            findings.append(
                InventoryFinding(
                    code=InventoryFindingCode.FAILED_SERVICES_PRESENT,
                    summary="one or more failed services were reported",
                    evidence_refs=refs,
                )
            )
        if item.command_id == "disk_usage":
            for match in re.finditer(r"(?m)\s(\d{1,3})%\s", item.output):
                if int(match.group(1)) >= 90:
                    findings.append(
                        InventoryFinding(
                            code=InventoryFindingCode.DISK_PRESSURE,
                            summary="filesystem usage is at or above 90 percent",
                            evidence_refs=refs,
                        )
                    )
                    break

    unique = {
        (finding.code.value, finding.evidence_refs): finding for finding in findings
    }
    return tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[0], item[1]))
    )


class InventoryExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str
    proposal_id: str
    proposal_checksum: str = Field(..., min_length=64, max_length=64)
    target_id: str
    state: str = Field(..., pattern=r"^(completed|failed)$")
    executed_steps: Tuple[str, ...]
    evidence: Tuple[InventoryEvidence, ...]


class GovernedInventoryExecutor:
    """Execute exact, approved, read-only inventory proposals."""

    def execute(
        self,
        *,
        target: InventoryTarget,
        proposal: InventoryProposal,
        approvals: Iterable[InventoryApproval],
        runner: InventoryRunner,
        timestamp: int,
    ) -> InventoryExecution:
        if proposal.target_id != target.target_id:
            raise PermissionError("inventory proposal target mismatch")

        approved_scopes = {
            scope
            for approval in approvals
            if (
                approval.proposal_id == proposal.proposal_id
                and approval.proposal_checksum == proposal.checksum
            )
            for scope in approval.scopes
        }
        required_scopes = {step.required_scope for step in proposal.steps}
        missing = sorted(required_scopes - approved_scopes)
        if missing:
            raise PermissionError(
                f"missing inventory approvals: {', '.join(missing)}"
            )

        destination = target.host_alias or target.endpoint
        evidence = []
        executed = []

        for step in proposal.steps:
            policy = INVENTORY_COMMANDS.get(step.command_id)
            if policy is None:
                raise PermissionError("inventory command is not allow-listed")
            expected_mode, expected_scope, command = policy
            if step.mode != expected_mode or step.required_scope != expected_scope:
                raise PermissionError("inventory step does not satisfy command policy")

            remote = command + ((step.subject,) if step.subject else ())
            argv = (
                "ssh",
                "-p",
                str(target.port),
                "--",
                f"{target.user}@{destination}",
                "--",
            ) + remote

            result = runner(argv, step.timeout)
            item = InventoryEvidence.build(
                target=target,
                command_id=step.command_id,
                result=result,
                collected_at=timestamp,
            )
            evidence.append(item)
            if not item.successful:
                return InventoryExecution(
                    execution_id=f"inventory_execution_{proposal.checksum[:24]}",
                    proposal_id=proposal.proposal_id,
                    proposal_checksum=proposal.checksum,
                    target_id=target.target_id,
                    state="failed",
                    executed_steps=tuple(executed),
                    evidence=tuple(evidence),
                )
            executed.append(step.step_id)

        return InventoryExecution(
            execution_id=f"inventory_execution_{proposal.checksum[:24]}",
            proposal_id=proposal.proposal_id,
            proposal_checksum=proposal.checksum,
            target_id=target.target_id,
            state="completed",
            executed_steps=tuple(executed),
            evidence=tuple(evidence),
        )


class InventoryCertification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    certified: bool
    checks: Tuple[Tuple[str, bool], ...]
    evidence_refs: Tuple[str, ...]
    finding_codes: Tuple[InventoryFindingCode, ...]


def certify_fleet_inventory(
    proposal: InventoryProposal,
    execution: InventoryExecution,
) -> InventoryCertification:
    findings = diagnose_fleet_inventory(execution.evidence)
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in execution.evidence],
        sort_keys=True,
    )
    secret_free = redact_inventory_evidence(encoded) == encoded
    evidence_refs = tuple(sorted(item.evidence_id for item in execution.evidence))
    expected_steps = tuple(step.step_id for step in proposal.steps)

    checks = (
        ("proposal_matches_execution", execution.proposal_id == proposal.proposal_id),
        ("checksum_matches_execution", execution.proposal_checksum == proposal.checksum),
        ("target_matches_proposal", execution.target_id == proposal.target_id),
        ("execution_completed", execution.state == "completed"),
        ("all_steps_executed", execution.executed_steps == expected_steps),
        ("evidence_count_matches_steps", len(execution.evidence) == len(proposal.steps)),
        ("all_evidence_successful", all(item.successful for item in execution.evidence)),
        ("evidence_secret_free", secret_free),
    )
    return InventoryCertification(
        certified=all(result for _, result in checks),
        checks=checks,
        evidence_refs=evidence_refs,
        finding_codes=tuple(sorted((item.code for item in findings), key=lambda item: item.value)),
    )
