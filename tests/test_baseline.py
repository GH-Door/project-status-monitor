"""baseline.py 테스트 — 일정 후보를 사람이 기준선으로 확정하고, 완료 표시는 승인 기록을 남긴다."""

import pytest

from psm import auth, baseline, metrics
from psm.overview import load_milestones
from psm.progress import calc_actual

SCHEDULE = """
| ID | 업무 | 담당 | 일정 | 상태 |
|---|---|---|---|---|
| L01 | 소프트런칭 개시 | 가 | 09-14 | 완료 |
| L02 | 9월 매출 마감 | 나 | 10-01 | 완료 |
| L08 | 온라인 정식 출시 | 다 | 10-12 10:00 | 확정 |
| L12 | 잔여 상품 회수 | 라 | 10-19 | 예정 |
"""


@pytest.fixture
def project(conn):
    conn.execute("INSERT INTO users (id, username, password_hash, display_name, is_admin) VALUES (1,'a','x','관리자',1)")
    conn.execute("INSERT INTO users (id, username, password_hash, display_name) VALUES (2,'v','x','조회자')")
    conn.execute("INSERT INTO projects (id, name) VALUES (1, '누베른코스')")
    conn.execute("INSERT INTO projects (id, name) VALUES (2, '다른사업')")
    conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (1, 2, 'viewer')")
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (5, 1, '02_출시_실행일정표.md', 'h', 'text/markdown', 1)"
    )
    conn.commit()
    metrics.save_schedule(conn, 1, 5, SCHEDULE, 2026)
    return {r["name"]: r["id"] for r in metrics.list_candidates(conn, 1)}


def test_adopt_creates_active_baseline_with_equal_weights(conn, project):
    baseline.adopt(conn, 1, actor_id=1, candidate_ids=list(project.values()), completed_ids=[])

    milestones = load_milestones(conn, 1)
    assert [(m.name, m.weight) for m in milestones] == [
        ("소프트런칭 개시", 1.0), ("9월 매출 마감", 1.0), ("온라인 정식 출시", 1.0), ("잔여 상품 회수", 1.0),
    ]
    assert calc_actual(milestones) == 0.0  # 사람이 완료를 확인하기 전에는 0
    assert metrics.list_candidates(conn, 1) == []  # 채택된 후보는 목록에서 빠진다


def test_completed_ids_are_approved_through_the_normal_approval_flow(conn, project):
    done = [project["소프트런칭 개시"], project["9월 매출 마감"]]

    baseline.adopt(conn, 1, actor_id=1, candidate_ids=list(project.values()), completed_ids=done)

    assert calc_actual(load_milestones(conn, 1)) == 50.0
    events = [r["event_type"] for r in conn.execute("SELECT event_type FROM milestone_events ORDER BY id")]
    assert events == ["request", "approve", "request", "approve"]  # 승인 기록이 남는다


def test_new_adoption_replaces_active_baseline_and_keeps_old_one(conn, project):
    baseline.adopt(conn, 1, 1, [project["온라인 정식 출시"]], [])
    first_version = conn.execute("SELECT version FROM baselines WHERE is_active = 1").fetchone()["version"]
    conn.execute(
        "INSERT INTO milestone_candidates (project_id, document_id, name, due_date, status_text) "
        "VALUES (1, 5, '추가 과제', '2026-10-30', '예정')"
    )
    conn.commit()
    extra = [r["id"] for r in metrics.list_candidates(conn, 1) if r["name"] == "추가 과제"]

    baseline.adopt(conn, 1, 1, extra, [])

    active = conn.execute("SELECT version FROM baselines WHERE is_active = 1").fetchall()
    assert [r["version"] for r in active] == [first_version + 1]
    assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 2  # 이전 기준선 보존


def test_candidate_of_another_project_is_rejected(conn, project):
    conn.execute(
        "INSERT INTO milestone_candidates (project_id, document_id, name, due_date, status_text) "
        "VALUES (2, 5, '남의 과제', '2026-10-30', '예정')"
    )
    conn.commit()
    foreign = conn.execute("SELECT id FROM milestone_candidates WHERE project_id = 2").fetchone()["id"]

    with pytest.raises(baseline.BaselineError):
        baseline.adopt(conn, 1, 1, [foreign], [])


