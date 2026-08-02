"""Independent, advisory-only Claude inspection of bounded Sigil evidence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping

from .claude import CLAUDE_PROVIDER_ID
from .models import Capability
from .provider import ModelProvider, ProviderInvocation
from .registry import canonical_digest

INSPECTION_CONTRACT_VERSION = 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_MATERIAL_CHARS = 24_000
_MAX_FINDINGS = 32
_ALLOWED_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})


class ClaudeInspectionFailure(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_OUTPUT = "malformed_output"
    CONTRACT_VIOLATION = "contract_violation"


class ClaudeInspectionValidationError(ValueError):
    """Inspection input or output violated the governed contract."""


@dataclass(frozen=True, slots=True)
class ClaudeInspectionRequest:
    inspection_id: str
    task_correlation_id: str
    target_revision: str
    target_digest: str
    evidence_digests: tuple[str, ...]
    inspection_scope: tuple[str, ...]
    sanitized_material: str
    allowed_provider_ids: frozenset[str]
    requested_at: str
    timeout_ms: int = 60_000
    schema_version: int = INSPECTION_CONTRACT_VERSION
    paper_only: bool = True
    broker_submission: bool = False
    execution_authorized: bool = False
    approval_authority: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != INSPECTION_CONTRACT_VERSION:
            raise ClaudeInspectionValidationError("unsupported inspection schema")
        if not self.inspection_id or not self.task_correlation_id:
            raise ClaudeInspectionValidationError("inspection identities cannot be blank")
        if not self.target_revision:
            raise ClaudeInspectionValidationError("target revision cannot be blank")
        if _SHA256.fullmatch(self.target_digest) is None:
            raise ClaudeInspectionValidationError("target digest must be SHA-256")
        if not self.evidence_digests or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_digests
        ):
            raise ClaudeInspectionValidationError(
                "inspection evidence must contain SHA-256 digests"
            )
        if not self.inspection_scope or any(not item.strip() for item in self.inspection_scope):
            raise ClaudeInspectionValidationError("inspection scope cannot be empty")
        if not self.sanitized_material.strip():
            raise ClaudeInspectionValidationError("inspection material cannot be empty")
        if len(self.sanitized_material) > _MAX_MATERIAL_CHARS:
            raise ClaudeInspectionValidationError("inspection material exceeds its bound")
        if self.allowed_provider_ids != frozenset({CLAUDE_PROVIDER_ID}):
            raise ClaudeInspectionValidationError(
                "independent Claude inspection requires explicit Claude-only admission"
            )
        if not 100 <= self.timeout_ms <= 300_000:
            raise ClaudeInspectionValidationError(
                "inspection timeout is outside its governed bound"
            )
        if not self.requested_at:
            raise ClaudeInspectionValidationError("inspection timestamp cannot be blank")
        if (
            self.paper_only is not True
            or self.broker_submission is not False
            or self.execution_authorized is not False
            or self.approval_authority is not False
            or self.portfolio_mutation is not False
            or self.tool_execution is not False
        ):
            raise ClaudeInspectionValidationError(
                "independent inspection cannot receive execution authority"
            )


@dataclass(frozen=True, slots=True)
class ClaudeInspectionFinding:
    finding_id: str
    severity: str
    category: str
    summary: str
    evidence_references: tuple[str, ...]
    recommendation: str

    def __post_init__(self) -> None:
        if not self.finding_id or not self.category or not self.summary:
            raise ClaudeInspectionValidationError("inspection finding fields cannot be blank")
        if self.severity not in _ALLOWED_SEVERITIES:
            raise ClaudeInspectionValidationError("inspection severity is invalid")
        if not self.evidence_references or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_references
        ):
            raise ClaudeInspectionValidationError(
                "inspection findings require digest evidence references"
            )
        if not self.recommendation:
            raise ClaudeInspectionValidationError(
                "inspection recommendation cannot be blank"
            )


@dataclass(frozen=True, slots=True)
class ClaudeInspectionReport:
    inspection_id: str
    target_revision: str
    target_digest: str
    provider_id: str
    model_id: str
    findings: tuple[ClaudeInspectionFinding, ...]
    limitations: tuple[str, ...]
    report_digest: str
    completed_at: str
    failure: ClaudeInspectionFailure | None = None
    paper_only: bool = True
    broker_submission: bool = False
    execution_authorized: bool = False
    approval_authority: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False

    @property
    def succeeded(self) -> bool:
        return self.failure is None


class GovernedClaudeInspectionService:
    """Invoke explicitly admitted Claude as an independent advisory reviewer."""

    def __init__(self, provider: ModelProvider) -> None:
        if provider.identity.provider_id != CLAUDE_PROVIDER_ID:
            raise ClaudeInspectionValidationError(
                "inspection service requires the governed Claude provider"
            )
        self.provider = provider

    def inspect(
        self,
        request: ClaudeInspectionRequest,
        *,
        completed_at: str,
    ) -> ClaudeInspectionReport:
        invocation = ProviderInvocation(
            request_id=request.inspection_id,
            task_correlation_id=request.task_correlation_id,
            model_id=self.provider.model_id,
            registry_revision=f"inspection:{request.target_digest}",
            capability=Capability.REASONING,
            input_payload={"prompt": _inspection_prompt(request)},
            timeout_ms=request.timeout_ms,
            started_at=request.requested_at,
            ended_at=completed_at,
        )
        result = self.provider.invoke(invocation)
        if not result.succeeded or result.output is None:
            return _failure_report(
                request,
                self.provider,
                completed_at,
                ClaudeInspectionFailure.PROVIDER_UNAVAILABLE,
            )

        content = result.output.get("content")
        if not isinstance(content, str):
            return _failure_report(
                request,
                self.provider,
                completed_at,
                ClaudeInspectionFailure.MALFORMED_OUTPUT,
            )

        try:
            findings, limitations = _parse_output(
                content,
                trusted_evidence=frozenset(request.evidence_digests),
            )
        except ClaudeInspectionValidationError:
            return _failure_report(
                request,
                self.provider,
                completed_at,
                ClaudeInspectionFailure.CONTRACT_VIOLATION,
            )

        digest_payload = {
            "inspection_id": request.inspection_id,
            "target_revision": request.target_revision,
            "target_digest": request.target_digest,
            "provider_id": self.provider.identity.provider_id,
            "model_id": self.provider.model_id,
            "findings": [asdict(finding) for finding in findings],
            "limitations": limitations,
            "completed_at": completed_at,
            "paper_only": True,
            "broker_submission": False,
            "execution_authorized": False,
            "approval_authority": False,
            "portfolio_mutation": False,
            "tool_execution": False,
        }
        return ClaudeInspectionReport(
            inspection_id=request.inspection_id,
            target_revision=request.target_revision,
            target_digest=request.target_digest,
            provider_id=self.provider.identity.provider_id,
            model_id=self.provider.model_id,
            findings=findings,
            limitations=limitations,
            report_digest=f"sha256:{canonical_digest(digest_payload)}",
            completed_at=completed_at,
        )


def _inspection_prompt(request: ClaudeInspectionRequest) -> str:
    scope = "\n".join(f"- {item}" for item in request.inspection_scope)
    evidence = "\n".join(f"- {item}" for item in request.evidence_digests)
    return (
        "Perform an independent advisory inspection of the bounded Sigil material below.\n"
        "Do not approve, execute, mutate policy, call tools, or infer missing evidence.\n"
        "Return JSON only with keys findings and limitations. Each finding must contain "
        "finding_id, severity, category, summary, evidence_references, recommendation.\n\n"
        f"Target revision: {request.target_revision}\n"
        f"Target digest: {request.target_digest}\n"
        f"Inspection scope:\n{scope}\n"
        f"Trusted evidence digests:\n{evidence}\n"
        f"Sanitized material:\n{request.sanitized_material}"
    )


def _parse_output(
    content: str,
    *,
    trusted_evidence: frozenset[str],
) -> tuple[tuple[ClaudeInspectionFinding, ...], tuple[str, ...]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ClaudeInspectionValidationError("inspection output must be JSON") from error
    if not isinstance(payload, Mapping):
        raise ClaudeInspectionValidationError("inspection output must be an object")
    if set(payload) != {"findings", "limitations"}:
        raise ClaudeInspectionValidationError("inspection output keys are invalid")

    raw_findings = payload["findings"]
    raw_limitations = payload["limitations"]
    if not isinstance(raw_findings, list) or len(raw_findings) > _MAX_FINDINGS:
        raise ClaudeInspectionValidationError("inspection findings are invalid")
    if not isinstance(raw_limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_limitations
    ):
        raise ClaudeInspectionValidationError("inspection limitations are invalid")

    findings: list[ClaudeInspectionFinding] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping) or set(raw) != {
            "finding_id",
            "severity",
            "category",
            "summary",
            "evidence_references",
            "recommendation",
        }:
            raise ClaudeInspectionValidationError("inspection finding schema is invalid")
        references = raw["evidence_references"]
        if not isinstance(references, list) or any(
            not isinstance(item, str) for item in references
        ):
            raise ClaudeInspectionValidationError(
                "inspection evidence references are invalid"
            )
        if not set(references).issubset(trusted_evidence):
            raise ClaudeInspectionValidationError(
                "inspection finding cited untrusted evidence"
            )
        findings.append(
            ClaudeInspectionFinding(
                finding_id=str(raw["finding_id"]),
                severity=str(raw["severity"]),
                category=str(raw["category"]),
                summary=str(raw["summary"]),
                evidence_references=tuple(references),
                recommendation=str(raw["recommendation"]),
            )
        )
    return tuple(findings), tuple(raw_limitations)


def _failure_report(
    request: ClaudeInspectionRequest,
    provider: ModelProvider,
    completed_at: str,
    failure: ClaudeInspectionFailure,
) -> ClaudeInspectionReport:
    payload = {
        "inspection_id": request.inspection_id,
        "target_revision": request.target_revision,
        "target_digest": request.target_digest,
        "provider_id": provider.identity.provider_id,
        "model_id": provider.model_id,
        "failure": failure.value,
        "completed_at": completed_at,
        "paper_only": True,
        "broker_submission": False,
    }
    return ClaudeInspectionReport(
        inspection_id=request.inspection_id,
        target_revision=request.target_revision,
        target_digest=request.target_digest,
        provider_id=provider.identity.provider_id,
        model_id=provider.model_id,
        findings=(),
        limitations=("Independent Claude inspection did not produce an admissible report.",),
        report_digest=f"sha256:{canonical_digest(payload)}",
        completed_at=completed_at,
        failure=failure,
    )
