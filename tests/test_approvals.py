"""승인 흐름 테스트 — 기획서 핵심 검증 사례 G05~G08, G17, G20."""

import pytest

from psm import approvals


def _weight(conn, milestone_id):
    return conn.execute(
        "SELECT weight, is_approved FROM milestones WHERE id = ?", (milestone_id,)
    ).fetchone()


# G05: 완료 요청만 제출한 상태에서는 공식 진척률(is_approved)을 바꾸지 않는다.
def test_request_alone_does_not_approve(conn, seed_milestone):
    milestone_id = seed_milestone()
    approvals.request_completion(conn, milestone_id, actor_id=1)
    assert _weight(conn, milestone_id)["is_approved"] == 0


# G06: 반려 시 공식값은 그대로, 사유가 기록된다.
def test_reject_keeps_official_value_and_records_reason(conn, seed_milestone):
    milestone_id = seed_milestone()
    approvals.reject(conn, milestone_id, actor_id=1, reason="증빙 부족", expected_version=1)
    assert _weight(conn, milestone_id)["is_approved"] == 0
    reason = conn.execute(
        "SELECT reason FROM milestone_events WHERE event_type = 'reject'"
    ).fetchone()["reason"]
    assert reason == "증빙 부족"


# G07: 승인 취소 — 승인된 값만 취소 가능, 취소 후 미승인으로 되돌아간다.
def test_revoke_reverts_to_unapproved(conn, seed_milestone):
    milestone_id = seed_milestone()
    approvals.approve(conn, milestone_id, actor_id=1, expected_version=1)
    assert _weight(conn, milestone_id)["is_approved"] == 1

    approvals.revoke(conn, milestone_id, actor_id=1, reason="오승인", expected_version=2)
    assert _weight(conn, milestone_id)["is_approved"] == 0


def test_revoke_unapproved_milestone_fails(conn, seed_milestone):
    milestone_id = seed_milestone()
    with pytest.raises(approvals.NotApprovedError):
        approvals.revoke(conn, milestone_id, actor_id=1, reason="x", expected_version=1)


# G08: 중복 승인 요청 — 이미 승인된 마일스톤을 다시 승인하면 거부, 값은 한 번만 반영.
def test_duplicate_approval_is_rejected(conn, seed_milestone):
    milestone_id = seed_milestone()
    approvals.approve(conn, milestone_id, actor_id=1, expected_version=1)
    with pytest.raises(approvals.AlreadyApprovedError):
        approvals.approve(conn, milestone_id, actor_id=1, expected_version=2)


# G17: 동시 수정 — 오래된 버전으로 승인 시도하면 거부한다.
def test_concurrent_modification_is_detected(conn, seed_milestone):
    milestone_id = seed_milestone()
    approvals.reject(conn, milestone_id, actor_id=1, reason="1차 반려", expected_version=1)
    # reject는 milestone 행을 바꾸지 않으므로 버전은 그대로 1 — 진짜 충돌은 승인 후 재시도로 재현
    approvals.approve(conn, milestone_id, actor_id=1, expected_version=1)  # version -> 2

    with pytest.raises(approvals.ConcurrentModificationError):
        approvals.revoke(conn, milestone_id, actor_id=1, reason="x", expected_version=1)  # 낡은 버전


# G17 (실제 UPDATE 시점 경쟁): SELECT 검사(_check_version) 통과 이후 다른 트랜잭션이 먼저
# 같은 행을 바꿔서 UPDATE가 0행에 매치되는 경우 — rowcount로 잡아내야 한다(code-review 지적).
def test_update_matching_zero_rows_is_treated_as_conflict(conn, seed_milestone):
    milestone_id = seed_milestone()
    # 다른 사용자가 이미 버전을 2로 올려둔 상황을 흉내낸다.
    conn.execute("UPDATE milestones SET version = 2 WHERE id = ?", (milestone_id,))
    conn.commit()

    with pytest.raises(approvals.ConcurrentModificationError):
        approvals._update_or_raise_conflict(
            conn,
            "UPDATE milestones SET is_approved = 1 WHERE id = ? AND version = ?",
            (milestone_id, 1),  # 낡은 버전(1)으로 시도 — 실제 버전은 2
            milestone_id,
        )


# G20: 승인된 증빙은 삭제 금지, 승인 취소 후에만 허용.
def test_evidence_of_approved_milestone_is_locked():
    with pytest.raises(approvals.EvidenceLockedError):
        approvals.assert_evidence_deletable(is_milestone_approved=True)


def test_evidence_of_unapproved_milestone_is_deletable():
    approvals.assert_evidence_deletable(is_milestone_approved=False)  # 예외 없이 통과
