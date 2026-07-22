"""Tests for Mission Control domain models."""

import pytest
from pydantic import ValidationError

from hermes_cli.mission_control.models import (
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TelemetryEvent,
    AgentStateSnapshot,
    BacklogItemStateSnapshot,
    ApprovalStateSnapshot,
    EvidenceStateSnapshot,
    PromotionStateSnapshot,
    MissionControlSnapshot,
    AgentState,
    BacklogItemState,
    ApprovalState,
    EvidenceState,
    PromotionState,
    TelemetrySeverity,
    new_telemetry_event_id,
    utc_timestamp,
    format_timestamp,
)


class TestSchemaVersion:
    def test_current_schema_version_is_1(self):
        assert CURRENT_SCHEMA_VERSION == 1

    def test_supported_versions_contains_only_1(self):
        assert SUPPORTED_SCHEMA_VERSIONS == frozenset({1})

    def test_unsupported_schema_version_rejected(self):
        with pytest.raises(ValidationError, match="schema version"):
            TelemetryEvent(
                event_id="t1",
                event_type="agent_started",
                project_id="proj",
                schema_version=99,
            )

    def test_unknown_event_type_rejected(self):
        with pytest.raises(ValidationError, match="unknown telemetry event_type"):
            TelemetryEvent(
                event_id="t1",
                event_type="not_a_real_event",
                project_id="proj",
            )

    def test_unknown_severity_rejected(self):
        with pytest.raises(ValidationError, match="unknown telemetry severity"):
            TelemetryEvent(
                event_id="t1",
                event_type="agent_started",
                project_id="proj",
                severity="super-critical",
            )

    def test_negative_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="non-negative"):
            TelemetryEvent(
                event_id="t1",
                event_type="agent_started",
                project_id="proj",
                timestamp=-1,
            )


class TestTelemetryEventRequiredFields:
    def test_event_id_required(self):
        with pytest.raises(ValidationError):
            TelemetryEvent(event_type="agent_started", project_id="proj")

    def test_event_type_required(self):
        with pytest.raises(ValidationError):
            TelemetryEvent(event_id="t1", project_id="proj")

    def test_project_id_required(self):
        with pytest.raises(ValidationError):
            TelemetryEvent(event_id="t1", event_type="agent_started")

    def test_all_provenance_fields_populated(self):
        event = TelemetryEvent(
            event_id="tevt_abc",
            event_type="agent_started",
            project_id="proj_x",
            launch_id="laun_x",
            task_id="task_x",
            backlog_id="bkit_x",
            agent_id="agnt_x",
            sequence=5,
            severity="info",
            correlation_id="corr_x",
            causation_id="caus_x",
            payload={"key": "val"},
        )
        assert event.event_id == "tevt_abc"
        assert event.project_id == "proj_x"
        assert event.launch_id == "laun_x"
        assert event.task_id == "task_x"
        assert event.backlog_id == "bkit_x"
        assert event.agent_id == "agnt_x"
        assert event.sequence == 5
        assert event.severity == "info"
        assert event.correlation_id == "corr_x"
        assert event.causation_id == "caus_x"
        assert event.payload == {"key": "val"}

    def test_optional_fields_default_to_none(self):
        event = TelemetryEvent(
            event_id="t1",
            event_type="agent_started",
            project_id="proj",
        )
        assert event.launch_id is None
        assert event.task_id is None
        assert event.backlog_id is None
        assert event.agent_id is None
        assert event.correlation_id is None
        assert event.causation_id is None
        assert event.payload == {}
        assert event.severity == "info"
        assert event.sequence == 0
        assert event.schema_version == CURRENT_SCHEMA_VERSION


