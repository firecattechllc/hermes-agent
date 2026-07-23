"""Transactional SQLite graph persistence and reconciliation."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

from hermes_cli.sqlite_util import write_txn

from .models import (
    ChangeSeverity,
    ChangeType,
    DiscoveryEvidence,
    DiscoverySnapshot,
    GraphChange,
    KnowledgeEntity,
    KnowledgeRelationship,
    LifecycleState,
    RelationshipType,
    stable_hash,
    stable_id,
    utc_now,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
 id TEXT PRIMARY KEY, node_id TEXT, entity_type TEXT NOT NULL, name TEXT NOT NULL,
 canonical_name TEXT NOT NULL, status TEXT NOT NULL, labels TEXT NOT NULL,
 body TEXT NOT NULL, fact_hash TEXT NOT NULL, first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0,
 missed_count INTEGER NOT NULL DEFAULT 0, collector TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_search ON entities(entity_type, canonical_name, node_id, status);
CREATE TABLE IF NOT EXISTS relationships (
 id TEXT PRIMARY KEY, node_id TEXT, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
 relationship_type TEXT NOT NULL, body TEXT NOT NULL, fact_hash TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0,
 missed_count INTEGER NOT NULL DEFAULT 0, collector TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relationships_source ON relationships(source_id, relationship_type);
CREATE INDEX IF NOT EXISTS relationships_target ON relationships(target_id, relationship_type);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, node_id TEXT NOT NULL, collector TEXT NOT NULL,
 collected_at TEXT NOT NULL, body TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, node_id TEXT NOT NULL, completed_at TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
 id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, detected_at TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS federation_messages (
 id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, received_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


def _fact(model, excluded: set[str]) -> dict:
    return model.model_dump(mode="json", exclude=excluded)


class KnowledgeGraphStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as conn, write_txn(conn):
            yield conn

    @staticmethod
    def _row_model(row: sqlite3.Row, model):
        return model.model_validate_json(row["body"])

    def entity(self, entity_id: str) -> Optional[KnowledgeEntity]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id=?", (entity_id,)
            ).fetchone()
        return None if row is None else self._row_model(row, KnowledgeEntity)

    def relationship(self, relationship_id: str) -> Optional[KnowledgeRelationship]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM relationships WHERE id=?", (relationship_id,)
            ).fetchone()
        return None if row is None else self._row_model(row, KnowledgeRelationship)

    def search_entities(
        self,
        *,
        entity_type: Optional[str] = None,
        name: Optional[str] = None,
        label: Optional[str] = None,
        node_id: Optional[str] = None,
        status: Optional[str] = None,
        include_stale: bool = True,
        limit: int = 500,
    ) -> tuple[KnowledgeEntity, ...]:
        clauses, args = [], []
        for column, value in (
            ("entity_type", entity_type),
            ("node_id", node_id),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        if name is not None:
            clauses.append("(name LIKE ? OR canonical_name LIKE ?)")
            args.extend((f"%{name}%", f"%{name.lower()}%"))
        if label is not None:
            clauses.append("labels LIKE ?")
            args.append(f'%"{label}"%')
        if not include_stale:
            clauses.append("stale=0")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM entities{where} ORDER BY entity_type, canonical_name LIMIT ?",
                (*args, min(limit, 1000)),
            ).fetchall()
        return tuple(self._row_model(row, KnowledgeEntity) for row in rows)

    def changes(self, since: Optional[datetime] = None) -> tuple[GraphChange, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body FROM changes"
                + (" WHERE detected_at>=?" if since else "")
                + " ORDER BY detected_at, id",
                (() if since is None else (since.isoformat(),)),
            ).fetchall()
        return tuple(GraphChange.model_validate_json(row["body"]) for row in rows)

    def apply_snapshot(
        self,
        snapshot: DiscoverySnapshot,
        entities: Sequence[KnowledgeEntity],
        relationships: Sequence[KnowledgeRelationship],
        evidence: Sequence[DiscoveryEvidence],
        *,
        missed_threshold: int = 3,
    ) -> tuple[GraphChange, ...]:
        successful = {
            result.collector_id
            for result in snapshot.collector_results
            if result.success
        }
        seen_entities = {item.entity_id for item in entities}
        seen_relationships = {item.relationship_id for item in relationships}
        changes: list[GraphChange] = []
        with self.transaction() as conn:
            for item in evidence:
                conn.execute(
                    "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?)",
                    (
                        item.evidence_id,
                        item.node_id,
                        item.collector,
                        item.collected_at.isoformat(),
                        item.model_dump_json(),
                        item.content_hash,
                    ),
                )
            for item in entities:
                prior = conn.execute(
                    "SELECT * FROM entities WHERE id=?", (item.entity_id,)
                ).fetchone()
                fact = _fact(item, {"first_seen_at", "last_seen_at", "observed_at"})
                digest = stable_hash(fact)
                kind = ChangeType.ADDED if prior is None else None
                first_seen = (
                    item.first_seen_at
                    if prior is None
                    else datetime.fromisoformat(prior["first_seen"])
                )
                if prior is not None:
                    prior_item = KnowledgeEntity.model_validate_json(prior["body"])
                    conflicting_source = not set(
                        prior_item.source_collectors
                    ).intersection(item.source_collectors)
                    if (
                        prior["fact_hash"] != digest
                        and conflicting_source
                        and item.confidence <= prior_item.confidence
                    ):
                        changes.append(
                            self._change(
                                ChangeType.CONFLICT,
                                item.entity_id,
                                None,
                                prior,
                                digest,
                                tuple(
                                    sorted(
                                        set(prior_item.evidence_refs)
                                        | set(item.evidence_refs)
                                    )
                                ),
                            )
                        )
                        continue
                    kind = (
                        ChangeType.RESTORED
                        if prior["stale"]
                        else (
                            ChangeType.CHANGED if prior["fact_hash"] != digest else None
                        )
                    )
                stored = item.model_copy(update={"first_seen_at": first_seen})
                conn.execute(
                    """INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET node_id=excluded.node_id,
                    entity_type=excluded.entity_type,name=excluded.name,
                    canonical_name=excluded.canonical_name,status=excluded.status,
                    labels=excluded.labels,body=excluded.body,fact_hash=excluded.fact_hash,
                    last_seen=excluded.last_seen,stale=0,missed_count=0,collector=excluded.collector""",
                    (
                        stored.entity_id,
                        stored.node_id,
                        stored.entity_type,
                        stored.name,
                        stored.canonical_name,
                        stored.operational_status,
                        json.dumps(stored.labels),
                        stored.model_dump_json(),
                        digest,
                        first_seen.isoformat(),
                        stored.last_seen_at.isoformat(),
                        0,
                        0,
                        stored.source_collectors[0],
                    ),
                )
                if kind:
                    changes.append(
                        self._change(
                            kind,
                            stored.entity_id,
                            None,
                            prior,
                            digest,
                            stored.evidence_refs,
                        )
                    )
            for item in relationships:
                prior = conn.execute(
                    "SELECT * FROM relationships WHERE id=?", (item.relationship_id,)
                ).fetchone()
                fact = _fact(item, {"first_seen_at", "last_seen_at", "observed_at"})
                digest = stable_hash(fact)
                kind = ChangeType.ADDED if prior is None else None
                first_seen = (
                    item.first_seen_at
                    if prior is None
                    else datetime.fromisoformat(prior["first_seen"])
                )
                if prior is not None:
                    prior_item = KnowledgeRelationship.model_validate_json(
                        prior["body"]
                    )
                    conflicting_source = not set(
                        prior_item.source_collectors
                    ).intersection(item.source_collectors)
                    if (
                        prior["fact_hash"] != digest
                        and conflicting_source
                        and item.confidence <= prior_item.confidence
                    ):
                        changes.append(
                            self._change(
                                ChangeType.CONFLICT,
                                None,
                                item.relationship_id,
                                prior,
                                digest,
                                tuple(
                                    sorted(
                                        set(prior_item.evidence_refs)
                                        | set(item.evidence_refs)
                                    )
                                ),
                            )
                        )
                        continue
                    kind = (
                        ChangeType.RESTORED
                        if prior["stale"]
                        else (
                            ChangeType.CHANGED if prior["fact_hash"] != digest else None
                        )
                    )
                stored = item.model_copy(update={"first_seen_at": first_seen})
                conn.execute(
                    """INSERT INTO relationships VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET body=excluded.body,fact_hash=excluded.fact_hash,
                    last_seen=excluded.last_seen,stale=0,missed_count=0,collector=excluded.collector""",
                    (
                        stored.relationship_id,
                        snapshot.node_id,
                        stored.source_entity_id,
                        stored.target_entity_id,
                        stored.relationship_type.value,
                        stored.model_dump_json(),
                        digest,
                        first_seen.isoformat(),
                        stored.last_seen_at.isoformat(),
                        0,
                        0,
                        stored.source_collectors[0],
                    ),
                )
                if kind:
                    changes.append(
                        self._change(
                            kind,
                            None,
                            stored.relationship_id,
                            prior,
                            digest,
                            stored.evidence_refs,
                        )
                    )
            self._mark_missing(
                conn,
                "entities",
                snapshot.node_id,
                successful,
                seen_entities,
                missed_threshold,
                changes,
            )
            self._mark_missing(
                conn,
                "relationships",
                snapshot.node_id,
                successful,
                seen_relationships,
                missed_threshold,
                changes,
            )
            conn.execute(
                "INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.node_id,
                    snapshot.completed_at.isoformat(),
                    snapshot.model_dump_json(),
                ),
            )
            for change in changes:
                subject = change.entity_id or change.relationship_id
                conn.execute(
                    "INSERT OR IGNORE INTO changes VALUES(?,?,?,?)",
                    (
                        change.change_id,
                        subject,
                        change.detected_at.isoformat(),
                        change.model_dump_json(),
                    ),
                )
        return tuple(changes)

    def _mark_missing(self, conn, table, node, successful, seen, threshold, changes):
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE node_id=? AND stale=0", (node,)
        ).fetchall()
        for row in rows:
            if row["id"] in seen or row["collector"] not in successful:
                continue
            missed = row["missed_count"] + 1
            stale = missed >= threshold
            conn.execute(
                f"UPDATE {table} SET missed_count=?,stale=? WHERE id=?",
                (missed, stale, row["id"]),
            )
            if stale:
                body = json.loads(row["body"])
                if table == "entities":
                    body["lifecycle_state"] = LifecycleState.STALE.value
                conn.execute(
                    f"UPDATE {table} SET body=? WHERE id=?",
                    (json.dumps(body), row["id"]),
                )
                changes.append(
                    self._change(
                        ChangeType.STALE,
                        row["id"] if table == "entities" else None,
                        row["id"] if table == "relationships" else None,
                        row,
                        None,
                        tuple(body.get("evidence_refs", ())),
                    )
                )

    @staticmethod
    def _change(kind, entity_id, relationship_id, prior, current_hash, refs):
        subject = entity_id or relationship_id
        detected = utc_now()
        severity = (
            ChangeSeverity.HIGH
            if kind in {ChangeType.STALE, ChangeType.REMOVED}
            else ChangeSeverity.WARNING
            if kind in {ChangeType.CHANGED, ChangeType.CONFLICT}
            else ChangeSeverity.INFO
        )
        return GraphChange(
            change_id=stable_id(
                "change", subject, kind.value, current_hash or "", detected.isoformat()
            ),
            change_type=kind,
            entity_id=entity_id,
            relationship_id=relationship_id,
            prior_hash=None if prior is None else prior["fact_hash"],
            current_hash=current_hash,
            detected_at=detected,
            severity=severity,
            summary=f"{kind.value}: {subject}",
            evidence_refs=refs,
            requires_approval=False,
        )

    def neighbors(
        self, entity_id: str, *, direction: str = "both", depth: int = 1
    ) -> tuple[tuple[KnowledgeEntity, ...], tuple[KnowledgeRelationship, ...]]:
        if direction not in {"upstream", "downstream", "both"} or not 1 <= depth <= 20:
            raise ValueError("invalid graph traversal bounds")
        visited, frontier, edge_ids = {entity_id}, {entity_id}, set()
        with self._connect() as conn:
            for _ in range(depth):
                if not frontier:
                    break
                marks = ",".join("?" for _ in frontier)
                clauses, args = [], []
                if direction in {"downstream", "both"}:
                    clauses.append(f"source_id IN ({marks})")
                    args.extend(frontier)
                if direction in {"upstream", "both"}:
                    clauses.append(f"target_id IN ({marks})")
                    args.extend(frontier)
                rows = conn.execute(
                    f"SELECT * FROM relationships WHERE stale=0 AND ({' OR '.join(clauses)})",
                    args,
                ).fetchall()
                next_frontier = set()
                for row in rows:
                    edge_ids.add(row["id"])
                    for candidate in (row["source_id"], row["target_id"]):
                        if candidate not in visited:
                            visited.add(candidate)
                            next_frontier.add(candidate)
                frontier = next_frontier
            entity_rows = conn.execute(
                f"SELECT * FROM entities WHERE id IN ({','.join('?' for _ in visited)})",
                tuple(visited),
            ).fetchall()
            edge_rows = (
                []
                if not edge_ids
                else conn.execute(
                    f"SELECT * FROM relationships WHERE id IN ({','.join('?' for _ in edge_ids)})",
                    tuple(edge_ids),
                ).fetchall()
            )
        return (
            tuple(self._row_model(row, KnowledgeEntity) for row in entity_rows),
            tuple(self._row_model(row, KnowledgeRelationship) for row in edge_rows),
        )

    def export_redacted(self) -> dict:
        entities = [item.model_dump(mode="json") for item in self.search_entities()]
        with self._connect() as conn:
            rels = [
                KnowledgeRelationship.model_validate_json(row["body"]).model_dump(
                    mode="json"
                )
                for row in conn.execute("SELECT body FROM relationships ORDER BY id")
            ]
        return {
            "schema_version": 1,
            "generated_at": utc_now().isoformat(),
            "entities": entities,
            "relationships": rels,
        }

    def accept_federation(self, message_id: str, content_hash: str) -> bool:
        with self.transaction() as conn:
            prior = conn.execute(
                "SELECT content_hash FROM federation_messages WHERE id=?", (message_id,)
            ).fetchone()
            if prior is not None:
                if prior["content_hash"] != content_hash:
                    raise ValueError("federation message identity collision")
                return False
            conn.execute(
                "INSERT INTO federation_messages VALUES(?,?,?)",
                (message_id, content_hash, utc_now().isoformat()),
            )
        return True
