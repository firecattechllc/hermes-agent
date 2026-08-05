"""Service layer for discovery, graph queries, reporting, and impact."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from .collectors import CommandExecutor, run_collectors, safe_execute
from .config import KnowledgeConfig
from .models import (
    DiscoverySnapshot,
    EvidencePath,
    ImpactAnalysis,
    KnowledgeEntity,
    KnowledgeFederationEnvelope,
    stable_id,
    utc_now,
)
from .store import KnowledgeGraphStore


class KnowledgeService:
    def __init__(
        self, config: KnowledgeConfig, *, store: KnowledgeGraphStore | None = None
    ) -> None:
        self.config = config
        self.store = store or KnowledgeGraphStore(config.database_path)

    def discover(
        self,
        collectors: Sequence[str] | None = None,
        *,
        execute: CommandExecutor = safe_execute,
    ) -> tuple[DiscoverySnapshot, tuple]:
        started = utc_now()
        results, entities, relationships, evidence = run_collectors(
            self.config, selected=collectors, execute=execute
        )
        completed = utc_now()
        snapshot = DiscoverySnapshot(
            snapshot_id=stable_id(
                "snapshot",
                self.config.node_id,
                completed.isoformat(),
                *(item.evidence_id for item in evidence),
            ),
            node_id=self.config.node_id,
            started_at=started,
            completed_at=completed,
            collector_results=results,
            entity_count=len(entities),
            relationship_count=len(relationships),
            evidence_count=len(evidence),
            warnings=tuple(item.warning for item in results if item.warning),
            errors=tuple(item.error for item in results if item.error),
        )
        changes = self.store.apply_snapshot(
            snapshot,
            entities,
            relationships,
            evidence,
            missed_threshold=self.config.missed_snapshot_threshold,
        )
        return snapshot, changes

    def status(self) -> dict:
        entities = self.store.search_entities()
        changes = self.store.changes()
        coverage: dict[str, int] = {}
        by_node: dict[str, int] = {}
        for item in entities:
            coverage[item.entity_type] = coverage.get(item.entity_type, 0) + 1
            by_node[item.node_id or "unknown"] = (
                by_node.get(item.node_id or "unknown", 0) + 1
            )
        return {
            "node_id": self.config.node_id,
            "node_role": self.config.node_role.value,
            "entity_count": len(entities),
            "coverage_by_type": coverage,
            "coverage_by_node": by_node,
            "stale_count": sum(
                item.lifecycle_state.value == "stale" for item in entities
            ),
            "low_confidence_count": sum(item.confidence < 0.5 for item in entities),
            "recent_drift": [item.model_dump(mode="json") for item in changes[-20:]],
            "unresolved_conflicts": sum(
                item.change_type.value == "conflict" and not item.acknowledged
                for item in changes
            ),
        }

    def impact(self, entity_id: str, scenario: str = "outage") -> ImpactAnalysis:
        if self.store.entity(entity_id) is None:
            raise ValueError("knowledge entity does not exist")
        upstream_entities, upstream_edges = self.store.neighbors(
            entity_id, direction="upstream", depth=self.config.traversal_depth
        )
        downstream_entities, downstream_edges = self.store.neighbors(
            entity_id, direction="downstream", depth=self.config.traversal_depth
        )
        upstream = tuple(
            sorted(
                item.entity_id
                for item in upstream_entities
                if item.entity_id != entity_id
            )
        )
        downstream = tuple(
            sorted(
                item.entity_id
                for item in downstream_entities
                if item.entity_id != entity_id
            )
        )
        capabilities = tuple(
            sorted(
                item.entity_id
                for item in downstream_entities
                if item.entity_type == "capability"
            )
        )
        evidence = tuple(
            sorted({
                ref
                for edge in (*upstream_edges, *downstream_edges)
                for ref in edge.evidence_refs
            })
        )
        uncertainty = []
        if any(
            item.lifecycle_state.value == "stale"
            for item in (*upstream_entities, *downstream_entities)
        ):
            uncertainty.append("traversal includes stale knowledge")
        if not evidence:
            uncertainty.append("no evidence-backed dependency path is available")
        paths = (
            ()
            if not evidence
            else (
                EvidencePath(
                    entity_ids=(entity_id, *downstream),
                    relationship_ids=tuple(
                        edge.relationship_id for edge in downstream_edges
                    ),
                    evidence_refs=evidence,
                ),
            )
        )
        return ImpactAnalysis(
            subject_id=entity_id,
            scenario=scenario,
            upstream_dependencies=upstream,
            downstream_dependents=downstream,
            affected_capabilities=capabilities,
            uncertainty=tuple(uncertainty),
            missing_evidence=tuple(uncertainty),
            traversal_depth=self.config.traversal_depth,
            paths=paths,
            generated_at=utc_now(),
        )

    def merge_federation(self, envelope: KnowledgeFederationEnvelope) -> bool:
        if envelope.recipient_node != self.config.node_id:
            raise ValueError("federation envelope addressed to another node")
        return self.store.accept_federation(envelope.message_id, envelope.content_hash)

    def export(self, path: Path, *, redacted: bool) -> None:
        if not redacted:
            raise ValueError("Step 33 exports must be explicitly redacted")
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.store.export_redacted(), indent=2) + "\n", encoding="utf-8"
        )
