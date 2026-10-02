"""가상 사업 3개 시드 — 팀장 피드백(가상 데이터로 테스트). 명세는 docs/design/mock-data.md.

날짜는 실행일 기준 상대값이라 언제 실행해도 정상/지연/갱신필요 시나리오가 유지된다.
사용: uv run python -m psm.demo_seed
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psm import approvals
from psm.config import TIMEZONE
from psm.db import connect, init_db

# (표시이름, 아이디, 역할) — 모두 가상 인물. 첫 번째 owner가 승인자.
Member = tuple[str, str, str]
# (이름, 가중치, 기한까지 남은 일수(음수=지남), 승인 여부, 마지막 확인이 며칠 전인지|None)
MilestoneSpec = tuple[str, float, int, bool, int | None]


@dataclass(frozen=True)
class ProjectSpec:
    name: str
    category: str
    stage: str
    goal: str
    members: list[Member]
    milestones: list[MilestoneSpec] = field(default_factory=list)


DEMO_PROJECTS = [
    ProjectSpec(
        "사내 문서 검색 포털", "내부 시스템", "개발",
        "사내 문서를 근거와 함께 검색하는 포털을 12월까지 오픈한다",
        [("김민준 (PM)", "demo.kim", "owner"), ("이서연", "demo.lee", "viewer"),
         ("박지호", "demo.park", "viewer"), ("최유나", "demo.choi", "viewer"),
         ("정도윤", "demo.jung", "viewer"), ("한소희", "demo.han", "viewer")],
        [("요구사항 확정", 20, -30, True, 2), ("검색 엔진 연동", 30, -10, True, 2),
         ("UI 개발", 30, 20, False, None), ("시범 운영", 20, 50, False, None)],
    ),
    ProjectSpec(
        "고객 문의 자동분류", "AI 서비스", "개발",
        "문의 메일을 유형별로 자동 분류해 처리 시간을 줄인다",
        [("오태영 (PM)", "demo.oh", "owner"), ("윤하늘", "demo.yoon", "viewer"),
         ("강민서", "demo.kang", "viewer"), ("신재현", "demo.shin", "viewer"),
         ("임수빈", "demo.lim", "viewer")],
        [("요구분석", 20, -20, True, 3), ("분류 모델 개발", 40, -5, False, None),
         ("통합 테스트", 25, 25, False, None), ("배포", 15, 45, False, None)],
    ),
    ProjectSpec(
        "ERP 데이터 이관", "인프라", "검증",
        "구 ERP 데이터를 신규 시스템으로 무손실 이관한다",
        [("서지훈 (PM)", "demo.seo", "owner"), ("조은비", "demo.jo", "viewer"),
         ("배준영", "demo.bae", "viewer"), ("문채원", "demo.moon", "viewer")],
        [("이관 범위 확정", 25, -40, True, 12), ("스키마 매핑", 25, 15, False, None),
         ("시범 이관", 25, 30, False, None), ("전체 이관", 25, 60, False, None)],
    ),
]


def _seed_project(conn: sqlite3.Connection, spec: ProjectSpec, today: date) -> None:
    project_id = conn.execute(
        "INSERT INTO projects (name, category, goal, stage, status) VALUES (?, ?, ?, ?, 'active')",
        (spec.name, spec.category, spec.goal, spec.stage),
    ).lastrowid

    owner_id = None
    for display_name, username, role in spec.members:
        user_id = conn.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, '!', ?)",
            (username, display_name),
        ).lastrowid
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
            (project_id, user_id, role),
        )
        if role == "owner" and owner_id is None:
            owner_id = user_id

    baseline_id = conn.execute(
        "INSERT INTO baselines (project_id, version, is_active, approved_by, approved_at) "
        "VALUES (?, 1, 1, ?, ?)",
        (project_id, owner_id, datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")),
    ).lastrowid

    for name, weight, due_in_days, approved, checked_days_ago in spec.milestones:
        milestone_id = conn.execute(
            "INSERT INTO milestones (baseline_id, project_id, name, weight, due_date, last_checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                baseline_id, project_id, name, weight,
                (today + timedelta(days=due_in_days)).isoformat(),
                (today - timedelta(days=checked_days_ago)).isoformat() if checked_days_ago is not None else None,
            ),
        ).lastrowid
        conn.commit()
        if approved:
            approvals.request_completion(conn, milestone_id, owner_id)
            approvals.approve(conn, milestone_id, owner_id, expected_version=1)
    conn.commit()


def seed(conn: sqlite3.Connection, today: date | None = None) -> int:
    """아직 없는 가상 사업만 추가한다(재실행해도 중복되지 않음). 새로 만든 사업 수를 반환."""
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    created = 0
    for spec in DEMO_PROJECTS:
        if conn.execute("SELECT 1 FROM projects WHERE name = ?", (spec.name,)).fetchone():
            continue
        _seed_project(conn, spec, today)
        created += 1
    return created


def main() -> None:
    conn = connect()
    init_db(conn)
    print(f"가상 사업 {seed(conn)}개 추가 (이미 있으면 건너뜀)")


if __name__ == "__main__":
    main()
