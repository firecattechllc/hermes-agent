from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.operator_approval import (
    ApprovalChannel,
    ApprovalRejectionCode,
    DEFAULT_MAX_APPROVAL_AGE_SECONDS,
    OperatorApproval,
    OperatorApprovalReplayStore,
    OperatorApprovalScope,
    compute_action_id,
    validate_operator_approval,
)


def _now() -> int:
    return int(time.time())


def _grant(**overrides) -> OperatorApproval:
    now = overrides.pop("granted_at", _now())
    fields = dict(
        scope=OperatorApprovalScope.DESKTOP_USE,
        action_id=compute_action_id({"action": "click", "app": "Finder"}),
        subject_identity_id="fid_node_mac",
        operator_identity="telegram:12345",
        channel=ApprovalChannel.TELEGRAM,
        granted_at=now,
        evidence_ref="evidence://approval-1",
    )
    fields.update(overrides)
    return OperatorApproval.grant(**fields)


def test_grant_produces_a_valid_short_lived_approval() -> None:
    now = _now()
    approval = _grant(granted_at=now)
    assert approval.expires_at == now + DEFAULT_MAX_APPROVAL_AGE_SECONDS
    assert approval.revoked is False
    assert len(approval.nonce) >= 16


def test_two_grants_for_the_same_action_have_different_nonces_and_ids() -> None:
    a = _grant()
    b = _grant()
    assert a.nonce != b.nonce
    assert a.approval_id != b.approval_id


def test_approval_cannot_outlive_the_governed_maximum() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        OperatorApproval(
            approval_id="opap_test",
            scope=OperatorApprovalScope.DESKTOP_USE,
            action_id="actn_test",
            subject_identity_id="fid_node_mac",
            operator_identity="telegram:12345",
            channel=ApprovalChannel.TELEGRAM,
            nonce="a" * 24,
            granted_at=now,
            expires_at=now + DEFAULT_MAX_APPROVAL_AGE_SECONDS + 1,
            evidence_ref="evidence://x",
        )


def test_approval_must_expire_after_it_is_granted() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        OperatorApproval(
            approval_id="opap_test",
            scope=OperatorApprovalScope.DESKTOP_USE,
            action_id="actn_test",
            subject_identity_id="fid_node_mac",
            operator_identity="telegram:12345",
            channel=ApprovalChannel.TELEGRAM,
            nonce="a" * 24,
            granted_at=now,
            expires_at=now,
            evidence_ref="evidence://x",
        )


def test_validate_accepts_a_fresh_matching_approval(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now)
    action_id = approval.action_id

    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=action_id,
        expected_subject_identity_id="fid_node_mac",
        now=now,
        replay_store=replay_store,
    )
    assert ok is True
    assert code is None


def test_validate_rejects_missing_approval(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    ok, code = validate_operator_approval(
        None,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id="actn_x",
        expected_subject_identity_id="fid_node_mac",
        now=_now(),
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.SCOPE_MISMATCH


def test_validate_rejects_action_mismatch(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now)
    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=compute_action_id({"action": "different-action"}),
        expected_subject_identity_id="fid_node_mac",
        now=now,
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.ACTION_MISMATCH


def test_validate_rejects_identity_mismatch(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now)
    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_titan",
        now=now,
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.IDENTITY_MISMATCH


def test_validate_rejects_scope_mismatch(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now, scope=OperatorApprovalScope.REMOTE_MAINTENANCE)
    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_mac",
        now=now,
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.SCOPE_MISMATCH


def test_validate_rejects_expired_approval(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now)
    later = now + DEFAULT_MAX_APPROVAL_AGE_SECONDS + 1
    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_mac",
        now=later,
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.EXPIRED


def test_validate_rejects_revoked_approval(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now).revoke(now=now + 1)
    ok, code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_mac",
        now=now + 2,
        replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.REVOKED


def test_validate_rejects_replayed_approval(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = _grant(granted_at=now)

    first_ok, _ = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_mac",
        now=now,
        replay_store=replay_store,
    )
    assert first_ok is True

    second_ok, second_code = validate_operator_approval(
        approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=approval.action_id,
        expected_subject_identity_id="fid_node_mac",
        now=now + 1,
        replay_store=replay_store,
    )
    assert second_ok is False
    assert second_code == ApprovalRejectionCode.REPLAYED


def test_replay_store_persists_across_instances(tmp_path) -> None:
    state_root = tmp_path / "prime"
    now = _now()
    approval = _grant(granted_at=now)

    first_store = OperatorApprovalReplayStore(state_root=state_root)
    first_store.consume(approval, now=now)

    second_store = OperatorApprovalReplayStore(state_root=state_root)
    assert second_store.is_consumed(approval.approval_id) is True


def test_action_id_is_deterministic_and_content_addressed() -> None:
    a = compute_action_id({"action": "click", "app": "Finder"})
    b = compute_action_id({"app": "Finder", "action": "click"})
    c = compute_action_id({"action": "click", "app": "Safari"})
    assert a == b  # key order doesn't matter
    assert a != c
