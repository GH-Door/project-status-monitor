"""demo_seed 테스트 — 명세(docs/design/mock-data.md)의 진척률·경고·팀 구성이 그대로 나오는지 확인한다."""

from datetime import date

from psm import demo_seed
from psm.alerts import evaluate_alerts
from psm.models import Milestone, ProjectInfo
from psm.progress import calc_actual, calc_planned

TODAY = date(2026, 10, 1)


def _milestones(conn, project_id):
    rows = conn.execute("SELECT * FROM milestones WHERE project_id = ?", (project_id,)).fetchall()
    return [
        Milestone(
            id=r["id"], name=r["name"], weight=r["weight"],
            due_date=date.fromisoformat(r["due_date"]), is_approved=bool(r["is_approved"]),
            last_checked_at=date.fromisoformat(r["last_checked_at"]) if r["last_checked_at"] else None,
        )
        for r in rows
    ]


def _project_id(conn, name):
    return conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()["id"]


def test_seed_creates_three_projects_and_is_idempotent(conn):
    assert demo_seed.seed(conn, TODAY) == 3
    assert demo_seed.seed(conn, TODAY) == 0
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 3


def test_team_sizes_match_spec(conn):
    demo_seed.seed(conn, TODAY)
    sizes = {
        spec.name: conn.execute(
            "SELECT COUNT(*) FROM project_members WHERE project_id = ?", (_project_id(conn, spec.name),)
        ).fetchone()[0]
        for spec in demo_seed.DEMO_PROJECTS
    }

    assert sizes == {"사내 문서 검색 포털": 6, "고객 문의 자동분류": 5, "ERP 데이터 이관": 4}


def test_progress_and_alerts_match_scenarios(conn):
    demo_seed.seed(conn, TODAY)

    def evaluate(name):
        milestones = _milestones(conn, _project_id(conn, name))
        info = ProjectInfo(status="active", owner_assigned=True, goal="x", has_active_baseline=True)
        kinds = [a.kind for a in evaluate_alerts(info, milestones, TODAY)]
        return calc_actual(milestones), calc_planned(milestones, TODAY), kinds

    assert evaluate("사내 문서 검색 포털") == (50.0, 50.0, [])  # 정상
    assert evaluate("고객 문의 자동분류") == (20.0, 60.0, ["지연", "주의"])  # 지연 + 계획 대비 40%p 뒤처짐
    assert evaluate("ERP 데이터 이관") == (25.0, 25.0, ["갱신필요"])  # 갱신필요


def test_sample_project_is_created_once_and_relinked(conn):
    from psm import sample_run

    first = sample_run.ensure_project(conn, "ds-1")
    second = sample_run.ensure_project(conn, "ds-2")

    assert first == second
    assert conn.execute("SELECT dify_dataset_id FROM projects WHERE id = ?", (first,)).fetchone()[0] == "ds-2"
