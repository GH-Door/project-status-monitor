"""일정 후보 → 기준선 확정(F04). 사람이 고른 후보만 마일스톤이 된다.

가중치는 일단 모두 같다(후보에는 가중치 정보가 없다). 완료로 표시한 항목은 일반 승인 흐름
(완료 요청 → 승인)을 그대로 거쳐 이력이 남는다 — 진척률은 여전히 사람의 승인으로만 오른다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from psm import approvals
from psm.auth import require_project_access

_DEFAULT_WEIGHT = 1.0


class BaselineError(Exception):
    pass


def _candidate_rows(
    conn: sqlite3.Connection, project_id: int, candidate_ids: list[int]
) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"SELECT id, project_id, name, due_date FROM milestone_candidates "
        f"WHERE id IN ({marks}) ORDER BY due_date, id",
        candidate_ids,
    ).fetchall()
    if len(rows) != len(set(candidate_ids)) or any(r["project_id"] != project_id for r in rows):
        raise BaselineError("이 사업의 일정 후보가 아닌 항목이 포함되어 있습니다")
    return rows


_DISMISSED = 2  # milestone_candidates.adopted: 0 대기, 1 채택, 2 제외


def _dismiss(conn: sqlite3.Connection, project_id: int, dismissed_ids: list[int]) -> None:
    if not dismissed_ids:
        return
    _candidate_rows(conn, project_id, dismissed_ids)  # 이 사업의 후보인지 검증
    marks = ",".join("?" * len(dismissed_ids))
    conn.execute(
        f"UPDATE milestone_candidates SET adopted = ? WHERE id IN ({marks})", [_DISMISSED, *dismissed_ids]
    )
    conn.commit()


def adopt(
    conn: sqlite3.Connection,
    project_id: int,
    actor_id: int,
    candidate_ids: list[int],
    completed_ids: list[int],
    dismissed_ids: list[int] | None = None,
) -> int | None:
    """새 활성 기준선을 만들고 baseline_id를 반환한다. 이전 기준선은 비활성으로 보존한다.

    사람이 체크 해제한 후보(dismissed_ids)는 목록에서 치운다. 고른 일정이 하나도 없고 제외만 했다면
    기준선은 그대로 두고 None을 반환한다.
    """
    require_project_access(conn, actor_id, project_id, action="approve")
    dismissed_ids = dismissed_ids or []
    if not candidate_ids:
        if not dismissed_ids:
            raise BaselineError("기준선으로 확정할 일정을 하나 이상 고르세요")
        _dismiss(conn, project_id, dismissed_ids)
        return None
    if not set(completed_ids) <= set(candidate_ids):
        raise BaselineError("완료로 표시한 항목은 선택한 일정 안에 있어야 합니다")
    rows = _candidate_rows(conn, project_id, candidate_ids)
    _dismiss(conn, project_id, dismissed_ids)

    version = (
        conn.execute("SELECT COALESCE(MAX(version), 0) FROM baselines WHERE project_id = ?", (project_id,)).fetchone()[0]
        + 1
    )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    carried = conn.execute(
        "SELECT m.name, m.weight, m.due_date, m.is_approved, m.approved_by, m.approved_at, m.version, "
        "m.last_checked_at FROM milestones m JOIN baselines b ON b.id = m.baseline_id "
        "WHERE b.project_id = ? AND b.is_active = 1",
        (project_id,),
    ).fetchall()
    conn.execute("UPDATE baselines SET is_active = 0 WHERE project_id = ?", (project_id,))
    baseline_id = conn.execute(
        "INSERT INTO baselines (project_id, version, is_active, approved_by, approved_at) "
        "VALUES (?, ?, 1, ?, ?)",
        (project_id, version, actor_id, now),
    ).lastrowid

    # 이전 기준선의 마일스톤(승인 상태 포함)을 그대로 이어받는다 — 일정을 추가 확정해도 진척이 사라지지 않는다.
    existing = {(m["name"], m["due_date"]) for m in carried}
    for m in carried:
        conn.execute(
            "INSERT INTO milestones (baseline_id, project_id, name, weight, due_date, is_approved, "
            "approved_by, approved_at, version, last_checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (baseline_id, project_id, m["name"], m["weight"], m["due_date"], m["is_approved"],
             m["approved_by"], m["approved_at"], m["version"], m["last_checked_at"]),
        )

    milestone_ids: dict[int, int] = {}
    for row in rows:
        if (row["name"], row["due_date"]) in existing:
            continue  # 이미 있는 마일스톤은 중복으로 만들지 않는다
        existing.add((row["name"], row["due_date"]))
        milestone_ids[row["id"]] = conn.execute(
            "INSERT INTO milestones (baseline_id, project_id, name, weight, due_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (baseline_id, project_id, row["name"], _DEFAULT_WEIGHT, row["due_date"]),
        ).lastrowid
    marks = ",".join("?" * len(candidate_ids))
    conn.execute(f"UPDATE milestone_candidates SET adopted = 1 WHERE id IN ({marks})", candidate_ids)
    conn.commit()

    for candidate_id in completed_ids:
        milestone_id = milestone_ids.get(candidate_id)
        if milestone_id is None:
            continue  # 이미 있던 마일스톤: 완료 여부는 기존 승인 흐름으로만 바뀐다
        approvals.request_completion(conn, milestone_id, actor_id)
        approvals.approve(conn, milestone_id, actor_id, expected_version=1)
    return baseline_id
