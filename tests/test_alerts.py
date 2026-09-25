"""경고 판정 테스트 — 기획서 §5 상태 표 + 경계값."""

from datetime import date, timedelta

from psm.alerts import evaluate_alerts
from psm.models import Milestone, ProjectInfo

TODAY = date(2026, 9, 25)


def _milestone(weight=50, due_date=TODAY, approved=False, last_checked_at=None):
    return Milestone(
        id=1,
        name="m",
        weight=weight,
        due_date=due_date,
        is_approved=approved,
        last_checked_at=last_checked_at,
    )


def _project(**overrides):
    defaults = {"status": "active", "owner_assigned": True, "goal": "목표", "has_active_baseline": True}
    return ProjectInfo(**{**defaults, **overrides})


def test_paused_project_has_no_alerts():
    milestones = [_milestone(due_date=TODAY - timedelta(days=10))]
    assert evaluate_alerts(_project(status="paused"), milestones, TODAY) == []


def test_missing_owner_yields_info_missing_only():
    alerts = evaluate_alerts(_project(owner_assigned=False), [], TODAY)
    assert [a.kind for a in alerts] == ["정보부족"]


def test_overdue_unapproved_milestone_is_delayed():
    milestones = [_milestone(due_date=TODAY - timedelta(days=1))]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "지연" in [a.kind for a in alerts]


def test_overdue_but_approved_milestone_is_not_delayed():
    milestones = [_milestone(due_date=TODAY - timedelta(days=1), approved=True)]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "지연" not in [a.kind for a in alerts]


def test_due_within_three_days_is_caution():
    milestones = [_milestone(due_date=TODAY + timedelta(days=3))]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "주의" in [a.kind for a in alerts]


def test_due_in_four_days_is_not_caution_by_deadline_rule():
    milestones = [_milestone(due_date=TODAY + timedelta(days=4))]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "주의" not in [a.kind for a in alerts]


def test_ten_points_behind_schedule_is_caution():
    # 가중치 20/50/30 중 첫 항목만 승인(실제 20%), 전부 기한 지남(계획 100%) → gap -80pp
    milestones = [
        _milestone(weight=20, due_date=TODAY - timedelta(days=1), approved=True),
        _milestone(weight=50, due_date=TODAY - timedelta(days=1), approved=False),
        _milestone(weight=30, due_date=TODAY - timedelta(days=1), approved=False),
    ]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "주의" in [a.kind for a in alerts]


def test_stale_check_over_seven_days_needs_refresh():
    milestones = [
        _milestone(
            due_date=TODAY + timedelta(days=30),
            last_checked_at=TODAY - timedelta(days=8),
        )
    ]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "갱신필요" in [a.kind for a in alerts]


def test_check_within_seven_days_is_not_stale():
    milestones = [
        _milestone(
            due_date=TODAY + timedelta(days=30),
            last_checked_at=TODAY - timedelta(days=7),
        )
    ]
    alerts = evaluate_alerts(_project(), milestones, TODAY)
    assert "갱신필요" not in [a.kind for a in alerts]


def test_healthy_project_has_no_alerts():
    milestones = [
        _milestone(
            due_date=TODAY + timedelta(days=30),
            approved=True,
            last_checked_at=TODAY,
        )
    ]
    assert evaluate_alerts(_project(), milestones, TODAY) == []