def test_completed_must_be_a_subset_of_adopted(conn, project):
    with pytest.raises(baseline.BaselineError):
        baseline.adopt(conn, 1, 1, [project["온라인 정식 출시"]], [project["9월 매출 마감"]])


def test_empty_selection_is_rejected(conn, project):
    with pytest.raises(baseline.BaselineError):
        baseline.adopt(conn, 1, 1, [], [])


def test_viewer_cannot_adopt(conn, project):
    with pytest.raises(auth.AccessDeniedError):
        baseline.adopt(conn, 1, actor_id=2, candidate_ids=[project["온라인 정식 출시"]], completed_ids=[])


def test_unchecked_candidates_are_dismissed_and_no_longer_listed(conn, project):
    keep, drop = project["온라인 정식 출시"], project["잔여 상품 회수"]

    baseline.adopt(conn, 1, 1, [keep], [], dismissed_ids=[drop])

    assert drop not in [c["id"] for c in metrics.list_candidates(conn, 1)]
    assert [m.name for m in load_milestones(conn, 1)] == ["온라인 정식 출시"]  # 제외한 것은 마일스톤이 아니다


def test_dismissing_without_any_selection_keeps_current_baseline(conn, project):
    baseline.adopt(conn, 1, 1, [project["온라인 정식 출시"]], [])

    result = baseline.adopt(conn, 1, 1, [], [], dismissed_ids=[project["잔여 상품 회수"]])

    assert result is None
    assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 1  # 새 기준선을 만들지 않는다
    assert [m.name for m in load_milestones(conn, 1)] == ["온라인 정식 출시"]


def test_dismissed_must_belong_to_the_project(conn, project):
    with pytest.raises(baseline.BaselineError):
        baseline.adopt(conn, 1, 1, [project["온라인 정식 출시"]], [], dismissed_ids=[99999])


def test_new_adoption_keeps_existing_milestones_and_their_approvals(conn, project):
    """새 일정을 추가 확정해도 이미 승인된 마일스톤과 진척률이 사라지면 안 된다."""
    first = [project["소프트런칭 개시"], project["9월 매출 마감"]]
    baseline.adopt(conn, 1, 1, first, completed_ids=[first[0]])
    assert calc_actual(load_milestones(conn, 1)) == 50.0

    later = [project["온라인 정식 출시"], project["잔여 상품 회수"]]
    baseline.adopt(conn, 1, 1, later, [])

    names = sorted(m.name for m in load_milestones(conn, 1))
    assert names == sorted(["소프트런칭 개시", "9월 매출 마감", "온라인 정식 출시", "잔여 상품 회수"])
    assert calc_actual(load_milestones(conn, 1)) == 25.0  # 승인 1건 / 전체 4건


def test_readopting_a_milestone_that_already_exists_does_not_duplicate_it(conn, project):
    baseline.adopt(conn, 1, 1, [project["온라인 정식 출시"]], [])
    conn.execute(  # 다른 일정표(개정본)에도 같은 일정이 적혀 있다
        "INSERT INTO documents (id, project_id, filename, sha256, mime_type, uploaded_by) "
        "VALUES (6, 1, '02_출시_실행일정표_개정.md', 'h2', 'text/markdown', 1)"
    )
    conn.execute(
        "INSERT INTO milestone_candidates (project_id, document_id, name, due_date, status_text) "
        "VALUES (1, 6, '온라인 정식 출시', '2026-10-12', '확정')"
    )
    conn.commit()
    dup = [c["id"] for c in metrics.list_candidates(conn, 1) if c["name"] == "온라인 정식 출시"]

    baseline.adopt(conn, 1, 1, dup, [])

    assert [m.name for m in load_milestones(conn, 1)] == ["온라인 정식 출시"]
