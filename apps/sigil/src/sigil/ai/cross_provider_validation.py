"""Deterministic advisory validation across governed provider outputs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum

from .registry import canonical_digest

CROSS_PROVIDER_VALIDATION_VERSION = 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class CrossProviderValidationState(str, Enum):
    CONSISTENT = "consistent"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class ProviderClaim:
    claim_id: str
    provider_id: str
    model_id: str
    subject: str
    normalized_value: str
    evidence_references: tuple[str, ...]
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.claim_id or not self.provider_id or not self.model_id:
            raise ValueError("provider claim identities cannot be blank")
        if not self.subject.strip() or not self.normalized_value.strip():
            raise ValueError("provider claim content cannot be blank")
        if not self.evidence_references or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_references
        ):
            raise ValueError("provider claims require SHA-256 evidence references")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("provider claim confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class CrossProviderComparison:
    subject: str
    gemma_claim_id: str | None
    claude_claim_id: str | None
    state: str
    shared_evidence: tuple[str, ...]
    gemma_value: str | None
    claude_value: str | None


@dataclass(frozen=True, slots=True)
class CrossProviderValidationReport:
    validation_id: str
    target_revision: str
    target_digest: str
    gemma_provider_id: str
    claude_provider_id: str
    comparisons: tuple[CrossProviderComparison, ...]
    agreement_count: int
    disagreement_count: int
    missing_coverage_count: int
    state: CrossProviderValidationState
    human_review_required: bool
    validated_at: str
    validation_digest: str
    promotion_authorized: bool = False
    release_authority: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.validation_id or not self.target_revision:
            raise ValueError("validation identities cannot be blank")
        if _SHA256.fullmatch(self.target_digest) is None:
            raise ValueError("validation target digest must be SHA-256")
        if _SHA256.fullmatch(self.validation_digest) is None:
            raise ValueError("validation digest must be SHA-256")
        if min(
            self.agreement_count,
            self.disagreement_count,
            self.missing_coverage_count,
        ) < 0:
            raise ValueError("validation counts cannot be negative")
        if not self.validated_at:
            raise ValueError("validation timestamp cannot be blank")
        if (
            self.promotion_authorized is not False
            or self.release_authority is not False
            or self.approval_authority is not False
            or self.execution_authorized is not False
            or self.broker_submission is not False
            or self.portfolio_mutation is not False
            or self.tool_execution is not False
            or self.paper_only is not True
        ):
            raise ValueError("cross-provider validation cannot receive authority")


def validate_cross_provider_claims(
    *,
    target_revision: str,
    target_digest: str,
    gemma_claims: tuple[ProviderClaim, ...],
    claude_claims: tuple[ProviderClaim, ...],
    validated_at: str,
) -> CrossProviderValidationReport:
    if not target_revision:
        raise ValueError("target revision cannot be blank")
    if _SHA256.fullmatch(target_digest) is None:
        raise ValueError("target digest must be SHA-256")
    if not gemma_claims or not claude_claims:
        raise ValueError("cross-provider validation requires both providers")
    if any(claim.provider_id != "local-gemma" for claim in gemma_claims):
        raise ValueError("Gemma claims must come from local-gemma")
    if any(claim.provider_id != "hermes-claude" for claim in claude_claims):
        raise ValueError("Claude claims must come from hermes-claude")

    gemma_by_subject = _index_claims(gemma_claims)
    claude_by_subject = _index_claims(claude_claims)
    comparisons = []

    for subject in sorted(set(gemma_by_subject) | set(claude_by_subject)):
        gemma = gemma_by_subject.get(subject)
        claude = claude_by_subject.get(subject)
        if gemma is None or claude is None:
            state = "missing_coverage"
            shared_evidence = ()
        else:
            shared_evidence = tuple(
                sorted(set(gemma.evidence_references) & set(claude.evidence_references))
            )
            if not shared_evidence:
                state = "insufficient_shared_evidence"
            elif gemma.normalized_value == claude.normalized_value:
                state = "agreement"
            else:
                state = "disagreement"
        comparisons.append(
            CrossProviderComparison(
                subject=subject,
                gemma_claim_id=None if gemma is None else gemma.claim_id,
                claude_claim_id=None if claude is None else claude.claim_id,
                state=state,
                shared_evidence=shared_evidence,
                gemma_value=None if gemma is None else gemma.normalized_value,
                claude_value=None if claude is None else claude.normalized_value,
            )
        )

    agreement_count = sum(item.state == "agreement" for item in comparisons)
    disagreement_count = sum(item.state == "disagreement" for item in comparisons)
    missing_count = sum(
        item.state in {"missing_coverage", "insufficient_shared_evidence"}
        for item in comparisons
    )

    if disagreement_count:
        report_state = CrossProviderValidationState.REVIEW_REQUIRED
    elif missing_count:
        report_state = CrossProviderValidationState.INSUFFICIENT_EVIDENCE
    else:
        report_state = CrossProviderValidationState.CONSISTENT

    human_review_required = report_state != CrossProviderValidationState.CONSISTENT
    validation_id = (
        "cross-provider-validation-"
        + canonical_digest(
            {
                "version": CROSS_PROVIDER_VALIDATION_VERSION,
                "target_revision": target_revision,
                "target_digest": target_digest,
                "gemma_claims": [
                    asdict(item)
                    for item in sorted(
                        gemma_claims,
                        key=lambda claim: (
                            claim.subject,
                            claim.claim_id,
                            claim.model_id,
                        ),
                    )
                ],
                "claude_claims": [
                    asdict(item)
                    for item in sorted(
                        claude_claims,
                        key=lambda claim: (
                            claim.subject,
                            claim.claim_id,
                            claim.model_id,
                        ),
                    )
                ],
            }
        )
    )
    payload = {
        "version": CROSS_PROVIDER_VALIDATION_VERSION,
        "validation_id": validation_id,
        "target_revision": target_revision,
        "target_digest": target_digest,
        "gemma_provider_id": "local-gemma",
        "claude_provider_id": "hermes-claude",
        "comparisons": [asdict(item) for item in comparisons],
        "agreement_count": agreement_count,
        "disagreement_count": disagreement_count,
        "missing_coverage_count": missing_count,
        "state": report_state.value,
        "human_review_required": human_review_required,
        "validated_at": validated_at,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }
    return CrossProviderValidationReport(
        validation_id=validation_id,
        target_revision=target_revision,
        target_digest=target_digest,
        gemma_provider_id="local-gemma",
        claude_provider_id="hermes-claude",
        comparisons=tuple(comparisons),
        agreement_count=agreement_count,
        disagreement_count=disagreement_count,
        missing_coverage_count=missing_count,
        state=report_state,
        human_review_required=human_review_required,
        validated_at=validated_at,
        validation_digest=f"sha256:{canonical_digest(payload)}",
    )


def _index_claims(claims: tuple[ProviderClaim, ...]) -> dict[str, ProviderClaim]:
    result = {}
    for claim in claims:
        if claim.subject in result:
            raise ValueError("provider claims cannot duplicate a subject")
        result[claim.subject] = claim
    return result

_ZERO_HASH = "0" * 64


class CrossProviderValidationStoreError(RuntimeError):
    """Base error for durable cross-provider validation persistence."""


class CrossProviderValidationStoreCorruptionError(
    CrossProviderValidationStoreError
):
    """Validation history failed integrity validation."""


class CrossProviderValidationStoreConflictError(
    CrossProviderValidationStoreError
):
    """A validation identity is already committed."""


class DurableCrossProviderValidationStore:
    """Append-only, hash-chained storage for validation reports."""

    def __init__(self, state_root):
        import fcntl
        import os
        from pathlib import Path

        if not isinstance(state_root, Path) or not state_root.is_absolute():
            raise CrossProviderValidationStoreError(
                "validation state root must be an absolute Path"
            )
        if (
            state_root.is_symlink()
            or not state_root.exists()
            or not state_root.is_dir()
        ):
            raise CrossProviderValidationStoreError(
                "validation state root must be an existing non-symlink directory"
            )

        self._fcntl = fcntl
        self._os = os
        self.directory = state_root / "governed-cross-provider-validation-v1"
        self.path = self.directory / "validations.jsonl"
        self.lock_path = self.directory / "validations.lock"
        self.directory.mkdir(mode=0o700, exist_ok=True)

        if (
            self.directory.is_symlink()
            or self.path.is_symlink()
            or self.lock_path.is_symlink()
        ):
            raise CrossProviderValidationStoreError(
                "validation paths cannot use symlinks"
            )

        descriptor = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        os.close(descriptor)

    def _locked(self):
        from contextlib import contextmanager

        @contextmanager
        def manager():
            descriptor = self._os.open(
                self.lock_path,
                self._os.O_RDWR | self._os.O_NOFOLLOW,
            )
            try:
                self._fcntl.flock(descriptor, self._fcntl.LOCK_EX)
                yield
            finally:
                self._fcntl.flock(descriptor, self._fcntl.LOCK_UN)
                self._os.close(descriptor)

        return manager()

    def append(
        self,
        report: CrossProviderValidationReport,
    ) -> CrossProviderValidationReport:
        with self._locked():
            records = self._read_unlocked(recover_truncated_tail=True)
            if any(item.validation_id == report.validation_id for item in records):
                raise CrossProviderValidationStoreConflictError(
                    "duplicate cross-provider validation identity"
                )

            previous = self._last_entry_hash() if records else _ZERO_HASH
            envelope = {
                "report": _validation_report_payload(report),
                "entry_hash": "",
                "previous_entry_hash": previous,
                "sequence": len(records) + 1,
                "store_version": CROSS_PROVIDER_VALIDATION_VERSION,
            }
            envelope["entry_hash"] = canonical_digest(
                {
                    key: value
                    for key, value in envelope.items()
                    if key != "entry_hash"
                }
            )

            import json

            encoded = (
                json.dumps(
                    envelope,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            descriptor = self._os.open(
                self.path,
                self._os.O_CREAT
                | self._os.O_APPEND
                | self._os.O_WRONLY
                | self._os.O_NOFOLLOW,
                0o600,
            )
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = self._os.write(descriptor, remaining)
                    if written <= 0:
                        raise CrossProviderValidationStoreError(
                            "validation report write made no progress"
                        )
                    remaining = remaining[written:]
                self._os.fsync(descriptor)
            finally:
                self._os.close(descriptor)

            self._fsync_directory()
            return report

    def read_reports(
        self,
        *,
        recover_truncated_tail: bool = True,
    ) -> tuple[CrossProviderValidationReport, ...]:
        with self._locked():
            return self._read_unlocked(
                recover_truncated_tail=recover_truncated_tail
            )

    def _read_unlocked(
        self,
        *,
        recover_truncated_tail: bool,
    ) -> tuple[CrossProviderValidationReport, ...]:
        import json

        if not self.path.exists():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise CrossProviderValidationStoreCorruptionError(
                "validation store path is unsafe"
            )

        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if not recover_truncated_tail:
                raise CrossProviderValidationStoreCorruptionError(
                    "validation store has a truncated tail"
                )
            descriptor = self._os.open(
                self.path,
                self._os.O_WRONLY | self._os.O_NOFOLLOW,
            )
            try:
                self._os.ftruncate(descriptor, boundary)
                self._os.fsync(descriptor)
            finally:
                self._os.close(descriptor)
            self._fsync_directory()
            raw = raw[:boundary]

        reports = []
        identities = set()
        previous = _ZERO_HASH
        self._validated_last_hash = _ZERO_HASH

        for number, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
                if set(envelope) != {
                    "report",
                    "entry_hash",
                    "previous_entry_hash",
                    "sequence",
                    "store_version",
                }:
                    raise CrossProviderValidationStoreCorruptionError(
                        "validation envelope shape is invalid"
                    )
                if envelope["store_version"] != CROSS_PROVIDER_VALIDATION_VERSION:
                    raise CrossProviderValidationStoreCorruptionError(
                        "unsupported validation store schema"
                    )
                if envelope["sequence"] != number:
                    raise CrossProviderValidationStoreCorruptionError(
                        "validation sequence mismatch"
                    )
                if envelope["previous_entry_hash"] != previous:
                    raise CrossProviderValidationStoreCorruptionError(
                        "validation hash chain mismatch"
                    )

                expected = canonical_digest(
                    {
                        key: value
                        for key, value in envelope.items()
                        if key != "entry_hash"
                    }
                )
                if envelope["entry_hash"] != expected:
                    raise CrossProviderValidationStoreCorruptionError(
                        "validation entry hash mismatch"
                    )

                report = _decode_validation_report(envelope["report"])
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise CrossProviderValidationStoreCorruptionError(
                    f"corrupt validation report line {number}"
                ) from error

            if report.validation_id in identities:
                raise CrossProviderValidationStoreCorruptionError(
                    "duplicate validation identity"
                )

            identities.add(report.validation_id)
            reports.append(report)
            previous = envelope["entry_hash"]
            self._validated_last_hash = previous

        return tuple(reports)

    def _last_entry_hash(self) -> str:
        return getattr(self, "_validated_last_hash", _ZERO_HASH)

    def _fsync_directory(self) -> None:
        descriptor = self._os.open(
            self.directory,
            self._os.O_RDONLY | self._os.O_DIRECTORY,
        )
        try:
            self._os.fsync(descriptor)
        finally:
            self._os.close(descriptor)


def _validation_report_payload(
    report: CrossProviderValidationReport,
) -> dict[str, object]:
    payload = asdict(report)
    payload["state"] = report.state.value
    return payload


def _decode_validation_report(
    payload: object,
) -> CrossProviderValidationReport:
    if not isinstance(payload, dict):
        raise CrossProviderValidationStoreCorruptionError(
            "validation report payload shape is invalid"
        )

    comparisons = tuple(
        CrossProviderComparison(
            subject=item["subject"],
            gemma_claim_id=item["gemma_claim_id"],
            claude_claim_id=item["claude_claim_id"],
            state=item["state"],
            shared_evidence=tuple(item["shared_evidence"]),
            gemma_value=item["gemma_value"],
            claude_value=item["claude_value"],
        )
        for item in payload["comparisons"]
    )

    return CrossProviderValidationReport(
        validation_id=payload["validation_id"],
        target_revision=payload["target_revision"],
        target_digest=payload["target_digest"],
        gemma_provider_id=payload["gemma_provider_id"],
        claude_provider_id=payload["claude_provider_id"],
        comparisons=comparisons,
        agreement_count=payload["agreement_count"],
        disagreement_count=payload["disagreement_count"],
        missing_coverage_count=payload["missing_coverage_count"],
        state=CrossProviderValidationState(payload["state"]),
        human_review_required=payload["human_review_required"],
        validated_at=payload["validated_at"],
        validation_digest=payload["validation_digest"],
        promotion_authorized=payload["promotion_authorized"],
        release_authority=payload["release_authority"],
        approval_authority=payload["approval_authority"],
        execution_authorized=payload["execution_authorized"],
        broker_submission=payload["broker_submission"],
        portfolio_mutation=payload["portfolio_mutation"],
        tool_execution=payload["tool_execution"],
        paper_only=payload["paper_only"],
    )


def cross_provider_validation_status(state_root) -> dict[str, object]:
    """Return a sanitized read-only validation status projection."""

    try:
        store = DurableCrossProviderValidationStore(state_root)
        reports = store.read_reports(recover_truncated_tail=False)
    except CrossProviderValidationStoreCorruptionError:
        return _empty_validation_status("invalid", "corrupt")
    except CrossProviderValidationStoreError:
        return _empty_validation_status("unavailable", "unavailable")

    latest = reports[-1] if reports else None
    return {
        "state": "ready" if reports else "empty",
        "store_health": "healthy" if reports else "empty",
        "report_count": len(reports),
        "consistent_count": sum(
            item.state == CrossProviderValidationState.CONSISTENT
            for item in reports
        ),
        "review_required_count": sum(
            item.state == CrossProviderValidationState.REVIEW_REQUIRED
            for item in reports
        ),
        "insufficient_evidence_count": sum(
            item.state
            == CrossProviderValidationState.INSUFFICIENT_EVIDENCE
            for item in reports
        ),
        "latest_report": None
        if latest is None
        else {
            "validation_id": latest.validation_id,
            "target_revision": latest.target_revision,
            "target_digest": latest.target_digest,
            "state": latest.state.value,
            "agreement_count": latest.agreement_count,
            "disagreement_count": latest.disagreement_count,
            "missing_coverage_count": latest.missing_coverage_count,
            "human_review_required": latest.human_review_required,
            "validation_digest": latest.validation_digest,
            "validated_at": latest.validated_at,
        },
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }


def _empty_validation_status(
    state: str,
    health: str,
) -> dict[str, object]:
    return {
        "state": state,
        "store_health": health,
        "report_count": 0,
        "consistent_count": 0,
        "review_required_count": 0,
        "insufficient_evidence_count": 0,
        "latest_report": None,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }
