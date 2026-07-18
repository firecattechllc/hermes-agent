"""Agent context renderer — produces bounded, role-filtered context packages.

The renderer produces provider-neutral structured data and text from a
:meth:`ContextService.build_snapshot`. It is intentionally free of any
model-provider-specific prompt code. Model providers receive structured data
and text; they decide how to incorporate it.

Agent roles supported:
- ``planner``    — objectives, roadmap, risks, blockers, architecture decisions
- ``builder``    — objectives, engineering lessons, operating constraints, project facts
- ``reviewer``   — architecture decisions, engineering lessons, known risks
- ``security``   — known risks, operating constraints, architecture decisions
- ``documentation`` — project facts, architecture decisions, operating constraints
- ``release``    — launches, roadmap, blockers, architecture decisions
- ``all``        — everything (default)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes_cli.context_engine import models as m

# ── Role → record type mapping ────────────────────────────────────────────────

_ROLE_RECORD_TYPES: Dict[str, List[m.RecordType]] = {
    "planner": [
        m.RecordType.OBJECTIVE,
        m.RecordType.ROADMAP_ITEM,
        m.RecordType.KNOWN_RISK,
        m.RecordType.BLOCKER,
        m.RecordType.ARCHITECTURE_DECISION,
    ],
    "builder": [
        m.RecordType.OBJECTIVE,
        m.RecordType.ENGINEERING_LESSON,
        m.RecordType.OPERATING_CONSTRAINT,
        m.RecordType.PROJECT_FACT,
        m.RecordType.ROADMAP_ITEM,
    ],
    "reviewer": [
        m.RecordType.ARCHITECTURE_DECISION,
        m.RecordType.ENGINEERING_LESSON,
        m.RecordType.KNOWN_RISK,
    ],
    "security": [
        m.RecordType.KNOWN_RISK,
        m.RecordType.OPERATING_CONSTRAINT,
        m.RecordType.ARCHITECTURE_DECISION,
    ],
    "documentation": [
        m.RecordType.PROJECT_FACT,
        m.RecordType.ARCHITECTURE_DECISION,
        m.RecordType.OPERATING_CONSTRAINT,
    ],
    "release": [
        m.RecordType.ROADMAP_ITEM,
        m.RecordType.BLOCKER,
        m.RecordType.ARCHITECTURE_DECISION,
    ],
}


# ── Agent context package ─────────────────────────────────────────────────────

@dataclass
class AgentContextPackage:
    """A bounded, structured agent context package.

    Produced by :func:`render_context`. Contains everything a planner, builder,
    reviewer, security, documentation, or release agent needs from a project,
    with provenance and snapshot metadata.
    """
    # Identity
    project_id: str
    project_name: Optional[str] = None
    repository_identity: Optional[str] = None

    # Mission
    active_objectives: List[m.ContextRecord] = field(default_factory=list)
    active_launches: List[m.LaunchContext] = field(default_factory=list)

    # Roadmaps and priorities
    roadmap_items: List[m.ContextRecord] = field(default_factory=list)

    # Architecture and decisions
    architecture_decisions: List[m.ContextRecord] = field(default_factory=list)

    # Knowledge
    engineering_lessons: List[m.ContextRecord] = field(default_factory=list)
    project_facts: List[m.ContextRecord] = field(default_factory=list)

    # Risk and constraints
    known_risks: List[m.ContextRecord] = field(default_factory=list)
    blockers: List[m.ContextRecord] = field(default_factory=list)
    operating_constraints: List[m.ContextRecord] = field(default_factory=list)

    # Snapshot metadata
    snapshot_version: int = 0
    snapshot_generated_at: int = 0
    snapshot_hash: Optional[str] = None
    record_count: int = 0

    # Provenance
    source_refs: List[m.SourceReference] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "repository_identity": self.repository_identity,
            "active_objectives": [r.model_dump() for r in self.active_objectives],
            "active_launches": [l.model_dump() for l in self.active_launches],
            "roadmap_items": [r.model_dump() for r in self.roadmap_items],
            "architecture_decisions": [r.model_dump() for r in self.architecture_decisions],
            "engineering_lessons": [r.model_dump() for r in self.engineering_lessons],
            "project_facts": [r.model_dump() for r in self.project_facts],
            "known_risks": [r.model_dump() for r in self.known_risks],
            "blockers": [r.model_dump() for r in self.blockers],
            "operating_constraints": [r.model_dump() for r in self.operating_constraints],
            "snapshot_version": self.snapshot_version,
            "snapshot_generated_at": self.snapshot_generated_at,
            "snapshot_hash": self.snapshot_hash,
            "record_count": self.record_count,
            "source_refs": [s.model_dump() for s in self.source_refs],
        }

    def to_markdown(self) -> str:
        """Render the context package as human-readable Markdown."""
        lines = [
            f"# Engineering Context — {self.project_name or self.project_id}",
            "",
            f"**Project ID:** `{self.project_id}`",
            f"**Repo:** `{self.repository_identity or 'unknown'}`",
            f"**Snapshot:** v{self.snapshot_version} "
            f"(@ {m.format_timestamp(self.snapshot_generated_at)})",
            "",
        ]

        def _section(title: str, records: List[m.ContextRecord]) -> List[str]:
            if not records:
                return []
            out = [f"## {title}", ""]
            for r in records:
                conf = f" [conf={r.confidence:.0%}]" if r.confidence is not None else ""
                out.append(f"### {r.title}{conf}")
                if r.body:
                    out.append("")
                    out.append(r.body.strip())
                if r.source_refs:
                    out.append("")
                    for sr in r.source_refs:
                        out.append(f"- [{sr.source_type}] {sr.source_identifier}")
                out.append("")
            return out

        lines.extend(_section("Active Objectives", self.active_objectives))
        lines.extend(_section("Roadmap", self.roadmap_items))
        lines.extend(_section("Architecture Decisions", self.architecture_decisions))
        lines.extend(_section("Engineering Lessons", self.engineering_lessons))
        lines.extend(_section("Known Risks", self.known_risks))
        lines.extend(_section("Blockers", self.blockers))
        lines.extend(_section("Operating Constraints", self.operating_constraints))
        lines.extend(_section("Project Facts", self.project_facts))

        if self.active_launches:
            lines.append("## Active Launches")
            lines.append("")
            for launch in self.active_launches:
                lines.append(
                    f"- **{launch.launch_id}** "
                    f"({launch.stage.value}, {launch.status.value}) "
                    f"— started {m.format_timestamp(launch.started_at)}"
                )
                if launch.task_id:
                    lines.append(f"  - task: `{launch.task_id}`")
                if launch.pull_request_urls:
                    for url in launch.pull_request_urls:
                        lines.append(f"  - PR: {url}")
                if launch.promotion_state:
                    lines.append(f"  - promotion: {launch.promotion_state}")
                if launch.failure_reason:
                    lines.append(f"  - failure: {launch.failure_reason}")
            lines.append("")

        lines.append("---")
        lines.append(
            f"*Source: {self.record_count} records · "
            f"Generated by Hermes Engineering Context*"
        )
        return "\n".join(lines)


# ── Render function ──────────────────────────────────────────────────────────

def render_context(
    snapshot: m.ContextSnapshot,
    role: str = "all",
) -> AgentContextPackage:
    """Render a snapshot into a bounded, role-filtered context package.

    Args:
        snapshot: A deterministic snapshot from :meth:`ContextService.build_snapshot`.
        role: Agent role filter. ``"all"`` includes every record type.
              Known roles: ``planner``, ``builder``, ``reviewer``,
              ``security``, ``documentation``, ``release``.
    """
    pkg = AgentContextPackage(
        project_id=snapshot.project_id,
        project_name=snapshot.project.display_name if snapshot.project else None,
        repository_identity=(
            snapshot.project.repository_identity
            if snapshot.project
            else None
        ),
        snapshot_version=snapshot.version,
        snapshot_generated_at=snapshot.generated_at,
        record_count=len(snapshot.records),
    )

    # Determine which record types to include.
    if role == "all":
        included_types: set = set(m.RecordType)
    else:
        included_types = set(_ROLE_RECORD_TYPES.get(role, set(m.RecordType)))

    # Build source refs from active records.
    source_refs_map: Dict[str, m.SourceReference] = {}

    for rec in snapshot.records:
        if rec.status != m.RecordStatus.ACTIVE:
            continue
        if rec.record_type not in included_types:
            continue

        if rec.record_type == m.RecordType.OBJECTIVE:
            pkg.active_objectives.append(rec)
        elif rec.record_type == m.RecordType.ROADMAP_ITEM:
            pkg.roadmap_items.append(rec)
        elif rec.record_type == m.RecordType.ARCHITECTURE_DECISION:
            pkg.architecture_decisions.append(rec)
        elif rec.record_type == m.RecordType.ENGINEERING_LESSON:
            pkg.engineering_lessons.append(rec)
        elif rec.record_type == m.RecordType.KNOWN_RISK:
            pkg.known_risks.append(rec)
        elif rec.record_type == m.RecordType.BLOCKER:
            pkg.blockers.append(rec)
        elif rec.record_type == m.RecordType.OPERATING_CONSTRAINT:
            pkg.operating_constraints.append(rec)
        elif rec.record_type == m.RecordType.PROJECT_FACT:
            pkg.project_facts.append(rec)

        # Collect source refs (deduplicated by identifier).
        for sr in rec.source_refs:
            key = f"{sr.source_type}:{sr.source_identifier}"
            if key not in source_refs_map:
                source_refs_map[key] = sr

    pkg.source_refs = sorted(source_refs_map.values(), key=lambda s: s.source_identifier)

    # Active launches only.
    pkg.active_launches = [
        l for l in snapshot.launches
        if l.status in {m.LaunchStatus.PENDING, m.LaunchStatus.RUNNING}
    ]

    # Snapshot hash for integrity verification.
    pkg.snapshot_hash = snapshot.integrity_hash()

    return pkg


# ── JSON export ──────────────────────────────────────────────────────────────

def export_json(package: AgentContextPackage, indent: int = 2) -> str:
    """Export an agent context package as JSON."""
    import json
    return json.dumps(package.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)


def export_json_file(package: AgentContextPackage, path: str) -> None:
    """Export an agent context package to a file atomically."""
    from pathlib import Path
    from utils import atomic_json_write
    atomic_json_write(Path(path), package.to_dict())