class TestTelemetryEventHelpers:
    def test_stable_sort_key_is_tuple(self):
        event = TelemetryEvent(
            event_id="t1",
            event_type="agent_started",
            project_id="proj",
            timestamp=1000,
            sequence=3,
        )
        key = event.stable_sort_key()
        assert isinstance(key, tuple)
        assert key == (1000, 3, "t1")

    def test_stable_sort_key_orders_by_timestamp_first(self):
        e1 = TelemetryEvent(event_id="t1", event_type="agent_started", project_id="proj", timestamp=1, sequence=0)
        e2 = TelemetryEvent(event_id="t2", event_type="agent_started", project_id="proj", timestamp=2, sequence=0)
        assert e1.stable_sort_key() < e2.stable_sort_key()

    def test_stable_sort_key_orders_by_sequence_second(self):
        e1 = TelemetryEvent(event_id="t1", event_type="agent_started", project_id="proj", timestamp=1, sequence=1)
        e2 = TelemetryEvent(event_id="t2", event_type="agent_started", project_id="proj", timestamp=1, sequence=2)
        assert e1.stable_sort_key() < e2.stable_sort_key()

    def test_stable_sort_key_orders_by_event_id_third(self):
        e1 = TelemetryEvent(event_id="aaa", event_type="agent_started", project_id="proj", timestamp=1, sequence=0)
        e2 = TelemetryEvent(event_id="bbb", event_type="agent_started", project_id="proj", timestamp=1, sequence=0)
        assert e1.stable_sort_key() < e2.stable_sort_key()

    def test_source_provenance_returns_dict(self):
        event = TelemetryEvent(
            event_id="t1",
            event_type="agent_started",
            project_id="proj",
            launch_id="laun",
            task_id="task",
            backlog_id="bkit",
            agent_id="agnt",
            causation_id="caus",
        )
        prov = event.source_provenance()
        assert prov["event_id"] == "t1"
        assert prov["project_id"] == "proj"
        assert prov["launch_id"] == "laun"
        assert prov["task_id"] == "task"
        assert prov["backlog_id"] == "bkit"
        assert prov["agent_id"] == "agnt"
        assert prov["causation_id"] == "caus"

    def test_source_provenance_returns_empty_str_for_none(self):
        event = TelemetryEvent(
            event_id="t1",
            event_type="agent_started",
            project_id="proj",
        )
        prov = event.source_provenance()
        assert prov["launch_id"] == ""
        assert prov["task_id"] == ""
        assert prov["backlog_id"] == ""
        assert prov["agent_id"] == ""
        assert prov["causation_id"] == ""


class TestSnapshotModels:
    def test_agent_state_snapshot_defaults(self):
        snap = AgentStateSnapshot(agent_id="a1", project_id="proj")
        assert snap.state == AgentState.IDLE
        assert snap.launch_id is None
        assert snap.last_event_id is None
        assert snap.last_event_type is None
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_backlog_item_state_snapshot_defaults(self):
        snap = BacklogItemStateSnapshot(backlog_id="b1", project_id="proj")
        assert snap.state == BacklogItemState.BACKLOG
        assert snap.title is None
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_approval_state_snapshot_defaults(self):
        snap = ApprovalStateSnapshot(approval_id="appr1", project_id="proj")
        assert snap.state == ApprovalState.PENDING
        assert snap.requested_at is None
        assert snap.resolved_at is None
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_evidence_state_snapshot_defaults(self):
        snap = EvidenceStateSnapshot(evidence_id="ev1", project_id="proj")
        assert snap.state == EvidenceState.PENDING
        assert snap.collected_at is None
        assert snap.verified_at is None
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_promotion_state_snapshot_defaults(self):
        snap = PromotionStateSnapshot(promotion_id="p1", project_id="proj")
        assert snap.state == PromotionState.NOT_STARTED
        assert snap.requested_at is None
        assert snap.approved_at is None
        assert snap.deployed_at is None
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_mission_control_snapshot_minimal(self):
        snap = MissionControlSnapshot(version=0, project_id="proj")
        assert snap.version == 0
        assert snap.project_id == "proj"
        assert snap.event_count == 0
        assert snap.events == []
        assert snap.agent_states == []
        assert snap.backlog_states == []
        assert snap.approval_states == []
        assert snap.evidence_states == []
        assert snap.promotion_states == []
        assert snap.schema_version == CURRENT_SCHEMA_VERSION

    def test_mission_control_snapshot_integrity_hash(self):
        snap1 = MissionControlSnapshot(version=0, project_id="proj")
        snap2 = MissionControlSnapshot(version=0, project_id="proj")
        assert snap1.integrity_hash() == snap2.integrity_hash()

    def test_integrity_hash_excludes_version(self):
        snap1 = MissionControlSnapshot(version=1, project_id="proj")
        snap2 = MissionControlSnapshot(version=2, project_id="proj")
        assert snap1.integrity_hash() == snap2.integrity_hash()


class TestIDHelpers:
    def test_new_telemetry_event_id_prefix(self):
        eid = new_telemetry_event_id()
        assert eid.startswith("tevt_")
        assert len(eid) == len("tevt_") + 16

    def test_new_telemetry_event_id_unique(self):
        ids = [new_telemetry_event_id() for _ in range(100)]
        assert len(ids) == len(set(ids))

    def test_utc_timestamp_positive(self):
        ts = utc_timestamp()
        assert ts > 0

    def test_format_timestamp_iso8601(self):
        ts = 1700000000
        fmt = format_timestamp(ts)
        assert fmt == "2023-11-14T22:13:20Z"
