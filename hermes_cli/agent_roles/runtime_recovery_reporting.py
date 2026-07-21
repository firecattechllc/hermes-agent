"""Governed runtime recovery reporting.

This module derives operator-facing reports from immutable recovery
reconciliation and closure projections. Reports do not create or mutate
runtime state and do not emit additional Mission Control events.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .runtime_recovery_closure_visibility import (
    RuntimeRecoveryClosureVisibilityService,
)
from .runtime_recovery_reconciliation_visibility import (
    RuntimeRecoveryReconciliationVisibilityService,
)


class RuntimeRecoveryReport(BaseModel):
    """Deterministic operator-facing summary of one recovery lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    schema_version: int = 1

    project_id: str
    recovery_id: str
    execution_id: str
    recovery_execution_id: str
    reconciliation_id: str
    closure_id: Optional[str]

    action: str
    recovery_execution_state: str
    reconciliation_state: str
    closure_state: Optional[str]

    actor_id: str
    correlation_id: str
    causation_id: str

    reconciled_at: int
    closed_at: Optional[int]
    closure_latency: Optional[int]

    reason: str
    closure_reason: Optional[str]

    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)

    reconciliation_checksum: str
    closure_checksum: Optional[str]
    source_checksums: Tuple[str, ...]

    is_terminal: bool
    requires_attention: bool

    checksum: str


