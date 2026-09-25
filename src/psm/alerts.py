"""경고 판정 — 순수 함수. Asia/Seoul 일 단위, 다음 날부터 기한 초과로 본다(기획서 §5).

상태 5종: 지연 / 주의 / 갱신필요 / 정보부족 / 정상.
보류·종료 사업(status != active)은 활성 경고 집계에서 제외한다.
"""

from __future__ import annotations

from datetime import date, timedelta

from psm.config import BEHIND_SCHEDULE_GAP_PP, DUE_SOON_DAYS, STALE_CHECK_DAYS
from psm.models import Alert, Milestone, ProjectInfo
from psm.progress import calc_actual, calc_gap, calc_planned


def evaluate_alerts(project: ProjectInfo, milestones: list[Milestone], today: date) -> list[Alert]:
    if project.status != "active":
        return []

    if not (project.owner_assigned and project.goal and project.has_active_baseline):
        # ponytail: 세부 항목별 경고 대신 단일 "정보부족"으로 단순화. 실제 필드별 원인 표시가
        # 필요해지면 owner/goal/baseline 각각을 별도 Alert로 나눈다.
        return [Alert("정보부족", "담당자·방향·기준선 중 누락된 값이 있습니다.")]

    return [
        *_overdue(milestones, today),
        *_due_soon_or_behind(milestones, today),
        *_stale_check(milestones, today),
    ]


def _overdue(milestones: list[Milestone], today: date) -> list[Alert]:
    late = [m for m in milestones if not m.is_approved and m.due_date < today]
    if not late:
        return []
    names = ", ".join(m.name for m in late)
    return [Alert("지연", f"예정일이 지난 미승인 마일스톤: {names}")]


def _due_soon_or_behind(milestones: list[Milestone], today: date) -> list[Alert]:
    soon_cutoff = today + timedelta(days=DUE_SOON_DAYS)
    due_soon = [
        m for m in milestones if not m.is_approved and today <= m.due_date <= soon_cutoff
    ]
    if due_soon:
        names = ", ".join(m.name for m in due_soon)
        return [Alert("주의", f"기한 {DUE_SOON_DAYS}일 이내 미완료 마일스톤: {names}")]

    gap = calc_gap(calc_actual(milestones), calc_planned(milestones, today))
    if gap is not None and gap <= -BEHIND_SCHEDULE_GAP_PP:
        return [Alert("주의", f"계획 대비 {abs(gap)}%p 뒤처짐")]

    return []


def _stale_check(milestones: list[Milestone], today: date) -> list[Alert]:
    stale = [
        m
        for m in milestones
        if m.last_checked_at is not None
        and (today - m.last_checked_at).days > STALE_CHECK_DAYS
    ]
    if not stale:
        return []
    names = ", ".join(m.name for m in stale)
    return [Alert("갱신필요", f"담당자 확인 후 {STALE_CHECK_DAYS}일 초과: {names}")]
