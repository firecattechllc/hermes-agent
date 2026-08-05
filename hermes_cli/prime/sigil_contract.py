"""Governed Sigil service contract.

Fleet Unification Stage 2F. Defines the typed, versioned, governed contract
between Hermes fleet services and Sigil.

This module composes rather than replaces existing infrastructure:

- ``sigil.worker_contract`` already defines a provider-neutral governed
  job/admission/lifecycle/result contract (``GovernedWorkerJob``,
  ``JobAdmissionDecision``). This module does not duplicate that contract;
  it adds the Sigil-specific envelope (caller/service identity, Prime
  admission and health preconditions, advisory/paper-only/broker-isolation
  locks, and compatibility negotiation) around a request that a caller would
  separately turn into a ``GovernedWorkerJob`` if dispatch were ever wired up.
- Sigil's own ``sigil.ai.fleet`` module already enforces
  ``paper_only=True``/``broker_submission=False`` on every fleet dataclass
  via its private ``_no_authority()`` guard, and
  ``sigil.desktop_bridge.paper_execution.submit()`` structurally rejects any
  non-paper or broker-submission request. This module's validators are a
  belt-and-suspenders echo of that same invariant at the contract-envelope
  level — they do not replace Sigil's own enforcement, and this module never
  reaches into Sigil's execution path.

Every ``SigilContractRequest`` is advisory-only by construction: a caller
cannot construct a request or response that claims execution authority,
broker-submission authority, or non-paper environment — the pydantic
validators reject it outright rather than merely defaulting it.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.health import HealthReport, is_usable_for_admission

SIGIL_CONTRACT_SCHEMA_VERSION = 1
SUPPORTED_SIGIL_CONTRACT_VERSIONS = frozenset({1})

# Closed allow-list of operations Hermes fleet services may request of
# Sigil. Adding an operation here is a deliberate governance decision, not
# an incidental code change — mirrors the closed MUTATION_POLICY catalogue
# convention in hermes_cli.agent_roles.remote_maintenance.
SUPPORTED_SIGIL_OPERATIONS = frozenset({
    "advisory_valuation",
    "advisory_risk_assessment",
    "advisory_portfolio_construction",
    "advisory_financial_sentiment",
    "advisory_research_summary",
    "certification_status_query",
})

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_SIGIL_CONTRACT_VERSIONS:
        raise ValueError(
            f"Sigil contract schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SIGIL_CONTRACT_VERSIONS)})"
        )
    return version


class SigilRejectionCode(str, Enum):
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CALLER_NOT_ADMITTED = "caller_not_admitted"
    SERVICE_NOT_ADMITTED = "service_not_admitted"
    CALLER_HEALTH_NOT_USABLE = "caller_health_not_usable"
    SERVICE_HEALTH_NOT_USABLE = "service_health_not_usable"
    MISSING_EVIDENCE_OBLIGATION = "missing_evidence_obligation"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


class SigilContractRequest(BaseModel):
    """A single governed request from a Hermes fleet service to Sigil.

    ``advisory``, ``paper_only``, ``broker_submission_denied``, and
    ``execution_authority_denied`` are not configuration flags — they are
    locked to the only safe values by validators. A caller cannot construct
    a request that asks Sigil to do anything other than produce an advisory,
    paper-only output with no broker-submission or execution authority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: int = Field(default=SIGIL_CONTRACT_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    caller_identity_id: str = Field(..., min_length=1, max_length=128)
    service_identity_id: str = Field(..., min_length=1, max_length=128)
    operation: str = Field(..., min_length=1, max_length=128)
    requested_at: int = Field(..., ge=0)
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS
    )
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    evidence_obligation_required: bool = True
    advisory: bool = True
    paper_only: bool = True
    broker_submission_denied: bool = True
    execution_authority_denied: bool = True
    production_mutation_denied: bool = True

    @field_validator("contract_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @field_validator("operation")
    @classmethod
    def _check_operation(cls, v: str) -> str:
        if v not in SUPPORTED_SIGIL_OPERATIONS:
            raise ValueError(f"unsupported Sigil operation: {v!r}")
        return v

    @model_validator(mode="after")
    def _locked_safety_invariants(self) -> "SigilContractRequest":
        if not self.advisory:
            raise ValueError("Sigil contract requests must be advisory")
        if not self.paper_only:
            raise ValueError("Sigil contract requests must be paper_only")
        if not self.broker_submission_denied:
            raise ValueError("Sigil contract requests must deny broker submission")
        if not self.execution_authority_denied:
            raise ValueError("Sigil contract requests must deny execution authority")
        if not self.production_mutation_denied:
            raise ValueError("Sigil contract requests must deny production mutation")
        if self.caller_identity_id == self.service_identity_id:
            raise ValueError("a Sigil contract request cannot self-address")
        return self

    @property
    def request_digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class SigilContractOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SigilContractResponse(BaseModel):
    """A single governed response from Sigil back to the requesting caller.

    ``advisory_output`` is always advisory: constructing a response with
    ``execution_authority_granted=True`` or ``broker_submission_granted=True``
    is rejected outright, matching the request-side lock.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    response_version: int = Field(default=SIGIL_CONTRACT_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    outcome: SigilContractOutcome
    rejection_code: Optional[SigilRejectionCode] = None
    advisory_output: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: Tuple[str, ...] = ()
    completed_at: int = Field(..., ge=0)
    execution_authority_granted: bool = False
    broker_submission_granted: bool = False

    @field_validator("response_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @model_validator(mode="after")
    def _consistency(self) -> "SigilContractResponse":
        if self.execution_authority_granted:
            raise ValueError(
                "a Sigil contract response can never grant execution authority"
            )
        if self.broker_submission_granted:
            raise ValueError(
                "a Sigil contract response can never grant broker submission"
            )
        if self.outcome == SigilContractOutcome.REJECTED and not self.rejection_code:
            raise ValueError("a rejected response requires a rejection_code")
        if self.outcome == SigilContractOutcome.ACCEPTED and self.rejection_code:
            raise ValueError("an accepted response cannot carry a rejection_code")
        if self.outcome == SigilContractOutcome.ACCEPTED and not self.evidence_refs:
            raise ValueError(
                "an accepted response requires at least one evidence reference"
            )
        return self


def evaluate_sigil_contract_request(
    request: SigilContractRequest,
    *,
    caller_admission: Optional[AdmissionDecision],
    service_admission: Optional[AdmissionDecision],
    caller_health: Optional[HealthReport],
    service_health: Optional[HealthReport],
    now: int,
) -> Tuple[bool, Optional[SigilRejectionCode]]:
    """Deterministically decide whether a request may proceed. Fail closed.

    Returns ``(admitted, rejection_code)``. This function performs no I/O and
    never calls into Sigil itself — it only evaluates whether the
    preconditions Sigil requires (caller/service admission and health) are
    satisfied. The actual advisory computation is the caller's
    responsibility; this function exists purely as a governance gate in
    front of it.
    """
    if request.contract_version not in SUPPORTED_SIGIL_CONTRACT_VERSIONS:
        return False, SigilRejectionCode.UNSUPPORTED_CONTRACT_VERSION

    if request.operation not in SUPPORTED_SIGIL_OPERATIONS:
        return False, SigilRejectionCode.UNSUPPORTED_OPERATION

    if (
        caller_admission is None
        or caller_admission.outcome != AdmissionOutcome.ADMITTED
        or not caller_admission.is_current(now)
    ):
        return False, SigilRejectionCode.CALLER_NOT_ADMITTED

    if (
        service_admission is None
        or service_admission.outcome != AdmissionOutcome.ADMITTED
        or not service_admission.is_current(now)
    ):
        return False, SigilRejectionCode.SERVICE_NOT_ADMITTED

    if not is_usable_for_admission(caller_health, now=now):
        return False, SigilRejectionCode.CALLER_HEALTH_NOT_USABLE

    if not is_usable_for_admission(service_health, now=now):
        return False, SigilRejectionCode.SERVICE_HEALTH_NOT_USABLE

    if request.evidence_obligation_required is not True:
        # Evidence obligation is mandatory for every real operation; a
        # request that opts out of it is malformed, not merely unusual.
        return False, SigilRejectionCode.MISSING_EVIDENCE_OBLIGATION

    return True, None