class RuntimeRecoveryProjectReport(BaseModel):
    """Deterministic project-level recovery reporting snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    schema_version: int = 1
    project_id: str
    generated_at: int

    total_recoveries: int
    terminal_recoveries: int
    open_recoveries: int
    attention_required: int

    reports: Tuple[RuntimeRecoveryReport, ...]

    checksum: str


def _clean_identifier(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _ordered_unique(values) -> Tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []

    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)

    return tuple(result)


def _checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    return getattr(record, name, default)


class RuntimeRecoveryReportingService:
    """Builds immutable reports from governed recovery projections."""

    def __init__(
        self,
        reconciliation_visibility: RuntimeRecoveryReconciliationVisibilityService,
        closure_visibility: RuntimeRecoveryClosureVisibilityService,
    ) -> None:
        self._reconciliation_visibility = reconciliation_visibility
        self._closure_visibility = closure_visibility

    def list_reports(
        self,
        project_id: str,
        *,
        requires_attention: Optional[bool] = None,
    ) -> Tuple[RuntimeRecoveryReport, ...]:
        project_id = _clean_identifier(project_id, "project_id")

        reconciliations = self._reconciliation_visibility.list_records(
            project_id
        )
        closures = self._closure_visibility.list_records(project_id)

        closures_by_reconciliation: dict[str, Any] = {}

        for closure in closures:
            reconciliation_id = _record_value(
                closure,
                "reconciliation_id",
            )

            if not reconciliation_id:
                raise ValueError(
                    "runtime recovery report closure missing reconciliation_id"
                )

            existing = closures_by_reconciliation.get(reconciliation_id)

            if existing is not None:
                raise ValueError(
                    "runtime recovery report found multiple closures for "
                    f"reconciliation {reconciliation_id}"
                )

            closures_by_reconciliation[reconciliation_id] = closure

        reports = tuple(
            self._build_report(
                reconciliation,
                closures_by_reconciliation.get(
                    _record_value(reconciliation, "reconciliation_id")
                ),
            )
            for reconciliation in sorted(
                reconciliations,
                key=lambda item: (
                    _record_value(item, "reconciled_at", 0),
                    _record_value(item, "reconciliation_id", ""),
                ),
            )
        )

        if requires_attention is not None:
            reports = tuple(
                report
                for report in reports
                if report.requires_attention is requires_attention
            )

        return reports

    def get_report(
        self,
        project_id: str,
        recovery_id: str,
    ) -> RuntimeRecoveryReport:
        project_id = _clean_identifier(project_id, "project_id")
        recovery_id = _clean_identifier(recovery_id, "recovery_id")

        matches = tuple(
            report
            for report in self.list_reports(project_id)
            if report.recovery_id == recovery_id
        )

        if not matches:
            raise KeyError(
                f"runtime recovery report not found: {recovery_id}"
            )

        if len(matches) > 1:
            raise ValueError(
                f"multiple runtime recovery reports found: {recovery_id}"
            )

        return matches[0]

    def project_report(
        self,
        project_id: str,
        *,
        generated_at: int,
    ) -> RuntimeRecoveryProjectReport:
        project_id = _clean_identifier(project_id, "project_id")

        if generated_at < 0:
            raise ValueError("generated_at must be non-negative")

        reports = self.list_reports(project_id)

        terminal_recoveries = sum(
            1 for report in reports if report.is_terminal
        )
        attention_required = sum(
            1 for report in reports if report.requires_attention
        )

        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "generated_at": generated_at,
            "total_recoveries": len(reports),
            "terminal_recoveries": terminal_recoveries,
            "open_recoveries": len(reports) - terminal_recoveries,
            "attention_required": attention_required,
            "report_checksums": [
                report.checksum for report in reports
            ],
        }

        checksum = _checksum(payload)

        return RuntimeRecoveryProjectReport(
            report_id=f"runtime_recovery_project_report_{checksum[:24]}",
            project_id=project_id,
            generated_at=generated_at,
            total_recoveries=len(reports),
            terminal_recoveries=terminal_recoveries,
            open_recoveries=len(reports) - terminal_recoveries,
            attention_required=attention_required,
            reports=reports,
            checksum=checksum,
        )

    def _build_report(
        self,
        reconciliation: Any,
        closure: Optional[Any],
    ) -> RuntimeRecoveryReport:
        project_id = _clean_identifier(
            _record_value(reconciliation, "project_id", ""),
            "project_id",
        )
        reconciliation_id = _clean_identifier(
            _record_value(reconciliation, "reconciliation_id", ""),
            "reconciliation_id",
        )
        recovery_id = _clean_identifier(
            _record_value(reconciliation, "recovery_id", ""),
            "recovery_id",
        )
        execution_id = _clean_identifier(
            _record_value(reconciliation, "execution_id", ""),
            "execution_id",
        )
        recovery_execution_id = _clean_identifier(
            _record_value(
                reconciliation,
                "recovery_execution_id",
                "",
            ),
            "recovery_execution_id",
        )

        reconciled_at = int(
            _record_value(reconciliation, "reconciled_at", 0)
        )

        if reconciled_at < 0:
            raise ValueError("reconciled_at must be non-negative")

        closure_id: Optional[str] = None
        closure_state: Optional[str] = None
        closed_at: Optional[int] = None
        closure_reason: Optional[str] = None
        closure_checksum: Optional[str] = None

        if closure is not None:
            if _record_value(closure, "project_id") != project_id:
                raise ValueError(
                    "runtime recovery report project provenance mismatch"
                )

            if (
                _record_value(closure, "reconciliation_id")
                != reconciliation_id
            ):
                raise ValueError(
                    "runtime recovery report reconciliation provenance "
                    "mismatch"
                )

            if _record_value(closure, "recovery_id") != recovery_id:
                raise ValueError(
                    "runtime recovery report recovery provenance mismatch"
                )

            closure_id = _clean_identifier(
                _record_value(closure, "closure_id", ""),
                "closure_id",
            )
            closure_state = _record_value(
                closure,
                "closure_state",
            )
            closed_at = int(_record_value(closure, "closed_at", 0))
            closure_reason = _record_value(closure, "reason")
            closure_checksum = _clean_identifier(
                _record_value(closure, "checksum", ""),
                "closure checksum",
            )

            if closed_at < reconciled_at:
                raise ValueError(
                    "runtime recovery closure predates reconciliation"
                )

        evidence_refs = _ordered_unique(
            tuple(_record_value(reconciliation, "evidence_refs", ()))
            + tuple(
                _record_value(closure, "evidence_refs", ())
                if closure is not None
                else ()
            )
        )

        reconciliation_checksum = _clean_identifier(
            _record_value(reconciliation, "checksum", ""),
            "reconciliation checksum",
        )

        source_checksums = _ordered_unique(
            (
                _record_value(
                    reconciliation,
                    "source_recovery_checksum",
                    "",
                ),
                _record_value(
                    reconciliation,
                    "source_recovery_execution_checksum",
                    "",
                ),
                reconciliation_checksum,
                closure_checksum or "",
            )
        )

        reconciliation_state = str(
            _record_value(
                reconciliation,
                "reconciliation_state",
                "",
            )
        )
        recovery_execution_state = str(
            _record_value(
                reconciliation,
                "recovery_execution_state",
                "",
            )
        )

        is_terminal = closure is not None

        healthy_reconciliation_states = {
            "reconciled",
            "completed",
            "confirmed",
        }
        healthy_closure_states = {
            "closed",
            "completed",
            "resolved",
        }

        requires_attention = (
            reconciliation_state.lower()
            not in healthy_reconciliation_states
            or (
                closure_state is not None
                and str(closure_state).lower()
                not in healthy_closure_states
            )
            or not is_terminal
        )

        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "recovery_id": recovery_id,
            "execution_id": execution_id,
            "recovery_execution_id": recovery_execution_id,
            "reconciliation_id": reconciliation_id,
            "closure_id": closure_id,
            "action": str(_record_value(reconciliation, "action", "")),
            "recovery_execution_state": recovery_execution_state,
            "reconciliation_state": reconciliation_state,
            "closure_state": closure_state,
            "actor_id": str(
                _record_value(reconciliation, "actor_id", "")
            ),
            "correlation_id": str(
                _record_value(reconciliation, "correlation_id", "")
            ),
            "causation_id": str(
                _record_value(reconciliation, "causation_id", "")
            ),
            "reconciled_at": reconciled_at,
            "closed_at": closed_at,
            "closure_latency": (
                closed_at - reconciled_at
                if closed_at is not None
                else None
            ),
            "reason": str(
                _record_value(reconciliation, "reason", "")
            ),
            "closure_reason": closure_reason,
            "evidence_refs": list(evidence_refs),
            "reconciliation_checksum": reconciliation_checksum,
            "closure_checksum": closure_checksum,
            "source_checksums": list(source_checksums),
            "is_terminal": is_terminal,
            "requires_attention": requires_attention,
        }

        checksum = _checksum(payload)

        return RuntimeRecoveryReport(
            report_id=f"runtime_recovery_report_{checksum[:24]}",
            **payload,
            checksum=checksum,
        )
