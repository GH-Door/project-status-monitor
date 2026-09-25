"""완료 요청·승인·반려·취소 — 사람의 승인만이 공식 진척률을 바꾼다(기획서 §4.3, §5).

- 완료 요청/반려는 milestone_events에만 기록한다. milestones.is_approved는 승인 때만 바뀐다.
- 동시 수정은 milestones.version 낙관적 잠금으로 막는다(G17).
- 승인된 증빙 삭제는 기본 금지, 승인 취소 후에만 허용한다(G20).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


class MilestoneNotFoundError(Exception):
    pass


class ConcurrentModificationError(Exception):
    """다른 사용자가 먼저 이 마일스톤을 바꿨다. 최신 상태를 다시 불러와야 한다."""


class AlreadyApprovedError(Exception):
    pass


class NotApprovedError(Exception):
    pass


class EvidenceLockedError(Exception):
    """승인된 마일스톤의 증빙은 승인 취소 후에만 삭제할 수 있다."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_event(
    conn: sqlite3.Connection, milestone_id: int, event_type: str, actor_id: int, reason: str | None
) -> None:
    conn.execute(
        "INSERT INTO milestone_events (milestone_id, event_type, actor_id, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (milestone_id, event_type, actor_id, reason, _now()),
    )


def _load(conn: sqlite3.Connection, milestone_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, version, is_approved FROM milestones WHERE id = ?", (milestone_id,)
    ).fetchone()
    if row is None:
        raise MilestoneNotFoundError(f"milestone {milestone_id} not found")
    return row


def _check_version(conn: sqlite3.Connection, milestone_id: int, expected_version: int) -> sqlite3.Row:
    row = _load(conn, milestone_id)
    if row["version"] != expected_version:
        raise ConcurrentModificationError(
            f"milestone {milestone_id}는 이미 버전 {row['version']}로 바뀌었습니다 "
            f"(요청한 버전: {expected_version})"
        )
    return row


def request_completion(conn: sqlite3.Connection, milestone_id: int, actor_id: int) -> None:
    """완료 요청 제출. 공식 진척률은 바뀌지 않는다(G05). 중복 요청을 막지 않는다."""
    _load(conn, milestone_id)
    _insert_event(conn, milestone_id, "request", actor_id, reason=None)
    conn.commit()


def _update_or_raise_conflict(
    conn: sqlite3.Connection, sql: str, params: tuple, milestone_id: int
) -> None:
    """버전 조건부 UPDATE. SELECT(_check_version)와 이 UPDATE 사이에 다른 트랜잭션이 먼저
    같은 행을 바꿨으면 0행 매치로 조용히 "성공"해버릴 수 있으므로, rowcount로 실제 반영 여부를
    확인한다(G17 — SELECT 시점 검사만으로는 동시 수정 경쟁을 못 잡는다)."""
    cursor = conn.execute(sql, params)
    if cursor.rowcount == 0:
        raise ConcurrentModificationError(
            f"milestone {milestone_id}는 이미 다른 사용자가 바꿨습니다. 최신 상태를 다시 불러오세요."
        )


def approve(conn: sqlite3.Connection, milestone_id: int, actor_id: int, expected_version: int) -> None:
    row = _check_version(conn, milestone_id, expected_version)
    if row["is_approved"]:
        raise AlreadyApprovedError(f"milestone {milestone_id}는 이미 승인되었습니다")  # G08
    _update_or_raise_conflict(
        conn,
        "UPDATE milestones SET is_approved = 1, approved_by = ?, approved_at = ?, version = version + 1 "
        "WHERE id = ? AND version = ?",
        (actor_id, _now(), milestone_id, expected_version),
        milestone_id,
    )
    _insert_event(conn, milestone_id, "approve", actor_id, reason=None)
    conn.commit()


def reject(
    conn: sqlite3.Connection, milestone_id: int, actor_id: int, reason: str, expected_version: int
) -> None:
    """반려. 공식값은 그대로 두고 사유만 기록한다(G06). 담당자가 보완 후 재요청한다."""
    _check_version(conn, milestone_id, expected_version)
    _insert_event(conn, milestone_id, "reject", actor_id, reason=reason)
    conn.commit()


def revoke(
    conn: sqlite3.Connection, milestone_id: int, actor_id: int, reason: str, expected_version: int
) -> None:
    """승인 취소. 승인된 마일스톤만 취소할 수 있다(G07)."""
    row = _check_version(conn, milestone_id, expected_version)
    if not row["is_approved"]:
        raise NotApprovedError(f"milestone {milestone_id}는 승인 상태가 아닙니다")
    _update_or_raise_conflict(
        conn,
        "UPDATE milestones SET is_approved = 0, approved_by = NULL, approved_at = NULL, "
        "version = version + 1 WHERE id = ? AND version = ?",
        (milestone_id, expected_version),
        milestone_id,
    )
    _insert_event(conn, milestone_id, "revoke", actor_id, reason=reason)
    conn.commit()


def assert_evidence_deletable(is_milestone_approved: bool) -> None:
    """승인된 마일스톤에 연결된 증빙은 삭제 전에 승인 취소부터 해야 한다(G20)."""
    if is_milestone_approved:
        raise EvidenceLockedError("승인된 마일스톤의 증빙입니다. 먼저 승인을 취소하세요.")
