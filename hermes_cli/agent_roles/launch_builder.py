"""Stateless governed launch-contract derivation.

The builder converts an existing assignment and its registered role into an
immutable :class:`LaunchContract`. It performs no persistence, assignment
transition, workspace creation, provider resolution, or process execution.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Tuple

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchWorkspace,
    LaunchWorkspaceMode,
)
from hermes_cli.agent_roles.models import (
    AgentRole,
    Assignment,
    AssignmentStatus,
)


class LaunchContractBuildError(ValueError):
    """Raised when source data cannot form a trustworthy contract."""


class LaunchContractBuilder:
    """Derive immutable launch contracts without mutating source state."""

    def build(
        self,
        assignment: Assignment,
        role: AgentRole,
        *,
        repository_root: str,
        runtime: str,
        timestamp: int,
        contract_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        base_ref: Optional[str] = None,
        engine: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        environment: Tuple[Tuple[str, str], ...] = (),
    ) -> LaunchContract:
        """Build a ready or explicitly blocked immutable launch contract."""
        self._require_source_identity(assignment, role)

        instructions = self._required_text(
            assignment.instructions,
            "assignment instructions",
        )
        agent_id = self._required_text(
            assignment.assigned_agent_id,
            "assigned_agent_id",
        )
        repository_root = self._required_text(
            repository_root,
            "repository_root",
        )
        runtime = self._required_text(runtime, "runtime")

        metadata = self._metadata(assignment.metadata)
        risk_level = self._metadata_text(
            metadata,
            "risk_level",
            default="medium",
        )
        requested_paths = self._metadata_text_tuple(
            metadata,
            "requested_paths",
        )
        metadata_denied_paths = self._metadata_text_tuple(
            metadata,
            "denied_paths",
        )
        modifies_repository = self._metadata_bool(
            metadata,
            "modifies_repository",
            default=False,
        )
        human_approved = self._metadata_bool(
            metadata,
            "human_approved",
            default=False,
        )

        blocked_reasons = self._blocked_reasons(
            assignment,
            role,
            risk_level=risk_level,
            requested_paths=requested_paths,
            modifies_repository=modifies_repository,
            human_approved=human_approved,
        )

        denied_paths = self._deduplicate(
            tuple(role.policy.denied_paths)
            + metadata_denied_paths
        )

        if modifies_repository:
            workspace_mode = LaunchWorkspaceMode.ISOLATED_WRITE
            resolved_workspace_id = (
                self._optional_text(workspace_id, "workspace_id")
                or f"workspace_{assignment.assignment_id}"
            )
        else:
            workspace_mode = LaunchWorkspaceMode.READ_ONLY
            resolved_workspace_id = self._optional_text(
                workspace_id,
                "workspace_id",
            )

        status = (
            LaunchContractStatus.BLOCKED
            if blocked_reasons
            else LaunchContractStatus.READY
        )

        return LaunchContract(
            contract_id=(
                self._optional_text(contract_id, "contract_id")
                or (
                    f"launch_{assignment.assignment_id}"
                    f"_v{assignment.version}"
                )
            ),
            project_id=assignment.project_id,
            assignment_id=assignment.assignment_id,
            role_id=assignment.role_id,
            agent_id=agent_id,
            backlog_item_id=assignment.backlog_item_id,
            status=status,
            instructions=instructions,
            created_at=timestamp,
            correlation_id=assignment.correlation_id,
            causation_id=assignment.assignment_id,
            blocked_reasons=blocked_reasons,
            workspace=LaunchWorkspace(
                mode=workspace_mode,
                repository_root=repository_root,
                workspace_id=resolved_workspace_id,
                base_ref=self._optional_text(base_ref, "base_ref"),
            ),
            policy=LaunchPolicy(
                risk_level=risk_level,
                modifies_repository=modifies_repository,
                human_approved=human_approved,
                allowed_paths=requested_paths,
                denied_paths=denied_paths,
                required_capabilities=tuple(
                    assignment.required_capabilities
                ),
            ),
            environment=LaunchEnvironment(
                runtime=runtime,
                engine=self._optional_text(engine, "engine"),
                provider=self._optional_text(provider, "provider"),
                model=self._optional_text(model, "model"),
                environment=environment,
            ),
        )

    @staticmethod
    def _require_source_identity(
        assignment: Assignment,
        role: AgentRole,
    ) -> None:
        if assignment.project_id != role.project_id:
            raise LaunchContractBuildError(
                "assignment and role project_id must match"
            )

        if assignment.role_id != role.role_id:
            raise LaunchContractBuildError(
                "assignment role_id does not match supplied role"
            )

    @classmethod
    def _blocked_reasons(
        cls,
        assignment: Assignment,
        role: AgentRole,
        *,
        risk_level: str,
        requested_paths: Tuple[str, ...],
        modifies_repository: bool,
        human_approved: bool,
    ) -> Tuple[str, ...]:
        reasons = []

        if assignment.status != AssignmentStatus.ACCEPTED:
            reasons.append(
                "assignment must be accepted before launch"
            )

        if not role.active:
            reasons.append("role is inactive")

        if risk_level not in role.policy.allowed_risk_levels:
            reasons.append(
                f"risk level {risk_level!r} is not allowed by role"
            )

        if (
            modifies_repository
            and not role.policy.may_modify_repository
        ):
            reasons.append(
                "role may not modify the repository"
            )

        if (
            role.policy.requires_human_approval
            and not human_approved
        ):
            reasons.append("human approval is required")

        available_capabilities = {
            capability.capability_id
            for capability in role.capabilities
        }
        missing_capabilities = [
            capability_id
            for capability_id in assignment.required_capabilities
            if capability_id not in available_capabilities
        ]

        if missing_capabilities:
            reasons.append(
                "role lacks required capabilities: "
                + ", ".join(missing_capabilities)
            )

        for path in requested_paths:
            if any(
                cls._path_is_within(path, denied)
                for denied in role.policy.denied_paths
            ):
                reasons.append(
                    f"requested path is denied by role policy: {path}"
                )
                continue

            if (
                role.policy.allowed_paths
                and not any(
                    cls._path_is_within(path, allowed)
                    for allowed in role.policy.allowed_paths
                )
            ):
                reasons.append(
                    f"requested path is outside role policy: {path}"
                )

        return cls._deduplicate(reasons)

    @staticmethod
    def _path_is_within(path: str, policy_path: str) -> bool:
        path = path.strip().strip("/")
        policy_path = policy_path.strip().strip("/")

        return (
            path == policy_path
            or path.startswith(policy_path + "/")
        )

    @staticmethod
    def _metadata(
        raw: object,
    ) -> Mapping[str, object]:
        if raw is None:
            return {}

        if not isinstance(raw, Mapping):
            raise LaunchContractBuildError(
                "assignment metadata must be a mapping"
            )

        return raw

    @classmethod
    def _metadata_text(
        cls,
        metadata: Mapping[str, object],
        key: str,
        *,
        default: str,
    ) -> str:
        value = metadata.get(key, default)

        if not isinstance(value, str):
            raise LaunchContractBuildError(
                f"assignment metadata {key!r} must be text"
            )

        return cls._required_text(value, key).lower()

    @classmethod
    def _metadata_text_tuple(
        cls,
        metadata: Mapping[str, object],
        key: str,
    ) -> Tuple[str, ...]:
        raw = metadata.get(key, ())

        if raw is None:
            return ()

        if isinstance(raw, str) or not isinstance(
            raw,
            (list, tuple),
        ):
            raise LaunchContractBuildError(
                f"assignment metadata {key!r} "
                "must be a list or tuple of text"
            )

        values = []

        for item in raw:
            if not isinstance(item, str):
                raise LaunchContractBuildError(
                    f"assignment metadata {key!r} "
                    "must contain only text"
                )

            values.append(cls._required_text(item, key))

        return cls._deduplicate(values)

    @staticmethod
    def _metadata_bool(
        metadata: Mapping[str, object],
        key: str,
        *,
        default: bool,
    ) -> bool:
        value = metadata.get(key, default)

        if not isinstance(value, bool):
            raise LaunchContractBuildError(
                f"assignment metadata {key!r} must be boolean"
            )

        return value

    @staticmethod
    def _required_text(
        value: Optional[str],
        field_name: str,
    ) -> str:
        if value is None:
            raise LaunchContractBuildError(
                f"{field_name} is required"
            )

        normalised = value.strip()

        if not normalised:
            raise LaunchContractBuildError(
                f"{field_name} must not be blank"
            )

        return normalised

    @classmethod
    def _optional_text(
        cls,
        value: Optional[str],
        field_name: str,
    ) -> Optional[str]:
        if value is None:
            return None

        return cls._required_text(value, field_name)

    @staticmethod
    def _deduplicate(
        values: Iterable[str],
    ) -> Tuple[str, ...]:
        result = []
        seen = set()

        for value in values:
            if value in seen:
                continue

            seen.add(value)
            result.append(value)

        return tuple(result)
